# System Architecture: Manager / Worker / SharedRingBuffer

---

## The Mental Model (Plain English First)

```
┌─────────────────────────────────────────┐
│  FLOOR 3: Manager                        │
│  The foreman. Owns everything.           │
│  Resolves specs into live objects.       │
│  Composes tasks. Spawns workers.         │
│  Cleans up when things die.              │
├─────────────────────────────────────────┤
│  FLOOR 2: Worker                         │
│  A dumb execution primitive.             │
│  Receives one composed callable.         │
│  Calls it in a loop. Nothing else.       │
├─────────────────────────────────────────┤
│  FLOOR 1: Specs + SharedRingBuffer       │
│  Pure config. No logic. No state.        │
│  RingSpec, EventSpec, TaskSpec,          │
│  WorkerSpec. Describes what things       │
│  should be. User constructs these        │
│  directly and passes them to Manager.    │
└─────────────────────────────────────────┘
```

Manager is the **root process**. It forks everything. Workers never know about
each other. SharedRingBuffer is the only thing they share at the OS level.

The consistent philosophy across all three floors:

> **Specs describe. Manager resolves. Worker executes.**

---

## Floor 1: Specs — `@dataclass(frozen=True)` + `__post_init__`

Specs are **pure configuration objects**. No logic. No state. No live objects.
They cross the process boundary via pickling, so they must be frozen and contain
only plain values — strings, ints, tuples, callables.

The user constructs specs directly and passes them to Manager. Every `create_*`
method on Manager takes a spec — there are no raw kwargs APIs. All validation
happens in `__post_init__` on the spec itself before Manager ever sees it.

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RingSpec:
    name:        str
    size:        int
    num_readers: int
    cache_align: bool = True
    cache_size:  int  = 64

    def __post_init__(self):
        if self.size <= 0:
            raise ValueError(f"RingSpec '{self.name}': size must be > 0")
        if self.num_readers < 1:
            raise ValueError(f"RingSpec '{self.name}': need at least 1 reader")
        if self.cache_align and (self.cache_size & (self.cache_size - 1)):
            raise ValueError(f"RingSpec '{self.name}': cache_size must be a power of two")

    def to_kwargs(self, *, create: bool, reader: int) -> dict:
        return dict(
            name=self.name, create=create, size=self.size,
            num_readers=self.num_readers, reader=reader,
            cache_align=self.cache_align, cache_size=self.cache_size,
        )

    def __repr__(self):
        return f"RingSpec(name={self.name!r}, size={self.size}, readers={self.num_readers})"


@dataclass(frozen=True)
class EventSpec:
    name:          str
    initial_state: bool = False

    def __repr__(self):
        state = "OPEN" if self.initial_state else "CLOSED"
        return f"EventSpec(name={self.name!r}, [{state}])"


@dataclass(frozen=True)
class TaskSpec:
    """
    One logical unit of work inside a worker's cycle.

    - fn:     the function to call.
    - events: event names this task interacts with. What to do with them
              (wait, signal, both) is determined by fn itself. The OS handles
              scheduling. The framework does not presume direction.
    - rings:  ring names this task uses. Whether it reads or writes is up to fn.
    - args / kwargs: passed directly to fn.

    Error handling is the responsibility of fn. If a task needs to recover
    from a failure, that logic lives inside fn — not in the framework.
    """
    fn:     callable
    events: tuple[str, ...] = ()
    rings:  tuple[str, ...] = ()
    args:   tuple           = ()
    kwargs: dict            = None

    def __post_init__(self):
        object.__setattr__(self, 'kwargs', self.kwargs or {})

    def __repr__(self):
        return f"TaskSpec(fn={self.fn.__name__!r}, events={self.events}, rings={self.rings})"


@dataclass(frozen=True)
class WorkerSpec:
    """
    A worker is one process running one or more tasks in sequence per cycle.

    - rings:  all rings this worker's process will open at startup.
              Rings are process-scoped — opened once, closed once on exit.
              Tasks share these open handles. Tasks do not open their own.
              This is a hard constraint: shared memory handles must be opened
              in the process that will use them, never in the parent.
    - events: all events this worker interacts with across all its tasks.
    - tasks:  at least one required. A single-task worker is just a worker
              with one task. There is no special single-function path —
              a task is always a task.

    Manager composes all tasks into one callable before forking.
    Worker never sees tasks — it receives one function and calls it in a loop.
    """
    name:   str
    rings:  tuple[RingSpec, ...]
    events: tuple[EventSpec, ...]
    tasks:  tuple[TaskSpec, ...]

    def __post_init__(self):
        if len(self.tasks) == 0:
            raise ValueError(f"WorkerSpec '{self.name}': must have at least one task")

    def __repr__(self):
        return f"WorkerSpec(name={self.name!r}, tasks={len(self.tasks)}, rings={len(self.rings)})"
```

**Why `frozen=True`?** Specs travel across process boundaries via pickling.
Mutable specs create bugs where a child sees a different spec than the parent
created. Frozen makes that class of mistake impossible.

**Why tuples not lists?** Hashable, picklable cleanly, and communicates
"this does not change after construction."

**Why are rings and events referenced by name strings in TaskSpec?** Because
specs are pure data. They hold no live objects. Manager resolves those strings
into real `SharedRingBuffer` and `WorkerEvent` instances inside the child
process at `start()` time — after the fork, in the address space that will
actually use them.

**Why no in/out distinction on rings or events?** The OS handles scheduling.
Whether a ring is read or written, whether an event is waited on or signalled,
is logic inside the task function — not metadata the framework needs to track.
The system does not presume to know the direction of data flow.

---

## Floor 1: SharedRingBuffer — `__enter__`/`__exit__` + `weakref.finalize`

Two additions to the existing class give deterministic cleanup with a safety net:

```python
import weakref
from multiprocessing import shared_memory

class SharedRingBuffer(shared_memory.SharedMemory):

    def __init__(self, name, create, size, num_readers, reader, ...):
        super().__init__(name=name, create=create, size=...)
        self._is_creator = create
        # ... existing init ...

        # Safety net: fires even if the process dies hard.
        # RULE: cleanup fn must NOT reference self — only plain serialisable values.
        # Holding a reference to self would keep self alive, defeating the purpose.
        weakref.finalize(
            self,
            SharedRingBuffer._finalizer_cleanup,
            self.name,
            create,
        )

    @staticmethod
    def _finalizer_cleanup(name: str, is_creator: bool) -> None:
        """Called by GC or interpreter shutdown. Must be static — no self reference."""
        try:
            shm = shared_memory.SharedMemory(name=name)
            shm.close()
            if is_creator:
                shm.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.ring_buffer.release()
        self.close()
        if self._is_creator:
            try:
                self.unlink()
            except FileNotFoundError:
                pass
```

Two-layer cleanup strategy:
- `__exit__` is the **normal path** — deterministic, immediate, preferred.
- `weakref.finalize` is the **safety net** — fires on crash or interpreter
  shutdown when `__exit__` never gets called. Shared memory lives at the OS
  level and persists until explicitly unlinked, so this matters.

---

## Floor 2: Worker — a dumb execution primitive

Worker has one job: call a function in a loop until it receives `SIGTERM`.
It knows nothing about tasks, events, rings, or composition. All of that
was Manager's job. By the time Worker is constructed it has one callable
and that is all.

```python
import signal
import multiprocessing as mp

class Worker:
    """
    One process. One loop. One function.
    Manager composes everything before handing it here.
    """

    # Registry for subclasses that need a genuinely different execution loop —
    # for example an asyncio-based worker or one that polls instead of blocking.
    # For all normal cases plain Worker is the right choice.
    # Populates automatically when a subclass is defined:
    #
    #   class AsyncWorker(Worker, worker_type="async"):
    #       def __call__(self): ...   # custom loop
    _registry: dict[str, type[Worker]] = {}

    def __init_subclass__(cls, worker_type: str = None, **kw):
        super().__init_subclass__(**kw)
        if worker_type:
            Worker._registry[worker_type] = cls

    def __init__(self, fn: callable):
        self._fn = fn   # composed by Manager — one opaque callable

    def __call__(self) -> None:
        """
        The process target.
        SIGTERM raises SystemExit which exits the loop cleanly.

        NOTE: on Windows proc.terminate() calls TerminateProcess which
        is equivalent to SIGKILL — the process dies immediately and the
        signal handler never runs. This is a Windows platform limitation.
        Document this for users; do not complicate the code to work around it.
        Shared memory cleanup is still handled by weakref.finalize on each
        SharedRingBuffer and by Manager.close() on the parent side.
        """
        def _handle_sigterm(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _handle_sigterm)

        while True:
            self._fn()

    def __repr__(self):
        return f"<Worker fn={self._fn.__name__}>"
```

The signal handler must be registered inside `__call__`, not in `__init__`.
Signal handlers must be set in the process that receives them. Setting them
in the parent before forking produces undefined behaviour with `spawn` context.

The hot path is `while True` → call function → repeat. No branches, no
attribute lookups, no event checks in the loop itself.

---

## Floor 3: Manager — resolves, composes, forks

Manager owns ring specs, live rings, event specs, live events, worker specs,
and live processes. Every `create_*` method takes a spec — Manager's job is
purely to turn those specs into live objects and wire them together.

**Critical constraint:** ring handles are opened in the child process, never
the parent. `mmap`-backed handles are tied to the address space of the process
that opened them. With the `spawn` context the child gets a fresh interpreter —
any live handle constructed in the parent is garbage by the time the child runs.
Only plain data (dicts of strings, ints, bools) crosses the process boundary.
Composition happens inside the child after handles are open.

```python
import signal
import logging
import multiprocessing as mp
from functools import wraps

class Manager:

    def __init__(self, mp_context: str = "spawn"):
        self._ctx           = mp.get_context(mp_context)
        self._ring_specs:   dict[str, RingSpec]         = {}
        self._rings:        dict[str, SharedRingBuffer] = {}  # strong refs: Manager owns
        self._event_specs:  dict[str, EventSpec]        = {}
        self._events:       dict[str, WorkerEvent]      = {}
        self._worker_specs: dict[str, WorkerSpec]       = {}
        self._processes:    dict[str, mp.Process]       = {}

    # ------------------------------------------------------------------ #
    # Registration — every method takes a spec, nothing else             #
    # ------------------------------------------------------------------ #

    def create_ring(self, spec: RingSpec) -> Manager:
        self._ring_specs[spec.name] = spec
        self._rings[spec.name] = SharedRingBuffer(**spec.to_kwargs(create=True, reader=0))
        return self

    def create_event(self, spec: EventSpec) -> Manager:
        self._event_specs[spec.name] = spec
        self._events[spec.name] = WorkerEvent(name=spec.name, initial_state=spec.initial_state)
        return self

    def create_worker(self, spec: WorkerSpec) -> Manager:
        self._worker_specs[spec.name] = spec
        return self

    # ------------------------------------------------------------------ #
    # Start — single public entry point for launching a worker            #
    # ------------------------------------------------------------------ #

    def start(self, name: str) -> mp.Process:
        spec = self._worker_specs[name]

        # Serialize ring specs to plain dicts — strings, ints, bools only.
        # These are the only things safe to pickle across the process boundary.
        # Live SharedRingBuffer handles are NOT passed here; they would carry
        # mmap pointers and file descriptors valid only in the parent's address
        # space, producing silent corruption or crashes in the child.
        ring_kwargs = [
            self._ring_specs[r.name].to_kwargs(create=False, reader=i)
            for i, r in enumerate(spec.rings)
        ]

        WorkerClass = Worker._registry.get(getattr(spec, 'worker_type', None), Worker)

        # _bootstrap runs entirely inside the child process.
        # It opens ring handles there, composes tasks against those handles,
        # then hands the composed callable to Worker.
        # Manager is NOT captured — it must never cross the boundary.
        def _bootstrap(spec=spec, ring_kwargs=ring_kwargs):
            rings = [SharedRingBuffer(**kw) for kw in ring_kwargs]
            try:
                composed = Manager._compose_tasks_static(spec, rings)
                WorkerClass(fn=composed)()
            finally:
                # Deterministic cleanup: close and unlink all child-side handles
                # regardless of how the loop exits (SystemExit from SIGTERM,
                # unhandled exception, or normal return).
                # weakref.finalize on each SharedRingBuffer is the hard safety
                # net beneath this.
                for r in rings:
                    r.__exit__(None, None, None)

        _bootstrap.__name__ = f"{spec.name}_bootstrap"
        proc = self._ctx.Process(target=_bootstrap, name=name, daemon=True)
        proc.start()
        self._processes[name] = proc
        return proc

    # ------------------------------------------------------------------ #
    # Composition — static: must not reference Manager or any live object #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compose_tasks_static(spec: WorkerSpec, rings: list[SharedRingBuffer]) -> callable:
        """
        Turn a sequence of TaskSpecs into one callable.
        Must be a staticmethod — self is Manager, and Manager must never
        be referenced inside _bootstrap (which runs in the child process).
        Capturing self would drag Manager across the process boundary.

        rings are the child-local handles opened by _bootstrap.
        All resolution and wiring happens here, once, before the hot loop starts.
        Worker receives an opaque callable — it never sees tasks.
        """
        ring_by_name = {r.name: r for r in rings}
        resolved = []

        for task in spec.tasks:
            task_rings  = [ring_by_name[n] for n in task.rings if n in ring_by_name]
            wrapped_fn  = Manager._wrap_fn_static(task.fn, label=f"{spec.name}/{task.fn.__name__}")
            effective_args = task.args if task.args else (task_rings,)
            resolved.append((wrapped_fn, effective_args, task.kwargs))

        def _composed():
            for fn, args, kwargs in resolved:
                fn(*args, **kwargs)

        _composed.__name__ = f"{spec.name}_composed"
        return _composed

    @staticmethod
    def _wrap_fn_static(fn: callable, label: str) -> callable:
        """
        Wrap any task function with logging.
        `label` is "worker_name/task_fn_name" so per-task timing is
        visible in logs automatically.
        functools.wraps preserves __name__ and __doc__ on the wrapper.
        Must be a staticmethod for the same reason as _compose_tasks_static.
        Tasks and Worker never see this — it is purely Manager's concern.
        """
        @wraps(fn)
        def _logged(*args, **kwargs):
            logging.debug("[%s] start", label)
            result = fn(*args, **kwargs)
            logging.debug("[%s] end", label)
            return result
        return _logged

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def stop(self, name: str) -> None:
        proc = self._processes.get(name)
        if proc and proc.is_alive():
            proc.terminate()

    def join(self, name: str, timeout: float = 5.0) -> None:
        proc = self._processes.get(name)
        if proc:
            proc.join(timeout=timeout)

    def stop_all(self) -> None:
        for name in list(self._processes):
            self.stop(name)

    def join_all(self, timeout: float = 5.0) -> None:
        for name in list(self._processes):
            self.join(name, timeout=timeout)

    def close(self) -> None:
        for ring in self._rings.values():
            ring.__exit__(None, None, None)
        self._rings.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop_all()
        self.join_all()
        self.close()

    def __repr__(self):
        return (
            f"<Manager workers={list(self._worker_specs)} "
            f"rings={list(self._ring_specs)} "
            f"events={list(self._event_specs)}>"
        )
```

---

## What User Code Looks Like

### Single task worker

```python
def produce(rings):
    ring = rings[0]
    # read or write — the framework does not care which

with Manager() as mgr:
    (mgr
     .create_ring(RingSpec("pipe", size=1024*1024, num_readers=1))
     .create_event(EventSpec("go", initial_state=True))
     .create_worker(WorkerSpec(
         name="producer",
         rings=(RingSpec("pipe", 1024*1024, 1),),
         events=(EventSpec("go"),),
         tasks=(
             TaskSpec(fn=produce, rings=("pipe",), events=("go",)),
         ),
     ))
    )
    mgr.start("producer")
```

### Multi-task worker — sequential checkpoints in one process

```python
def fetch(rings):
    ring = rings[0]
    # read a batch — error handling lives here if needed

def process(rings):
    # transform — error handling lives here if needed

def emit(rings):
    ring = rings[0]
    # write results — error handling lives here if needed

with Manager() as mgr:
    (mgr
     .create_ring(RingSpec("raw",     size=1024*1024, num_readers=1))
     .create_ring(RingSpec("results", size=1024*1024, num_readers=1))
     .create_event(EventSpec("data_ready", initial_state=True))
     .create_worker(WorkerSpec(
         name="pipeline",
         rings=(
             RingSpec("raw",     1024*1024, 1),
             RingSpec("results", 1024*1024, 1),
         ),
         events=(EventSpec("data_ready"),),
         tasks=(
             TaskSpec(fn=fetch,   rings=("raw",)),
             TaskSpec(fn=process, rings=("raw",)),
             TaskSpec(fn=emit,    rings=("results",)),
         ),
     ))
    )
    mgr.start("pipeline")
# __exit__ stops, joins, cleans up all shared memory
```

All three task functions run in one process. They share the same open ring
handles — handles that were opened inside that process, not passed in from
the parent. Manager composed them into one callable inside `_bootstrap`.
Worker calls that callable in a loop and knows nothing about the tasks inside
it. Per-task timing appears in logs automatically because Manager wrapped each
task function individually before composing them.

---

## Pattern Map

| Pattern | Where | Why |
|---|---|---|
| `@dataclass(frozen=True)` | All specs | Cross process boundary via pickle; immutability prevents drift |
| `__post_init__` | All specs | Validate at construction, not at use |
| `from __future__ import annotations` | Module top | Forward references without quoting type hints manually |
| `weakref.finalize` | `SharedRingBuffer.__init__` | OS-level leak guard, fires even on hard process death |
| `__enter__`/`__exit__` | `SharedRingBuffer`, `Manager` | Deterministic cleanup on the normal path |
| `signal.SIGTERM` handler | `Worker.__call__` | Turns `proc.terminate()` into clean `SystemExit`; not graceful on Windows |
| `__call__` | `Worker` | Worker IS the process target — two line hot loop, no branches |
| `__init_subclass__` | `Worker` | Auto-registration for subclasses that need a genuinely different loop |
| Spec-only `create_*` methods | `Manager` | Uniform API; all validation happens in specs before Manager sees them |
| `ring_kwargs` dicts in `start()` | `Manager` | Only plain data crosses the process boundary; live handles stay in child |
| `_bootstrap` closure in `start()` | `Manager` | Opens ring handles in child address space; Manager never crosses boundary |
| `_compose_tasks_static` | `Manager` | `@staticmethod` enforces no `self` reference — Manager cannot leak into child |
| `try/finally` in `_bootstrap` | `Manager` | Deterministic child-side ring cleanup regardless of how the loop exits |
| `functools.wraps` in `_wrap_fn_static` | `Manager` | Per-task logging injected transparently; tasks and Worker never see it |
| Fluent `return self` | `Manager.create_*` | Builder pattern for readable pipeline setup |

---

## The Rules

> **Specs describe. Manager resolves. Worker executes.**

> **Rings are process-scoped. Tasks are logic-scoped.**

> **Handles open where they are used. Specs travel. Objects do not.**

> **Error handling belongs in the task function, not the framework.**

> **Every `create_*` method takes a spec. Nothing else.**

Manager turns specs into plain kwargs dicts, passes those into the child
process, opens ring handles there, composes tasks against those handles,
then runs the Worker loop — all inside `_bootstrap`. Once forked the Worker
is completely self-contained and never calls back to Manager.

A multi-task worker is not a different kind of worker. It is a worker whose
composed function happens to call several things in sequence. The rings do not
move. The cleanup does not change. The hot loop does not change. Only what
Manager put inside the composed callable changes.