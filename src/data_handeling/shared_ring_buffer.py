from multiprocessing import shared_memory
import time
import numpy as np


#Pressure Def: here pressure is a measure of the free space of the writer. It is defined as the
#complement of the total free space to write in the buffer as a percentage of the total buffer size
#for example, if there are 100 spaces in the buffer and the write and min_reader_position are 33 
#spaces aprart then the memory pressure is recorded as 1 - (33/100) or 67% memory pressure
#this is then by the  process managers to be able determine metrics for process process interaction
# usefull for detecting hangs due to slow readers or slow producers 



class SharedRingBuffer(shared_memory.SharedMemory):
        #header array design: 
            # size = Q (uint64)                 0
            # pressure = Q (uint64)             1
            # dropped_size = Q (uint64)         2
            # write_position = Q (uint64, monotonic logical position)       3
            # max_amount_writable = Q (uint64)  4
            # num_readers = Q (uint64)          5
            # repeated per reader (num_readers times):
            #   reader_position = Q (uint64, monotonic logical position)
            #   reader_dead = Q (uint64, 0/1)
            #   reader_last_seen_ns = Q (uint64, time.time_ns())

    def __init__(self, name, create, size, num_readers, reader:int):
        self.header_size = 8* (6 + num_readers * 3) #8 byte uint64 times the number of readers *3 and all 6 statics values
        self.shared_mem_size = size + self.header_size
        self.max_amount_writable_index = 4
        self.write_pos_index = 3
        self.pressure_index = 1
        self.dropped_size_index = 2
        self.num_readers_index = 5
        self.ring_buffer_size = size
        self.num_readers = num_readers
        super().__init__(name=name, create=create, size=self.shared_mem_size)
        self.header = np.ndarray((6 + num_readers * 3), np.uint64, memoryview(self.buf[0:self.header_size]), 0)
        if create:
            self.header[:] = 0
            self.header[0] = self.ring_buffer_size
            self.header[self.num_readers_index] = self.num_readers
        self.reader = reader
        self.reader_pos_index = 6 + (reader * 3)
        self.reader_pos = 0
        self.write_pos = 0
        self.max_amount_writable = self.ring_buffer_size
        self.ring_buffer = memoryview(self.buf[self.header_size:])
        # min-reader cache: avoids O(num_readers) scans on every write-path query.
        # Stale cache is conservative (smaller writable), never optimistic.
        self._min_reader_pos_refresh_interval = 64
        # Also refresh on wall-clock cadence so external reader progress is seen
        # even when writer is stalled (no local writes to trigger periodic scan).
        self._min_reader_pos_refresh_s = 0.005
        self._writes_since_min_scan = 0
        self._reader_positions_dirty = False
        self._min_reader_pos_cache = self._scan_min_reader_pos()
        self._last_min_scan_t = time.perf_counter()

    def calculate_pressure(self) ->int:
        size = self.compute_max_amount_writable(force_rescan=True)
        self.header[self.pressure_index] = int(100 -(size/ self.ring_buffer_size))
        return int( 100 -(size/ self.ring_buffer_size))


    def int_to_pos(self, value: int) -> int:
        return value % self.ring_buffer_size

    def update_reader_pos(self, new_reader_pos):
        self.header[self.reader_pos_index] = new_reader_pos
        self.reader_pos = new_reader_pos
        self._reader_positions_dirty = True
            
    def update_write_pos(self, new_writer_pos):
        self.header[self.write_pos_index] = new_writer_pos
        self.write_pos = new_writer_pos
        self._writes_since_min_scan += 1

    def inc_writer_pos(self, inc_amount): 
        self.header[self.write_pos_index] = self.header[self.write_pos_index] + inc_amount
        self.write_pos = self.header[self.write_pos_index]
        self._writes_since_min_scan += 1

    def inc_reader_pos(self, inc_amount):
        self.header[self.reader_pos_index] = self.header[self.reader_pos_index] + inc_amount
        self.reader_pos = self.header[self.reader_pos_index]
        self._reader_positions_dirty = True

    def get_write_pos(self):
        return self.header[self.write_pos_index]

    def _scan_min_reader_pos(self) -> int:
        min_reader_pos = int(self.header[self.write_pos_index])
        for i in range(6, len(self.header), 3):
            reader_pos = int(self.header[i])
            if reader_pos < min_reader_pos:
                min_reader_pos = reader_pos
        return min_reader_pos
    
    def compute_max_amount_writable(self, force_rescan: bool = False) -> int:
        """returns max amount writable in bytes"""
        write_pos = int(self.header[self.write_pos_index])
        if (
            force_rescan
            or self._min_reader_pos_cache is None
            or self._reader_positions_dirty
            or self._writes_since_min_scan >= self._min_reader_pos_refresh_interval
            or (time.perf_counter() - self._last_min_scan_t) >= self._min_reader_pos_refresh_s
        ):
            min_reader_pos = self._scan_min_reader_pos()
            self._min_reader_pos_cache = min_reader_pos
            self._writes_since_min_scan = 0
            self._reader_positions_dirty = False
            self._last_min_scan_t = time.perf_counter()
        else:
            min_reader_pos = self._min_reader_pos_cache

        used = write_pos - min_reader_pos
        # If cached min is too old, used can exceed ring size conservatively.
        # Refresh once before treating it as a hard invariant failure.
        if used > self.ring_buffer_size:
            min_reader_pos = self._scan_min_reader_pos()
            self._min_reader_pos_cache = min_reader_pos
            self._writes_since_min_scan = 0
            self._reader_positions_dirty = False
            self._last_min_scan_t = time.perf_counter()
            used = write_pos - min_reader_pos

        assert used >= 0, (
            f"invariant violation: used < 0 (write_pos={write_pos}, "
            f"min_reader_pos={min_reader_pos}, used={used})"
        )
        assert used <= self.ring_buffer_size, (
            f"invariant violation: used > ring_buffer_size "
            f"(write_pos={write_pos}, min_reader_pos={min_reader_pos}, "
            f"used={used}, ring_buffer_size={self.ring_buffer_size})"
        )
        self.max_amount_writable = self.ring_buffer_size - used
        self.header[self.max_amount_writable_index] = self.max_amount_writable
        return self.max_amount_writable


    def expose_writer_mem_view(self, size) -> tuple[memoryview, memoryview | None, int, bool]:
        """method returning the memory view for a write to write directly to, up to the max allowalbe size"""
        """avl-size is the real size avaliable"""
        """bool represents wrap-around ie: if memview2 needs to be used"""
        # Recompute every call; cached writable becomes stale after position updates.
        self.compute_max_amount_writable()
        if self.max_amount_writable >= size: 
            size_writeable = size
        else: 
            size_writeable = self.max_amount_writable
        write_pos = self.int_to_pos(int(self.header[self.write_pos_index]))
        if write_pos + size_writeable < self.ring_buffer_size:
            mv1 = memoryview(self.ring_buffer[write_pos: write_pos + size_writeable])
            mv2 = None
            wrap_around = False
        else: 
            mv1 = memoryview(self.ring_buffer[write_pos:])
            mv2 = memoryview(self.ring_buffer[0:(size_writeable - (self.ring_buffer_size - write_pos))]) 
            wrap_around = True
        
        return (mv1, mv2, size_writeable, wrap_around)
    

    def expose_reader_mem_view(self, size) -> tuple[memoryview, memoryview | None, int, bool]:
        """method returning the memory view for a read to read directly from, up to the max allowalbe size"""
        """avl-size is the real size avaliable"""
        """bool represents wrap-around ie: if memview2 needs to be used"""
        write_pos = int(self.header[self.write_pos_index])
        read_pos = int(self.header[self.reader_pos_index])
        max_amount_readable = write_pos - read_pos
        assert max_amount_readable >= 0, (
            f"invariant violation: max_amount_readable < 0 "
            f"(write_pos={write_pos}, read_pos={read_pos}, "
            f"max_amount_readable={max_amount_readable})"
        )
        assert max_amount_readable <= self.ring_buffer_size, (
            f"invariant violation: max_amount_readable > ring_buffer_size "
            f"(write_pos={write_pos}, read_pos={read_pos}, "
            f"max_amount_readable={max_amount_readable}, "
            f"ring_buffer_size={self.ring_buffer_size})"
        )
        if max_amount_readable >= size:
            size_readable = size
        else:
            size_readable = max_amount_readable
        reader_pos = self.int_to_pos(read_pos)
        if reader_pos + size_readable <= self.ring_buffer_size:
            mv1 = memoryview(self.ring_buffer[reader_pos: reader_pos + size_readable])
            mv2 = None
            wrap_around = False
        else:
            mv1 = memoryview(self.ring_buffer[reader_pos:])
            remaining = size_readable - (self.ring_buffer_size - reader_pos)
            mv2 = memoryview(self.ring_buffer[0:remaining])
            wrap_around = True

        return (mv1, mv2, size_readable, wrap_around)
    
    def simple_write(self, writer_mem_view:tuple[memoryview, memoryview | None, int, bool], src:memoryview) -> None:
        """simple write from a buffer to the tuple returned by expose_writer_mem_view"""
        """if write_buffer is bigger than that aloted area in the 2 lists the extra is discarded"""
        mv1, mv2, _, _ = writer_mem_view
        src_mv = memoryview(src).cast("B")
        total_dst = mv1.nbytes + (mv2.nbytes if mv2 is not None else 0)
        bytes_to_copy = min(src_mv.nbytes, total_dst)
        first_copy = min(bytes_to_copy, mv1.nbytes)
        if first_copy:
            mv1[:first_copy] = src_mv[:first_copy]
        if mv2 is not None and bytes_to_copy > first_copy:
            second_copy = bytes_to_copy - first_copy
            mv2[:second_copy] = src_mv[first_copy:first_copy + second_copy]

    def simple_read(self, reader_mem_view:tuple[memoryview, memoryview | None, int, bool], dst:memoryview) -> None:
        """simple read from the tuple returned by expose_reader_mem_view into a buffer"""
        """if read_buffer is smaller than the data in the mem views then the extra is discarded"""
        mv1, mv2, _, _ = reader_mem_view
        dst_mv = memoryview(dst).cast("B")
        total_src = mv1.nbytes + (mv2.nbytes if mv2 is not None else 0)
        bytes_to_copy = min(dst_mv.nbytes, total_src)
        first_copy = min(bytes_to_copy, mv1.nbytes)
        if first_copy:
            dst_mv[:first_copy] = mv1[:first_copy]
        if mv2 is not None and bytes_to_copy > first_copy:
            second_copy = bytes_to_copy - first_copy
            dst_mv[first_copy:first_copy + second_copy] = mv2[:second_copy]



    


    
