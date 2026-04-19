# Frontend

The frontend module turns a collection of `gui_utils` widgets into a single
callable task.
The task reads live stream data, renders the operator desk via ImGui, and
writes a float64 state vector whenever a writable control changes.

## Install

```bash
pip install -e ".[gui]"
```

```python
from viviian.frontend import Frontend, GlfwBackend, HeadlessBackend
```

## Mental Model

```text
streams (readers)
       │
       │  bind + consume each frame
       ▼
  ┌─────────────┐
  │  ImGui loop │  ◄── GlfwBackend (live) or HeadlessBackend (test)
  │             │
  │  SensorGraph│  read-only, displays live batches
  │  SensorGauge│  read-only, displays single values
  │  ToggleButton│  writable, latches 0.0 / 1.0
  │  MomentaryButton│  writable, pulses 1.0 then resets to 0.0
  └─────────────┘
       │
       │  float64 state vector
       ▼
  output writer (ring buffer or any object with .write(np.ndarray))
```

## Minimal Example

```python
import pyarrow as pa
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import SensorGraph, GraphSeries, ToggleButton

with Frontend("rocket-desk") as desk:
    graph = desk.add(SensorGraph(
        graph_id="pressure",
        series=(GraphSeries(stream_name="pressure_kpa", label="Pressure"),),
    ))
    arm = desk.add(ToggleButton(
        button_id="arm",
        state_id="arm",
        label_on="ARMED",
        label_off="SAFE",
    ))

task = desk.build_task(backend=GlfwBackend())

# task is callable — wire it into your orchestrator or run directly
task(pressure_kpa=pressure_reader, output=output_writer)
```

## Building a Frontend

`Frontend` is a builder.
Add components, then call `build_task`.

### `Frontend(name)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | `"frontend"` | Window title and task name. |

### `frontend.add(component)`

Register a widget.
Components must be added before `compile()` or `build_task()` is called.
Component IDs must be unique across the frontend.
Returns the component for fluent patterns.

### `frontend.build_task(*, backend, output_binding, window_title)`

Compile the frontend and return a `FrontendTask`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `backend` | `BackendSpec \| None` | `GlfwBackend()` | Window and ImGui session provider. |
| `output_binding` | `str` | `"output"` | Key name for the output writer in the `task(**bindings)` call. |
| `window_title` | `str \| None` | `frontend.name` | Override the ImGui window title. |

### `frontend.output_ring_size(headroom_frames=8)`

Returns the minimum ring buffer size in bytes to hold the float64 state
vector with `headroom_frames` of headroom.

### `frontend.required_reads`

Tuple of stream names the task will request at call time.
Available after `compile()`.

### `frontend.output_slots`

Tuple of `OutputSlotSpec` — one per writable control, in component
registration order.
Each slot carries: `index`, `component_id`, `state_id`, `initial_value`.

## Running the Task

`FrontendTask` is a frozen dataclass.
Calling it starts the render loop.

```python
task(
    pressure_kpa=pressure_reader,   # one keyword per required_reads entry
    output=output_writer,           # matches output_binding
)
```

The loop runs until the window is closed.
Each iteration:

1. calls `consume()` on every adapter (pulls latest data from readers)
2. renders each widget via ImGui
3. if any writable control changed, writes one float64 snapshot vector to the output writer

The output writer must expose a `.write(np.ndarray)` interface.

## Component Dispatch

`Frontend.compile()` calls `adapt_component()` on each registered object.
The dispatch rules are:

| Component Type | Behaviour | Writable |
|----------------|-----------|---------|
| `StateButton` (any subclass) | `ButtonComponentAdapter` | yes |
| `ToggleButton` | latches `0.0` or `1.0` | yes |
| `MomentaryButton` | pulses `1.0`, resets to `0.0` after snapshot written | yes |
| `SensorGraph` | `BaseComponentAdapter`, reads all declared series streams | no |
| `SensorGauge` | `BaseComponentAdapter`, reads one stream | no |
| `ModelViewer` | `BaseComponentAdapter`, reads body + pose streams | no |
| Custom `FrontendComponent` | `GenericComponentAdapter` | if `WritableFrontendComponent` |

Objects that match none of the above raise `TypeError` at compile time.

### MomentaryButton Pulse Semantics

`MomentaryButton` pulses exactly once per press.
When the button is pressed:

1. `snapshot_value()` returns `1.0`
2. after the snapshot is written, `after_snapshot_written()` resets the pulse
3. subsequent snapshots return `0.0` until the next press

This means one press → one `1.0` frame in the output stream, then `0.0`.

### Gate and Interlock State

`StateButton` supports `gate_id` and `interlock_ids`.
The frontend collects the boolean state of every writable adapter with a
`state_id` and passes it to each button's `render()` call as `gate_states`
and `interlock_states`.
Buttons whose gate is `False` or whose interlock conflicts are disabled
automatically.

## Backends

### `GlfwBackend`

Opens a real GLFW/OpenGL window.
Requires `pip install -e ".[gui]"`.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `width` | `1280` | Window width in pixels. |
| `height` | `900` | Window height in pixels. |
| `clear_color` | `(0.02, 0.03, 0.05, 1.0)` | OpenGL clear color (RGBA). |
| `vsync` | `1` | GLFW swap interval. |

### `HeadlessBackend`

Runs the render loop without a window.
Used in tests and CI.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_frames` | `1` | Number of frames to render before `should_close()` returns `True`. |
| `button_presses` | `()` | Sequence of booleans consumed by `imgui.button()` calls in order. |
| `delta_time` | `1/60` | Simulated frame delta time. |
| `frame_sleep_s` | `0.0` | Optional sleep per frame (useful for pacing tests). |

```python
from viviian.frontend import Frontend, HeadlessBackend
from viviian.gui_utils import ToggleButton

with Frontend("test-desk") as desk:
    btn = desk.add(ToggleButton(
        button_id="arm",
        state_id="arm",
        label_on="ARMED",
        label_off="SAFE",
    ))

task = desk.build_task(backend=HeadlessBackend(max_frames=2, button_presses=[True]))
task(output=mock_writer)
```

## Custom Components

Implement the `FrontendComponent` protocol to add custom widgets:

```python
from typing import Any, Mapping
from viviian.frontend import FrontendComponent

class MyWidget:
    component_id = "my_widget"

    def required_streams(self) -> tuple[str, ...]:
        return ("telemetry",)

    def bind(self, readers: Mapping[str, Any]) -> None:
        self._reader = readers["telemetry"]

    def consume(self) -> bool:
        self._latest = self._reader.latest_batch()
        return True

    def render(self) -> None:
        imgui.text(f"value: {self._latest}")
```

For writable custom components, also implement `WritableFrontendComponent`:

```python
from viviian.frontend import WritableFrontendComponent

class MyControl(MyWidget):
    _state: float = 0.0

    def snapshot_value(self) -> float:
        return self._state

    def after_snapshot_written(self) -> bool:
        return False
```

## Output State Vector

The output is a 1D `np.ndarray` of `float64` with one element per writable
control, in the order they were added to the frontend.

The vector is written to the output binding on every frame where at least one
writable control changed.
An initial snapshot is also written before the render loop starts.

Use `frontend.output_slots` to map vector indices back to component and state IDs.

## Interactive Lab

Run the lab from the repo root; the script resolves the repo root and `src/`
automatically when launched directly.

```bash
python tests/gui_runnables/frontend_lab.py
```

The frontend lab wires simulated pressure and a toggle + momentary button
into a live `GlfwBackend` desk.

## What To Read Next

- [GUI Utils](gui-utils.md) — all available widgets
- [Orchestrator](orchestrator.md) — how tasks compose into a pipeline
- [Architecture](architecture.md) — full system context
