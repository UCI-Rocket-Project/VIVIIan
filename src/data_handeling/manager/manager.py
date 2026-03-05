from ..shared_ring_buffer import SharedRingBuffer
from ..workers import AbstractWorker, ManagedWorker, WorkerState
import multiprocessing as mp
import os
import threading
import asyncio
from multiprocessing.reduction import send_handle, recv_handle
import logging
import select
from dataclasses import dataclass
import socket
from typing import Optional, Dict, Any
from enum import IntEnum
import numpy as np


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


@dataclass(frozen=True)
class SharedMemorySpec:
    name: str
    size: int
    num_readers: int
    cache_allign: bool = True
    cache_size: int = 64

    def to_kwargs(self, *, create: bool, reader: int) -> dict[str, Any]:
        return {
            "name": self.name,
            "create": create,
            "size": self.size,
            "num_readers": self.num_readers,
            "reader": reader,
            "cache_allign": self.cache_allign,
            "cache_size": self.cache_size,
        }


class Manager:
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

    
    
