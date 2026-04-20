from __future__ import annotations

WINDOW_TITLE = "UCIRPLGUI"
THEME_NAME = "tau_ceti"

SIGNAL_STREAM = "signal_stream"
PRESSURE_STREAM = "pressure_stream"
UI_STATE_STREAM = "ui_state"

TELEMETRY_STREAMS = (
    SIGNAL_STREAM,
    PRESSURE_STREAM,
)

DEFAULT_SAMPLE_RATE_HZ = 60.0
DEFAULT_ROWS_PER_BATCH = 12
DEFAULT_BATCH_SLEEP_S = 0.05

DEFAULT_PUBLISH_HOST = "127.0.0.1"
DEFAULT_PUBLISH_PORT = 6767

# TODO: Keep stream names stable; the frontend binds by exact string match.
# TODO: Swap tau_ceti for the real theme after it is added to viviian.gui_utils.theme.
