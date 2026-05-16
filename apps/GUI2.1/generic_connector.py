
#generic connector 
import pyarrow.flight as flight
import socket
import struct
import threading
import time
from typing import Callable

import numpy as np
import pyarrow as pa


class StorageServer(flight.FlightServerBase):
    """Flight receiver: each do_put stream is turned into NumPy frames on the pythusa ring."""

    def __init__(
        self,
        location: str,
        stream_writer,
        *,
        rows_per_frame: int,
        num_signals: int,
    ) -> None:
        super().__init__(location)
        self._stream = stream_writer
        self._write_lock = threading.Lock()
        self._rows_per_frame = rows_per_frame
        self._num_signals = num_signals

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        start_time = time.time()
        received_bytes = 0
        for chunk in reader:
            record_batch = chunk.data
            arrays = [
                record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)
            ]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            if data.shape != (self._rows_per_frame, self._num_signals):
                raise ValueError(
                    f"Expected frame shape {(self._rows_per_frame, self._num_signals)}, got {data.shape}"
                )
            with self._write_lock:
                self._stream.write(data)
            received_bytes += int(data.nbytes)
            if time.time() - start_time > 1.0:
                print(f"Flight ingest: {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
                start_time = time.time()
                received_bytes = 0



class CommandServer (flight.FlightServerBase):
    """Flight receiver: each do_put stream is turned into NumPy frames on the pythusa ring."""

    def __init__(
        self,
        location: str,
        tcp_connection: socket.socket,
        flight_tcp_data_converter: Callable[[np.ndarray], bytes],
    ) -> None:
        super().__init__(location)
        self._tcp_connection = tcp_connection
        self._flight_tcp_data_converter = flight_tcp_data_converter

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        for chunk in reader:
            record_batch = chunk.data
            arrays = [
                record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)
            ]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            flight_data = self._flight_tcp_data_converter(data)
            if flight_data is None:
                continue
            self._tcp_connection.sendall(flight_data)




def run_generic_receiver(
    *,
    grpc_bind: str,
    stream_writer,
    rows_per_frame: int,
    num_signals: int,
) -> None:
    """Start a StorageServer on *grpc_bind* and block on serve().

    *stream_writer* is anything with a ``.write(np.ndarray)`` method —
    a pythusa stream, a CommandBuffer, etc.
    """
    server = StorageServer(
        grpc_bind,
        stream_writer,
        rows_per_frame=rows_per_frame,
        num_signals=num_signals,
    )
    server.serve()






def generic_tcp_to_flight_connector(rows_per_frame: int, tcp_connection: socket.socket, nbytes: int, struct_format: str, field_names: list[str], flight_address: str) -> None:
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in field_names])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                try:
                    data_batch = np.empty((rows_per_frame, len(field_names)), dtype=np.float64)
                    for i in range(rows_per_frame):
                        parts: list[bytes] = []
                        remaining = nbytes
                        while remaining > 0:
                            chunk = tcp_connection.recv(remaining)
                            if not chunk:
                                raise ConnectionError("socket closed")
                            parts.append(chunk)
                            remaining -= len(chunk)
                        recv_bytes = b"".join(parts)
                        unpacked_data = struct.unpack(struct_format, recv_bytes[:-4]) #get rid of the crc check 
                        data_batch[i] = unpacked_data
                    arrays = [pa.array(data_batch[:, i], type=pa.float64()) for i in range(len(field_names))]
                    batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                    writer.write_batch(batch)
                    written_bytes += int(batch.nbytes)
                    if time.time() - start_time > 1.0:
                        print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                        start_time = time.time()
                        written_bytes = 0
                except Exception as e:
                    print(f"Error sending data: {e}")
                    time.sleep(0.01)
        except Exception as e:
            print(f"Error connecting to Flight: {e}")
            time.sleep(0.01)

def generic_flight_to_tcp_connector(flight_address: str, tcp_connection: socket.socket, nbytes: int, struct_format: str, field_names: list[str]) -> None:
    """Flight server: receives do_put batches and forwards each row as struct-packed bytes over TCP.

    Inverse of generic_tcp_to_flight_connector — binds at *flight_address*, waits for a
    do_put stream, unpacks each row from float64 back to the original wire format defined
    by *struct_format*, and sends the raw bytes to *tcp_connection*.
    """
    assert struct.calcsize(struct_format) == nbytes, (
        f"struct_format '{struct_format}' calcsize {struct.calcsize(struct_format)} != nbytes {nbytes}"
    )

    def _converter(data: np.ndarray) -> bytes:
        out = bytearray()
        for i in range(data.shape[0]):
            out += struct.pack(struct_format, *data[i])
        return bytes(out)

    server = CommandServer(flight_address, tcp_connection, _converter)
    server.serve()











def generic_stream_connector(*, stream, field_names: list[str], flight_address: str) -> None:
    """Generic connector for any flight address."""
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in field_names])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                frame = stream.read()
                if frame is None:
                    continue
                arrays = [pa.array(frame[:, i], type=pa.float64()) for i in range(len(field_names))]
                batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                writer.write_batch(batch)
                written_bytes += int(frame.nbytes)
                if time.time() - start_time > 1.0:
                    print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                    start_time = time.time()
                    written_bytes = 0
        except Exception as e:
            print(f"Error sending data: {e}")
            time.sleep(0.01)