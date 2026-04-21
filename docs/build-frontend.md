# Frontend

This guide is for engineers building operator-facing dashboards with the current
`viviian.frontend` runtime.

It covers the practical questions users hit first:

- how to compose widgets into a frontend
- how to bind live readers into those widgets
- how writable controls emit output state
- how to run the same frontend live with GLFW and in tests with a headless backend

Use [Frontend](frontend.md) when you need the reference surface.

## Frontend Mental Model

`Frontend` is a builder.
You add widgets, compile once, then run the generated task.

The task does three things in a loop:

1. consumes the latest data from each required reader
2. renders the widgets
3. writes a `float64` control-state snapshot whenever a writable control changes

That snapshot is a vector, not a dict.
Slot order is determined by component registration order.

## Smallest Working Example

This is a minimal frontend you can run in a headless test path:

```python
from __future__ import annotations

import numpy as np

from viviian.frontend import Frontend, HeadlessBackend
from viviian.gui_utils import AnalogNeedleGauge, GraphSeries, SensorGraph, ToggleButton


class OneShotReader:
    def __init__(self, frame: np.ndarray) -> None:
        self.shape = tuple(frame.shape)
        self.dtype = np.dtype(frame.dtype)
        self._frame = np.asarray(frame, dtype=self.dtype).copy()

    def set_blocking(self, blocking: bool) -> None:
        del blocking

    def read(self) -> np.ndarray | None:
        if self._frame is None:
            return None
        frame = self._frame.copy()
        self._frame = None
        return frame


class RecordingWriter:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape
        self.dtype = np.dtype(np.float64)

    def write(self, frame: np.ndarray) -> bool:
        snapshot = np.asarray(frame, dtype=self.dtype)
        print(snapshot)
        return True


frontend = Frontend("demo_frontend")
frontend.add(
    SensorGraph(
        "signal_graph",
        title="Signal",
        series=(
            GraphSeries(
                series_id="signal",
                label="Signal",
                stream_name="signal_stream",
                color_rgba=(0.16, 0.73, 0.78, 1.0),
            ),
        ),
    )
)
frontend.add(
    AnalogNeedleGauge(
        gauge_id="pressure_gauge",
        label="Pressure",
        stream_name="pressure_stream",
        low_value=0.0,
        high_value=100.0,
    )
)
frontend.add(
    ToggleButton(
        button_id="arm_toggle",
        label="Arm",
        state_id="desk.arm",
        state=False,
    )
)

task = frontend.build_task(
    backend=HeadlessBackend(max_frames=1, button_presses=(True,)),
)

task(
    signal_stream=OneShotReader(
        np.array(
            [[0.0, 1.0, 2.0, 3.0], [10.0, 11.0, 12.0, 13.0]],
            dtype=np.float64,
        )
    ),
    pressure_stream=OneShotReader(
        np.array(
            [[0.0, 1.0, 2.0, 3.0], [45.0, 50.0, 55.0, 60.0]],
            dtype=np.float64,
        )
    ),
    output=RecordingWriter(frontend.output_shape),
)
```

What this example shows:

- `SensorGraph` and `AnalogNeedleGauge` are read-only consumers
- `ToggleButton` is writable and therefore creates one output slot
- the task requires `signal_stream`, `pressure_stream`, and `output`
- `HeadlessBackend` lets you validate behavior without opening a window

## Common Tasks

### 1) Add a Read-Only Widget

For a graph or gauge, add the component and bind a reader whose shape and dtype
match the widget's numeric expectations.

Typical examples:

- `SensorGraph` for time series
- `AnalogNeedleGauge` for single-value dial displays
- `LedBarGauge` for segmented status displays

At compile time, the frontend gathers the set of required stream names across
all registered widgets.

### 2) Add a Writable Control

Writable controls become entries in the output snapshot vector.
The built-in writable path is driven by `StateButton` subclasses such as:

- `ToggleButton`
- `MomentaryButton`

The important rule is that output values must be coercible to `float64`.
That means writable state values must be:

- `bool`
- `int`
- `float`

String-valued writable state is rejected at compile time.

### 3) Understand the Output Snapshot

The snapshot order is the order in which writable components were added.
Use `frontend.output_slots` to map indices back to semantic control IDs.

```python
frontend.compile()
for slot in frontend.output_slots:
    print(slot.index, slot.component_id, slot.state_id, slot.initial_value)
```

The runtime also writes an initial snapshot before the main render loop starts.
That gives downstream consumers a defined starting state.

### 4) Run the Same Frontend in Tests and Live

For tests and CI:

```python
task = frontend.build_task(backend=HeadlessBackend(max_frames=2))
```

For a real operator desk:

```python
from viviian.frontend import GlfwBackend

task = frontend.build_task(
    backend=GlfwBackend(
        width=1600,
        height=980,
        theme_name="tau_ceti",
    ),
    window_title="My Desk",
)
```

That split is deliberate.
You should not have to choose between having a real frontend and being able to
test it.

### 5) Use `fill_backend_window` for Full-Surface Layouts

If your top-level component is meant to occupy the whole GLFW window, build the
task like this:

```python
task = frontend.build_task(
    backend=GlfwBackend(width=1600, height=980, theme_name="tau_ceti"),
    window_title="UCIRPLGUI",
    fill_backend_window=True,
)
```

That causes the frontend runtime to size and pin the top-level ImGui window to
the current backend display size.

### 6) Size an Output Ring Correctly

If you are wiring the frontend into a shared-memory output stream, use
`frontend.output_ring_size()` instead of hand-rolling the buffer size.

The reference pattern is in `tests/gui_runnables/frontend_lab.py`, where the
frontend:

- computes `frontend.output_shape`
- allocates a ring sized with `frontend.output_ring_size()`
- creates writer/reader bindings for the output vector
- runs the live frontend while another thread prints snapshots

That file is the best template when you want a real app path, not just a unit
test.

### 7) Use Binding Helpers When Another Runtime Owns Stream Names

`Frontend` exposes two helper methods for composition code:

```python
frontend.read_bindings()
frontend.write_bindings("ui_state")
```

Those are useful when an orchestrator or pipeline layer is mapping frontend
requirements onto deployment-local stream names.

## UCIRPLGUI Equivalent

The frontend side of the reference app is split cleanly:

- `apps/ucirplgui/src/ucirplgui/components/dashboard.py`
  defines the actual dashboard surface
- `apps/ucirplgui/src/ucirplgui/frontend/frontend.py`
  opens connectors, primes per-widget readers, and runs the `FrontendTask`

That split is worth keeping in your own apps:

- components own layout and widget composition
- the frontend runtime owns stream binding and event loop setup

## Failure Modes Worth Avoiding

The frontend errors users usually hit first are:

- duplicate component IDs
- missing reader bindings at task call time
- missing output binding when writable controls exist
- trying to mutate the frontend after it has been compiled
- expecting queue semantics from readers that only expose the latest batch

Design against them directly:

1. keep component IDs unique and human-readable
2. compile early in tests
3. inspect `required_reads`, `output_shape`, and `output_slots`
4. keep custom writable state numeric

## What To Read Next

- [Frontend](frontend.md) for the full reference surface
- [GUI Utils](gui-utils.md) for the available widget set
- [Backend](build-backend.md) for the upstream runtime that usually feeds the frontend
