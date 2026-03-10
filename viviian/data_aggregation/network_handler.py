import socket
import multiprocessing as mp
import logging
from dataclasses import dataclass
from data_handeling import shared_ring_buffer

import pyarrow as pa

# Header size of pyarrow IPC (lets you know how big the next batch is going to be)
HEADER_SIZE = 4
DEFAULT_BUFFER_SIZE = 1 << 20  # 1 megabyte


@dataclass
class Membuf: 
    data_set: list
    queue: shared_ring_buffer




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

        self.cur_payload_buf_size = DEFAULT_BUFFER_SIZE
        self._payload = bytearray(self.cur_payload_buf_size)  # initially allocate a 1MB buffer
        self._payload_mv = memoryview(self._payload)


    def _recv_header(self) -> None: 
        pos = 0
        while pos < HEADER_SIZE:
            got = self.socket.recv_into(self._header_mv[pos:HEADER_SIZE], HEADER_SIZE - pos)
            if got == 0:
                raise ConnectionError(f"No batch size received for {self.name}")
            pos += got
        
    def _split_columns_on_membufs(self, membuf:Membuf, batch:pa.RecordBatch):
        cols = batch.select(membuf.data_set)
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, cols.schema) as stream:
            stream.write_batch(cols)

        membuf.queue.write(sink.getvalue().to_pybytes())
        

    # come back and add memory error handling
    def _resize_payload_capacity(self, size: int) -> None: 
        if size > self.cur_payload_buf_size:
            self.cur_payload_buf_size = size * 2
            logging.warning(
                "resizing network payload buffer for %s to %s bytes",
                self.name,
                self.cur_payload_buf_size,
            )
            del self._payload_mv
            if len(self._payload) < self.cur_payload_buf_size:
                self._payload.extend(b"\x00" * (self.cur_payload_buf_size - len(self._payload)))
            self._payload_mv = memoryview(self._payload)

    def _recv_exact_into_payload(self, n:int) -> None: 
        pos = 0
        while pos < n: 
            got =self.socket.recv_into(self._payload_mv[pos:n], n - pos) 
            if got == 0:
                raise ConnectionError("socket closed or no read on socket")
            pos += got

    def overwrite_membufs_cols(self, values, membufs_range:range) -> None:
        for i in membufs_range:
            membuf = self.membufs[i]
            # Copy list-like inputs so membufs do not share the same mutable object.
            membuf.data_set = list(values)

        
    def start_reading(self) -> None: 
        while True: 
            try:
                logging.info("Starting reading on Network Reader %s", self.name)
                while True: 
                    logging.info("reader: waiting header")
                    self._recv_header()
                    payload_len = int.from_bytes(self._header_mv, "big")
                    logging.info("payload received by %s is %s bytes", self.name, payload_len)
                    self._resize_payload_capacity(payload_len)
                    self._recv_exact_into_payload(payload_len)
                    payload_bytes = self._payload_mv[:payload_len]
                    table = pa.ipc.open_stream(pa.BufferReader(payload_bytes)).read_all()
                    for membuf in self.membufs:
                        self._split_columns_on_membufs(membuf, table) 
            except Exception as e: 
                logging.exception(e)
                pass

    




    


        

    

