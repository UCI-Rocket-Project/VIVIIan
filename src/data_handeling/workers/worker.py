from __future__ import annotations

from typing import Any
import numpy as np

from dataclasses import dataclass
from enum import IntEnum
import logging


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
    a database. The in and the out of the worker will only be standardized on thier output which will be of the same form as an numpy.ndarray
    made into bytes. 

    """


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
        proc_function:  main function run by worker on data
    """
        
    data_in:       np.ndarray   
    data_out:      np.ndarray  
    proc_func:     callable 
    
    def __post_init__(self):
        if self.proc_func is None:
            raise ValueError("proc_func must be provided")
        self.last_run = None
        
    def run(self): 
        try: 
            self.proc_func(self.data_in, self.data_out)
            return WorkerReturnState(0)
        except Exception as e:
            logging.error(e)
            return 1

    


@dataclass
class ManagedWorker(AbstractWorker):
    










