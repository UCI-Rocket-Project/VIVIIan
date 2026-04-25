from __future__ import annotations

import functools
import threading
import time

import numpy as np
import pyarrow as pa
import pythusa
from pyarrow import flight

# Match board1 producer: one ring frame == one Flight batch worth of samples
ROWS_PER_FRAME = 1000
NUM_SIGNALS = 8
DEFAULT_GRPC_BIND = "grpc://0.0.0.0:8815"


class StorageServer(flight.FlightServerBase):
    """Flight receiver: each do_put stream is turned into NumPy frames on the pythusa ring."""

    def __init__(self, location: str, stream_writer) -> None:
        super().__init__(location)
        self._stream = stream_writer
        self._write_lock = threading.Lock()

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        start_time = time.time()
        received_bytes = 0
        for chunk in reader:
            record_batch = chunk.data
            arrays = [record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            if data.shape != (ROWS_PER_FRAME, NUM_SIGNALS):
                raise ValueError(
                    f"Expected frame shape {(ROWS_PER_FRAME, NUM_SIGNALS)}, got {data.shape}"
                )
            with self._write_lock:
                self._stream.write(data)
            received_bytes += int(data.nbytes)
            if time.time() - start_time > 1.0:
                print(f"Flight ingest: {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
                start_time = time.time()
                received_bytes = 0


def backend_run_flight_server(*, stream, grpc_bind: str) -> None:
    """Runs the Flight server (blocking). Picklable via functools.partial for grpc_bind only."""
    server = StorageServer(grpc_bind, stream_writer=stream)
    server.serve()


def backend_storage_sink(*, stream) -> None:
    """Consumes ring frames (storage / accounting hook). Keeps the stream graph valid."""
    received_bytes = 0
    start_time = time.time()
    while True:
        frame = stream.read()
        if frame is None:
            continue
        received_bytes += int(frame.nbytes)
        if time.time() - start_time > 1.0:
            print(f"Sink: received {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
            start_time = time.time()
            received_bytes = 0


def main() -> None:
    flight_fn = functools.partial(backend_run_flight_server, grpc_bind=DEFAULT_GRPC_BIND)
    with pythusa.Pipeline("backend") as pipeline:
        pipeline.add_stream(
            "received_data",
            shape=(ROWS_PER_FRAME, NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_task(
            "flight_server",
            fn=flight_fn,
            writes={"stream": "received_data"},
        )
        pipeline.add_task(
            "storage_sink",
            fn=backend_storage_sink,
            reads={"stream": "received_data"},
        )
        pipeline.run()


if __name__ == "__main__":
    print("Storage backend pipeline (Flight + pythusa ring). Listening on", DEFAULT_GRPC_BIND)
    main()
