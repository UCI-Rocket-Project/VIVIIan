from __future__ import annotations

from pythusa import Pipeline

from ucirplgui.device_interfaces import BackendSimDeviceInterface, FrontendOperatorDesk
from ucirplgui import config


def build_ucirplgui_pipeline() -> Pipeline:
    """Return the empty pipeline scaffold for UCIRPLGUI.

    Intended wiring:
    - backend source task writes signal_stream
    - backend source task writes pressure_stream
    - frontend task reads signal_stream and pressure_stream
    - frontend task writes ui_state
    """

    backend = BackendSimDeviceInterface()
    frontend = FrontendOperatorDesk()
    pipeline = Pipeline("ucirplgui")

    # TODO: Add Pipeline streams here before registering tasks:
    # - signal_stream
    # - pressure_stream
    # - ui_state
    #
    # TODO: Reserve ui_state for operator commands; do not mix command frames
    # with telemetry read streams.
    _ = (backend, frontend, config.TELEMETRY_STREAMS, config.UI_STATE_STREAM)
    return pipeline
