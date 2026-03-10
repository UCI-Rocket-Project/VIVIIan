from __future__ import annotations

import logging
import multiprocessing as mp
from functools import wraps
from typing import Optional

from ..shared_ring_buffer import SharedRingBuffer, RingSpec
from ..workers import Worker, TaskSpec, WorkerSpec, EventSpec


__all__ = [
    "Manager",
    "WorkerEvent",
    "SharedRingBuffer",
    "Worker",
    "TaskSpec",
    "WorkerSpec",
    "EventSpec",
]


# ------------------------------------------------------------------ #
# WorkerEvent — live synchronisation primitive, Manager-owned         #
# ------------------------------------------------------------------ #

class WorkerEvent:
    """
    Human-readable wrapper over multiprocessing.Event.
    Constructed and owned by Manager. Never crosses the process boundary
    directly — multiprocessing.Event handles that internally.
    """
    __slots__ = ("name", "_event")

    def __init__(self, name: str, initial_state: bool = False):
        self.name = name
        self._event = mp.Event()
        if initial_state:
            self._event.set()

    def signal(self) -> None:
        """Open this gate (wake all waiting workers)."""
        self._event.set()

    def reset(self) -> None:
        """Close this gate (re-arm for a new round)."""
        self._event.clear()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)

    def is_open(self) -> bool:
        return self._event.is_set()

    @property
    def event(self):
        return self._event

    def __repr__(self) -> str:
        state = "OPEN" if self.is_open() else "CLOSED"
        return f"<WorkerEvent '{self.name}' [{state}]>"


# ------------------------------------------------------------------ #
# Manager                                                             #
# ------------------------------------------------------------------ #

class Manager:
    """
    Owns all specs and live objects. Resolves, composes, and forks.

    Registration order:
        1. create_ring  — for every RingSpec the system needs
        2. create_event — for every EventSpec the system needs
        3. create_task  — for every TaskSpec (independently of workers)
        4. create_worker — optional grouping: names tasks to share a process

    Starting:
        mgr.start("task_name")   — runs one task in its own process
        mgr.start("worker_name") — runs grouped tasks in one process

    If a task name is passed to start() directly, Manager auto-wraps it
    in a single-task group internally. There is no difference in execution.
    """

    def __init__(self, mp_context: str = "spawn"):
        self._ctx           = mp.get_context(mp_context)
        self._ring_specs:   dict[str, RingSpec]       = {}   # RingSpec / SharedMemorySpec
        self._rings:        dict[str, SharedRingBuffer] = {}  # strong refs: Manager owns
        self._event_specs:  dict[str, EventSpec]    = {}
        self._events:       dict[str, WorkerEvent]  = {}
        self._task_specs:   dict[str, TaskSpec]     = {}
        self._worker_specs: dict[str, WorkerSpec]   = {}
        self._processes:    dict[str, mp.Process]   = {}
        # Tracks how many reader slots have been assigned per ring so each
        # reader process gets a unique index matching the header layout.
        self._ring_reader_counters: dict[str, int]  = {}

    # ------------------------------------------------------------------ #
    # Registration                                                       #
    # ------------------------------------------------------------------ #

    def create_ring(self, spec:RingSpec) -> Manager:
        """Register a ring spec, open the creator-side writer handle"""
        self._ring_specs[spec.name] = spec
        return self
    
    def add_ring(self, spec:RingSpec) -> None: 
        self._rings[spec.name] = SharedRingBuffer(**spec.to_kwargs(create=True, reader=-1))

    def create_event(self, spec: EventSpec) -> Manager:
        """Register an event spec and create the live WorkerEvent."""
        self._event_specs[spec.name] = spec
        self._events[spec.name] = WorkerEvent(name=spec.name, initial_state=spec.initial_state)
        return self

    def create_task(self, spec: TaskSpec) -> Manager:
        """
        Register a task. Tasks are independent of workers — the user
        decides later whether to group them or start each in its own process.
        """
        if spec.name in self._task_specs:
            raise ValueError(f"Task '{spec.name}' is already registered")
        self._task_specs[spec.name] = spec
        
        return self

    def create_worker(self, spec: WorkerSpec) -> Manager:
        """
        Register a worker group. spec.tasks must be names of already-registered
        tasks. Manager validates this at start() time, not here, so create_task
        and create_worker calls can appear in any order during setup.
        """
        if spec.name in self._worker_specs:
            raise ValueError(f"Worker '{spec.name}' is already registered")
        self._worker_specs[spec.name] = spec
        return self
    
    def _update_ring_counts(self) -> None: 
        for task in self._task_specs.values(): 
            for reading_ring in task.reading_rings():
                self._ring_specs[reading_ring].num_readers += 1


    # ------------------------------------------------------------------ #
    # Start            --continute checking here                         #
    # ------------------------------------------------------------------ #


    def _create_ring_kwargs(self, tasks:list[TaskSpec]) -> tuple[dict[str, dict], dict[str, dict]]:
        reading_ring_kwargs: dict[str, dict] = {}
        writing_ring_kwargs: dict[str, dict] = {}
         
        for task in tasks:
            for rname in task.reading_rings:
                if rname not in self._ring_specs:
                    raise KeyError(
                        f"Task '{task.name}' reading_ring '{rname}' is not registered. "
                        f"Call create_ring() first."
                    )
                if rname not in reading_ring_kwargs:
                    # Assign the next available reader slot for this ring
                    slot = self._ring_reader_counters[rname]
                    spec = self._ring_specs[rname]
                    if slot >= spec.num_readers:
                        raise ValueError(
                            f"Ring '{rname}' has num_readers={spec.num_readers} but "
                            f"slot {slot} was requested. Increase num_readers in the RingSpec."
                        )
                    self._ring_reader_counters[rname] += 1
                    reading_ring_kwargs[rname] = spec.to_kwargs(create=False, reader=slot)

            for rname in task.writing_rings:
                if rname not in self._ring_specs:
                    raise KeyError(
                        f"Task '{task.name}' writing_ring '{rname}' is not registered. "
                        f"Call create_ring() first."
                    )
                if rname not in writing_ring_kwargs:
                    writing_ring_kwargs[rname] = self._ring_specs[rname].to_kwargs(
                        create=False, reader=-1
                    )
        return (reading_ring_kwargs, writing_ring_kwargs)






    def start(self, name: str) -> mp.Process:
        """
        Start a named worker group or a named task.

        If `name` matches a WorkerSpec, the grouped tasks run together in
        one process. If `name` matches a TaskSpec, that task runs alone in
        its own process. Either way the execution model is identical.
        """
        if name in self._worker_specs:
            task_names = self._worker_specs[name].tasks
        elif name in self._task_specs:
            task_names = (name,)
        else:
            raise KeyError(
                f"'{name}' is not a registered worker or task. "
                f"Workers: {list(self._worker_specs)}. "
                f"Tasks: {list(self._task_specs)}."
            )

        # Resolve task names → TaskSpec objects and validate
        tasks:list[TaskSpec] = []
        for tname in task_names:
            if tname not in self._task_specs:
                raise KeyError(
                    f"Worker '{name}' references unknown task '{tname}'. "
                    f"Register it with create_task() first."
                )
            tasks.append(self._task_specs[tname])

        # Validate all rings are registered and assign reader slots.
        # Reading rings get a unique monotonically assigned reader index per ring.
        # Writing rings open with reader=-1 (writer handle).
        # A ring may appear as a writing_ring in one task and reading_ring in
        # ring may appear as reading in the same task that is writing, this is bad practice but possible
        # another within the same group — each role gets its own handle.
        reading_ring_kwargs: dict[str, dict] = {}
        writing_ring_kwargs: dict[str, dict] = {}

        reading_ring_kwargs, writing_ring_kwargs = self._create_ring_kwargs(tasks = tasks)
        # Derive WorkerEvent objects for this group. mp.Event crosses the
        # process boundary safely via multiprocessing internals.
        events: dict[str, WorkerEvent] = {}
        for task in tasks:
            for ename in task.events:
                if ename not in self._events:
                    raise KeyError(
                        f"Task '{task.name}' references event '{ename}' which is not registered. "
                        f"Call create_event() first."
                    )
                events[ename] = self._events[ename]

        WorkerClass = Worker._registry.get(
            getattr(self._worker_specs.get(name), 'worker_type', None), Worker
        )

        # _bootstrap runs entirely inside the child process.
        # Opens ring handles in the child's address space with the correct
        # reader/writer role, then composes and runs the Worker loop.
        # Manager is NOT captured — it must never cross the boundary.
        def _bootstrap(
            tasks=tasks,
            reading_ring_kwargs=reading_ring_kwargs,
            writing_ring_kwargs=writing_ring_kwargs,
            events=events,
        ):
            reading_rings = {rname: SharedRingBuffer(**kw) for rname, kw in reading_ring_kwargs.items()}
            writing_rings = {rname: SharedRingBuffer(**kw) for rname, kw in writing_ring_kwargs.items()}
            for ring in reading_rings.values():
                ring.__enter__()
            try:
                composed = Manager._compose_tasks_static(
                    name, tasks, reading_rings, writing_rings, events
                )
                WorkerClass(fn=composed)()
            finally:
                # reading_rings mark themselves dead in __exit__ via the alive flag.
                # writing_rings have no slot to clear (reader=-1) but still need closing.
                for ring in reading_rings.values():
                    ring.__exit__(None, None, None)
                for ring in writing_rings.values():
                    ring.__exit__(None, None, None)

        _bootstrap.__name__ = f"{name}_bootstrap"
        proc = self._ctx.Process(target=_bootstrap, name=name, daemon=True)
        proc.start()
        self._processes[name] = proc
        return proc

    # ------------------------------------------------------------------ #
    # Composition — static: must not reference Manager or any live object #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compose_tasks_static(
        group_name: str,
        tasks: list[TaskSpec],
        reading_rings: dict[str, SharedRingBuffer],
        writing_rings: dict[str, SharedRingBuffer],
        events: dict[str, WorkerEvent],
    ) -> callable:
        """
        Turn a list of TaskSpecs into one callable.
        Runs inside the child — must be static, no Manager reference.

        reading_rings and writing_rings are the child-local handles opened
        by _bootstrap with the correct reader slot / writer role respectively.
        All resolution happens here, once, before the hot loop starts.
        """
        resolved = []
        for task in tasks:
            task_readers = [reading_rings[n] for n in task.reading_rings if n in reading_rings]
            task_writers = [writing_rings[n] for n in task.writing_rings if n in writing_rings]
            task_events  = [events[n] for n in task.events if n in events]
            wrapped_fn   = Manager._wrap_fn_static(
                task.fn, label=f"{group_name}/{task.fn.__name__}"
            )
            if task.args:
                effective_args = task.args
            else:
                # Pass readers and writers as separate positional arguments so fn
                # always knows which list is which:  fn(readers, writers, ...)
                # If a task only reads or only writes, the empty list is still passed
                # so fn signatures stay consistent.
                effective_args = (task_readers, task_writers)
                if task_events:
                    effective_args = effective_args + (task_events,)
            resolved.append((wrapped_fn, effective_args, task.kwargs))

        def _composed():
            for fn, args, kwargs in resolved:
                fn(*args, **kwargs)

        _composed.__name__ = f"{group_name}_composed"
        return _composed

    @staticmethod
    def _wrap_fn_static(fn: callable, label: str) -> callable:
        """
        Inject per-task debug logging transparently.
        label is "group_name/task_fn_name" — visible in logs automatically.
        Must be static for the same reason as _compose_tasks_static.
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
        """Close and unlink all creator-side ring handles."""
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
            f"<Manager"
            f" tasks={list(self._task_specs)}"
            f" workers={list(self._worker_specs)}"
            f" rings={list(self._ring_specs)}"
            f" events={list(self._event_specs)}>"
        )
