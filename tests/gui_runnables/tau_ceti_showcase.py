from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Sequence

import numpy as np

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    _PYTHUSA_SRC = _REPO_ROOT / "packages" / "pythusa" / "src"
    for _path in (_REPO_ROOT, _REPO_ROOT / "src", _PYTHUSA_SRC):
        _path_str = str(_path)
        if _path_str not in sys.path:
            sys.path.insert(0, _path_str)

from pythusa import Pipeline
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils._streaming import fan_out_reader_groups
from viviian.gui_utils import (
    AnalogNeedleGauge,
    ConsoleComponent,
    EventLogPanel,
    EventRecord,
    GraphSeries,
    KeyValuePanel,
    KeyValueRow,
    LedBarGauge,
    MicroButton,
    ModelViewerConfig,
    MomentaryButton,
    OperatorToolbar,
    ProcedureCarousel,
    ProcedureStep,
    ReadoutCard,
    SensorGraph,
    SetpointButton,
    Subbar,
    TelemetryCard,
    TelemetryFilmstrip,
    TelemetryTicker,
    ToggleButton,
    ToolbarButton,
    ToolbarMeter,
    ToolbarSearch,
    discover_single_obj_asset,
    resolve_compiled_obj_assets,
    theme,
)
from viviian.simulation_utils import (
    RotationMatrixSignalGenerator,
    SpectralSignalConfig,
    SpectralTerm,
)
from tests.gui_runnables._support import BufferedFrameReader

_WINDOW_TITLE = "Tau-Ceti Showcase"
_WINDOW_SIZE = (1480, 980)
_ROWS_PER_BATCH = 16
_SAMPLE_RATE_HZ = 60.0
_SOURCE_SLEEP_S = 0.05


class TauCetiShowcaseDashboard(ConsoleComponent):
    component_id = "tau_ceti_dashboard"

    def __init__(self) -> None:
        self.graph = SensorGraph(
            "tau_graph",
            title="Primary Telemetry",
            series=(
                GraphSeries(series_id="px",        label="PX_CHAMBER",  stream_name="px_chamber",  color_rgba=theme.ACID,                          overlay=False),
                GraphSeries(series_id="temp",      label="T_ENGINE",    stream_name="t_engine",    color_rgba=theme.ALERT,                         overlay=True),
                GraphSeries(series_id="level",     label="LOX_LEVEL",   stream_name="lox_level",   color_rgba=theme.WARN,                          overlay=True),
                GraphSeries(series_id="vib",       label="V_AXIAL",     stream_name="v_axial",     color_rgba=(0.000, 0.898, 0.761, 1.0),          overlay=True),
                GraphSeries(series_id="cur",       label="I_BUS28",     stream_name="i_bus28",     color_rgba=(0.847, 1.000, 0.000, 1.0),          overlay=True),
                GraphSeries(series_id="bearing",   label="T_BEARING",   stream_name="t_bearing",   color_rgba=theme.INK,                           overlay=True),
                GraphSeries(series_id="loxflow",   label="LOX_FLOW",    stream_name="lox_flow",    color_rgba=(0.200, 0.600, 1.000, 1.0),          overlay=True),
                GraphSeries(series_id="feedp",     label="P_FEEDLINE",  stream_name="p_feedline",  color_rgba=(0.700, 0.250, 1.000, 1.0),          overlay=True),
                GraphSeries(series_id="nozzle",    label="T_NOZZLE",    stream_name="t_nozzle",    color_rgba=(1.000, 0.200, 0.700, 1.0),          overlay=True),
                GraphSeries(series_id="n2press",   label="N2_PRESS",    stream_name="n2_pressure", color_rgba=(0.200, 1.000, 0.550, 1.0),          overlay=True),
            ),
            window_seconds=12.0,
            max_points_per_series=2048,
            theme_name="tau_ceti",
            plot_height=420.0,
        )
        self.pressure_gauge = AnalogNeedleGauge(
            gauge_id="pressure",
            label="Chamber Press · P-01",
            stream_name="px_chamber",
            low_value=0.0,
            high_value=1000.0,
            width=300.0,
            height=196.0,
            theme_name="tau_ceti",
            unit_label="PSI",
            display_precision=0,
            footer_right="P-01",
        )
        self.level_gauge = LedBarGauge(
            gauge_id="level",
            label="LOX Tank Level · L-02",
            stream_name="lox_level",
            low_value=0.0,
            high_value=100.0,
            width=300.0,
            height=124.0,
            theme_name="tau_ceti",
            unit_label="%",
            display_precision=1,
            secondary_label="CAPACITY",
            secondary_value="12,480 L",
        )
        self.temperature_gauge = AnalogNeedleGauge(
            gauge_id="engine_temp",
            label="Engine Temp · T-01",
            stream_name="t_engine",
            low_value=0.0,
            high_value=1200.0,
            width=300.0,
            height=260.0,
            theme_name="tau_ceti",
            layout_style="radial",
            unit_label="°C",
            display_precision=0,
        )
        self.vibration_gauge = AnalogNeedleGauge(
            gauge_id="vibration",
            label="Vibration · V-04",
            stream_name="v_axial",
            low_value=0.0,
            high_value=2.0,
            width=300.0,
            height=196.0,
            theme_name="tau_ceti",
            unit_label="g",
            display_precision=2,
            footer_right="DLM×1",
        )
        self.current_gauge = LedBarGauge(
            gauge_id="bus_current",
            label="Bus Current · I-12",
            stream_name="i_bus28",
            low_value=0.0,
            high_value=28.0,
            width=300.0,
            height=124.0,
            theme_name="tau_ceti",
            unit_label="A",
            display_precision=1,
            secondary_label="LIMIT",
            secondary_value="28.0 A",
            footer_right="HAZARD BLOCK",
        )
        self.overtemp_gauge = LedBarGauge(
            gauge_id="bearing_temp",
            label="Overtemp · T-03",
            stream_name="t_bearing",
            low_value=0.0,
            high_value=100.0,
            width=300.0,
            height=124.0,
            theme_name="tau_ceti",
            unit_label="%",
            display_precision=1,
            secondary_label="REDLINE",
            secondary_value="95.0",
            footer_left="■ CRIT · ACK REQUIRED",
            footer_right="ACK >",
        )
        self._telemetry_widgets = (
            self.graph,
            self.pressure_gauge,
            self.level_gauge,
            self.temperature_gauge,
            self.vibration_gauge,
            self.current_gauge,
            self.overtemp_gauge,
        )

        self.buttons = [
            ToggleButton(
                button_id="ignition",
                label="Ignition Main",
                state_id="ign.main",
                state=False,
                theme_name="tau_ceti",
                variant="primary",
            ),
            ToggleButton(
                button_id="record",
                label="Data Record",
                state_id="rec.enable",
                state=True,
                theme_name="tau_ceti",
                variant="primary",
            ),
            MomentaryButton(
                button_id="purge",
                label="Purge Cycle",
                state_id="purge.cmd",
                state="ARM",
                gate_id="safe_key",
                theme_name="tau_ceti",
                variant="alert",
            ),
            MomentaryButton(
                button_id="abort",
                label="Abort",
                state_id="abort.cmd",
                state="!!",
                interlock_ids=("safe_key", "go_flight", "flight_mode"),
                theme_name="tau_ceti",
                variant="crit",
            ),
        ]
        self.setpoint_buttons = [
            SetpointButton(
                button_id="chamber_sp",
                label="Chamber Pressure Target",
                state_id="sp.chamber",
                state=500.0,
                unit="psi",
                step=50.0,
                min_value=0.0,
                max_value=1000.0,
                theme_name="tau_ceti",
                variant="primary",
            ),
            SetpointButton(
                button_id="thrust_sp",
                label="Thrust Target",
                state_id="sp.thrust",
                state=80.0,
                unit="%",
                step=5.0,
                min_value=0.0,
                max_value=100.0,
                theme_name="tau_ceti",
            ),
            SetpointButton(
                button_id="n2_sp",
                label="N2 Purge Pressure",
                state_id="sp.n2",
                state=290.0,
                unit="psi",
                step=10.0,
                min_value=0.0,
                max_value=500.0,
                theme_name="tau_ceti",
                variant="alert",
                gate_id="safe_key",
            ),
        ]
        self.micro_buttons = [
            MicroButton(component_id="m0", label="", icon="●", active=True),
            MicroButton(component_id="m1", label="", icon="▲"),
            MicroButton(component_id="m2", label="", icon="■"),
        ]
        self.toolbar = OperatorToolbar(
            component_id="toolbar",
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
            search=ToolbarSearch(query="find stream px_chamber"),
            meter=ToolbarMeter(label="Bus Load", value=0.58, right_label="58% · 60HZ"),
        )
        self.subbar = Subbar(
            component_id="subbar",
            tabs=["DASHBOARD", "TELEMETRY", "COMMAND", "LOG"],
            breadcrumbs=["MISSION", "0217·A", "STAGE", "PRE·IGN"],
            status_text="GO-FLIGHT",
            status_severity="ok",
        )
        self.readouts = [
            ReadoutCard(
                component_id="clock",
                title="Mission Clock",
                value="T+00:00:00",
                subtitle="RUN 0217·A · GO-FLIGHT",
                footer_left="PAD 39·B",
                footer_right="GO",
                severity="ok",
                width=240.0,
            ),
            ReadoutCard(
                component_id="thermal_margin",
                title="Thermal Margin",
                value="--",
                subtitle="REDLINE VS BEARING TEMP",
                footer_left="AUTO",
                footer_right="TRACK",
                severity="info",
                width=240.0,
            ),
        ]
        self.kv_panel = KeyValuePanel(
            component_id="streams",
            title="Telemetry Streams",
            rows=[],
            width=280.0,
        )
        self.event_log = EventLogPanel(
            component_id="events",
            records=[
                EventRecord("00:00.000", "info", "UPLINK", "Tau-Ceti showcase initialized", "E·BOOT"),
            ],
        )
        self.carousel = ProcedureCarousel(
            component_id="carousel",
            steps=[
                ProcedureStep("Purge & dry", "N2 cycle · 6 bar · 40s", "done"),
                ProcedureStep("Chill-down", "LOX loop · target -183C", "active"),
                ProcedureStep("Main valve lead", "MPV ramp · 1.2s", "pending"),
                ProcedureStep("Ignition", "Main torch ignition", "pending"),
            ],
            active_index=1,
        )
        self.filmstrip = TelemetryFilmstrip(
            component_id="filmstrip",
            cards=[],
            cards_per_view=3,
            auto_scroll=True,
            scroll_period_s=2.0,
        )
        self.ticker = TelemetryTicker(
            component_id="ticker",
            items=[],
            visible_items=3,
            auto_scroll=True,
            scroll_period_s=1.5,
        )
        try:
            obj_path = discover_single_obj_asset()
            cache_path, manifest_path = resolve_compiled_obj_assets(obj_path=obj_path)
            mv_config = ModelViewerConfig(
                viewer_id="showcase_3d",
                title="Telemetry · 3D Model",
                mesh_cache_path=str(cache_path),
                manifest_path=str(manifest_path),
                pose_stream_name="attitude",
                theme_name="tau_ceti",
                show_legend=False,
                show_axes=True,
            )
            self.model_viewer = mv_config.build_viewer()
            self._attitude_reader: BufferedFrameReader | None = BufferedFrameReader(expected_rows=10, max_rows=32)
            self.model_viewer.bind({"attitude": self._attitude_reader})
            self._orientation_gen: RotationMatrixSignalGenerator | None = _build_showcase_orientation_gen()
        except Exception:
            self.model_viewer = None
            self._attitude_reader = None
            self._orientation_gen = None

        self._last_severity: dict[str, str] = {}
        self._sync_views()

    def required_streams(self) -> tuple[str, ...]:
        return (
            "px_chamber",
            "t_engine",
            "lox_level",
            "v_axial",
            "i_bus28",
            "t_bearing",
            "lox_flow",
            "p_feedline",
            "t_nozzle",
            "n2_pressure",
        )

    def bind(self, readers: dict[str, object]) -> None:
        widget_stream_groups = tuple(
            _telemetry_widget_streams(widget) for widget in self._telemetry_widgets
        )
        reader_groups = fan_out_reader_groups(readers, widget_stream_groups)
        for widget, widget_readers in zip(self._telemetry_widgets, reader_groups):
            widget.bind(widget_readers)

    def consume(self) -> bool:
        changed = False
        for widget in self._telemetry_widgets:
            changed = widget.consume() or changed
        changed = self.filmstrip.consume() or changed
        changed = self.ticker.consume() or changed
        if self.model_viewer is not None:
            assert self._attitude_reader is not None and self._orientation_gen is not None
            self._attitude_reader.prime(self._orientation_gen.next_batch(16))
            self.model_viewer.consume()
        self._sync_views()
        return changed

    def render(self) -> None:
        import imgui

        self.toolbar.render()
        imgui.spacing()
        self.subbar.render()
        imgui.spacing()

        for index, card in enumerate(self.readouts):
            card.render()
            if index < len(self.readouts) - 1:
                imgui.same_line()
        imgui.same_line()
        self.kv_panel.render()
        imgui.spacing()

        self.graph.render()
        imgui.spacing()

        self.pressure_gauge.render()
        imgui.same_line()
        self.level_gauge.render()
        imgui.same_line()
        self.temperature_gauge.render()
        imgui.spacing()

        self.vibration_gauge.render()
        imgui.same_line()
        self.current_gauge.render()
        imgui.same_line()
        self.overtemp_gauge.render()
        imgui.spacing()

        gate_states = {"safe_key": True, "go_flight": True, "flight_mode": True}
        imgui.columns(len(self.buttons), "buttons_row", border=False)
        for index, button in enumerate(self.buttons):
            button.render(gate_states=gate_states, interlock_states=gate_states)
            imgui.next_column()
        imgui.columns(1)
        imgui.spacing()

        for button in self.setpoint_buttons:
            button.render(gate_states=gate_states, interlock_states=gate_states)
        imgui.spacing()

        for index, button in enumerate(self.micro_buttons):
            button.render()
            if index < len(self.micro_buttons) - 1:
                imgui.same_line()
        imgui.spacing()

        self.event_log.render()
        imgui.spacing()
        self.carousel.render()
        imgui.spacing()
        self.filmstrip.render()
        imgui.spacing()
        self.ticker.render()

        if self.model_viewer is not None:
            imgui.spacing()
            self.model_viewer.render()

    def _sync_views(self) -> None:
        mission_time = self.pressure_gauge.latest_timestamp or 0.0
        self.readouts[0].value = _format_mission_clock(mission_time)

        thermal_margin = 95.0 - self.overtemp_gauge.display_value
        self.readouts[1].value = f"{thermal_margin:+.1f}"
        self.readouts[1].subtitle = f"BEARING {self.overtemp_gauge.display_value:.1f}% · REDLINE 95.0"
        self.readouts[1].severity = _severity_from_value(thermal_margin, good_is_positive=True)

        self.kv_panel.rows = [
            KeyValueRow("px_chamber", f"{self.pressure_gauge.display_value:.1f} PSI", self.pressure_gauge.resolved_status_severity),
            KeyValueRow("t_engine", f"{self.temperature_gauge.display_value:.0f} °C", self.temperature_gauge.resolved_status_severity),
            KeyValueRow("lox_level", f"{self.level_gauge.display_value:.1f} %", self.level_gauge.resolved_status_severity),
            KeyValueRow("t_bearing", f"{self.overtemp_gauge.display_value:.1f} %", self.overtemp_gauge.resolved_status_severity),
        ]
        self.filmstrip.cards = [
            TelemetryCard("PX_CHAMBER", f"{self.pressure_gauge.display_value:.0f}", " PSI", self.pressure_gauge.formatted_rate().replace("Δ ", ""), self.pressure_gauge.resolved_status_severity),
            TelemetryCard("T_ENGINE", f"{self.temperature_gauge.display_value:.0f}", " °C", self.temperature_gauge.formatted_rate().replace("Δ ", ""), self.temperature_gauge.resolved_status_severity),
            TelemetryCard("V_AXIAL", f"{self.vibration_gauge.display_value:.2f}", " g", self.vibration_gauge.formatted_rate().replace("Δ ", ""), self.vibration_gauge.resolved_status_severity),
            TelemetryCard("T_BEARING", f"{self.overtemp_gauge.display_value:.1f}", " %", self.overtemp_gauge.formatted_rate().replace("Δ ", ""), self.overtemp_gauge.resolved_status_severity),
        ]
        self.ticker.items = [
            f"PX_CHAMBER {self.pressure_gauge.display_value:.1f} PSI",
            f"T_ENGINE {self.temperature_gauge.display_value:.0f} °C",
            f"LOX {self.level_gauge.display_value:.1f}%",
            f"V_AXIAL {self.vibration_gauge.display_value:.2f} g",
            f"I_BUS28 {self.current_gauge.display_value:.1f} A",
            f"T_BEARING {self.overtemp_gauge.display_value:.1f}%",
        ]
        self._update_events()

    def _update_events(self) -> None:
        self._maybe_append_event("I_BUS28", self.current_gauge.resolved_status_severity, f"Bus current {self.current_gauge.display_value:.1f} A")
        self._maybe_append_event("T_BEARING", self.overtemp_gauge.resolved_status_severity, f"Bearing temperature {self.overtemp_gauge.display_value:.1f}%")
        self._maybe_append_event("T_ENGINE", self.temperature_gauge.resolved_status_severity, f"Engine temperature {self.temperature_gauge.display_value:.0f} °C")
        self.event_log.records = self.event_log.records[:12]

    def _maybe_append_event(self, source: str, severity: str, message: str) -> None:
        previous = self._last_severity.get(source)
        self._last_severity[source] = severity
        if previous == severity or severity == "ok":
            return
        timestamp = _format_event_timestamp(self.pressure_gauge.latest_timestamp or 0.0)
        self.event_log.records.insert(
            0,
            EventRecord(timestamp, severity, source, message, f"E·{source}"),
        )


def build_showcase_dashboard() -> TauCetiShowcaseDashboard:
    return TauCetiShowcaseDashboard()


def build_showcase_frontend() -> Frontend:
    frontend = Frontend("tau_ceti_showcase")
    frontend.add(build_showcase_dashboard())
    return frontend


def build_showcase_pipeline() -> Pipeline:
    frontend = build_showcase_frontend()
    frontend_task = frontend.build_task(
        backend=GlfwBackend(
            width=_WINDOW_SIZE[0],
            height=_WINDOW_SIZE[1],
            theme_name="tau_ceti",
        ),
        window_title=_WINDOW_TITLE,
        fill_backend_window=True,
    )
    pipe = Pipeline("tau-ceti-showcase")
    for stream_name in frontend.required_reads:
        pipe.add_stream(
            stream_name,
            shape=(2, _ROWS_PER_BATCH),
            dtype=np.float64,
            cache_align=False,
        )
    pipe.add_task(
        "source",
        fn=_tau_ceti_source,
        writes={stream_name: stream_name for stream_name in frontend.required_reads},
    )
    pipe.add_task(
        "frontend",
        fn=frontend_task,
        reads=frontend.read_bindings(),
    )
    return pipe


def run() -> None:
    pipe = build_showcase_pipeline()
    try:
        pipe.start()
        while True:
            frontend_proc = pipe._manager._processes.get("frontend")
            if frontend_proc is None or not frontend_proc.is_alive():
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        return
    finally:
        pipe.close()


def _tau_ceti_source(
    px_chamber,
    t_engine,
    lox_level,
    v_axial,
    i_bus28,
    t_bearing,
    lox_flow,
    p_feedline,
    t_nozzle,
    n2_pressure,
) -> None:
    sample_index = 0
    while True:
        timestamps = (
            np.arange(sample_index, sample_index + _ROWS_PER_BATCH, dtype=np.float64)
            / _SAMPLE_RATE_HZ
        )
        signal_values = _clamp_series(
            420.0 + (180.0 * np.sin(timestamps * 1.8)) + (24.0 * np.sin(timestamps * 7.5)),
            0.0,
            1000.0,
        )
        engine_values = _clamp_series(
            760.0 + (160.0 * np.sin((timestamps * 0.7) + 1.2)),
            0.0,
            1200.0,
        )
        level_values = _clamp_series(
            76.0 + (12.0 * np.sin(timestamps * 0.4)),
            0.0,
            100.0,
        )
        vibration_values = _clamp_series(
            0.32 + (0.16 * np.sin(timestamps * 3.1)) + (0.04 * np.sin(timestamps * 13.0)),
            0.0,
            2.0,
        )
        current_values = _clamp_series(
            16.0 + (6.0 * np.sin((timestamps * 0.55) + 0.4)) + (1.4 * np.sin(timestamps * 6.0)),
            0.0,
            28.0,
        )
        bearing_values = _clamp_series(
            88.0 + (7.0 * np.sin((timestamps * 0.63) + 0.9)) + (3.0 * np.sin(timestamps * 4.5)),
            0.0,
            100.0,
        )
        lox_flow_values = _clamp_series(
            500.0 + (200.0 * np.sin((timestamps * 1.4) + 0.8)) + (18.0 * np.sin(timestamps * 6.2)),
            0.0,
            1000.0,
        )
        feedline_values = _clamp_series(
            380.0 + (160.0 * np.sin((timestamps * 0.95) + 2.5)) + (22.0 * np.sin(timestamps * 4.8)),
            0.0,
            1000.0,
        )
        nozzle_values = _clamp_series(
            650.0 + (200.0 * np.sin((timestamps * 0.5) + 1.8)) + (30.0 * np.sin(timestamps * 3.3)),
            0.0,
            1000.0,
        )
        n2_values = _clamp_series(
            290.0 + (130.0 * np.sin((timestamps * 0.65) + 1.3)) + (15.0 * np.sin(timestamps * 5.5)),
            0.0,
            1000.0,
        )
        batches = {
            px_chamber: signal_values,
            t_engine: engine_values,
            lox_level: level_values,
            v_axial: vibration_values,
            i_bus28: current_values,
            t_bearing: bearing_values,
            lox_flow: lox_flow_values,
            p_feedline: feedline_values,
            t_nozzle: nozzle_values,
            n2_pressure: n2_values,
        }
        for writer, values in batches.items():
            writer.write(np.vstack((timestamps, values)))
        sample_index += _ROWS_PER_BATCH
        time.sleep(_SOURCE_SLEEP_S)


def _clamp_series(values: np.ndarray, low: float, high: float) -> np.ndarray:
    return np.clip(values, low, high).astype(np.float64, copy=False)


def _build_showcase_orientation_gen() -> RotationMatrixSignalGenerator:
    _SR = 60.0
    _CYC = int(round(_SR * 8.0))
    return RotationMatrixSignalGenerator(
        roll=SpectralSignalConfig(
            signal_id="sc_roll",
            sample_rate_hz=_SR,
            samples_per_cycle=_CYC,
            terms=(SpectralTerm(bin_index=1, real=0.0, imag=-100.0),),
        ).build_generator(),
        pitch=SpectralSignalConfig(
            signal_id="sc_pitch",
            sample_rate_hz=_SR,
            samples_per_cycle=_CYC,
            terms=(SpectralTerm(bin_index=1, real=60.0, imag=0.0),),
        ).build_generator(),
        yaw=SpectralSignalConfig(
            signal_id="sc_yaw",
            sample_rate_hz=_SR,
            samples_per_cycle=_CYC,
            terms=(SpectralTerm(bin_index=1, real=0.0, imag=-150.0),),
        ).build_generator(),
    )


def _format_mission_clock(timestamp_s: float) -> str:
    total_seconds = max(0, int(timestamp_s))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"T+{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_event_timestamp(timestamp_s: float) -> str:
    whole = max(0.0, float(timestamp_s))
    minutes = int(whole // 60.0)
    seconds = whole - (minutes * 60.0)
    return f"{minutes:02d}:{seconds:06.3f}"


def _severity_from_value(value: float, *, good_is_positive: bool) -> str:
    if good_is_positive:
        if value <= 0.0:
            return "crit"
        if value <= 5.0:
            return "warn"
        return "ok"
    if value >= 95.0:
        return "crit"
    if value >= 80.0:
        return "warn"
    return "ok"


def _telemetry_widget_streams(widget: object) -> tuple[str, ...]:
    if isinstance(widget, SensorGraph):
        stream_names: list[str] = []
        for series in widget.series:
            if series.stream_name not in stream_names:
                stream_names.append(series.stream_name)
        return tuple(stream_names)
    if isinstance(widget, (AnalogNeedleGauge, LedBarGauge)):
        return (widget.stream_name,)
    raise TypeError(f"Unsupported telemetry widget type: {type(widget).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual Tau-Ceti showcase runnable.")
    parser.parse_args(argv)
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
