from ..shared_ring_buffer import SharedRingBuffer, SharedMemorySpec
from ..workers import Worker, TaskSpec, WorkerSpec, EventSpec
import multiprocessing as mp
from typing import Optional, Dict, Any
from enum import IntEnum
from __future__ import annotations

class InitialState(IntEnum):
    OPEN = 1
    CLOSED = 0


class WorkerEvent:
    """quick access and human-readable interface over multiprocessing.Event"""

    """
         Parameters
    ----------
    name          : human-readable identifier
    initially_open: if True the gate starts signalled (useful for breaking
                    cycles — open exactly one gate in the cycle).
    """

    __slots__ = ("name", "_event")

    def __init__(self, name: str, initial_state: bool = False):
        self.name = name
        self._event = mp.Event()
        if initial_state:
            self._event.set()

    @property
    def event(self):
        return self._event

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

    def __repr__(self) -> str:
        state = "OPEN" if self.is_open() else "CLOSED"
        return f"<Gate '{self.name}' [{state}]>"





class Manager:

    def __init__(self, mp_context:str = "spawn"):
        # not using spawn on linux creates issues with shared memory but improves performance
        self._ctx           = mp.get_context(mp_context)
        self._ring_specs:   dict[str, SharedMemorySpec] = {}
        self._rings:        dict[str, SharedRingBuffer] = {}  # strong refs: Manager owns
        self._events:       dict[str, WorkerEvent]      = {}
        self._worker_specs: dict[str, WorkerSpec]       = {}
        self._processes:    dict[str, mp.Process]       = {}



    # apis for creating objects, user can call them in order to make ring memory, workers, or events


    def create_ring(self, spec: SharedMemorySpec) -> Manager:
        self._ring_specs[spec.name] = spec
        self._rings[spec.name] = SharedRingBuffer(**spec.to_kwargs(create=True, reader=0))
        return self

    def create_worker(self, spec: WorkerSpec) -> Manager:
        self._worker_specs[spec.name] = spec
        return self

    def create_event(self, spec: EventSpec) -> Manager:
        self._events[spec.name] = WorkerEvent(name=spec.name, initial_state=spec.initial_state)
        return self
    


    # api for launching one worker


    def _calc_reader_number(self, ring_name:str, spec: TaskSpec):
        """ if the task is reading, make sure to increment the number in the ring spec"""
        if ring_name in spec.reading_rings: 
            return self._ring_specs[ring_name].reader +  1
        else: 
            return self._ring_specs[ring_name].reader


        

    def start(self, name:str): 
        try: 
            spec = self._worker_specs[name]
        except KeyError as K: 
            raise ValueError(f"Start Failed: Worker: {name} isn't registered with manager")
        
        rings = []
        for ring in spec.rings:
            self._ring_specs[ring].reader = self._calc_reader_number(ring, spec)
            rings.append(SharedRingBuffer())    

        






class depManager:


    def __init__(self):
        self._worker_events: dict[str, WorkerEvent] = {}
        self._workers: dict[str, AbstractWorker] = {}
        self._shared_memory: dict[str, SharedRingBuffer] = {}
        self._shared_memory_specs: dict[str, SharedMemorySpec] = {}

    def crt_worker_event(self, name: str, initial_state: int = InitialState.CLOSED) -> None:
        if name in self._worker_events:
            raise ValueError(f"WorkerEvent of name: {name} has already been created")
        self._worker_events[name] = WorkerEvent(name=name, initial_state=initial_state)

    def crt_worker(
        self,
        name: str,
        proc_func: callable,
        data_in: list,
        data_out: list,
        wait_events: list,
        signal_events: list,
        args: tuple,
        kwargs: Optional[Dict[str, any]],
    ):
        if name in self._workers:
            raise ValueError(f"Worker of name: {name} has already been created")
        self._workers[name] = AbstractWorker(
            data_in=data_in,
            data_out=data_out,
            proc_func=proc_func,
            shd_mem_init=SharedRingBuffer,
            wait_events=wait_events,
            signal_events=signal_events,
            args=args,
            kwargs=kwargs,
        )

    def crt_shared_memory(
        self,
        name,
        size: int,
        num_readers,
        reader: int,
        cache_allign: bool = True,
        cache_size: int = 64,
    ):
        if name in self._shared_memory:
            raise ValueError(f"SharedMemory of name: f{name} has already been created")
        if reader is None:
            raise ValueError("reader cannot be None when creating shared memory")
        spec = SharedMemorySpec(
            name=name,
            size=size,
            num_readers=num_readers,
            cache_allign=cache_allign,
            cache_size=cache_size,
        )
        self._shared_memory_specs[name] = spec
        self._shared_memory[name] = SharedRingBuffer(**spec.to_kwargs(create=True, reader=reader))

    def get_shared_memory_kwargs(self, name: str, *, create: bool = False, reader: int) -> dict[str, Any]:
        if name not in self._shared_memory_specs:
            raise KeyError(f"SharedMemory spec not found for name: {name}")
        return self._shared_memory_specs[name].to_kwargs(create=create, reader=reader)

    def open_shared_memory(self, name: str, reader: int) -> SharedRingBuffer:
        return SharedRingBuffer(**self.get_shared_memory_kwargs(name, create=False, reader=reader))

    @staticmethod
    def _cleanup_ring(ring: SharedRingBuffer, *, unlink: bool) -> None:
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
        if unlink:
            try:
                ring.unlink()
            except FileNotFoundError:
                pass

    def close_all_shared_memory(self) -> None:
        for ring in list(self._shared_memory.values()):
            self._cleanup_ring(ring, unlink=True)
        self._shared_memory.clear()

    
    
