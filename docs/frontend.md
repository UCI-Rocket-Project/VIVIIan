# Frontend

If you are trying to build a dashboard or wire a new control, start with
[Build With VIVIIan -> Frontend](build-frontend.md).
This page is the runtime reference for `viviian.frontend`.

The current frontend module exports:

- `Frontend`
- `FrontendTask`
- `GlfwBackend`
- `HeadlessBackend`
- `HeadlessImgui`
- `OutputSlotSpec`
- `FrontendComponent`
- `WritableFrontendComponent`
- `RenderContext`

## Mental Model

`Frontend` is a compile-once builder around GUI components.
You register widgets, compile the frontend, then run the returned task with
reader and writer bindings.

The runtime is intentionally small:

- widgets pull the latest data from bound readers
- the backend implementation provides either a real window or a headless test session
- writable controls emit one `float64` state vector

## Minimal Example

```python
from __future__ import annotations

import numpy as np

from viviian.frontend import Frontend, HeadlessBackend
from viviian.gui_utils import AnalogNeedleGauge, ToggleButton


class StaticReader:
    shape = (2, 4)
    dtype = np.dtype(np.float64)

    def __init__(self, frame: np.ndarray) -> None:
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
    shape = (1,)
    dtype = np.dtype(np.float64)

    def write(self, frame: np.ndarray) -> bool:
        print(np.asarray(frame, dtype=self.dtype))
        return True


frontend = Frontend("desk")
frontend.add(
    AnalogNeedleGauge(
        gauge_id="pressure",
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
    pressure_stream=StaticReader(
        np.array(
            [[0.0, 1.0, 2.0, 3.0], [20.0, 30.0, 40.0, 50.0]],
            dtype=np.float64,
        )
    ),
    output=RecordingWriter(),
)
```

## `Frontend`

### `Frontend(name="frontend")`

`name` must be a non-empty string.
It becomes the default task name and window title.

### `frontend.add(component)`

Registers a component and returns it.

Rules:

- components must be added before compilation
- component IDs must be unique after adaptation
- once compiled, the frontend becomes immutable

Trying to `add(...)` after compilation raises `RuntimeError`.

### `frontend.compile()`

Compiles the registered components into adapters.
Compilation is idempotent.

During compilation, the runtime:

- adapts each component into a frontend adapter
- validates unique component IDs
- collects `required_reads`
- derives `output_slots`

### `frontend.required_reads`

Tuple of unique stream names required by the registered components, in
first-seen order.

### `frontend.output_shape`

Tuple containing the length of the output state vector.

Examples:

- no writable controls -> `(0,)`
- two writable controls -> `(2,)`

### `frontend.output_slots`

Tuple of `OutputSlotSpec`, one per writable component.

Each slot includes:

- `index`
- `component_id`
- `state_id`
- `initial_value`

This is the authoritative mapping between state-vector index and control meaning.

### `frontend.read_bindings()`

Returns:

```python
{stream_name: stream_name for stream_name in frontend.required_reads}
```

This is mainly useful when another composition layer needs a default binding map.

### `frontend.write_bindings(stream_name, output_binding="output")`

Returns:

```python
{output_binding: stream_name}
```

This is useful when wiring the frontend output vector into a named app stream.

### `frontend.output_ring_size(headroom_frames=8)`

Returns the minimum byte size for a ring buffer that should hold the output
state vector with the requested headroom.

Use this instead of hand-computing ring sizes for writable frontends.

### `frontend.build_task(...)`

```python
frontend.build_task(
    output_binding="output",
    backend=None,
    window_title=None,
    fill_backend_window=False,
)
```

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `output_binding` | `str` | `"output"` | Binding name used when the task resolves its writer. |
| `backend` | `BackendSpec \| None` | `GlfwBackend()` | Backend that provides the render session. |
| `window_title` | `str \| None` | `frontend.name` | Window title override. |
| `fill_backend_window` | `bool` | `False` | When `True`, sizes and pins the top-level window to the backend display area. |

Returns a `FrontendTask`.

## `FrontendTask`

`FrontendTask` is the compiled callable.
You do not construct it directly.

Calling the task:

```python
task(
    pressure_stream=reader,
    output=writer,
)
```

Rules:

- every stream in `required_reads` must be provided as a keyword argument
- if writable controls exist, the output binding must also be provided
- if there are no writable controls, the output binding is optional

The runtime raises `KeyError` when required bindings are missing.

### Runtime Loop Behavior

Each frame:

1. calls `consume()` on every adapter
2. begins a backend frame
3. renders each adapted component
4. ends the backend frame
5. writes the latest output snapshot if the state is dirty

The runtime also writes an initial snapshot before the main loop starts when the
frontend has writable controls.

If `writer.write(snapshot)` returns `False`, the task retains the dirty state and
retries the latest snapshot on the next frame.

## Component Adaptation Rules

The current adapter dispatch is:

| Component type | Adapter behavior | Writable |
| --- | --- | --- |
| `StateButton` subclasses such as `ToggleButton` and `MomentaryButton` | `ButtonComponentAdapter` | yes |
| `SensorGraph` | reads all declared series streams | no |
| `SensorGauge` subclasses such as `AnalogNeedleGauge` and `LedBarGauge` | reads one stream | no |
| `ModelViewer` | reads body and pose streams | no |
| custom `FrontendComponent` | `GenericComponentAdapter` | only if it also implements `WritableFrontendComponent` |

Unsupported component types raise `TypeError` at compile time.

### Writable Control Semantics

The built-in state rules are:

- `ToggleButton` latches `0.0` or `1.0`
- `MomentaryButton` emits one pulse value, then resets after a successful snapshot write

Writable output values must be numeric and finite.
Accepted types are:

- `bool`
- `int`
- `float`

String state values are not valid for writable frontend output.

### Gate and Interlock State

For buttons with `gate_id` or `interlock_ids`, the runtime builds a
`RenderContext` from all currently known writable boolean states and passes it
into button rendering.

This allows buttons to disable themselves based on the state of other controls
without every app inventing its own coordination layer.

## Custom Components

Implement `FrontendComponent` when you need a custom widget:

```python
from __future__ import annotations

from typing import Any, Mapping

from viviian.frontend import FrontendComponent


class MyWidget:
    component_id = "my_widget"

    def required_streams(self) -> tuple[str, ...]:
        return ("telemetry",)

    def bind(self, readers: Mapping[str, Any]) -> None:
        self._reader = readers["telemetry"]

    def consume(self) -> bool:
        self._latest = self._reader.read()
        return self._latest is not None

    def render(self) -> None:
        ...
```

Implement `WritableFrontendComponent` as well if the component should contribute
to the output state vector.

## Backends

### `HeadlessBackend`

`HeadlessBackend` runs the frontend without a real window.
This is the test and CI backend.

| Field | Default | Meaning |
| --- | --- | --- |
| `max_frames` | `1` | Number of frames before the session closes |
| `button_presses` | `()` | Sequence consumed by `imgui.button()` calls in order |
| `delta_time` | `1.0 / 60.0` | Simulated frame delta time |
| `frame_sleep_s` | `0.0` | Optional sleep between frames |
| `theme_name` | `"legacy"` | Theme applied to the headless session |

### `GlfwBackend`

`GlfwBackend` opens a real GLFW/OpenGL window.

| Field | Default | Meaning |
| --- | --- | --- |
| `width` | `1280` | Window width |
| `height` | `900` | Window height |
| `clear_color` | `(0.020, 0.030, 0.050, 1.0)` | OpenGL clear color |
| `vsync` | `1` | GLFW swap interval |
| `theme_name` | `"legacy"` | ImGui theme name |

The backend raises `RuntimeError` if required GUI dependencies are missing,
including `glfw`, `imgui` with the GLFW integration, or `PyOpenGL`.

## Useful Source Examples

The best current examples in the repo are:

- `tests/test_frontend.py` for compile-time and runtime semantics
- `tests/gui_runnables/frontend_lab.py` for a minimal live frontend with ring-buffer output
- `apps/ucirplgui/src/ucirplgui/frontend/frontend.py` for the full app integration path

## What To Read Next

- [Frontend](build-frontend.md) — task-oriented guide for building dashboards
- [GUI Utils](gui-utils.md) — widget catalog and behavior
- [Backend](build-backend.md) — how frontend-facing streams are usually produced
