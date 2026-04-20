from __future__ import annotations

# TODO: Keep GUI composition in this file so layout decisions stay separate
# from transport, simulation, and pipeline wiring.
#
# Planned render order:
# 1. top status or toolbar row
# 2. primary signal graph
# 3. pressure gauge row
# 4. control buttons row

DASHBOARD_RENDER_ORDER = (
    "toolbar",
    "signal_graph",
    "pressure_gauge",
    "controls",
)

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

class Dashboard(ConsoleComponent): 
    # TODO: Implement the dashboard here, 
    # it should be a mimic of the @rocket2-gui dashboard
    # it should have the same widgets as the @rocket2-gui dashboard
    # it should have the same functionality as the @rocket2-gui dashboard
    # it should have the same layout as the @rocket2-gui dashboard
    # it should use the tau-ceti theme 