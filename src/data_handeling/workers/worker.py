from typing import Any
import numpy as np
from dataclasses import dataclass
from enum import IntEnum
import logging
import os
import threading 
import multiprocessing as mp
import asyncio
from multiprocessing.reduction import send_handle, recv_handle
import select
from ..shared_ring_buffer import SharedRingBuffer


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

    









































@dataclass
class ManagedWorker(AbstractWorker):
    """
        blocks:             the file descriptors to decriment and block this worker on
        signals:            the file descriptors to increment and signal completion to other functions on 
        metrics_shm         the shared memory where metrics will be writen to
        calc_metrics:       the function to calculate the metrics, this wraps the proc_function and uses its return
        WorkerState:        current state of the worker idealy, created off of blocking by worker signals
    """

    block: any
    signal: any

    def __post_init__(self):
        return super().__post_init__()

    @staticmethod
    def signal(signal):
        os.write(signal, b'\x01')
    
    @staticmethod
    def wait(block):
        select.select([block], [], [])
        os.read(block, 1)

    async def async_worker(conn, name): 
        pass
        

    


    

        
    








