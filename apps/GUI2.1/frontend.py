#some stuff to implement 
# buttons: 
# they update state all the tiem, then you have a small function on a timer, reads through the state, 
# learns how to find toggls or state buttons, triggers events 
# resets at the end, sleeps a little then wakes up and does it again 
# good because we control how often commands get set, can be fast enough for anything practical 
# avoids hanging gui on commands that don't matter 








from __future__ import annotations

import colorsys
import functools
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_SRC = _REPO_ROOT / "packages" / "viviian_core" / "src"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

import numpy as np
from pythusa import Pipeline
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import GraphSeries, SensorGraph

from backend import StorageServer
from constants import GSE_SIGNAL_LISTS

GSE_DECIMATED_ROWS = 50  # 1000 raw rows // 200 average window
FRONTEND_FLIGHT_GSE_BIND = "grpc://0.0.0.0:8819"

GSE_VALUE_SIGNALS = GSE_SIGNAL_LISTS[1:]  # everything except packet_time


def _signal_colors(n: int) -> list[tuple[float, float, float, float]]:
    colors = []
    for i in range(n):
        r, g, b = colorsys.hsv_to_rgb(i / n, 0.75, 0.92)
        colors.append((r, g, b, 1.0))
    return colors


def _graph_stream_name(signal: str) -> str:
    return f"gse_graph_{signal}"


def frontend_run_flight_server(
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


def gse_split_to_graph_streams(*, stream, **graph_writers) -> None:
    """Split (rows, signals) telemetry into per-signal (2, rows) graph streams."""
    while True:
        frame = stream.read()
        if frame is None:
            time.sleep(0.001)
            continue
        x_values = frame[:, 0]
        for i, name in enumerate(GSE_VALUE_SIGNALS):
            writer = graph_writers.get(f"gw_{i}")
            if writer is not None:
                out = np.vstack((x_values, frame[:, i + 1])).astype(np.float64, copy=False)
                writer.write(out)


def build_frontend():
    colors = _signal_colors(len(GSE_VALUE_SIGNALS))
    frontend = Frontend("gui2_1_frontend")
    frontend.add(
        SensorGraph(
            graph_id="gse_graph",
            title="GSE Telemetry",
            series=tuple(
                GraphSeries(
                    series_id=f"gse_{name}",
                    label=name,
                    stream_name=_graph_stream_name(name),
                    color_rgba=colors[i],
                )
                for i, name in enumerate(GSE_VALUE_SIGNALS)
            ),
            theme_name="tau_ceti",
            show_series_controls=True,
            window_seconds=20000.0,
            
        )
    )
    return frontend


def build_frontend_task(frontend: Frontend):
    return frontend.build_task(
        backend=GlfwBackend(width=1280, height=720, theme_name="tau_ceti"),
        window_title="GUI 2.1",
        fill_backend_window=True,
    )


def main() -> None:
    frontend = build_frontend()
    frontend_task = build_frontend_task(frontend)
    gse_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_GSE_BIND,
        rows_per_frame=GSE_DECIMATED_ROWS,
        num_signals=len(GSE_SIGNAL_LISTS),
    )

    graph_write_bindings = {
        f"gw_{i}": _graph_stream_name(name)
        for i, name in enumerate(GSE_VALUE_SIGNALS)
    }

    with Pipeline("frontend") as pipeline:
        pipeline.add_stream(
            "frontend_gse_decimated_data",
            shape=(GSE_DECIMATED_ROWS, len(GSE_SIGNAL_LISTS)),
            dtype=np.float64,
            cache_align=True,
            frames=256,
        )
        for name in GSE_VALUE_SIGNALS:
            pipeline.add_stream(
                _graph_stream_name(name),
                shape=(2, GSE_DECIMATED_ROWS),
                dtype=np.float64,
                cache_align=True,
                frames=256,
            )

        pipeline.add_task(
            "frontend_gse_flight_server",
            fn=gse_flight_fn,
            writes={"stream": "frontend_gse_decimated_data"},
        )
        pipeline.add_task(
            "gse_split_to_graph_streams",
            fn=gse_split_to_graph_streams,
            reads={"stream": "frontend_gse_decimated_data"},
            writes=graph_write_bindings,
        )
        pipeline.add_task(
            "frontend_gui",
            fn=frontend_task,
            reads=frontend.read_bindings(),
        )
        pipeline.run()


if __name__ == "__main__":
    main()
