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



class Gate: 
    """quick access and human-readable interface over multiprocessing.Event"""

    """
         Parameters
    ----------
    name          : human-readable identifier
    initially_open: if True the gate starts signalled (useful for breaking
                    cycles — open exactly one gate in the cycle).
    """

    __slots__ = ("name", "_event")

    def __init__(self, name: str, initial_state:bool = False):
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

    def __init__(self): 
        self.workers = []

        
    def create_worker_async(): 
        pass 


    def create_worker_thread(): 
        pass



    def create_worker_proccess(proc_func, data_in, data_out, name, depends_on:list[str], set_initial:int):
        for i in depends_on:
            r_sock, w_sock = socket.socketpair()

        ManagedWorker(data_in=data_in, data_out=data_out, proc_func=proc_func, name=name)









    def create_shared_memory():
        pass
































    






