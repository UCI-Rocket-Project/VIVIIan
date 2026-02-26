from multiprocessing import Process, shared_memory, Value, Array, Lock, Semaphore
from collections import deque
import struct
import time



#Pressure Def: here pressure is a measure of the free space of the writer. It is defined as the
#complement of the total free space to write in the buffer as a percentage of the total buffer size
#for example, if there are 100 spaces in the buffer and the write and min_reader_position are 33 
#spaces aprart then the memory pressure is recorded as 1 - (33/100) or 67% memory pressure
#this is then by the  process managers to be able determine metrics for process process interaction
# usefull for detecting hangs due to slow consumers or slow producers 





class SharedRingBuffer(shared_memory.SharedMemory):
    
    @staticmethod
    def _struct_creation_string(num_consumers:int) -> str: 
        #header packet design: 
            # size = Q (uint64)
            # pressure = d (double)
            # dropped_size = Q (uint64)
            # write_position = Q (uint64)
            # min_read_position = Q (uint64)
            # num_readers = Q (uint64)
            # repeated per reader (num_consumers times):
            #   reader_position = Q (uint64, monotonic logical position)
            #   reader_alive = b (signed char, 0/1)
            #   reader_last_seen_ns = Q (uint64, time.time_ns())
        
        format_string = "<QdQQQQ" + (num_consumers * "QbQ")
        return format_string

    def __init__(self, name, create, size, track, num_consumers, reader:int):
        self.format = self._struct_creation_string(num_consumers=num_consumers)
        self.header_size = struct.calcsize(self.format) #calculated in bytes
        self.shared_mem_size = size + self.header_size
        self.min_read_pos_offset = struct.calcsize("<QdQQ")
        self.write_pos_offset = struct.calcsize("<QdQ")
        self.pressure_offset = struct.calcsize("<Q")
        self.dropped_size_offset = struct.calcsize("<Qd")
        self.num_readers_offset = struct.calcsize("<QdQQQ")
        self.payload_size = size
        self.num_consumers = num_consumers
        super().__init__(name=name, create=create, size=self.shared_mem_size, track=track)
        if create:
            self.buf[0:self.header_size] = b"\x00" * self.header_size
            struct.pack_into("<Q", self.buf, 0, self.payload_size)
            struct.pack_into("<Q", self.buf, self.num_readers_offset, self.num_consumers)
        self.reader = reader
        self.header_fixed_size = struct.calcsize("<QdQQQQ")
        self.reader_slot_size = struct.calcsize("<QbQ")
        self.reader_offset = self.header_fixed_size + (reader * self.reader_slot_size)
        self.reader_pos = 0
        self.write_pos = 0
        self.min_read_pos = 0


    def int_to_pos(self, value: int) -> int:
        return self.header_size + (value % self.payload_size)

    def pos_to_int(self, pos: int) -> int:
        return (pos - self.header_size) % self.payload_size

    def update_reader_pos(self, read_size):
        self.reader_pos += read_size
        struct.pack_into(
            "<QbQ",
            self.buf,
            self.reader_offset,
            self.reader_pos,
            1,
            time.time_ns(),
        )

    def update_min_reader_pos(self):
        # In <QdQQQQ, min_read_position is the 5th field => byte offset after Q,d,Q,Q
        values = struct.unpack_from(self.format, self.buf, 0)
        fixed_fields = 6  # <QdQQQQ
        min_pos = None
        for i in range(fixed_fields, len(values), 3):
            pos = values[i]
            alive = values[i + 1]
            if alive and (min_pos is None or pos < min_pos):
                min_pos = pos

        if min_pos is None:
            min_pos = self.write_pos

        struct.pack_into("<Q", self.buf, self.min_read_pos_offset, min_pos)
        return min_pos

    def update_write_pos(self, write_size:int):
        self.write_pos += write_size
        struct.pack_into(
            "<Q",
            self.buf,
            self.write_pos_offset,
            self.write_pos
        )

    def max_writable(self) ->int:
        min_read_pos = self.get_min_reader_pos()
        used = self.write_pos - min_read_pos
        if used < 0:
            used = 0
        if used > self.payload_size:
            return 0
        return self.payload_size - used

    def max_readable(self) ->int:
        return max(0, self.get_write_pos() - self.reader_pos)

    def write(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        payload = memoryview(data)
        write_size = len(payload)
        if write_size > self.payload_size:
            raise ValueError("write larger than payload capacity")
        if write_size > self.max_writable():
            raise BufferError("not enough writable space")

        start = self.int_to_pos(self.write_pos)
        first_len = min(write_size, (self.header_size + self.payload_size) - start)
        self.buf[start:start + first_len] = payload[:first_len]
        remaining = write_size - first_len
        if remaining:
            payload_start = self.header_size
            self.buf[payload_start:payload_start + remaining] = payload[first_len:first_len + remaining]

        self.update_write_pos(write_size)
        self.update_header_writer()
        return write_size

    def calculate_pressure(self):
        writable = self.max_writable()
        pressure = 1.0 - (writable / self.payload_size if self.payload_size else 0.0)
        struct.pack_into("<d", self.buf, self.pressure_offset, pressure)
        return pressure

    def get_pressure(self):
        return struct.unpack_from("<d", self.buf, self.pressure_offset)[0]

    def read(self, size):
        if size < 0:
            raise ValueError("size must be >= 0")
        read_size = min(size, self.max_readable())
        start = self.int_to_pos(self.reader_pos)
        first_len = min(read_size, (self.header_size + self.payload_size) - start)
        out = bytearray(read_size)
        out[:first_len] = self.buf[start:start + first_len]
        remaining = read_size - first_len
        if remaining:
            payload_start = self.header_size
            out[first_len:] = self.buf[payload_start:payload_start + remaining]

        self.update_reader_pos(read_size)
        self.update_min_reader_pos()
        self.update_header_reader()
        return bytes(out)

    def get_available_read(self) -> int:
        return self.max_readable()

    def get_min_reader_pos(self):
        self.min_read_pos = struct.unpack_from("<Q", self.buf, self.min_read_pos_offset)[0]
        return self.min_read_pos

    def get_write_pos(self):
        self.write_pos = struct.unpack_from("<Q", self.buf, self.write_pos_offset)[0]
        return self.write_pos

    def update_header_writer(self):
        self.calculate_pressure()
        return self.write_pos

    def update_header_reader(self):
        return self.update_min_reader_pos()





     
