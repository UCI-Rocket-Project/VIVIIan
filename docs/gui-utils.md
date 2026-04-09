# GUI Utils

The `gui_utils` package contains the current top-level ImGui primitives for the operator desk.
Today, that means:

- time-series graphs in `gui_utils/graphs.py`
- generic state buttons in `gui_utils/buttons.py`
- animated telemetry gauges in `gui_utils/gauges.py`
- OBJ-backed telemetry model viewing in `gui_utils/3dmodel.py`
- small TOML helpers in `gui_utils/configure.py`

The design is deliberately direct:

- graphs consume numeric batches with explicit timestamps
- gauges consume the latest numeric sample from the same reader contract
- the model viewer consumes scalar body streams plus a timestamped pose stream
- buttons emit typed state updates instead of embedding command semantics
- graphs, gauges, buttons, and the model viewer can export and reconstruct configuration from TOML

For the 3D viewer specifically:

- use [3D Viewer](3d-viewer.md) for the public API and runtime contracts
- use [3D Viewer Maintainer Notes](3d-viewer-maintainer.md) for internal architecture and performance constraints

## SensorGraph

`SensorGraph` is the main graph runtime.
It combines:

- a serializable graph configuration
- one or more named series
- a rolling display history per series
- ImGui rendering

### Core Input Contract

Every graph series expects a reader bound to a stream name.
That reader must expose:

- `shape`
- `dtype`
- `read()` or `look()/increment()`

The graph batch shape is:

```python
(2, rows)
```

with:

- row `0`: timestamps
- row `1`: values

Accepted dtypes are:

- `np.float32`
- `np.float64`

## Graph Window Semantics

This point matters for telemetry desks.

The graph window is **not** based on each series’s own last update.
It is based on the **latest timestamp seen anywhere on the graph**.

That means:

- if a signal stops updating, its current trace freezes immediately
- once other live series advance graph time far enough, that frozen trace ages out
- if no series advance, the whole graph stays frozen

This shared-graph-clock behavior is what keeps stale disabled series from corrupting the y-range of the still-running signals.

## GraphSeries

Each plotted line is described by a `GraphSeries`.

Key fields:

| Field | Meaning |
| --- | --- |
| `series_id` | unique graph-local series identifier |
| `label` | user-facing label |
| `stream_name` | reader binding name |
| `color_rgba` | line color |
| `visible_by_default` | initial visibility state |
| `overlay` | whether the series should render with overlay thickness |

## SensorGraph Fields

The main fields you will use most often are:

| Field | Meaning |
| --- | --- |
| `graph_id` | unique graph identifier |
| `title` | title shown above the plot |
| `series` | tuple of `GraphSeries` |
| `window_seconds` | rolling visible history window |
| `max_points_per_series` | hard storage cap per series |
| `backpressure_mode` | `latest_only` or `blocking` |
| `show_axes` | draw the zero axis when visible |
| `show_series_controls` | show or hide the built-in series visibility buttons |
| `stable_y` | smooth y-range contraction rather than snapping every update |

## Basic Graph Example

```python
from __future__ import annotations

import numpy as np

from gui_utils.graphs import GraphSeries, SensorGraph


class Reader:
    def __init__(self) -> None:
        self.shape = (2, 4)
        self.dtype = np.dtype(np.float64)
        self._frames = []

    def set_blocking(self, _blocking: bool) -> None:
        return None

    def push(self, frame: np.ndarray) -> None:
        self._frames.append(np.asarray(frame, dtype=np.float64))

    def read(self) -> np.ndarray | None:
        if not self._frames:
            return None
        return self._frames.pop(0)


reader = Reader()
graph = SensorGraph(
    "pressures",
    title="Pressure Deck",
    series=(
        GraphSeries(
            series_id="copv",
            label="COPV",
            stream_name="copv_stream",
            color_rgba=(0.92, 0.32, 0.29, 1.0),
        ),
        GraphSeries(
            series_id="lox",
            label="LOX",
            stream_name="lox_stream",
            color_rgba=(0.16, 0.73, 0.78, 1.0),
        ),
    ),
    window_seconds=10.0,
    max_points_per_series=4096,
)

graph.bind(
    {
        "copv_stream": reader,
        "lox_stream": reader,
    }
)
```

In a real app you would bind distinct readers per stream.
The graph runtime will:

- validate reader shape and dtype
- drain all currently available batches
- trim every series against the shared graph clock
- update y-limits from the post-trim data

## Graph Hooks

If your UI loop wants no-argument callables, use:

```python
consume_fn, render_fn = graph.build_dashboard_hooks(readers)
```

Then call:

```python
consume_fn()
render_fn()
```

inside the GUI loop.

## TOML Export And Reconstruction

Graphs export to one TOML file per graph:

```python
path = graph.export("configs/pressure_graph.toml")
rebuilt = SensorGraph.reconstruct(path)
```

The file stores:

- top-level graph settings
- per-series labels and colors
- `show_series_controls`
- windowing and y-range behavior

This makes graph definitions reconstructable without hand-wiring the object graph every time.

## Buttons

The button layer is intentionally generic.
Buttons model **state emission**, not domain-specific commands.

The current types are:

- `StateButton` base class
- `ToggleButton`
- `MomentaryButton`

Every button emits a `ButtonStateUpdate` with:

| Field | Meaning |
| --- | --- |
| `button_id` | UI-local button identifier |
| `state_id` | semantic state key |
| `state` | emitted value |

### ToggleButton

`ToggleButton` stores a boolean state and flips it on press.

```python
from gui_utils.buttons import ToggleButton

button = ToggleButton(
    button_id="signal_1",
    label="signal_1",
    state_id="signal_1.enabled",
    state=False,
)

update = button.render()
if update is not None:
    print(update.state)  # True after the first click
```

### MomentaryButton

`MomentaryButton` emits a configured value without latching its own state machine.

```python
from gui_utils.buttons import MomentaryButton

button = MomentaryButton(
    button_id="generate_bank",
    label="Generate 8 Random Signals",
    state_id="signal_bank.generate",
    state="generate",
)
```

This is useful for operator actions like “generate,” “arm,” or “send pulse,” where the button press should emit a value but not remain visually latched by itself.

## Button Gating And Interlocks

Buttons also support:

- `gate_id`
- `interlock_ids`
- `enabled_by_default`

That allows the UI layer to express “this control is only enabled if these gate / interlock states are currently true” without baking a specific rocket-control protocol into `gui_utils`.

## Button TOML

Buttons export and reconstruct the same way as graphs:

```python
path = button.export("configs/generate_button.toml")
rebuilt = StateButton.reconstruct(path)
```

That round-trip preserves:

- type
- ids
- label
- emitted state
- gate and interlock metadata
- color

## Current Best References

The best live examples of `gui_utils` in use today are:

```bash
python tests/gui_runnables/signal_graph_lab.py
python tests/gui_runnables/gauge_lab.py
```

`signal_graph_lab.py` wires:

- one `MomentaryButton`
- eight `ToggleButton`s
- one `SensorGraph`
- eight deterministic spectral generators

into a small operator-style desk implemented entirely with ImGui.

`gauge_lab.py` wires:

- one `AnalogNeedleGauge`
- one `LedBarGauge`
- one `SensorGraph`
- one deterministic scalar source with explicit low/high guide lines

into a compact gauge-focused dashboard so you can compare the animated gauges against the raw line trace.

## Gauges

The gauge layer is the new single-value telemetry widget family.
It follows the same top-level pattern as the graph layer:

- bind a named reader
- `consume()` the latest available samples
- `render()` an ImGui widget
- `export(...)` and `reconstruct(...)` a TOML config

### Gauge Model

Gauges are intentionally simpler than graphs:

- they keep only the latest finite sample from the bound reader
- they normalize that sample against `low_value` and `high_value`
- they animate a displayed value toward the latest target value
- they persist configuration only, not runtime state

### Shared Gauge Input Contract

Gauge readers use the same numeric batch contract as `SensorGraph`:

- `shape`
- `dtype`
- `read()` or `look()/increment()`

The batch shape is:

```python
(2, rows)
```

with:

- row `0`: timestamps
- row `1`: values

Unlike `SensorGraph`, a gauge keeps only the most recent finite sample.

### AnalogNeedleGauge

`AnalogNeedleGauge` renders a bounded circular dial with:

- a larger green-to-red sweep ring
- an animated needle
- 5 labeled major ticks at `0% / 25% / 50% / 75% / 100%` of the configured range
- minor ticks between each major interval

The default footprint is compact enough for dashboard grids:

- `width = 196.0`
- `height = 156.0`
- `arc_thickness = 14.0`

```python
from gui_utils.gauges import AnalogNeedleGauge

gauge = AnalogNeedleGauge(
    "chamber_pressure",
    label="Chamber Pressure",
    stream_name="pressure_stream",
    low_value=0.0,
    high_value=300.0,
)
```

The needle angle is determined by the normalized position of the current reading between `low_value` and `high_value`.
The displayed value eases toward the latest consumed reading using a simple frame-rate-independent damping step.

### LedBarGauge

`LedBarGauge` renders a segmented horizontal bar.
By default it uses 10 sections.

```python
from gui_utils.gauges import LedBarGauge

gauge = LedBarGauge(
    "battery",
    label="Battery",
    stream_name="battery_stream",
    low_value=0.0,
    high_value=100.0,
    segment_count=10,
)
```

As the value rises, segments turn on from left to right.
The default active palette is a fixed severity band:

- 5 green segments
- 2 orange segments
- 1 orange-red segment
- 2 red segments

If `segment_count` is not `10`, the gauge maps each segment to the nearest band in that canonical 10-segment palette. It does not interpolate a continuous gradient.

### Gauge Hooks

Gauges expose the same hook pattern as graphs:

```python
consume_fn, render_fn = gauge.build_dashboard_hooks(readers)
```

Then call:

```python
consume_fn()
render_fn()
```

inside the GUI loop.

### Gauge TOML

Each gauge exports to one TOML file:

```python
path = gauge.export("configs/chamber_pressure.toml")
rebuilt = AnalogNeedleGauge.reconstruct(path)
```

or through the base dispatcher:

```python
from gui_utils.gauges import SensorGauge

rebuilt = SensorGauge.reconstruct(path)
```

The file stores:

- the gauge kind
- ids and stream binding
- range bounds
- animation settings
- shared colors
- type-specific fields like sweep angles or segment count

`__repr__` is configuration-oriented and intentionally excludes runtime reader state or the latest animated value.

### Gauge Lab

`tests/gui_runnables/gauge_lab.py` is the reference runnable for the current gauge stack.
It renders:

- one `AnalogNeedleGauge`
- one `LedBarGauge`
- one `SensorGraph`
- shared low/high guide lines against the same scalar source

Use it to visually confirm that the graph, analog dial, and LED activation all agree on the same telemetry range semantics.

### Maintainer Notes

- Keep visual defaults defined once and reuse them from both constructors and TOML reconstruction.
- Treat analog geometry as a bounded layout problem. The sweep should stay inside the panel by construction, not by clipping.
- Treat LED colors as canonical operator bands, not a free-form gradient.
- Prefer testing public behavior over private helpers so rendering internals can evolve without forcing broad test churn.
