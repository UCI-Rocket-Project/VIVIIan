from __future__ import annotations

from viviian.frontend import Frontend

from ucirplgui import config
from ucirplgui.components.dashboard import DASHBOARD_RENDER_ORDER


class FrontendOperatorDesk:
    """Scaffold for the operator-facing GUI desk."""

    interface_id = "frontend_operator_desk"

    def build_frontend(self) -> Frontend:
        frontend = Frontend(name="ucirplgui_frontend")

        # TODO: Add widgets here in the same order you want them rendered.
        # Planned starter widgets:
        # - SensorGraph bound to signal_stream
        # - AnalogNeedleGauge bound to pressure_stream
        # - ToggleButton writing ui_state
        # - MomentaryButton writing ui_state
        _ = DASHBOARD_RENDER_ORDER
        return frontend

    def required_streams(self) -> tuple[str, ...]:
        return config.TELEMETRY_STREAMS

    def output_stream_name(self) -> str:
        return config.UI_STATE_STREAM

    def planned_widgets(self) -> tuple[str, ...]:
        return (
            "SensorGraph(signal_stream)",
            "AnalogNeedleGauge(pressure_stream)",
            "ToggleButton(ui_state)",
            "MomentaryButton(ui_state)",
        )
