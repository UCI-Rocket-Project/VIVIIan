from __future__ import annotations

import logging
import socket
import struct
import time
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa
import viviian as vivii


__all__ = [
    "HOST",
    "PORT",
    "ROWS_PER_BATCH",
    "COLUMN_ONE_RING",
    "COLUMN_TWO_RING",
    "run_server",
    "run_client",
    "run_fft_consumer",
]

HOST = "127.0.0.1"
PORT = 9876
ROWS_PER_BATCH = 4096
BYTES_PER_GIGABYTE = 1_000_000_000.0
THROUGHPUT_LOG_INTERVAL_SECONDS = 1.0

COLUMN_ONE_RING = "column_one"
COLUMN_TWO_RING = "column_two"
COLUMN_THREE_RING = "column_three"


SCHEMA = pa.schema(
    [
        ("one", pa.float64()),
        ("two", pa.float64()),
    ]
)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(processName)s %(levelname)s %(message)s",
        )
    else:
        root_logger.setLevel(logging.INFO)


@dataclass
class ThroughputLogger:
    label: str
    interval_seconds: float = THROUGHPUT_LOG_INTERVAL_SECONDS
    logger: logging.Logger = field(init=False)
    total_bytes: int = 0
    interval_bytes: int = 0
    interval_started_at: float = field(default_factory=time.perf_counter)

    def __post_init__(self) -> None:
        self.logger = logging.getLogger(__name__)

    def record(self, nbytes: int) -> None:
        self.total_bytes += nbytes
        self.interval_bytes += nbytes

        now = time.perf_counter()
        elapsed = now - self.interval_started_at
        if elapsed < self.interval_seconds:
            return

        self.logger.info(
            "%s throughput: %.6f GB/s (%.6f GB total)",
            self.label,
            self.interval_bytes / elapsed / BYTES_PER_GIGABYTE,
            self.total_bytes / BYTES_PER_GIGABYTE,
        )
        self.interval_bytes = 0
        self.interval_started_at = now


def _recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < nbytes:
        chunk = sock.recv(nbytes - len(chunks))
        if not chunk:
            raise ConnectionError("socket closed while receiving data")
        chunks.extend(chunk)
    return bytes(chunks)


def run_server() -> None:
    configure_logging()
    throughput = ThroughputLogger("generated")

    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(1)
        conn, _ = server.accept()

        with conn:
            while True:
                batch = pa.record_batch(
                    {
                        "one": pa.array(np.random.randn(ROWS_PER_BATCH), pa.float64()),
                        "two": pa.array(np.random.randn(ROWS_PER_BATCH), pa.float64()),
                    },
                    schema=SCHEMA,
                )

                sink = pa.BufferOutputStream()
                with pa.ipc.new_stream(sink, SCHEMA) as writer:
                    writer.write_batch(batch)
                payload = sink.getvalue().to_pybytes()
                conn.sendall(struct.pack(">I", len(payload)) + payload)
                throughput.record(len(payload))


def run_client() -> None:
    configure_logging()
    writer_one = vivii.context.get_writer(COLUMN_ONE_RING)
    writer_two = vivii.context.get_writer(COLUMN_TWO_RING)

    with socket.socket() as client:
        client.connect((HOST, PORT))
        while True:
            length = struct.unpack(">I", _recv_exact(client, 4))[0]
            payload = _recv_exact(client, length)
            reader = pa.ipc.open_stream(pa.py_buffer(payload))
            batch = reader.read_next_batch()

            col_one = batch.column("one").to_numpy()
            col_two = batch.column("two").to_numpy()

            writer_one.write_array(col_one)
            writer_two.write_array(col_two)


def fft_two_columns(
    column_one: np.ndarray,
    column_two: np.ndarray,
    rows: int,
) -> tuple[np.ndarray, np.ndarray]:
    return np.fft.fft(column_one[:rows]), np.fft.rfft(column_two[:rows])


def run_fft_consumer() -> None:
    configure_logging()
    reader_one = vivii.context.get_reader(COLUMN_ONE_RING)
    reader_two = vivii.context.get_reader(COLUMN_TWO_RING)
    nbytes = ROWS_PER_BATCH * np.dtype(np.float64).itemsize
    throughput = ThroughputLogger("processed")

    while True:
        column_one = reader_one.read_array(nbytes, dtype=np.float64)
        column_two = reader_two.read_array(nbytes, dtype=np.float64)

        if len(column_one) != ROWS_PER_BATCH or len(column_two) != ROWS_PER_BATCH:
            continue

        fft_two_columns(column_one, column_two, ROWS_PER_BATCH)
        throughput.record(column_one.nbytes + column_two.nbytes)
