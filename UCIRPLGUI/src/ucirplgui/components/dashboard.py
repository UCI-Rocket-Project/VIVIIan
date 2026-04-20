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
