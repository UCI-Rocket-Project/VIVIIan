from __future__ import annotations

import functools
import time
from pathlib import Path

import pyarrow as pa
import numpy as np
import pythusa
from data_storage import write_from_stream
from generic_connector import StorageServer
from generic_connector import generic_stream_connector as generic_connector
from gse21connector import (
    GSE2V1_FIELD_NAMES,
    GSE2V1_NUM_SIGNALS,
    GSE2V1_ROWS_PER_FRAME,
)

GSE2V1_FLIGHT_BIND = "grpc://0.0.0.0:8815"

from nidaq_gse import (
    NIDAQ_AVERAGE_OVER,
    NIDAQ_FIELD_NAMES,
    NIDAQ_NUM_SIGNALS,
    NIDAQ_ROWS_PER_FRAME,
)
NIDAQ_FLIGHT_BIND = "grpc://0.0.0.0:8825"

FRONTEND_FLIGHT_GSE2V1_CONNECT = "grpc://127.0.0.1:8819"
FRONTEND_FLIGHT_NIDAQ_CONNECT = "grpc://127.0.0.1:8826"
DATA_DIR = Path(__file__).resolve().parent / "data"


def backend_run_flight_server(
    *,
    stream,
    grpc_bind: str,
    rows_per_frame: int,
    num_signals: int,
) -> None:
    server = StorageServer(
        grpc_bind,
        stream_writer=stream,
        rows_per_frame=rows_per_frame,
        num_signals=num_signals,
    )
    server.serve()


def nidaq_decimate_signals(
    *,
    window_size: int,
    col_names: list[str],
    instream,
    outstream,
) -> None:
    col_indices = [NIDAQ_FIELD_NAMES.index(name) for name in col_names]
    k = len(col_indices)
    if NIDAQ_ROWS_PER_FRAME % window_size != 0:
        raise ValueError(
            f"window_size must divide NIDAQ_ROWS_PER_FRAME ({NIDAQ_ROWS_PER_FRAME}), got {window_size}"
        )
    out_rows = NIDAQ_ROWS_PER_FRAME // window_size

    while True:
        frame = instream.read()
        while frame is not None:
            subset = frame[:, col_indices]
            out = subset.reshape(out_rows, window_size, k).mean(axis=1)
            outstream.write(out.astype(np.float64, copy=False))
            frame = instream.read()
        time.sleep(0.01)


def gse2v1_raw_telemetry_storage_write(*, stream) -> None:
    write_from_stream(
        stream,
        DATA_DIR / "gse2v1_raw_telemetry_data",
        list(GSE2V1_FIELD_NAMES),
        [pa.float64() for _ in range(GSE2V1_NUM_SIGNALS)],
    )


def nidaq_raw_telemetry_storage_write(*, stream) -> None:
    write_from_stream(
        stream,
        DATA_DIR / "nidaq_raw_telemetry_data",
        list(NIDAQ_FIELD_NAMES),
        [pa.float64() for _ in range(NIDAQ_NUM_SIGNALS)],
    )


def main() -> None:
    gse2v1_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=GSE2V1_FLIGHT_BIND,
        rows_per_frame=GSE2V1_ROWS_PER_FRAME,
        num_signals=GSE2V1_NUM_SIGNALS,
    )
    frontend_gse2v1_connector_fn = functools.partial(
        generic_connector,
        field_names=list(GSE2V1_FIELD_NAMES),
        flight_address=FRONTEND_FLIGHT_GSE2V1_CONNECT,
    )
    nidaq_flight_fn = functools.partial(
        backend_run_flight_server,
        grpc_bind=NIDAQ_FLIGHT_BIND,
        rows_per_frame=NIDAQ_ROWS_PER_FRAME,
        num_signals=NIDAQ_NUM_SIGNALS,
    )
    nidaq_decimate_fn = functools.partial(
        nidaq_decimate_signals,
        window_size=NIDAQ_AVERAGE_OVER,
        col_names=list(NIDAQ_FIELD_NAMES),
    )
    frontend_nidaq_connector_fn = functools.partial(
        generic_connector,
        field_names=list(NIDAQ_FIELD_NAMES),
        flight_address=FRONTEND_FLIGHT_NIDAQ_CONNECT,
    )

    with pythusa.Pipeline("backend") as pipeline:
        pipeline.add_stream(
            "gse2v1_received_data",
            shape=(GSE2V1_ROWS_PER_FRAME, GSE2V1_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "nidaq_received_data",
            shape=(NIDAQ_ROWS_PER_FRAME, NIDAQ_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "nidaq_decimated_signals",
            shape=(NIDAQ_ROWS_PER_FRAME // NIDAQ_AVERAGE_OVER, len(NIDAQ_FIELD_NAMES)),
            dtype=np.float64,
            cache_align=True,
            frames=256,
        )
        pipeline.add_task(
            "gse2v1_flight_server",
            fn=gse2v1_flight_fn,
            writes={"stream": "gse2v1_received_data"},
        )
        pipeline.add_task(
            "gse2v1_raw_telemetry_storage_write",
            fn=gse2v1_raw_telemetry_storage_write,
            reads={"stream": "gse2v1_received_data"},
        )
        pipeline.add_task(
            "frontend_gse2v1_connector",
            fn=frontend_gse2v1_connector_fn,
            reads={"stream": "gse2v1_received_data"},
        )
        pipeline.add_task(
            "nidaq_flight_server",
            fn=nidaq_flight_fn,
            writes={"stream": "nidaq_received_data"},
        )
        pipeline.add_task(
            "nidaq_decimate_signals",
            fn=nidaq_decimate_fn,
            reads={"instream": "nidaq_received_data"},
            writes={"outstream": "nidaq_decimated_signals"},
        )
        pipeline.add_task(
            "nidaq_raw_telemetry_storage_write",
            fn=nidaq_raw_telemetry_storage_write,
            reads={"stream": "nidaq_received_data"},
        )
        pipeline.add_task(
            "frontend_nidaq_connector",
            fn=frontend_nidaq_connector_fn,
            reads={"stream": "nidaq_decimated_signals"},
        )
        pipeline.run()


if __name__ == "__main__":
    print("GSE2V1 Flight:", GSE2V1_FLIGHT_BIND)
    print("NIDAQ Flight:", NIDAQ_FLIGHT_BIND)
    main()
