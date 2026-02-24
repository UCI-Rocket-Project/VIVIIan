from multiprocessing import Process, shared_memory, Value, Array, Lock, Semaphore
from collections import deque
import ctypes
import pyarrow as pa


class SharedProcessRingBuffer: 
    """Process-Safe ring buffer for pyarrow data, designed in a producer-consumer arhitecture in order for reads and processing to occure"""

    def __init__(self, num_slots, slot_size, name): 
        self.name = name
        self.num_slots = num_slots
        self.slot_size = slot_size
        self.ring_buffer = shared_memory.SharedMemory(name = name, create=True, size = num_slots * slot_size)
        
        self.write_seq = Value(ctypes.c_ulonglong, num_slots, 0)
        self.lengths = Array(ctypes.c_ulonglong, num_slots, lock=False)
        self.seq = Array(ctypes.c_ulonglong, num_slots, lock=False)

        self.availiable = Semaphore(0)
        self.lock = Lock()
    

    def publish(self, batch: pa.RecordBatch):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, batch.schema) as w: 
            w.write_batch(batch)
        buf = sink.getvalue()


        if buf.size > self.slot



    
class RingBuffer: 
     