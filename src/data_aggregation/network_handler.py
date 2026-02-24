import socket 
import multiprocessing as mp
import time 
import logging
import pyarrow as pa
import socket


#Header size of pyarrow IPC  ie:(lets you know how big the next batch is going to be) 
HEADER_SIZE = 4
DEFAULT_BUFFER_SIZE = 1<<20


class NetworkReader: 
    def __init__(self, name, sender_addr, sender_port, schema: pa.Schema, batch_size, membufs:list ): 
        self.name = name 
        self.sender_addr = sender_addr 
        self.sender_port = sender_port 
        self.schema = schema
        self.membufs = membufs
        self.socket = socket.create_connection((sender_addr, sender_port))
        
        self._header = bytearray(HEADER_SIZE)
        self._header_mv = memoryview(self._header)
        
        self._payload = bytearray(DEFAULT_BUFFER_SIZE) #initialy allocated a 1MB buffer but
        self.payload_mv = memoryview(self._payload)

    def _resize_payload_capacity(self, size: int) -> None: 
        pass

    def _recv_exact_into(self, n:int) -> None: 
        pos = 0
        while pos < n: 
            got =self.socket.recv_into(self._payload_mv[pos:n], n - pos) 
            if got == 0:
                raise ConnectionError("socket closed or no read on socket")
            pos += got

    def _recv_message

        


    


        

    


