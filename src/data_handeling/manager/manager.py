from ..shared_ring_buffer import SharedRingBuffer
from ..workers import AbstractWorker, ManagedWorker, WorkerState
import multiprocessing
import os 
import threading
import asyncio
from multiprocessing.reduction import send_handle, recv_handle
import logging
import select
from dataclasses import dataclass
import socket

@dataclass
class WorkerSockets:
    pass
    






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
































    






