from __future__ import annotations

from typing import Any, Mapping

from viviian.gui_utils import (
    AnalogNeedleGauge,
    ConsoleComponent,
    GraphSeries,
    SensorGraph,
    theme,
)
from viviian.gui_utils._streaming import fan_out_reader_groups


def _require_imgui() -> Any:
    try:
        import imgui  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for dashboard rendering. Install GUI dependencies "
            "with `pip install -e \".[gui]\"`."
        ) from exc
    return imgui


class UCIRPLDashboard(ConsoleComponent):
    component_id = "ucirpl_dashboard"

    def __init__(self) -> None:
        self.tank_graph = SensorGraph(
            "tank_pressures_graph",
            title="Tank Pressures",
            series=(
                GraphSeries("copv", "COPV", "tank_copv", color_rgba=theme.ACID),
                GraphSeries("lox", "LOX", "tank_lox", color_rgba=theme.WARN, overlay=True),
                GraphSeries("lng", "LNG", "tank_lng", color_rgba=theme.INK, overlay=True),
            ),
            window_seconds=300.0,
            max_points_per_series=2048,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=180.0,
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
            window_seconds=300.0,
            max_points_per_series=2048,
            show_series_controls=True,
            theme_name="tau_ceti",
            plot_height=180.0,
        )
        self.load_graph = SensorGraph(
            "loadcell_graph",
            title="Load Cell",
            series=(GraphSeries("load", "Force", "load_force", color_rgba=theme.ALERT),),
            window_seconds=300.0,
            max_points_per_series=2048,
            show_series_controls=False,
            theme_name="tau_ceti",
            plot_height=150.0,
        )
        self.fft_graph = SensorGraph(
            "fft_graph",
            title="COPV FFT Magnitude",
            series=(GraphSeries("fft", "FFT", "fft_mag", color_rgba=(0.25, 0.95, 0.95, 1.0)),),
            window_seconds=120.0,
            max_points_per_series=1024,
            show_series_controls=False,
            theme_name="tau_ceti",
            plot_height=120.0,
        )

        self.copv_gauge = AnalogNeedleGauge(
            "copv_gauge",
            label="COPV Pressure",
            stream_name="tank_copv",
            low_value=0.0,
            high_value=4500.0,
            unit_label="PSI",
            theme_name="tau_ceti",
            width=260.0,
            height=190.0,
        )
        self.lox_gauge = AnalogNeedleGauge(
            "lox_gauge",
            label="LOX Pressure",
            stream_name="tank_lox",
            low_value=0.0,
            high_value=1200.0,
            unit_label="PSI",
            theme_name="tau_ceti",
            width=260.0,
            height=190.0,
        )
        self.lng_gauge = AnalogNeedleGauge(
            "lng_gauge",
            label="LNG Pressure",
            stream_name="tank_lng",
            low_value=0.0,
            high_value=1200.0,
            unit_label="PSI",
            theme_name="tau_ceti",
            width=260.0,
            height=190.0,
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

        self._widgets = (
            self.tank_graph,
            self.line_graph,
            self.load_graph,
            self.fft_graph,
            self.copv_gauge,
            self.lox_gauge,
            self.lng_gauge,
            self.force_gauge,
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
        )

    def bind(self, readers: Mapping[str, Any]) -> None:
        reader_groups = fan_out_reader_groups(
            readers,
            (
                ("tank_copv", "tank_lox", "tank_lng"),
                ("line_vent", "line_lox_mvas", "line_lox_inj_tee", "line_inj_lox", "line_inj_lng"),
                ("load_force",),
                ("fft_mag",),
                ("tank_copv",),
                ("tank_lox",),
                ("tank_lng",),
                ("load_force",),
            ),
        )
        for widget, group in zip(self._widgets, reader_groups):
            widget.bind(group)

    def consume(self) -> bool:
        dirty = False
        for widget in self._widgets:
            dirty = widget.consume() or dirty
        return dirty

    def render(self) -> None:
        imgui = _require_imgui()
        imgui.text_colored("UCIRPL Operator Dashboard", *theme.INK)
        imgui.separator()

        self.tank_graph.render()
        imgui.spacing()
        self.line_graph.render()
        imgui.spacing()
        self.load_graph.render()
        imgui.spacing()
        self.fft_graph.render()
        imgui.spacing()

        self.copv_gauge.render()
        imgui.same_line()
        self.lox_gauge.render()
        imgui.same_line()
        self.lng_gauge.render()
        imgui.same_line()
        self.force_gauge.render()


def build_dashboard() -> UCIRPLDashboard:
    return UCIRPLDashboard()