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
from typing import Any, Iterable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_SRC = _REPO_ROOT / "packages" / "viviian_core" / "src"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

import numpy as np
from pythusa import Pipeline
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import (
    GraphSeries,
    MomentaryButton,
    SensorGraph,
    StateButton,
    ToggleButton,
)

from backend import StorageServer
from constants import GSE_SIGNAL_LISTS
from gse_connector import (
    CONNECTED_ECHO_INDEX,
    ECHO_FIELD_NAMES,
    ECHO_ROWS_PER_FRAME,
    GN2_FILL_CMD_INDEX,
    MVAS_OPEN_CMD_INDEX,
    NUM_ECHO_SIGNALS,
    forward_ui_state_to_gse_commands,
)

GSE_DECIMATED_ROWS = 50  # 1000 raw rows // 200 average window
FRONTEND_FLIGHT_GSE_BIND = "grpc://0.0.0.0:8819"
FRONTEND_FLIGHT_GSE_CMD_ECHO_BIND = "grpc://0.0.0.0:8820"

GSE_VALUE_SIGNALS = GSE_SIGNAL_LISTS[1:]  # everything except packet_time

COMMAND_ECHO_STREAM = "frontend_gse_command_echo"
UI_STATE_STREAM = "frontend_ui_state"

CONNECTED_ECHO_INDEX = ECHO_FIELD_NAMES.index("connected")


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


class CommandEchoSync:
    """Frontend component that consumes the (1, NUM_ECHO_SIGNALS) command-echo
    stream and uses it to drive GSE button state:

    - while the ``connected`` flag is 0, all wired buttons are forced disabled
      so the operator cannot push state into a board that isn't listening
    - on every 0→1 transition, toggle buttons are synced to the device's
      current command bits so the UI reflects the board after a reconnect
      without overwriting whatever state the board was holding
    """

    component_id = "gse_command_echo_sync"
    state_id: str | None = None

    def __init__(
        self,
        *,
        stream_name: str,
        toggle_indices: Mapping[int, ToggleButton],
        momentary_buttons: Sequence[MomentaryButton],
    ) -> None:
        self._stream_name = stream_name
        self._toggle_indices = dict(toggle_indices)
        self._momentary_buttons = list(momentary_buttons)
        self._toggle_adapters: dict[int, Any] = {}
        self._reader: Any = None
        self._latest_row: np.ndarray | None = None
        self._prev_connected: bool | None = None

    def attach_adapters(self, adapters: Iterable[Any]) -> None:
        """Look up the runtime adapter for each wired button so we can also
        update the writer-snapshot value on sync (otherwise the GUI display
        would diverge from the output stream until the operator clicks)."""
        by_id = {getattr(a, "component_id", None): a for a in adapters}
        for cmd_idx, button in self._toggle_indices.items():
            adapter = by_id.get(button.button_id)
            if adapter is None:
                raise RuntimeError(
                    f"No frontend adapter for button {button.button_id!r}"
                )
            self._toggle_adapters[cmd_idx] = adapter

    def required_streams(self) -> tuple[str, ...]:
        return (self._stream_name,)

    def bind(self, readers: Mapping[str, Any]) -> None:
        self._reader = readers[self._stream_name]
        if hasattr(self._reader, "set_blocking"):
            self._reader.set_blocking(False)

    def consume(self) -> bool:
        if self._reader is None:
            return False
        latest = None
        while True:
            frame = self._reader.read()
            if frame is None:
                break
            latest = frame
        if latest is None:
            return False
        self._latest_row = np.asarray(latest, dtype=np.float64).reshape(-1)
        return True

    def render(self) -> None:
        import imgui

        connected = False
        commands: np.ndarray | None = None
        if self._latest_row is not None:
            connected = bool(self._latest_row[CONNECTED_ECHO_INDEX] > 0.5)
            commands = self._latest_row[:CONNECTED_ECHO_INDEX]

        imgui.text_unformatted(
            f"GSE link: {'CONNECTED' if connected else 'DISCONNECTED'}"
        )

        all_buttons: list[StateButton] = list(self._toggle_indices.values()) + list(
            self._momentary_buttons
        )
        for button in all_buttons:
            button.enabled_by_default = connected

        if connected and not self._prev_connected and commands is not None:
            for cmd_idx, toggle in self._toggle_indices.items():
                value = bool(commands[cmd_idx] > 0.5)
                toggle.state = value
                adapter = self._toggle_adapters.get(cmd_idx)
                if adapter is not None:
                    # Keep the writer snapshot in step with the displayed
                    # toggle state so the next operator click flips from the
                    # correct baseline.  Touches the adapter's internal field
                    # because viviian does not expose a public setter.
                    adapter._current_value = 1.0 if value else 0.0  # noqa: SLF001

        self._prev_connected = connected


def build_frontend() -> tuple[Frontend, CommandEchoSync]:
    toggle_gn2_fill = ToggleButton(
        button_id="gse_sol_gn2_fill",
        label="GN2 Fill",
        state_id="gse.sol_gn2_fill",
        state=False,
        enabled_by_default=False,
        theme_name="tau_ceti",
    )
    momentary_mvas_open = MomentaryButton(
        button_id="gse_sol_mvas_open",
        label="MVAS Open",
        state_id="gse.sol_mvas_open",
        state=1.0,
        enabled_by_default=False,
        theme_name="tau_ceti",
    )

    sync_component = CommandEchoSync(
        stream_name=COMMAND_ECHO_STREAM,
        toggle_indices={GN2_FILL_CMD_INDEX: toggle_gn2_fill},
        momentary_buttons=(momentary_mvas_open,),
    )

    colors = _signal_colors(len(GSE_VALUE_SIGNALS))
    frontend = Frontend("gui2_1_frontend")
    # Sync must be registered before the buttons so its consume()/render()
    # runs first in the frontend task loop and can mutate button state and
    # ``enabled_by_default`` before the button adapters render this frame.
    frontend.add(sync_component)
    frontend.add(toggle_gn2_fill)
    frontend.add(momentary_mvas_open)
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

    frontend.compile()
    sync_component.attach_adapters(frontend._adapters)  # noqa: SLF001
    return frontend, sync_component


def build_frontend_task(frontend: Frontend):
    return frontend.build_task(
        backend=GlfwBackend(width=1280, height=720, theme_name="tau_ceti"),
        window_title="GUI 2.1",
        fill_backend_window=True,
    )


def main() -> None:
    frontend, _sync_component = build_frontend()
    frontend_task = build_frontend_task(frontend)
    gse_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_GSE_BIND,
        rows_per_frame=GSE_DECIMATED_ROWS,
        num_signals=len(GSE_SIGNAL_LISTS),
    )
    echo_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_GSE_CMD_ECHO_BIND,
        rows_per_frame=ECHO_ROWS_PER_FRAME,
        num_signals=NUM_ECHO_SIGNALS,
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
        pipeline.add_stream(
            COMMAND_ECHO_STREAM,
            shape=(ECHO_ROWS_PER_FRAME, NUM_ECHO_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        for name in GSE_VALUE_SIGNALS:
            pipeline.add_stream(
                _graph_stream_name(name),
                shape=(2, GSE_DECIMATED_ROWS),
                dtype=np.float64,
                cache_align=True,
                frames=256,
            )
        pipeline.add_stream(
            UI_STATE_STREAM,
            shape=frontend.output_shape,
            dtype=np.float64,
            cache_align=False,
            frames=64,
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
            "frontend_gse_command_echo_server",
            fn=echo_flight_fn,
            writes={"stream": COMMAND_ECHO_STREAM},
        )
        pipeline.add_task(
            "frontend_gui",
            fn=frontend_task,
            reads=frontend.read_bindings(),
            writes=frontend.write_bindings(UI_STATE_STREAM),
        )
        pipeline.add_task(
            "gse_command_forwarder",
            fn=forward_ui_state_to_gse_commands,
            reads={
                "ui_state": UI_STATE_STREAM,
                "command_echo": COMMAND_ECHO_STREAM,
            },
        )
        pipeline.run()


if __name__ == "__main__":
    main()
