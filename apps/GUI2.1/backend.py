from __future__ import annotations

import functools
import threading
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pythusa
from pyarrow import flight
from constants import GSE_SIGNAL_LISTS
from generic_connector import generic_connector, StorageServer
from data_storage import write_from_stream
from legacy_conn import (
    ECU_FIELD_NAMES,
    EXTR_ECU_FIELD_NAMES,
    GSE_FIELD_NAMES,
    LOAD_CELL_FIELD_NAMES,
)

GSE_ROWS_PER_FRAME = 1000
ECU_ROWS_PER_FRAME = 1000
EXTR_ECU_ROWS_PER_FRAME = 1000
LOAD_CELL_ROWS_PER_FRAME = 1000
GSE_NUM_SIGNALS = len(GSE_FIELD_NAMES)
ECU_NUM_SIGNALS = len(ECU_FIELD_NAMES)
EXTR_ECU_NUM_SIGNALS = len(EXTR_ECU_FIELD_NAMES)
LOAD_CELL_NUM_SIGNALS = len(LOAD_CELL_FIELD_NAMES)
GSE_FLIGHT_BIND = "grpc://0.0.0.0:8815"
ECU_FLIGHT_BIND = "grpc://0.0.0.0:8816"
EXTR_ECU_FLIGHT_BIND = "grpc://0.0.0.0:8817"
LOAD_CELL_FLIGHT_BIND = "grpc://0.0.0.0:8818"


FRONTEND_FLIGHT_GSE_BIND = "grpc://0.0.0.0:8819"
FRONTEND_FLIGHT_ECU_BIND = "grpc://0.0.0.0:8820"
FRONTEND_FLIGHT_EXTR_ECU_BIND = "grpc://0.0.0.0:8821"
FRONTEND_FLIGHT_LOAD_CELL_BIND = "grpc://0.0.0.0:8822"
FRONTEND_FLIGHT_GSE_CONNECT = "grpc://127.0.0.1:8819"


GSE_AVERAGE_OVER = 20

def backend_run_flight_server(
    *,
    stream,
    grpc_bind: str,
    rows_per_frame: int,
    num_signals: int,
) -> None:
    """Runs the Flight server (blocking). Picklable via functools.partial."""
    server = StorageServer(
        grpc_bind,
        stream_writer=stream,
        rows_per_frame=rows_per_frame,
        num_signals=num_signals,
    )
    server.serve()


def backend_storage_sink(*, stream) -> None:
    """Consumes ring frames (storage / accounting hook). Keeps the stream graph valid."""
    received_bytes = 0
    start_time = time.time()
    while True:
        time.sleep(0.01)
        frame = stream.read()
        if frame is None:
            continue
        received_bytes += int(frame.nbytes)
        if time.time() - start_time > 1.0:
            print(f"Sink: received {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
            start_time = time.time()
            received_bytes = 0


def gse_decimate_signals(*, window_size: int, col_names: list[str], instream, outstream) -> None:
    col_indices = [GSE_FIELD_NAMES.index(name) for name in col_names]  # col_names: e.g. tuple[str, ...]
    k = len(col_indices)
    if GSE_ROWS_PER_FRAME % window_size != 0:
        raise ValueError(
            f"window_size must divide GSE_ROWS_PER_FRAME ({GSE_ROWS_PER_FRAME}), got {window_size}"
        )
    out_rows = GSE_ROWS_PER_FRAME // window_size

    while True:
        frame = instream.read()
        if frame is None:
            time.sleep(0.001)
            continue

        subset = frame[:, col_indices]  # (GSE_ROWS_PER_FRAME, k)
        out = subset.reshape(out_rows, window_size, k).mean(axis=1)  # (out_rows, k)
        outstream.write(out.astype(np.float64, copy=False))


def gse_raw_telemetry_storage_write(*, stream) -> None:
    column_names = list(GSE_FIELD_NAMES)
    column_types = [pa.float64() for _ in range(GSE_NUM_SIGNALS)]
    path = Path(__file__).resolve().parent / "data" / "gse_raw_telemetry_data"
    write_from_stream(stream, path, column_names, column_types)

def gse_decimate_telemetry_storage_write(*, stream) -> None:
    column_names = list(GSE_SIGNAL_LISTS)
    column_types = [pa.float64() for _ in range(len(GSE_SIGNAL_LISTS))]
    path = Path(__file__).resolve().parent / "data" / "gse_decimated_telemetry_data"
    write_from_stream(stream, path, column_names, column_types)

def ecu_raw_telemetry_storage_write(*, stream) -> None:
    column_names = list(ECU_FIELD_NAMES)
    column_types = [pa.float64() for _ in range(ECU_NUM_SIGNALS)]
    path = Path(__file__).resolve().parent / "data" / "ecu_raw_telemetry_data"
    write_from_stream(stream, path, column_names, column_types)


def extr_ecu_raw_telemetry_storage_write(*, stream) -> None:
    column_names = list(EXTR_ECU_FIELD_NAMES)
    column_types = [pa.float64() for _ in range(EXTR_ECU_NUM_SIGNALS)]
    path = Path(__file__).resolve().parent / "data" / "extr_ecu_raw_telemetry_data"
    write_from_stream(stream, path, column_names, column_types)


def load_cell_raw_telemetry_storage_write(*, stream) -> None:
    column_names = list(LOAD_CELL_FIELD_NAMES)
    column_types = [pa.float64() for _ in range(LOAD_CELL_NUM_SIGNALS)]
    path = Path(__file__).resolve().parent / "data" / "load_cell_raw_telemetry_data"
    write_from_stream(stream, path, column_names, column_types)





def main() -> None:
    gse_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=GSE_FLIGHT_BIND,
        rows_per_frame=GSE_ROWS_PER_FRAME,
        num_signals=GSE_NUM_SIGNALS,
    )
    gse_decimate_fn = functools.partial(
        gse_decimate_signals,
        window_size=GSE_AVERAGE_OVER,
        col_names=GSE_SIGNAL_LISTS,
    )
    frontend_gse_generic_connector_fn = functools.partial(
        generic_connector,
        field_names=GSE_SIGNAL_LISTS,
        flight_address=FRONTEND_FLIGHT_GSE_CONNECT,
    )
    ecu_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=ECU_FLIGHT_BIND,
        rows_per_frame=ECU_ROWS_PER_FRAME,
        num_signals=ECU_NUM_SIGNALS,
    )
    extr_ecu_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=EXTR_ECU_FLIGHT_BIND,
        rows_per_frame=EXTR_ECU_ROWS_PER_FRAME,
        num_signals=EXTR_ECU_NUM_SIGNALS,
    )
    load_cell_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=LOAD_CELL_FLIGHT_BIND,
        rows_per_frame=LOAD_CELL_ROWS_PER_FRAME,
        num_signals=LOAD_CELL_NUM_SIGNALS,
    )

    with pythusa.Pipeline("backend") as pipeline:
        pipeline.add_stream(
            "gse_received_data",
            shape=(GSE_ROWS_PER_FRAME, GSE_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "gse_decimated_signals", 
            shape=(GSE_ROWS_PER_FRAME // GSE_AVERAGE_OVER, len(GSE_SIGNAL_LISTS)), 
            dtype=np.float64, 
            cache_align=True,
            frames=256
        )
        pipeline.add_stream(
            "ecu_received_data",
            shape=(ECU_ROWS_PER_FRAME, ECU_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "extr_ecu_received_data",
            shape=(EXTR_ECU_ROWS_PER_FRAME, EXTR_ECU_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "load_cell_received_data",
            shape=(LOAD_CELL_ROWS_PER_FRAME, LOAD_CELL_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_task(
            "gse_flight_server",
            fn=gse_flight_fn,
            writes={"stream": "gse_received_data"},
        )
        pipeline.add_task(
            "gse_decimate_signals",
            fn=gse_decimate_fn,
            reads={"instream": "gse_received_data"},
            writes={"outstream": "gse_decimated_signals"},
        )
        pipeline.add_task(
            "ecu_flight_server",
            fn=ecu_flight_fn,
            writes={"stream": "ecu_received_data"},
        )
        pipeline.add_task(
            "extr_ecu_flight_server",
            fn=extr_ecu_flight_fn,
            writes={"stream": "extr_ecu_received_data"},
        )
        pipeline.add_task(
            "load_cell_flight_server",
            fn=load_cell_flight_fn,
            writes={"stream": "load_cell_received_data"},
        )
        pipeline.add_task(
            "gse_storage_sink",
            fn=backend_storage_sink,
            reads={"stream": "gse_received_data"},
        )
        pipeline.add_task(
            "ecu_storage_sink",
            fn=backend_storage_sink,
            reads={"stream": "ecu_received_data"},
        )
        pipeline.add_task(
            "extr_ecu_storage_sink",
            fn=backend_storage_sink,
            reads={"stream": "extr_ecu_received_data"},
        )
        pipeline.add_task(
            "load_cell_storage_sink",
            fn=backend_storage_sink,
            reads={"stream": "load_cell_received_data"},
        )
        pipeline.add_task(
            "gse_raw_telemetry_storage_write",
            fn=gse_raw_telemetry_storage_write,
            reads={"stream": "gse_received_data"},
        )
        pipeline.add_task(
            "gse_decimate_telemetry_storage_write",
            fn=gse_decimate_telemetry_storage_write,
            reads={"stream": "gse_decimated_signals"},
        )
        pipeline.add_task(
            "ecu_raw_telemetry_storage_write",
            fn=ecu_raw_telemetry_storage_write,
            reads={"stream": "ecu_received_data"},
        )
        pipeline.add_task(
            "extr_ecu_raw_telemetry_storage_write",
            fn=extr_ecu_raw_telemetry_storage_write,
            reads={"stream": "extr_ecu_received_data"},
        )
        pipeline.add_task(
            "load_cell_raw_telemetry_storage_write",
            fn=load_cell_raw_telemetry_storage_write,
            reads={"stream": "load_cell_received_data"},
        )



        pipeline.add_task(
            "frontend_gse_generic_connector",
            fn=frontend_gse_generic_connector_fn,
            reads={"stream": "gse_decimated_signals"},
        )

        pipeline.run()


if __name__ == "__main__":
    print("GSE Flight:", GSE_FLIGHT_BIND)
    print("ECU Flight:", ECU_FLIGHT_BIND)
    print("EXTR_ECU Flight:", EXTR_ECU_FLIGHT_BIND)
    print("Load cell Flight:", LOAD_CELL_FLIGHT_BIND)
    main()
