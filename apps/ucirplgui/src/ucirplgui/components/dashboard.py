from __future__ import annotations

import threading
import time
from typing import Any, Mapping

import numpy as np
from viviian.gui_utils import (
    AnalogNeedleGauge,
    ConsoleComponent,
    EventLogPanel,
    EventRecord,
    GraphSeries,
    KeyValuePanel,
    KeyValueRow,
    MomentaryButton,
    OperatorToolbar,
    ProcedureCarousel,
    ProcedureStep,
    ReadoutCard,
    SensorGraph,
    Subbar,
    TelemetryCard,
    TelemetryFilmstrip,
    TelemetryTicker,
    ToggleButton,
    ToolbarButton,
    ToolbarMeter,
    ToolbarSearch,
    theme,
)
from viviian.gui_utils._streaming import fan_out_reader_groups

from ucirplgui.device_link_read import DeviceLinkBoardSnapshot, format_age_s, staleness_severity


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
    """Thread-safe store updated from frontend feed_loop (JSON snapshots from device interfaces)."""

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


# Rocket2 ``CombustionChamber`` PT is not in UCIRPL GSE schema; GN2 bus pressure is used as a stand-in proxy.
_CHAMBER_PROXY_FOOTNOTE = "GN2 proxy (not chamber PT)"


class UCIRPLDashboard(ConsoleComponent):
    component_id = "ucirpl_dashboard"

    def __init__(
        self,
        *,
        command_writer: Any,
        link_store: DeviceLinkStore,
    ) -> None:
        self._command_writer = command_writer
        self._link_store = link_store
        self._last_severity: dict[str, str] = {}

        self.toolbar = OperatorToolbar(
            component_id="ucirpl_toolbar",
            file_buttons=[
                ToolbarButton("New Run", icon="◩"),
                ToolbarButton("Load CFG", icon="⤓"),
                ToolbarButton("Record", icon="●", variant="primary", active=True),
            ],
            ops_buttons=[
                ToolbarButton("Play", icon="▶"),
                ToolbarButton("Hold", icon="⏸"),
                ToolbarButton("Arm", icon="◆", variant="warn"),
                ToolbarButton("Abort", icon="■", variant="crit"),
            ],
            search=ToolbarSearch(query="find stream tank_copv"),
            meter=ToolbarMeter(label="Link", value=0.0, right_label="DEVICE JSON"),
        )
        self.subbar = Subbar(
            component_id="ucirpl_subbar",
            tabs=["DASHBOARD", "TELEMETRY", "COMMAND", "LOG"],
            breadcrumbs=["UCIRPL", "PAD", "GSE", "ECU"],
            status_text="RUNNING",
            status_severity="ok",
        )
        self.readout_clock = ReadoutCard(
            component_id="ucirpl_clock",
            title="Wall Clock",
            value="--",
            subtitle="LOCAL TIME · UCIRPL",
            footer_left="GUI",
            footer_right="LIVE",
            severity="ok",
            width=200.0,
        )
        self.readout_links = ReadoutCard(
            component_id="ucirpl_links",
            title="Raw Streams",
            value="--",
            subtitle="TANK / LINE / FFT",
            footer_left="BACKEND",
            footer_right="ARROW",
            severity="info",
            width=200.0,
        )
        self.kv_panel = KeyValuePanel(
            component_id="ucirpl_kv",
            title="Key Channels",
            rows=[],
            width=300.0,
        )

        self.tank_graph = SensorGraph(
            "tank_pressures_graph",
            title="Tank Pressures",
            series=(
                GraphSeries("copv", "COPV", "tank_copv", color_rgba=theme.ACID),
                GraphSeries("lox", "LOX", "tank_lox", color_rgba=theme.WARN, overlay=True),
                GraphSeries("lng", "LNG", "tank_lng", color_rgba=theme.INK, overlay=True),
            ),
            window_seconds=90.0,
            max_points_per_series=1536,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=200.0,
        )
        self.line_graph = SensorGraph(
            "line_pressures_graph",
            title="Line Pressures",
            series=(
                GraphSeries("vent", "VENT", "line_vent", color_rgba=(0.95, 0.70, 0.20, 1.0)),
                GraphSeries("mvas", "LOX_MVAS", "line_lox_mvas", color_rgba=(0.45, 0.90, 0.75, 1.0), overlay=True),
                GraphSeries("injtee", "LOX_INJ_TEE", "line_lox_inj_tee", color_rgba=(0.38, 0.72, 1.0, 1.0), overlay=True),
                GraphSeries("injlox", "INJ_LOX", "line_inj_lox", color_rgba=(0.90, 0.35, 0.85, 1.0), overlay=True),
                GraphSeries("injlng", "INJ_LNG", "line_inj_lng", color_rgba=(0.70, 0.95, 0.35, 1.0), overlay=True),
            ),
            window_seconds=90.0,
            max_points_per_series=1536,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=200.0,
        )
        self.load_graph = SensorGraph(
            "loadcell_graph",
            title="Load Cell",
            series=(GraphSeries("load", "Force", "load_force", color_rgba=theme.ALERT),),
            window_seconds=90.0,
            max_points_per_series=1536,
            show_series_controls=False,
            theme_name="tau_ceti",
            plot_height=140.0,
        )
        self.fft_graph = SensorGraph(
            "fft_graph",
            title="COPV FFT Magnitude",
            series=(GraphSeries("fft", "FFT", "fft_mag", color_rgba=(0.25, 0.95, 0.95, 1.0)),),
            window_seconds=120.0,
            max_points_per_series=1024,
            show_series_controls=False,
            theme_name="tau_ceti",
            plot_height=110.0,
        )

        # GSE-style row (Rocket2 Gse.jsx ranges; engine temps shown in °C from simulator).
        self.eng_tc_1 = AnalogNeedleGauge(
            "eng_tc_1",
            label="Engine TC 1",
            stream_name="eng_tc_1",
            low_value=0.0,
            high_value=120.0,
            width=252.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="°C",
            display_precision=0,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.eng_tc_2 = AnalogNeedleGauge(
            "eng_tc_2",
            label="Engine TC 2",
            stream_name="eng_tc_2",
            low_value=0.0,
            high_value=120.0,
            width=252.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="°C",
            display_precision=0,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.chamber_proxy_gn2 = AnalogNeedleGauge(
            "chamber_proxy_gn2",
            label="Chamber PT (proxy)",
            stream_name="gn2_chamber_proxy",
            low_value=0.0,
            high_value=350.0,
            width=252.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            secondary_label="NOMINAL",
            secondary_value="180 PSI",
            footer_right=_CHAMBER_PROXY_FOOTNOTE,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )

        # ECU-style row (Rocket2 Ecu.jsx).
        self.copv_tc = AnalogNeedleGauge(
            "copv_tc",
            label="COPV TC",
            stream_name="copv_tc",
            low_value=0.0,
            high_value=40.0,
            width=236.0,
            height=212.0,
            theme_name="tau_ceti",
            unit_label="°C",
            display_precision=0,
            layout_style="radial",
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.copv_pt = AnalogNeedleGauge(
            "copv_pt",
            label="COPV PT",
            stream_name="tank_copv",
            low_value=0.0,
            high_value=5000.0,
            width=236.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.lox_pt = AnalogNeedleGauge(
            "lox_pt",
            label="LOX PT",
            stream_name="tank_lox",
            low_value=0.0,
            high_value=700.0,
            width=236.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.lng_pt = AnalogNeedleGauge(
            "lng_pt",
            label="LNG PT",
            stream_name="tank_lng",
            low_value=0.0,
            high_value=700.0,
            width=236.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            low_color_rgba=(0.357, 0.882, 0.173, 1.0),
            high_color_rgba=(0.918, 0.259, 0.157, 1.0),
        )
        self.inj_lox_pt = AnalogNeedleGauge(
            "inj_lox_pt",
            label="LOX INJ PT",
            stream_name="line_inj_lox",
            low_value=0.0,
            high_value=500.0,
            width=236.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            low_color_rgba=(0.918, 0.259, 0.157, 1.0),
            high_color_rgba=(0.357, 0.882, 0.173, 1.0),
        )
        self.inj_lng_pt = AnalogNeedleGauge(
            "inj_lng_pt",
            label="LNG INJ PT",
            stream_name="line_inj_lng",
            low_value=0.0,
            high_value=500.0,
            width=236.0,
            height=188.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            low_color_rgba=(0.918, 0.259, 0.157, 1.0),
            high_color_rgba=(0.357, 0.882, 0.173, 1.0),
        )
        self.force_gauge = AnalogNeedleGauge(
            "force_gauge",
            label="Load Cell Force",
            stream_name="load_force",
            low_value=0.0,
            high_value=20000.0,
            unit_label="lbf",
            theme_name="tau_ceti",
            width=260.0,
            height=190.0,
        )

        self.event_log = EventLogPanel(
            component_id="ucirpl_events",
            records=[
                EventRecord("00:00.000", "info", "UCIRPL", "Operator desk online", "E·BOOT"),
            ],
        )
        self.carousel = ProcedureCarousel(
            component_id="ucirpl_carousel",
            steps=[
                ProcedureStep("Pad idle", "Verify GN2 / vent paths", "done"),
                ProcedureStep("Fill", "Pressurize COPV + prop lines", "active"),
                ProcedureStep("Chill", "LOX/LNG conditioning", "pending"),
                ProcedureStep("Go", "Arm igniters + final checks", "pending"),
            ],
            active_index=1,
        )
        self.filmstrip = TelemetryFilmstrip(
            component_id="ucirpl_filmstrip",
            cards=[],
            cards_per_view=3,
            auto_scroll=True,
            scroll_period_s=2.2,
        )
        self.ticker = TelemetryTicker(
            component_id="ucirpl_ticker",
            items=[],
            visible_items=4,
            auto_scroll=True,
            scroll_period_s=1.4,
        )

        self._toggle_igniter_0 = ToggleButton(
            "igniter_0",
            "Igniter 1",
            "cmd.gse.igniter_0",
            False,
            theme_name="tau_ceti",
        )
        self._toggle_igniter_1 = ToggleButton(
            "igniter_1",
            "Igniter 2",
            "cmd.gse.igniter_1",
            False,
            theme_name="tau_ceti",
        )
        self._toggle_alarm = ToggleButton("alarm", "Alarm", "cmd.gse.alarm", False, theme_name="tau_ceti")
        self._toggle_gn2_fill = ToggleButton(
            "gn2_fill", "GN2 Fill", "cmd.gse.gn2_fill", False, theme_name="tau_ceti"
        )
        self._toggle_gn2_vent = ToggleButton(
            "gn2_vent", "GN2 Vent", "cmd.gse.gn2_vent", False, theme_name="tau_ceti"
        )
        self._toggle_gn2_disconnect = ToggleButton(
            "gn2_disconnect", "GN2 Disconnect", "cmd.gse.gn2_disconnect", False, theme_name="tau_ceti"
        )
        self._toggle_mvas_fill = ToggleButton(
            "mvas_fill", "MVAS Fill", "cmd.gse.mvas_fill", False, theme_name="tau_ceti"
        )
        self._toggle_mvas_vent = ToggleButton(
            "mvas_vent", "MVAS Vent", "cmd.gse.mvas_vent", False, theme_name="tau_ceti"
        )
        self._momentary_mvas_open = MomentaryButton(
            "mvas_open", "MVAS Open", "cmd.gse.mvas_open", 1.0, theme_name="tau_ceti"
        )
        self._momentary_mvas_close = MomentaryButton(
            "mvas_close", "MVAS Close", "cmd.gse.mvas_close", 1.0, theme_name="tau_ceti"
        )
        self._toggle_lox_vent = ToggleButton(
            "lox_vent", "LOX Vent", "cmd.gse.lox_vent", False, theme_name="tau_ceti"
        )
        self._toggle_lng_vent = ToggleButton(
            "lng_vent", "LNG Vent", "cmd.gse.lng_vent", False, theme_name="tau_ceti"
        )
        self._toggle_copv_vent = ToggleButton(
            "copv_vent", "COPV Vent", "cmd.ecu.copv_vent", False, theme_name="tau_ceti"
        )
        self._toggle_pv1 = ToggleButton("pv1", "PV1", "cmd.ecu.pv1", False, theme_name="tau_ceti")
        self._toggle_pv2 = ToggleButton("pv2", "PV2", "cmd.ecu.pv2", False, theme_name="tau_ceti")
        self._toggle_vent = ToggleButton("vent", "Vent", "cmd.ecu.vent", False, theme_name="tau_ceti")

        self._telemetry_widgets = (
            self.tank_graph,
            self.line_graph,
            self.load_graph,
            self.fft_graph,
            self.eng_tc_1,
            self.eng_tc_2,
            self.chamber_proxy_gn2,
            self.copv_tc,
            self.copv_pt,
            self.lox_pt,
            self.lng_pt,
            self.inj_lox_pt,
            self.inj_lng_pt,
            self.force_gauge,
        )
        self._reader_groups_spec = (
            ("tank_copv", "tank_lox", "tank_lng"),
            ("line_vent", "line_lox_mvas", "line_lox_inj_tee", "line_inj_lox", "line_inj_lng"),
            ("load_force",),
            ("fft_mag",),
            ("eng_tc_1",),
            ("eng_tc_2",),
            ("gn2_chamber_proxy",),
            ("copv_tc",),
            ("tank_copv",),
            ("tank_lox",),
            ("tank_lng",),
            ("line_inj_lox",),
            ("line_inj_lng",),
            ("load_force",),
        )

    def required_streams(self) -> tuple[str, ...]:
        return (
            "tank_copv",
            "tank_lox",
            "tank_lng",
            "line_vent",
            "line_lox_mvas",
            "line_lox_inj_tee",
            "line_inj_lox",
            "line_inj_lng",
            "load_force",
            "fft_mag",
            "eng_tc_1",
            "eng_tc_2",
            "gn2_chamber_proxy",
            "copv_tc",
        )

    def bind(self, readers: Mapping[str, Any]) -> None:
        reader_groups = fan_out_reader_groups(readers, self._reader_groups_spec)
        for widget, group in zip(self._telemetry_widgets, reader_groups):
            widget.bind(group)

    def _sync_views(self) -> None:
        now = time.time()
        self.readout_clock.value = time.strftime("%H:%M:%S", time.localtime(now))
        self.readout_links.value = f"COPV {self.copv_pt.display_value:.0f} PSI"
        self.readout_links.subtitle = f"LOX {self.lox_pt.display_value:.0f} · LNG {self.lng_pt.display_value:.0f}"

        self.kv_panel.rows = [
            KeyValueRow("COPV", f"{self.copv_pt.display_value:.0f} PSI", self.copv_pt.resolved_status_severity),
            KeyValueRow("INJ_LOX", f"{self.inj_lox_pt.display_value:.0f} PSI", self.inj_lox_pt.resolved_status_severity),
            KeyValueRow("INJ_LNG", f"{self.inj_lng_pt.display_value:.0f} PSI", self.inj_lng_pt.resolved_status_severity),
            KeyValueRow("LOAD", f"{self.force_gauge.display_value:.0f} lbf", self.force_gauge.resolved_status_severity),
        ]
        self.filmstrip.cards = [
            TelemetryCard("COPV", f"{self.copv_pt.display_value:.0f}", " PSI", self.copv_pt.formatted_rate().replace("Δ ", ""), self.copv_pt.resolved_status_severity),
            TelemetryCard("LOX", f"{self.lox_pt.display_value:.0f}", " PSI", self.lox_pt.formatted_rate().replace("Δ ", ""), self.lox_pt.resolved_status_severity),
            TelemetryCard("ENG1", f"{self.eng_tc_1.display_value:.0f}", " °C", self.eng_tc_1.formatted_rate().replace("Δ ", ""), self.eng_tc_1.resolved_status_severity),
            TelemetryCard("ENG2", f"{self.eng_tc_2.display_value:.0f}", " °C", self.eng_tc_2.formatted_rate().replace("Δ ", ""), self.eng_tc_2.resolved_status_severity),
        ]
        self.ticker.items = [
            f"COPV {self.copv_pt.display_value:.0f} PSI",
            f"LOX {self.lox_pt.display_value:.0f} PSI",
            f"LNG {self.lng_pt.display_value:.0f} PSI",
            f"INJ_LOX {self.inj_lox_pt.display_value:.0f} PSI",
            f"LOAD {self.force_gauge.display_value:.0f} lbf",
        ]
        self.toolbar.meter.right_label = f"{self.copv_pt.display_value:.0f} PSI COPV"

        self._maybe_append_event("COPV", self.copv_pt.resolved_status_severity, f"COPV {self.copv_pt.display_value:.0f} PSI")
        self.event_log.records = self.event_log.records[:14]

    def _maybe_append_event(self, source: str, severity: str, message: str) -> None:
        previous = self._last_severity.get(source)
        self._last_severity[source] = severity
        if previous == severity or severity == "ok":
            return
        stamp = time.strftime("%H:%M:%S", time.localtime())
        self.event_log.records.insert(
            0,
            EventRecord(stamp, severity, source, message, f"E·{source}"),
        )

    def consume(self) -> bool:
        dirty = False
        for widget in self._telemetry_widgets:
            dirty = widget.consume() or dirty
        dirty = self.filmstrip.consume() or dirty
        dirty = self.ticker.consume() or dirty
        self.send_commands_if_needed()
        self._sync_views()
        return dirty

    def _control_vector(self) -> np.ndarray:
        gate = {"safe_key": True, "go_flight": True, "flight_mode": True}

        def _f(tb: ToggleButton) -> float:
            return 1.0 if bool(tb.state) else 0.0

        def _m(mb: MomentaryButton) -> float:
            return 1.0 if mb._is_on() else 0.0  # noqa: SLF001 — mirror adapter semantics

        return np.array(
            [
                _f(self._toggle_igniter_0),
                _f(self._toggle_igniter_1),
                _f(self._toggle_alarm),
                _f(self._toggle_gn2_fill),
                _f(self._toggle_gn2_vent),
                _f(self._toggle_gn2_disconnect),
                _f(self._toggle_mvas_fill),
                _f(self._toggle_mvas_vent),
                _m(self._momentary_mvas_open),
                _m(self._momentary_mvas_close),
                _f(self._toggle_lox_vent),
                _f(self._toggle_lng_vent),
                _f(self._toggle_copv_vent),
                _f(self._toggle_pv1),
                _f(self._toggle_pv2),
                _f(self._toggle_vent),
            ],
            dtype=np.float64,
        )

    def send_commands_if_needed(self) -> None:
        self._command_writer.write(self._control_vector())

    def _render_device_strip(self) -> None:
        imgui = _require_imgui()
        boards = self._link_store.snapshot()
        now = time.time()
        imgui.text_colored("DEVICE INTERFACES", *theme.INK_3)
        imgui.separator()
        for board in ("gse", "ecu", "extr_ecu", "loadcell"):
            snap = boards.get(board)
            if snap is None:
                line = f"{board.upper():8}  NO SNAPSHOT"
                imgui.text_colored(line, *theme.INK_2)
                continue
            sev = staleness_severity(now_s=now, snap=snap)
            color = theme.INK_2 if sev == "info" else theme.ACID if sev == "ok" else theme.ALERT if sev == "warn" else theme.CRIT
            rx_age = format_age_s(now, snap.last_rx_epoch_s)
            conn_age = format_age_s(now, snap.last_connect_epoch_s) if snap.last_connect_epoch_s else "—"
            state = "UP" if snap.connected else "DOWN"
            host = snap.endpoint_host
            port = snap.endpoint_port
            imgui.text_colored(f"{board.upper():8}  {state:4}  RX {rx_age:>8}  CONN {conn_age:>8}  {host}:{port}", *color)
        imgui.spacing()

    def render(self) -> None:
        imgui = _require_imgui()
        gate_states = {"safe_key": True, "go_flight": True, "flight_mode": True}

        self.toolbar.render()
        imgui.spacing()
        self.subbar.render()
        imgui.spacing()

        self.readout_clock.render()
        imgui.same_line()
        self.readout_links.render()
        imgui.same_line()
        self.kv_panel.render()
        imgui.spacing()

        self._render_device_strip()

        imgui.begin_child("ucirpl_scroll_region", height=-300.0, border=False)
        imgui.columns(3, "gauge_graph_layout", border=False)

        # Left column: GSE gauges.
        imgui.text_colored("GSE", *theme.ACID)
        self.eng_tc_1.render()
        imgui.spacing()
        self.eng_tc_2.render()
        imgui.spacing()
        self.chamber_proxy_gn2.render()
        imgui.spacing()
        self.force_gauge.render()
        imgui.next_column()

        # Middle column: primary graphs.
        self.tank_graph.render()
        imgui.spacing()
        self.line_graph.render()
        imgui.spacing()
        self.load_graph.render()
        imgui.spacing()
        self.fft_graph.render()
        imgui.next_column()

        # Right column: ECU gauges.
        imgui.text_colored("ECU", *theme.ACID)
        self.copv_tc.render()
        imgui.spacing()
        self.copv_pt.render()
        imgui.spacing()
        self.lox_pt.render()
        imgui.spacing()
        self.lng_pt.render()
        imgui.spacing()
        self.inj_lox_pt.render()
        imgui.spacing()
        self.inj_lng_pt.render()

        imgui.columns(1)
        imgui.spacing()

        self.event_log.render()
        imgui.spacing()
        self.carousel.render()
        imgui.spacing()
        self.filmstrip.render()
        imgui.spacing()
        self.ticker.render()
        imgui.end_child()

        imgui.separator()
        imgui.text_colored("GSE CONTROLS", *theme.INK_3)
        imgui.columns(2, "gse_cols", border=True)
        self._toggle_igniter_0.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_igniter_1.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.next_column()
        self._toggle_alarm.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_gn2_fill.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.columns(1)
        imgui.columns(2, "gse_cols2", border=True)
        self._toggle_gn2_vent.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_gn2_disconnect.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.next_column()
        self._toggle_mvas_fill.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_mvas_vent.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.columns(1)
        imgui.columns(2, "gse_cols3", border=True)
        self._momentary_mvas_open.render(gate_states=gate_states, interlock_states=gate_states)
        self._momentary_mvas_close.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.next_column()
        self._toggle_lox_vent.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_lng_vent.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.columns(1)
        imgui.spacing()

        imgui.text_colored("ECU CONTROLS", *theme.INK_3)
        imgui.columns(2, "ecu_cols", border=True)
        self._toggle_copv_vent.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_vent.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.next_column()
        self._toggle_pv1.render(gate_states=gate_states, interlock_states=gate_states)
        self._toggle_pv2.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.columns(1)


def build_dashboard(*, command_writer: Any, link_store: DeviceLinkStore) -> UCIRPLDashboard:
    return UCIRPLDashboard(command_writer=command_writer, link_store=link_store)
