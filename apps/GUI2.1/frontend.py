from __future__ import annotations

import colorsys
import functools
import time
from typing import Any, Iterable, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight
from pythusa import Pipeline
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import (
    GraphSeries,
    MomentaryButton,
    SensorGraph,
    StateButton,
    ToggleButton,
)

from generic_connector import StorageServer
from gse21connector import (
    GSE2V1_COMMAND_FIELD_NAMES,
    GSE2V1_ECHO_FIELD_NAMES,
    GSE2V1_FIELD_NAMES,
    GSE2V1_NUM_COMMAND_SIGNALS,
    GSE2V1_NUM_ECHO_SIGNALS,
    GSE2V1_NUM_SIGNALS,
    GSE2V1_ROWS_PER_FRAME,
    GSE2V1_STATE_FIELD_NAMES,
)
from nidaq_gse import (
    NIDAQ_AVERAGE_OVER,
    NIDAQ_FIELD_NAMES,
    NIDAQ_NUM_SIGNALS,
    NIDAQ_ROWS_PER_FRAME,
    RATE as NIDAQ_RATE,
)


FRONTEND_FLIGHT_GSE2V1_BIND = "grpc://0.0.0.0:8819"
FRONTEND_FLIGHT_GSE2V1_CMD_ECHO_BIND = "grpc://0.0.0.0:8820"
FRONTEND_FLIGHT_NIDAQ_BIND = "grpc://0.0.0.0:8826"
GSE2V1_CMD_FLIGHT_CONNECT = "grpc://127.0.0.1:8827"

COMMAND_ECHO_STREAM = "frontend_gse2v1_command_echo"
UI_STATE_STREAM = "frontend_gse2v1_ui_state"
UI_OUTPUT_ORDER = tuple(f"gse2v1.{name}" for name in GSE2V1_COMMAND_FIELD_NAMES)

CONNECTED_ECHO_INDEX = GSE2V1_ECHO_FIELD_NAMES.index("connected")
COMMAND_ECHO_OFFSET = 1
GSE2V1_TELEMETRY_STREAM = "frontend_gse2v1_data"
GSE2V1_STATE_FIELD_INDICES = {
    name: GSE2V1_FIELD_NAMES.index(name)
    for name in GSE2V1_STATE_FIELD_NAMES
    if name in GSE2V1_FIELD_NAMES
}
GSE2V1_COMMAND_STATE_FIELDS = {
    "igniter0Fire": "igniter0Continuity",
    "igniter1Fire": "igniter1Continuity",
    "solenoidState0": "solenoidCurrent0",
    "solenoidState1": "solenoidCurrent1",
    "solenoidState2": "solenoidCurrent2",
    "solenoidState3": "solenoidCurrent3",
    "solenoidState4": "solenoidCurrent4",
    "solenoidState5": "solenoidCurrent5",
    "solenoidState6": "solenoidCurrent6",
    "solenoidState7": "solenoidCurrent7",
    "solenoidState8": "solenoidCurrent8",
    "solenoidState9": "solenoidCurrent9",
    "solenoidState10": "solenoidCurrent10",
    "solenoidState11": "solenoidCurrent11",
}
GSE2V1_COMMAND_STATE_FIELD_INDICES = {
    command_index: GSE2V1_STATE_FIELD_INDICES[state_field]
    for command_index, command_name in enumerate(GSE2V1_COMMAND_FIELD_NAMES)
    if (state_field := GSE2V1_COMMAND_STATE_FIELDS.get(command_name))
    in GSE2V1_STATE_FIELD_INDICES
}


def _signal_colors(n: int) -> list[tuple[float, float, float, float]]:
    colors = []
    for i in range(n):
        r, g, b = colorsys.hsv_to_rgb(i / max(1, n), 0.75, 0.92)
        colors.append((r, g, b, 1.0))
    return colors


def _label_from_field(name: str) -> str:
    label = []
    for char in name:
        if label and char.isupper() and label[-1] != " ":
            label.append(" ")
        label.append(char)
    return "".join(label).replace("State", "State ").strip().title()


def _nidaq_graph_stream_name(field_name: str) -> str:
    return f"nidaq_graph_{field_name}"


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


def nidaq_split_to_graph_streams(*, stream, **graph_writers) -> None:
    seconds_per_sample = NIDAQ_AVERAGE_OVER / NIDAQ_RATE
    sample_offset = 0
    while True:
        frame = stream.read()
        if frame is None:
            time.sleep(0.001)
            continue
        n_rows = frame.shape[0]
        x_values = (sample_offset + np.arange(n_rows, dtype=np.float64)) * seconds_per_sample
        sample_offset += n_rows
        for i, name in enumerate(NIDAQ_FIELD_NAMES):
            writer = graph_writers.get(f"nidaq_graph_{name}")
            if writer is not None:
                out = np.vstack((x_values, frame[:, i])).astype(np.float64, copy=False)
                writer.write(out)


class StateValueMixin:
    telemetry_field_name: str | None = None
    telemetry_value: float | None = None

    def set_telemetry_value(self, value: float | None) -> None:
        self.telemetry_value = value

    def _meta_text(self, *, enabled: bool) -> str:
        base = super()._meta_text(enabled=enabled)
        if self.telemetry_field_name is None:
            return base
        value_text = "--" if self.telemetry_value is None else f"{self.telemetry_value:.3g}"
        return f"{self.telemetry_field_name}: {value_text} | {base}"


class StateValueToggleButton(StateValueMixin, ToggleButton):
    pass


class StateValueMomentaryButton(StateValueMixin, MomentaryButton):
    pass


class CommandEchoSync:
    component_id = "gse2v1_command_echo_sync"
    state_id: str | None = None

    def __init__(
        self,
        *,
        echo_stream_name: str,
        telemetry_stream_name: str,
        toggle_indices: Mapping[int, ToggleButton],
        momentary_indices: Mapping[int, MomentaryButton],
        state_value_buttons: Mapping[int, StateValueMixin],
    ) -> None:
        self._echo_stream_name = echo_stream_name
        self._telemetry_stream_name = telemetry_stream_name
        self._toggle_indices = dict(toggle_indices)
        self._momentary_indices = dict(momentary_indices)
        self._state_value_buttons = dict(state_value_buttons)
        self._toggle_adapters: dict[int, Any] = {}
        self._echo_reader: Any = None
        self._telemetry_reader: Any = None
        self._latest_echo_row: np.ndarray | None = None
        self._latest_telemetry_row: np.ndarray | None = None
        self._prev_connected: bool | None = None
        self._prev_echo_key: tuple | None = None

    def attach_adapters(self, adapters: Iterable[Any]) -> None:
        by_id = {getattr(a, "component_id", None): a for a in adapters}
        for cmd_idx, button in self._toggle_indices.items():
            adapter = by_id.get(button.button_id)
            if adapter is None:
                raise RuntimeError(f"No frontend adapter for button {button.button_id!r}")
            self._toggle_adapters[cmd_idx] = adapter

    def required_streams(self) -> tuple[str, ...]:
        return (self._echo_stream_name, self._telemetry_stream_name)

    def bind(self, readers: Mapping[str, Any]) -> None:
        self._echo_reader = readers[self._echo_stream_name]
        self._telemetry_reader = readers[self._telemetry_stream_name]
        for reader in (self._echo_reader, self._telemetry_reader):
            if hasattr(reader, "set_blocking"):
                reader.set_blocking(False)

    def consume(self) -> bool:
        if self._echo_reader is None or self._telemetry_reader is None:
            return False
        changed = False
        while True:
            frame = self._echo_reader.read()
            if frame is None:
                break
            self._latest_echo_row = np.asarray(frame, dtype=np.float64).reshape(-1)
            changed = True
        while True:
            frame = self._telemetry_reader.read()
            if frame is None:
                break
            self._latest_telemetry_row = np.asarray(frame, dtype=np.float64)
            changed = True
        return changed

    def render(self) -> None:
        import imgui

        connected = False
        row = self._latest_echo_row
        if row is not None and row.size > CONNECTED_ECHO_INDEX:
            connected = bool(row[CONNECTED_ECHO_INDEX] > 0.5)

        imgui.text_unformatted(
            f"GSE2.1 link: {'CONNECTED' if connected else 'DISCONNECTED'}"
        )

        all_buttons: list[StateButton] = (
            list(self._toggle_indices.values())
            + list(self._momentary_indices.values())
        )
        for button in all_buttons:
            button.enabled_by_default = connected

        telemetry_row = self._latest_telemetry_row
        if telemetry_row is not None:
            latest_values = np.asarray(telemetry_row, dtype=np.float64).reshape(-1)
            for cmd_idx, button in self._state_value_buttons.items():
                field_index = GSE2V1_COMMAND_STATE_FIELD_INDICES.get(cmd_idx)
                value = None
                if field_index is not None and field_index < latest_values.size:
                    value = float(latest_values[field_index])
                button.set_telemetry_value(value)

        if connected != self._prev_connected:
            print(f"[FRONTEND] GSE2.1 link: {'CONNECTED' if connected else 'DISCONNECTED'}")

        if row is not None:
            echo_key = tuple(
                bool(row[COMMAND_ECHO_OFFSET + i] > 0.5)
                for i in range(GSE2V1_NUM_COMMAND_SIGNALS)
                if COMMAND_ECHO_OFFSET + i < row.size
            )
            if echo_key != self._prev_echo_key:
                state_str = ", ".join(
                    f"{name}={v}" for name, v in zip(GSE2V1_COMMAND_FIELD_NAMES, echo_key)
                )
                print(f"[FRONTEND ECHO] {state_str}")
                self._prev_echo_key = echo_key

        if connected and not self._prev_connected and row is not None:
            for cmd_idx, toggle in self._toggle_indices.items():
                echo_idx = COMMAND_ECHO_OFFSET + cmd_idx
                if echo_idx >= row.size:
                    continue
                value = bool(row[echo_idx] > 0.5)
                toggle.state = value
                adapter = self._toggle_adapters.get(cmd_idx)
                if adapter is not None:
                    adapter._current_value = 1.0 if value else 0.0  # noqa: SLF001

        self._prev_connected = connected


class Gse2v1CommandFlightClient:
    def __init__(self, flight_address: str = GSE2V1_CMD_FLIGHT_CONNECT) -> None:
        self._flight_address = flight_address
        self._schema = pa.schema(
            [(name, pa.float64()) for name in GSE2V1_COMMAND_FIELD_NAMES]
        )
        self._descriptor = flight.FlightDescriptor.for_path("gse2v1_commands")
        self._client: Any = None
        self._writer: Any = None

    def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._client = None

    def send_row(self, row: np.ndarray) -> None:
        batch_row = np.asarray(row, dtype=np.float64).reshape(1, GSE2V1_NUM_COMMAND_SIGNALS)
        if batch_row.shape != (1, GSE2V1_NUM_COMMAND_SIGNALS):
            raise ValueError(
                f"expected command row shape (1, {GSE2V1_NUM_COMMAND_SIGNALS}), got {batch_row.shape}"
            )
        if self._writer is None:
            self._client = flight.connect(self._flight_address)
            self._writer, _ = self._client.do_put(self._descriptor, self._schema)
        arrays = [
            pa.array(batch_row[:, i], type=pa.float64())
            for i in range(GSE2V1_NUM_COMMAND_SIGNALS)
        ]
        batch = pa.RecordBatch.from_arrays(arrays, schema=self._schema)
        self._writer.write_batch(batch)

    def send_row_safe(self, row: np.ndarray) -> bool:
        try:
            self.send_row(row)
            return True
        except Exception as e:
            print(f"GSE2.1 command Flight client error: {e}")
            self.close()
            return False


def forward_ui_state_to_gse2v1_commands(
    *,
    ui_state: Any,
    command_echo: Any,
    cmd_flight: str = GSE2V1_CMD_FLIGHT_CONNECT,
    poll_sleep_s: float = 0.02,
) -> None:
    flight_client = Gse2v1CommandFlightClient(cmd_flight)
    last_snapshot: np.ndarray | None = None
    connected = False

    try:
        while True:
            latest_ui: np.ndarray | None = None
            while True:
                frame = ui_state.read()
                if frame is None:
                    break
                latest_ui = np.asarray(frame, dtype=np.float64).reshape(-1)

            while True:
                frame = command_echo.read()
                if frame is None:
                    break
                echo_row = np.asarray(frame, dtype=np.float64).reshape(-1)
                if echo_row.size > CONNECTED_ECHO_INDEX:
                    connected = bool(echo_row[CONNECTED_ECHO_INDEX] > 0.5)

            if latest_ui is None:
                time.sleep(poll_sleep_s)
                continue
            if not connected:
                last_snapshot = None
                time.sleep(poll_sleep_s)
                continue
            if last_snapshot is not None and np.array_equal(latest_ui, last_snapshot):
                time.sleep(poll_sleep_s)
                continue

            if flight_client.send_row_safe(latest_ui):
                cmd_str = ", ".join(
                    f"{name}={bool(latest_ui[i] > 0.5)}"
                    for i, name in enumerate(GSE2V1_COMMAND_FIELD_NAMES)
                    if i < latest_ui.size
                )
                print(f"[FRONTEND CMD] {cmd_str}")
                last_snapshot = latest_ui.copy()
            time.sleep(poll_sleep_s)
    finally:
        flight_client.close()


def build_frontend() -> tuple[Frontend, CommandEchoSync]:
    toggle_indices: dict[int, StateValueToggleButton] = {}
    momentary_indices: dict[int, StateValueMomentaryButton] = {}
    state_value_buttons: dict[int, StateValueMixin] = {}
    command_buttons: list[StateButton] = []

    for i, name in enumerate(GSE2V1_COMMAND_FIELD_NAMES):
        state_id = f"gse2v1.{name}"
        label = _label_from_field(name)
        state_field_name = GSE2V1_COMMAND_STATE_FIELDS.get(name)
        if name.startswith("solenoidState"):
            button = StateValueToggleButton(
                button_id=f"gse2v1_{name}",
                label=label,
                state_id=state_id,
                state=False,
                enabled_by_default=False,
                theme_name="tau_ceti",
            )
            toggle_indices[i] = button
        else:
            button = StateValueMomentaryButton(
                button_id=f"gse2v1_{name}",
                label=label,
                state_id=state_id,
                state=1.0,
                enabled_by_default=False,
                theme_name="tau_ceti",
            )
            momentary_indices[i] = button
        if state_field_name is not None:
            button.telemetry_field_name = state_field_name
            state_value_buttons[i] = button
        command_buttons.append(button)

    sync_component = CommandEchoSync(
        echo_stream_name=COMMAND_ECHO_STREAM,
        telemetry_stream_name=GSE2V1_TELEMETRY_STREAM,
        toggle_indices=toggle_indices,
        momentary_indices=momentary_indices,
        state_value_buttons=state_value_buttons,
    )

    nidaq_colors = _signal_colors(len(NIDAQ_FIELD_NAMES))
    frontend = Frontend("gui2_1_frontend", output_order=UI_OUTPUT_ORDER)
    frontend.add(sync_component)
    for button in command_buttons:
        frontend.add(button)
    frontend.add(
        SensorGraph(
            graph_id="nidaq_graph",
            title="NIDAQ Telemetry",
            series=tuple(
                GraphSeries(
                    series_id=f"nidaq_{name}",
                    label=name,
                    stream_name=_nidaq_graph_stream_name(name),
                    color_rgba=nidaq_colors[i],
                )
                for i, name in enumerate(NIDAQ_FIELD_NAMES)
            ),
            theme_name="tau_ceti",
            show_series_controls=True,
            window_seconds=2.0,
            plot_height=400,
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
    gse2v1_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_GSE2V1_BIND,
        rows_per_frame=GSE2V1_ROWS_PER_FRAME,
        num_signals=GSE2V1_NUM_SIGNALS,
    )
    echo_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_GSE2V1_CMD_ECHO_BIND,
        rows_per_frame=1,
        num_signals=GSE2V1_NUM_ECHO_SIGNALS,
    )
    nidaq_flight_fn = functools.partial(
        frontend_run_flight_server,
        grpc_bind=FRONTEND_FLIGHT_NIDAQ_BIND,
        rows_per_frame=NIDAQ_ROWS_PER_FRAME // NIDAQ_AVERAGE_OVER,
        num_signals=NIDAQ_NUM_SIGNALS,
    )

    nidaq_graph_write_bindings = {
        f"nidaq_graph_{name}": _nidaq_graph_stream_name(name)
        for name in NIDAQ_FIELD_NAMES
    }

    with Pipeline("frontend") as pipeline:
        pipeline.add_stream(
            GSE2V1_TELEMETRY_STREAM,
            shape=(GSE2V1_ROWS_PER_FRAME, GSE2V1_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=256,
        )
        pipeline.add_stream(
            COMMAND_ECHO_STREAM,
            shape=(1, GSE2V1_NUM_ECHO_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_stream(
            "frontend_nidaq_decimated_data",
            shape=(NIDAQ_ROWS_PER_FRAME // NIDAQ_AVERAGE_OVER, NIDAQ_NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=2560,
        )
        for name in NIDAQ_FIELD_NAMES:
            pipeline.add_stream(
                _nidaq_graph_stream_name(name),
                shape=(2, NIDAQ_ROWS_PER_FRAME // NIDAQ_AVERAGE_OVER),
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
            "frontend_gse2v1_flight_server",
            fn=gse2v1_flight_fn,
            writes={"stream": GSE2V1_TELEMETRY_STREAM},
        )
        pipeline.add_task(
            "frontend_gse2v1_command_echo_server",
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
            "gse2v1_command_forwarder",
            fn=forward_ui_state_to_gse2v1_commands,
            reads={
                "ui_state": UI_STATE_STREAM,
                "command_echo": COMMAND_ECHO_STREAM,
            },
        )
        pipeline.add_task(
            "frontend_nidaq_flight_server",
            fn=nidaq_flight_fn,
            writes={"stream": "frontend_nidaq_decimated_data"},
        )
        pipeline.add_task(
            "nidaq_split_to_graph_streams",
            fn=nidaq_split_to_graph_streams,
            reads={"stream": "frontend_nidaq_decimated_data"},
            writes=nidaq_graph_write_bindings,
        )
        pipeline.run()


if __name__ == "__main__":
    main()
