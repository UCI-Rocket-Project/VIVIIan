from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from viviian.gui_utils import (
    AnalogNeedleGauge,
    ConsoleComponent,
    EventLogPanel,
    EventRecord,
    GraphSeries,
    LedBarGauge,
    MomentaryButton,
    SensorGraph,
    ToggleButton,
    theme,
)
from viviian.gui_utils._streaming import fan_out_reader_groups

from ucirplgui.device_link_read import DeviceLinkBoardSnapshot


def _require_imgui() -> Any:
    try:
        import imgui  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for dashboard rendering. Install GUI dependencies "
            'with `pip install -e ".[gui]"`.'
        ) from exc
    return imgui


class DeviceLinkStore:
    """Thread-safe store updated from frontend feed_loop."""

    __slots__ = ("_lock", "_data")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, DeviceLinkBoardSnapshot] = {}

    def update(self, boards: dict[str, DeviceLinkBoardSnapshot]) -> None:
        with self._lock:
            self._data = dict(boards)

    def snapshot(self) -> dict[str, DeviceLinkBoardSnapshot]:
        with self._lock:
            return dict(self._data)


_CONNECTION_GAUGE_WIDTH = 500.0
_CONNECTION_GAUGE_HEIGHT = 92.0
_TANK_GAUGE_WIDTH = 300.0
_TANK_GAUGE_HEIGHT = 250.0
_PRIMARY_GRAPH_HEIGHT = 370.0
_STATUS_COLUMN_WIDTH = 400.0
_GAUGES_COLUMN_WIDTH = 400.0
_CONNECTION_GAUGE_HEADER_RIGHT = "RX Age"
_BACKEND_THROUGHPUT_HEADER_RIGHT = "Mbps"
_BACKEND_THROUGHPUT_GAUGE_HIGH = 1.0
_EVENT_LOG_INLINE_HEIGHT = 240.0
_EVENT_LOG_WINDOW_WIDTH = 560.0
_EVENT_LOG_WINDOW_HEIGHT = 360.0
_EVENT_LOG_MAX_RECORDS = 64
_PING_CRIT_THRESHOLD_MS = 110.0
_EVENT_LOG_ACTION_BUTTON_WIDTH = 28.0
_EVENT_LOG_ACTION_BUTTON_HEIGHT = 24.0

_SHOWCASE_COLOR_ACID = theme.ACID
_SHOWCASE_COLOR_ALERT = theme.ALERT
_SHOWCASE_COLOR_WARN = theme.WARN
_SHOWCASE_COLOR_CYAN = (0.000, 0.898, 0.761, 1.0)
_SHOWCASE_COLOR_LIME = (0.847, 1.000, 0.000, 1.0)
_SHOWCASE_COLOR_BLUE = (0.200, 0.600, 1.000, 1.0)
_SHOWCASE_COLOR_PURPLE = (0.700, 0.250, 1.000, 1.0)
_SHOWCASE_COLOR_PINK = (1.000, 0.200, 0.700, 1.0)
_SHOWCASE_COLOR_CRIT = theme.CRIT


def _mix_rgba(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    ratio: float,
) -> tuple[float, float, float, float]:
    return tuple(
        ((1.0 - ratio) * lhs) + (ratio * rhs)
        for lhs, rhs in zip(left, right, strict=True)
    )


def _format_event_timestamp(timestamp_s: float) -> str:
    whole = max(0.0, float(timestamp_s))
    minutes = int(whole // 60.0)
    seconds = whole - (minutes * 60.0)
    return f"{minutes:02d}:{seconds:06.3f}"


def _event_severity_color(severity: str) -> tuple[float, float, float, float]:
    if severity == "ok":
        return theme.ACID
    if severity == "warn":
        return theme.ALERT
    if severity == "crit":
        return theme.CRIT
    return theme.INK_2


class UCIRPLDashboard(ConsoleComponent):
    component_id = "ucirpl_dashboard"

    def __init__(
        self,
        *,
        command_writer: Any,
        link_store: DeviceLinkStore,
    ) -> None:
        self.command_writer = command_writer
        self.link_store = link_store

        self._toggle_igniter = self._make_toggle("igniter", "Igniter", "igniter_pair", variant="alert")
        self._toggle_abort = self._make_toggle("abort", "Abort", "alarm", variant="crit")
        self._toggle_arm = self._make_toggle("arm", "Arm", "alarm", variant="alert")
        self._toggle_alarm = self._make_toggle("alarm", "Alarm", "cmd.gse.alarm")
        self._toggle_gn2_fill = self._make_toggle("gn2_fill", "GN2 Fill", "cmd.gse.gn2_fill")
        self._toggle_gn2_vent = self._make_toggle("gn2_vent", "GN2 Vent", "cmd.gse.gn2_vent")
        self._toggle_gn2_disconnect = self._make_toggle("gn2_disconnect", "GN2 Disconnect", "cmd.gse.gn2_disconnect")
        self._toggle_mvas_fill = self._make_toggle("mvas_fill", "MVAS Fill", "cmd.gse.mvas_fill")
        self._toggle_mvas_vent = self._make_toggle("mvas_vent", "MVAS Vent", "cmd.gse.mvas_vent")
        self._momentary_mvas_open = self._make_momentary("mvas_open", "MVAS Open", "cmd.gse.mvas_open")
        self._momentary_mvas_close = self._make_momentary("mvas_close", "MVAS Close", "cmd.gse.mvas_close")
        self._toggle_lox_vent = self._make_toggle("lox_vent", "LOX Vent", "cmd.gse.lox_vent")
        self._toggle_lng_vent = self._make_toggle("lng_vent", "LNG Vent", "cmd.gse.lng_vent")
        self._toggle_copv_vent = self._make_toggle("copv_vent", "COPV Vent", "cmd.ecu.copv_vent")
        self._toggle_pv1 = self._make_toggle("pv1", "PV1", "cmd.ecu.pv1")
        self._toggle_pv2 = self._make_toggle("pv2", "PV2", "cmd.ecu.pv2")
        self._toggle_vent = self._make_toggle("vent", "Vent", "cmd.ecu.vent")

        self._gse_control_pairs = (
            (self._toggle_alarm, self._toggle_gn2_fill),
            (self._toggle_gn2_vent, self._toggle_gn2_disconnect),
            (self._toggle_mvas_fill, self._toggle_mvas_vent),
            (self._momentary_mvas_open, self._momentary_mvas_close),
            (self._toggle_lox_vent, self._toggle_lng_vent),
        )
        self._ecu_control_pairs = (
            (self._toggle_copv_vent, self._toggle_pv1),
            (self._toggle_pv2, self._toggle_vent),
        )
        self._abort_reset_toggles = (
            self._toggle_igniter,
            self._toggle_arm,
            self._toggle_alarm,
            self._toggle_gn2_fill,
            self._toggle_gn2_vent,
            self._toggle_gn2_disconnect,
            self._toggle_mvas_fill,
            self._toggle_mvas_vent,
            self._toggle_lox_vent,
            self._toggle_lng_vent,
            self._toggle_copv_vent,
            self._toggle_pv1,
            self._toggle_pv2,
            self._toggle_vent,
        )
        self._momentary_buttons = (
            self._momentary_mvas_open,
            self._momentary_mvas_close,
        )
        self._command_buttons = (
            self._toggle_igniter,
            self._toggle_abort,
            self._toggle_arm,
            self._toggle_alarm,
            self._toggle_gn2_fill,
            self._toggle_gn2_vent,
            self._toggle_gn2_disconnect,
            self._toggle_mvas_fill,
            self._toggle_mvas_vent,
            self._momentary_mvas_open,
            self._momentary_mvas_close,
            self._toggle_lox_vent,
            self._toggle_lng_vent,
            self._toggle_copv_vent,
            self._toggle_pv1,
            self._toggle_pv2,
            self._toggle_vent,
        )

        self.ecu_connection_guage = self._make_connection_gauge("ecu_connection")
        self.gse_connection_guage = self._make_connection_gauge("gse_connection")
        self.load_cell_connection_guage = self._make_connection_gauge("load_cell_connection")
        self.extr_ecu_connection_guage = self._make_connection_gauge("extr_ecu_connection")
        self.backend_throughput_guage = self._make_backend_throughput_gauge(
            "backend_throughput_mbps"
        )

        self._telemetry_widgets = (
            self.gse_connection_guage,
            self.ecu_connection_guage,
            self.extr_ecu_connection_guage,
            self.load_cell_connection_guage,
            self.backend_throughput_guage,
        )
        self._connection_event_gauges = (
            ("GSE", self.gse_connection_guage),
            ("ECU", self.ecu_connection_guage),
            ("EXTR_ECU", self.extr_ecu_connection_guage),
            ("LOADCELL", self.load_cell_connection_guage),
        )
        self.event_log = EventLogPanel(
            component_id="events",
            records=[
                EventRecord("00:00.000", "info", "UI", "UCIRPL dashboard initialized", "E001"),
            ],
            title="EVENT LOG",
        )
        self._session_start_s = time.monotonic()
        self._event_counter = 1
        self._last_control_snapshot = self._control_state_snapshot()
        self._ping_crit_active = {
            source: False
            for source, _gauge in self._connection_event_gauges
        }
        self._event_log_popout = False
        self._event_log_window_size_initialized = False

        self.UPPER_FEED_SYSTEM_GRAPH = SensorGraph(
            graph_id="UPPER_FEED_SYSTEM_GRAPH",
            title="UPPER FEED SYSTEM",
            series=(
                GraphSeries("copv", "COPV", "tank_copv", color_rgba=_SHOWCASE_COLOR_ACID),
                GraphSeries("lox", "LOX", "tank_lox", color_rgba=_SHOWCASE_COLOR_ALERT, overlay=True),
                GraphSeries("lng", "LNG", "tank_lng", color_rgba=_SHOWCASE_COLOR_WARN, overlay=True),
                GraphSeries("vent", "VENT", "line_vent", color_rgba=_SHOWCASE_COLOR_CYAN),
                GraphSeries("fft_tank_copv", "COPV FFT", "fft_tank_copv", color_rgba=_mix_rgba(_SHOWCASE_COLOR_ACID, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_tank_lox", "LOX FFT", "fft_tank_lox", color_rgba=_mix_rgba(_SHOWCASE_COLOR_ALERT, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_tank_lng", "LNG FFT", "fft_tank_lng", color_rgba=_mix_rgba(_SHOWCASE_COLOR_WARN, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_line_vent", "VENT FFT", "fft_line_vent", color_rgba=_mix_rgba(_SHOWCASE_COLOR_CYAN, theme.INK, 0.35), overlay=True),
            ),
            window_seconds=300.0,
            max_points_per_series=1024,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=_PRIMARY_GRAPH_HEIGHT,
        )

        self.LOWER_FEED_SYSTEM_GRAPH = SensorGraph(
            graph_id="LOWER_FEED_SYSTEM_GRAPH",
            title="LOWER FEED SYSTEM",
            series=(
                GraphSeries("lox_mvas", "LOX_MVAS", "line_lox_mvas", color_rgba=_SHOWCASE_COLOR_LIME, overlay=True),
                GraphSeries("injtee", "LOX_INJ_TEE", "line_lox_inj_tee", color_rgba=_SHOWCASE_COLOR_BLUE, overlay=True),
                GraphSeries("injlox", "INJ_LOX", "line_inj_lox", color_rgba=_SHOWCASE_COLOR_PURPLE, overlay=True),
                GraphSeries("injlng", "INJ_LNG", "line_inj_lng", color_rgba=_SHOWCASE_COLOR_PINK, overlay=True),
                GraphSeries("fft_line_lox_mvas", "LOX MVAS FFT", "fft_line_lox_mvas", color_rgba=_mix_rgba(_SHOWCASE_COLOR_LIME, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_line_lox_inj_tee", "LOX INJ FFT", "fft_line_lox_inj_tee", color_rgba=_mix_rgba(_SHOWCASE_COLOR_BLUE, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_line_inj_lox", "INJ LOX FFT", "fft_line_inj_lox", color_rgba=_mix_rgba(_SHOWCASE_COLOR_PURPLE, theme.INK, 0.35), overlay=True),
                GraphSeries("fft_line_inj_lng", "INJ LNG FFT", "fft_line_inj_lng", color_rgba=_mix_rgba(_SHOWCASE_COLOR_PINK, theme.INK, 0.35), overlay=True),
            ),
            window_seconds=90.0,
            max_points_per_series=1024,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=_PRIMARY_GRAPH_HEIGHT,
        )

        self.LOAD_CELL_GRAPH = SensorGraph(
            graph_id="LOAD_CELL_GRAPH",
            title="LOAD CELL",
            series=(
                GraphSeries("load", "Force", "load_force", color_rgba=_SHOWCASE_COLOR_CRIT),
                GraphSeries("fft_load_force", "FORCE FFT", "fft_load_force", color_rgba=_mix_rgba(_SHOWCASE_COLOR_CRIT, theme.INK, 0.35), overlay=True),
            ),
            window_seconds=90.0,
            max_points_per_series=1024,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=_PRIMARY_GRAPH_HEIGHT,
        )

        self._graph_widgets = (
            self.UPPER_FEED_SYSTEM_GRAPH,
            self.LOWER_FEED_SYSTEM_GRAPH,
            self.LOAD_CELL_GRAPH,
        )

        self.copv_pt = self._make_tank_gauge("copv_pt", "COPV PT", "tank_copv", 5000.0)
        self.lox_pt = self._make_tank_gauge("lox_pt", "LOX PT", "tank_lox", 700.0)
        self.lng_pt = self._make_tank_gauge("lng_pt", "LNG PT", "tank_lng", 700.0)
        self.vent_pt = self._make_tank_gauge("vent_pt", "VENT PT", "line_vent", 400.0)
        self.lox_mvas_pt = self._make_tank_gauge("lox_mvas_pt", "LOX MVAS PT", "line_lox_mvas", 600.0)
        self.lox_inj_tee_pt = self._make_tank_gauge("lox_inj_tee_pt", "LOX INJ TEE", "line_lox_inj_tee", 600.0)
        self.inj_lox_pt = self._make_tank_gauge("inj_lox_pt", "LOX INJ PT", "line_inj_lox", 500.0)
        self.inj_lng_pt = self._make_tank_gauge("inj_lng_pt", "LNG INJ PT", "line_inj_lng", 500.0)

        self._tank_gauge_widgets = (
            self.copv_pt,
            self.lox_pt,
            self.lng_pt,
            self.vent_pt,
            self.lox_mvas_pt,
            self.lox_inj_tee_pt,
            self.inj_lox_pt,
            self.inj_lng_pt,
        )
        self._bindable_widgets = (
            *self._telemetry_widgets,
            *self._graph_widgets,
            *self._tank_gauge_widgets,
        )
        self._reader_groups_spec = (
            *((widget.stream_name,) for widget in self._telemetry_widgets),
            *(tuple(series.stream_name for series in graph.series) for graph in self._graph_widgets),
            *((widget.stream_name,) for widget in self._tank_gauge_widgets),
        )

    def _make_toggle(
        self,
        button_id: str,
        label: str,
        state_id: str,
        *,
        variant: str | None = None,
    ) -> ToggleButton:
        return ToggleButton(
            button_id=button_id,
            label=label,
            state_id=state_id,
            state=False,
            theme_name="tau_ceti",
            variant=variant,
        )

    def _make_momentary(
        self,
        button_id: str,
        label: str,
        state_id: str,
    ) -> MomentaryButton:
        return MomentaryButton(
            button_id=button_id,
            label=label,
            state_id=state_id,
            state=1.0,
            theme_name="tau_ceti",
        )

    def _make_connection_gauge(self, stream_name: str) -> LedBarGauge:
        return LedBarGauge(
            gauge_id=stream_name,
            label=f"{stream_name}_gauge",
            stream_name=stream_name,
            low_value=0.0,
            high_value=110.0,
            width=_CONNECTION_GAUGE_WIDTH,
            height=_CONNECTION_GAUGE_HEIGHT,
            theme_name="tau_ceti",
            unit_label="ms",
            display_precision=1,
            secondary_value="110 ms",
            show_stream_label=False,
            header_right=_CONNECTION_GAUGE_HEADER_RIGHT,
            footer_left="",
            footer_right="",
        )

    def _make_backend_throughput_gauge(self, stream_name: str) -> LedBarGauge:
        return LedBarGauge(
            gauge_id=stream_name,
            label="BACKEND THROUGHPUT",
            stream_name=stream_name,
            low_value=0.0,
            high_value=_BACKEND_THROUGHPUT_GAUGE_HIGH,
            width=_CONNECTION_GAUGE_WIDTH,
            height=_CONNECTION_GAUGE_HEIGHT,
            theme_name="tau_ceti",
            unit_label="Mbps",
            display_precision=3,
            secondary_value="1.0 Mbps",
            show_stream_label=False,
            header_right=_BACKEND_THROUGHPUT_HEADER_RIGHT,
            footer_left="",
            footer_right="",
        )

    def _make_tank_gauge(
        self,
        gauge_id: str,
        label: str,
        stream_name: str,
        high_value: float,
    ) -> AnalogNeedleGauge:
        return AnalogNeedleGauge(
            gauge_id,
            label=label,
            stream_name=stream_name,
            low_value=0.0,
            high_value=high_value,
            width=_TANK_GAUGE_WIDTH,
            height=_TANK_GAUGE_HEIGHT,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            status_layout="centered",
            show_stream_label=False,
            header_right="",
            footer_right="",
            footer_left="",
        )

    def _clear_controls_for_abort(self) -> None:
        for button in self._abort_reset_toggles:
            button.state = False
        for button in self._momentary_buttons:
            button._press_timestamp = None  # noqa: SLF001 - reset latched momentary state

    def _append_event(self, severity: str, source: str, message: str) -> None:
        self._event_counter += 1
        timestamp = _format_event_timestamp(time.monotonic() - self._session_start_s)
        self.event_log.records.insert(
            0,
            EventRecord(
                timestamp=timestamp,
                severity=severity,
                source=source,
                message=message,
                event_id=f"E{self._event_counter:03d}",
            ),
        )
        self.event_log.records = self.event_log.records[:_EVENT_LOG_MAX_RECORDS]

    def _toggle_value(self, button: ToggleButton) -> float:
        return 1.0 if bool(button.state) else 0.0

    def _momentary_value(self, button: MomentaryButton) -> float:
        return 1.0 if button._is_on() else 0.0  # noqa: SLF001 - same state contract as old dashboard

    def _button_state_value(self, button: ToggleButton | MomentaryButton) -> float:
        if isinstance(button, ToggleButton):
            return self._toggle_value(button)
        return self._momentary_value(button)

    def _control_state_snapshot(self) -> tuple[float, ...]:
        return tuple(self._button_state_value(button) for button in self._command_buttons)

    def _describe_command_changes(
        self,
        previous: tuple[float, ...],
        current: tuple[float, ...],
    ) -> str:
        changes: list[str] = []
        for button, previous_value, current_value in zip(
            self._command_buttons, previous, current, strict=True
        ):
            if previous_value == current_value:
                continue
            state_text = "ON" if current_value else "OFF"
            changes.append(f"{button.label.upper()} -> {state_text}")
        return ", ".join(changes)

    def _update_ping_events(self) -> None:
        for source, gauge in self._connection_event_gauges:
            ping_crit = float(gauge.display_value) > _PING_CRIT_THRESHOLD_MS
            previous = self._ping_crit_active[source]
            self._ping_crit_active[source] = ping_crit
            if ping_crit and not previous:
                self._append_event(
                    "crit",
                    source,
                    f"RX age {gauge.display_value:.1f} ms exceeds {_PING_CRIT_THRESHOLD_MS:.1f} ms",
                )

    def _record_abort_blocked_interaction(self, button: ToggleButton | MomentaryButton) -> None:
        self._append_event(
            "warn",
            "ABORT",
            f"Ignored {button.label.upper()} while abort is active",
        )

    def _render_control_button(self, button: ToggleButton | MomentaryButton) -> None:
        previous_state = button.state
        previous_press_timestamp = getattr(button, "_press_timestamp", None)
        update = button.render()
        if update is None:
            return
        if self._toggle_abort.state and button is not self._toggle_abort:
            button.state = previous_state
            if isinstance(button, MomentaryButton):
                button._press_timestamp = previous_press_timestamp  # noqa: SLF001 - revert blocked momentary pulse
            self._record_abort_blocked_interaction(button)

    def _render_button_pairs(
        self,
        *,
        table_id: str,
        button_pairs: tuple[tuple[ToggleButton | MomentaryButton, ToggleButton | MomentaryButton], ...],
    ) -> None:
        imgui = _require_imgui()
        flags = imgui.TABLE_SIZING_STRETCH_SAME | imgui.TABLE_NO_SAVED_SETTINGS
        with imgui.begin_table(table_id, 2, flags) as table:
            if table.opened:
                imgui.table_setup_column("Left", imgui.TABLE_COLUMN_WIDTH_STRETCH)
                imgui.table_setup_column("Right", imgui.TABLE_COLUMN_WIDTH_STRETCH)
                for left_button, right_button in button_pairs:
                    imgui.table_next_row()
                    imgui.table_set_column_index(0)
                    self._render_control_button(left_button)
                    imgui.table_set_column_index(1)
                    self._render_control_button(right_button)

    def required_streams(self) -> tuple[str, ...]:
        streams = [
            *(widget.stream_name for widget in self._telemetry_widgets),
            *(series.stream_name for graph in self._graph_widgets for series in graph.series),
            *(widget.stream_name for widget in self._tank_gauge_widgets),
        ]
        return tuple(dict.fromkeys(streams))

    def bind(self, readers: Mapping[str, Any]) -> None:
        reader_groups = fan_out_reader_groups(readers, self._reader_groups_spec)
        for widget, group in zip(self._bindable_widgets, reader_groups):
            widget.bind(group)

    def consume(self) -> bool:
        dirty = False
        for widget in self._bindable_widgets:
            dirty = widget.consume() or dirty
        if self._toggle_abort.state:
            self._clear_controls_for_abort()
        self._update_ping_events()
        self.send_commands_if_needed()
        return dirty

    def _control_vector(self) -> list[float]:
        igniter_on = self._toggle_value(self._toggle_igniter)
        alarm_on = 1.0 if (
            bool(self._toggle_alarm.state)
            or bool(self._toggle_arm.state)
            or bool(self._toggle_abort.state)
        ) else 0.0
        return [
            igniter_on,
            igniter_on,
            alarm_on,
            self._toggle_value(self._toggle_gn2_fill),
            self._toggle_value(self._toggle_gn2_vent),
            self._toggle_value(self._toggle_gn2_disconnect),
            self._toggle_value(self._toggle_mvas_fill),
            self._toggle_value(self._toggle_mvas_vent),
            self._momentary_value(self._momentary_mvas_open),
            self._momentary_value(self._momentary_mvas_close),
            self._toggle_value(self._toggle_lox_vent),
            self._toggle_value(self._toggle_lng_vent),
            self._toggle_value(self._toggle_copv_vent),
            self._toggle_value(self._toggle_pv1),
            self._toggle_value(self._toggle_pv2),
            self._toggle_value(self._toggle_vent),
        ]

    def send_commands_if_needed(self) -> None:
        current_snapshot = self._control_state_snapshot()
        if current_snapshot == self._last_control_snapshot:
            return
        if self.command_writer is None:
            self._last_control_snapshot = current_snapshot
            return
        writer = getattr(self.command_writer, "write", None)
        if callable(writer):
            if writer(self._control_vector()):
                changes = self._describe_command_changes(
                    self._last_control_snapshot,
                    current_snapshot,
                )
                message = "Command sent" if not changes else f"Command sent: {changes}"
                self._append_event("ok", "CMD", message)
                self._last_control_snapshot = current_snapshot

    def _render_event_log_filter_button(self, severity: str, count: int) -> None:
        imgui = _require_imgui()
        active = severity in self.event_log.active_filters
        color = _event_severity_color(severity)
        imgui.push_style_color(imgui.COLOR_BUTTON, *(theme.PANEL_BG_2 if active else theme.PANEL_BG))
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *theme.PANEL_BG_2)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *theme.PANEL_BG_3)
        imgui.push_style_color(imgui.COLOR_TEXT, *color)
        if hasattr(imgui, "COLOR_BORDER"):
            imgui.push_style_color(imgui.COLOR_BORDER, *color)
            color_count = 5
        else:
            color_count = 4
        if imgui.button(f"{severity.upper()} {count}", width=0.0, height=24.0):
            if active:
                self.event_log.active_filters.discard(severity)
            else:
                self.event_log.active_filters.add(severity)
        imgui.pop_style_color(color_count)

    def _render_event_log_action_button(
        self,
        *,
        action_icon: str,
        action_id: str,
    ) -> None:
        imgui = _require_imgui()
        imgui.push_style_color(imgui.COLOR_BUTTON, *theme.PANEL_BG_2)
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *theme.PANEL_BG_3)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *theme.BUTTON_OFF_ACTIVE)
        imgui.push_style_color(imgui.COLOR_TEXT, *theme.INK)
        if hasattr(imgui, "COLOR_BORDER"):
            imgui.push_style_color(imgui.COLOR_BORDER, *theme.PANEL_BORDER)
            color_count = 5
        else:
            color_count = 4
        if imgui.button(
            f"{action_icon}##{action_id}",
            width=_EVENT_LOG_ACTION_BUTTON_WIDTH,
            height=_EVENT_LOG_ACTION_BUTTON_HEIGHT,
        ):
            self._event_log_popout = not self._event_log_popout
            self._event_log_window_size_initialized = False
        imgui.pop_style_color(color_count)

    def _render_event_log_records(self) -> None:
        imgui = _require_imgui()
        for record in self.event_log.records:
            if record.severity not in self.event_log.active_filters:
                continue
            imgui.text_colored(record.timestamp, *theme.INK_3)
            imgui.same_line()
            imgui.text_colored(record.source, *_event_severity_color(record.severity))
            imgui.same_line()
            suffix = f" [{record.event_id}]" if record.event_id else ""
            imgui.text_unformatted(f"{record.message}{suffix}")

    def _render_event_log_widget(
        self,
        *,
        action_icon: str,
        action_id: str,
    ) -> None:
        imgui = _require_imgui()
        imgui.text_unformatted(self.event_log.title)
        flags = imgui.TABLE_SIZING_STRETCH_SAME | imgui.TABLE_NO_SAVED_SETTINGS
        with imgui.begin_table(f"{action_id}_controls", 2, flags) as table:
            if table.opened:
                imgui.table_setup_column("Filters", imgui.TABLE_COLUMN_WIDTH_STRETCH)
                imgui.table_setup_column(
                    "Action",
                    imgui.TABLE_COLUMN_WIDTH_FIXED,
                    _EVENT_LOG_ACTION_BUTTON_WIDTH,
                )
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                for index, severity in enumerate(("info", "ok", "warn", "crit")):
                    count = sum(
                        1 for record in self.event_log.records if record.severity == severity
                    )
                    self._render_event_log_filter_button(severity, count)
                    if index < 3:
                        imgui.same_line()
                imgui.table_set_column_index(1)
                self._render_event_log_action_button(
                    action_icon=action_icon,
                    action_id=action_id,
                )
        imgui.separator()
        self._render_event_log_records()

    def _render_event_log_inline(self) -> None:
        imgui = _require_imgui()
        if hasattr(imgui, "begin_child"):
            imgui.begin_child(
                "ucirpl_event_log_inline",
                0.0,
                _EVENT_LOG_INLINE_HEIGHT,
                border=True,
            )
            self._render_event_log_widget(action_icon="[]", action_id="event_log_popout")
            imgui.end_child()
        else:
            self._render_event_log_widget(action_icon="[]", action_id="event_log_popout")

    def _render_event_log_window(self) -> None:
        imgui = _require_imgui()
        if not self._event_log_popout:
            return
        display_size = getattr(imgui.get_io(), "display_size", (0.0, 0.0))
        width = float(display_size[0]) if len(display_size) > 0 else _EVENT_LOG_WINDOW_WIDTH
        height = float(display_size[1]) if len(display_size) > 1 else _EVENT_LOG_WINDOW_HEIGHT
        if hasattr(imgui, "set_next_window_position"):
            imgui.set_next_window_position(0.0, 0.0)
        if hasattr(imgui, "set_next_window_size"):
            imgui.set_next_window_size(width, height)
        window_flags = 0
        for flag_name in (
            "WINDOW_NO_RESIZE",
            "WINDOW_NO_MOVE",
            "WINDOW_NO_COLLAPSE",
            "WINDOW_NO_SAVED_SETTINGS",
        ):
            window_flags |= int(getattr(imgui, flag_name, 0))
        imgui.begin("UCIRPL Event Log", flags=window_flags)
        if hasattr(imgui, "begin_child"):
            imgui.begin_child("ucirpl_event_log_popout", 0.0, 0.0, border=True)
            self._render_event_log_widget(action_icon="_", action_id="event_log_minimize")
            imgui.end_child()
        else:
            self._render_event_log_widget(action_icon="_", action_id="event_log_minimize")
        imgui.end()

    def column0(self) -> None:
        imgui = _require_imgui()
        imgui.text_colored("DEVICE INTERFACES", *theme.INK_3)
        imgui.separator()
        self.gse_connection_guage.render()
        self.ecu_connection_guage.render()
        self.extr_ecu_connection_guage.render()
        self.load_cell_connection_guage.render()
        self.backend_throughput_guage.render()
        imgui.spacing()
        imgui.text_colored("GSE CONTROLS", *theme.INK_3)
        imgui.separator()
        self._render_button_pairs(
            table_id="gse_controls_table",
            button_pairs=self._gse_control_pairs,
        )
        imgui.spacing()
        imgui.text_colored("ECU CONTROLS", *theme.INK_3)
        imgui.separator()
        self._render_button_pairs(
            table_id="ecu_controls_table",
            button_pairs=self._ecu_control_pairs,
        )
        imgui.spacing()
        self._render_event_log_inline()


    def column1(self) -> None:
        imgui = _require_imgui()
        imgui.text_colored("PRIMARY GRAPHS", *theme.INK_3)
        imgui.separator() 
        self.UPPER_FEED_SYSTEM_GRAPH.render()
        self.LOWER_FEED_SYSTEM_GRAPH.render()
        self.LOAD_CELL_GRAPH.render()

    def column2(self) -> None:
        imgui = _require_imgui()
        imgui.text_colored("TANK GAUGES", *theme.INK_3)
        imgui.separator()
        flags = imgui.TABLE_SIZING_STRETCH_SAME | imgui.TABLE_NO_SAVED_SETTINGS
        with imgui.begin_table("tank_gauges_table", 2, flags) as table:
            if table.opened:
                imgui.table_setup_column("Left", imgui.TABLE_COLUMN_WIDTH_STRETCH)
                imgui.table_setup_column("Right", imgui.TABLE_COLUMN_WIDTH_STRETCH)
                gauge_pairs = (
                    (self.copv_pt, self.lox_pt),
                    (self.lng_pt, self.vent_pt),
                    (self.lox_mvas_pt, self.lox_inj_tee_pt),
                    (self.inj_lox_pt, self.inj_lng_pt),
                )
                for left_gauge, right_gauge in gauge_pairs:
                    imgui.table_next_row()
                    imgui.table_set_column_index(0)
                    left_gauge.render()
                    imgui.table_set_column_index(1)
                    right_gauge.render()
        imgui.spacing()
        imgui.text_colored("CONTROLS", *theme.INK_3)
        imgui.separator()
        self._render_control_button(self._toggle_igniter)
        self._render_control_button(self._toggle_arm)
        self._render_control_button(self._toggle_abort)
        if self._toggle_abort.state:
            self._clear_controls_for_abort()
    

    def render(self) -> None:
        imgui = _require_imgui()
        flags = (
            imgui.TABLE_BORDERS
            | imgui.TABLE_ROW_BACKGROUND
            | imgui.TABLE_NO_SAVED_SETTINGS
            | imgui.TABLE_SIZING_STRETCH_PROP
        )
        with imgui.begin_table("ucirpl_dashboard_table", 3, flags) as table:
            if table.opened:
                imgui.table_setup_column(
                    "Status",
                    imgui.TABLE_COLUMN_WIDTH_FIXED,
                    _STATUS_COLUMN_WIDTH,
                )
                imgui.table_setup_column(
                    "Primary",
                    imgui.TABLE_COLUMN_WIDTH_STRETCH,
                )
                imgui.table_setup_column(
                    "GUAGES",
                    imgui.TABLE_COLUMN_WIDTH_FIXED,
                    _GAUGES_COLUMN_WIDTH,
                )
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                self.column0()
                imgui.table_set_column_index(1)
                self.column1()
                imgui.table_set_column_index(2)
                self.column2()
        self._render_event_log_window()

def build_dashboard(*, command_writer: Any, link_store: DeviceLinkStore) -> UCIRPLDashboard:
    return UCIRPLDashboard(command_writer=command_writer, link_store=link_store)
