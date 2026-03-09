from dataclasses import dataclass
from enum import IntEnum
import logging
import os
from ..shared_ring_buffer import SharedMemorySpec
import logging
from  signal import signal
from __future__ import annotations


""" Give me Vectors and Tables or give me Death""" 

""" The above quote is a parody of Patrick Henry's 'Give me liberty or give me death'(1775)"""
""" It is a good representation of how workers in VIVIIan operate. They are designed to operate on only 1 type of data in different forms. 
    These types of data are tables and vectors. Vectors here stand in as the building blocks of a table, each row is a vector and a table
    is a collection of these vectors in some demension, although for our intents and proposes we can think of that dimension as time.  The
    generic worker class is a collection of common attributes that workers will share such as how they recive instructions, the data they 
    will access, common utilizations of the ring buffer and so on. Ideally these generic workers will be created or described by VIVIIan's
    api and a seperate Manager program will be responsible for thier management.


    The best way to think of a worker is something that takes in data, performs some operation on this data, and then updates the data. 
    Workers might write the data to a different memory location but not always. Workers might also write to a network connection or manage
    a database. The in and the out of the worker will only be standardized on thier input which will be of the same form as an numpy.ndarray
    made into bytes. 
    """

class WorkerState(IntEnum): 
    RUNNING = 1
    STOPPED = 0

class WorkerReturnState(IntEnum): 
    SUCCESS = 0
    ERROR = 1

class CallType(IntEnum):
    TIME = 0
    DATA = 1
    ONETIME = 2
    INF = 3
    ONTRUE = 4    





@dataclass(frozen=True)
class EventSpec:
    name:          str
    initial_state: bool = False

    def __repr__(self):
        state = "OPEN" if self.initial_state else "CLOSED"
        return f"EventSpec(name={self.name!r}, initial_state={state})"


@dataclass(frozen=True)
class TaskSpec: 
    """ one logical process, these are the building blocks of workers and the main point of 
        computation. Tasks are meant to be used across workers and serve as the smallest
        unit of execution inside of a process, a worker can have one or more Tasks    
    """
    name:           str
    fn:      callable
    events:         tuple[str, ...]
    rings:          tuple[str, ...]
    reading_rings:  tuple[str, ...]
    args:           tuple = ()
    kwargs:         dict = None

    def __post_init__(self): 
        object.__setattr__(self, 'kwargs', self.kwargs or {})

    def __repr__(self):
        return f"TaskSpec(fn={self.fn.__name__!r}, events={self.events}, rings={self.rings})"





@dataclass(frozen=True)
class WorkerSpec:
    name:           str
    rings:          tuple[SharedMemorySpec, ...]
    events:         tuple[EventSpec, ...]
    tasks:          tuple[TaskSpec, ...] = []
    args:           tuple =()
    kwargs:         dict = None


    def __post_init__(self):
        if len(self.tasks) == 0:
            raise ValueError(f"WorkerSpec '{self.name}': please provide at least one Task")
        object.__setattr__(self, 'kwargs', self.kwargs or {}) 

    def __repr__(self): 
        return f"WorkerSpec(name={self.name!r}, Tasks={len(self.tasks)})"
    


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



























































































    

@dataclass
class AbstractWorker:
    """ 
        data_in:        the buffer that takes in data from
        data_out:       the buffer to write data out to
        proc_func:  main function run by worker on data
        name:           name of the worker, this is a unique string assigned to each worker
    """
        
    data_in:       list   
    data_out:      list  
    proc_func:     callable 
    shd_mem_init:  callable
    wait_events:   list
    signal_events: list
    args:          tuple
    kwargs:        dict
    
    def __post_init__(self):
        if self.proc_func is None:
            raise ValueError("proc_func must be provided")
        self.last_run = None
        
    def _bootstrap(
        name: str,
        fn: callable,
        wait_events: list,
        signal_events: list,
        data_in:list,
        data_out:list,
        shd_mem_init:callable,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """
        Launched inside every child process.
        1. Block until ALL required gates are open.
        2. Execute the user function.
        3. Signal all output gates.
        """
        pid = os.getpid()
        opened_in = []
        opened_out = []

        def _event_reset(ev):
            if hasattr(ev, "reset"):
                ev.reset()
            elif hasattr(ev, "clear"):
                ev.clear()

        def _event_signal(ev):
            if hasattr(ev, "signal"):
                ev.signal()
            elif hasattr(ev, "set"):
                ev.set()

        def _cleanup_ring(ring):
            try:
                ring.ring_buffer.release()
            except Exception:
                pass
            try:
                del ring.ring_buffer
            except Exception:
                pass
            try:
                ring.header = None
            except Exception:
                pass
            try:
                del ring.header
            except Exception:
                pass
            try:
                ring.close()
            except Exception:
                pass

        try:
            for spec in data_in:
                opened_in.append(shd_mem_init(**spec))
            for spec in data_out:
                opened_out.append(shd_mem_init(**spec))

            if args:
                runtime_args = args
            elif opened_out:
                runtime_args = (opened_out,)
            elif opened_in:
                runtime_args = (opened_in,)
            else:
                runtime_args = tuple()

            while True:
                logging.info(f"[{name}] pid={pid} waiting on {len(wait_events)} gate(s)...")
                for ev in wait_events:
                    ev.wait()
                    _event_reset(ev)

                logging.info(f"[{name}] pid={pid} gates open -> running")
                fn(*runtime_args, **kwargs)
                logging.info(f"[{name}] pid={pid} done -> signalling {len(signal_events)} gate(s)")
                for ev in signal_events:
                    _event_signal(ev)
        finally:
            for ring in opened_in:
                _cleanup_ring(ring)
            for ring in opened_out:
                _cleanup_ring(ring)


    @staticmethod
    def default_cleanup_ring(ring):
        try:
            ring.ring_buffer.release()
        except Exception:
            pass
        try:
            del ring.ring_buffer
        except Exception:
            pass
        try:
            ring.header = None
        except Exception:
            pass
        try:
            del ring.header
        except Exception:
            pass
        try:
            ring.close()
        except Exception:
            pass

    @staticmethod
    def default_event_reset(ev):
        if hasattr(ev, "reset"):
            ev.reset()
        elif hasattr(ev, "clear"):
            ev.clear()

    @staticmethod
    def default_event_signal(ev):
        if hasattr(ev, "signal"):
            ev.signal()
        elif hasattr(ev, "set"):
            ev.set()

    @staticmethod
    def default_make_bootstrap(
        name: str,
        fn: callable,
        wait_events: list,
        signal_events: list,
        data_in:list,
        data_out:list,
        shd_mem_init:callable,
        args: tuple,
        kwargs: dict,
        cleanup_ring: callable|None = default_cleanup_ring,
        event_reset: callable|None = default_event_reset,
        event_signal:callable|None = default_event_signal,

    ) -> None:
        """
        Launched inside every child process.
        1. Block until ALL required gates are open.
        2. Execute the user function.
        3. Signal all output gates.
        """
        pid = os.getpid()
        opened_in = []
        opened_out = []

        try:
            for spec in data_in:
                opened_in.append(shd_mem_init(**spec))
            for spec in data_out:
                opened_out.append(shd_mem_init(**spec))

            if args:
                runtime_args = args
            elif opened_out:
                runtime_args = (opened_out,)
            elif opened_in:
                runtime_args = (opened_in,)
            else:
                runtime_args = tuple()

            while True:
                logging.info(f"[{name}] pid={pid} waiting on {len(wait_events)} gate(s)...")
                for ev in wait_events:
                    ev.wait()
                    event_reset(ev)

                logging.info(f"[{name}] pid={pid} gates open -> running")
                fn(*runtime_args, **kwargs)
                logging.info(f"[{name}] pid={pid} done -> signalling {len(signal_events)} gate(s)")
                for ev in signal_events:
                    event_signal(ev)
        finally:
            for ring in opened_in:
                cleanup_ring(ring)
            for ring in opened_out:
                cleanup_ring(ring)

    

        
    








