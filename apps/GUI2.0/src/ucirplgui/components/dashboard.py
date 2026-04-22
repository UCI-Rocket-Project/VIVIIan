from __future__ import annotations

import threading
from typing import Any, Mapping

from viviian.gui_utils import AnalogNeedleGauge, ConsoleComponent, GraphSeries, LedBarGauge, MomentaryButton, SensorGraph, ToggleButton, theme
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




        self.UPPER_FEED_SYSTEM_GRAPH = SensorGraph(
            graph_id="UPPER_FEED_SYSTEM_GRAPH",
            title="UPPER FEED SYSTEM",
            series=(
                GraphSeries("copv", "COPV", "tank_copv", color_rgba=theme.ACID),
                GraphSeries("lox", "LOX", "tank_lox", color_rgba=theme.WARN, overlay=True),
                GraphSeries("lng", "LNG", "tank_lng", color_rgba=theme.INK, overlay=True),
                GraphSeries("vent", "VENT", "line_vent", color_rgba=(0.95, 0.70, 0.20, 1.0)),
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
                GraphSeries("lox_mvas", "LOX_MVAS", "line_lox_mvas", color_rgba=(0.45, 0.90, 0.75, 1.0), overlay=True),
                GraphSeries("injtee", "LOX_INJ_TEE", "line_lox_inj_tee", color_rgba=(0.38, 0.72, 1.0, 1.0), overlay=True),
                GraphSeries("injlox", "INJ_LOX", "line_inj_lox", color_rgba=(0.90, 0.35, 0.85, 1.0), overlay=True),
                GraphSeries("injlng", "INJ_LNG", "line_inj_lng", color_rgba=(0.70, 0.95, 0.35, 1.0), overlay=True),
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
            series=(GraphSeries("load", "Force", "load_force", color_rgba=theme.ALERT),),
            window_seconds=90.0,
            max_points_per_series=1024,
            show_series_controls=False,
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

    def _toggle_value(self, button: ToggleButton) -> float:
        return 1.0 if bool(button.state) else 0.0

    def _momentary_value(self, button: MomentaryButton) -> float:
        return 1.0 if button._is_on() else 0.0  # noqa: SLF001 - same state contract as old dashboard

    def _render_control_button(self, button: ToggleButton | MomentaryButton) -> None:
        button.render()

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
        if self.command_writer is None:
            return
        writer = getattr(self.command_writer, "write", None)
        if callable(writer):
            writer(self._control_vector())

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

def build_dashboard(*, command_writer: Any, link_store: DeviceLinkStore) -> UCIRPLDashboard:
    return UCIRPLDashboard(command_writer=command_writer, link_store=link_store)
