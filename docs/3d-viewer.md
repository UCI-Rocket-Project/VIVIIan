# 3D Viewer

The 3D viewer lives in `gui_utils/3dmodel.py`.
Its job is narrow:

- compile one OBJ asset into a cached triangle bundle
- bind scalar telemetry streams to named mesh bodies
- bind one pose stream to the model orientation
- render the result inside an ImGui window through an offscreen OpenGL framebuffer

This page is the public guide.
For internal architecture, maintenance constraints, and current performance risks, use [3D Viewer Maintainer Notes](3d-viewer-maintainer.md).

## Quick Start

The default runtime asset layout is:

```text
gui_assets/cad/
gui_assets/compiled/
```

Put exactly one `.obj` file in `gui_assets/cad/`.
The runnable discovers that file automatically, reuses an existing compiled cache under `gui_assets/compiled/` when possible, and otherwise compiles a fresh cache.
The repository also includes checked-in sample assets under `src/gui_assets/`, but the runnable’s default lookup path is the repo-root `gui_assets/` directory shown above.

The manual example is:

```bash
python tests/gui_runnables/rocket_viewer_lab.py
```

Desktop requirements:

```bash
python -m pip install glfw PyOpenGL
```

## Public API

The primary public types are:

- `ModelBodyBinding`
  Declarative mapping from one mesh body to one scalar telemetry stream and one explicit low/high color range.
- `ModelViewerConfig`
  Serializable configuration surface for a viewer instance.
- `ModelViewer`
  Runtime object with `bind()`, `consume()`, `render()`, and `export()` methods.
- `ModelBodySnapshot`
  Latest body-state snapshot for observability and tests.
- `ModelPoseSnapshot`
  Latest accepted pose snapshot for observability and tests.
- `compile_obj_to_cache(...)`
  One-shot OBJ compile helper that writes an `.npz` cache plus a TOML manifest.
- `resolve_compiled_obj_assets(...)`
  Hash-based cache resolver used by the runnable and expected top-level workflows.

The preferred API names are `Model*`.
`RocketViewer`, `RocketViewerConfig`, and `RocketPartBinding` still exist as compatibility aliases, but they are not the preferred names for new code.

## Asset and Compile Flow

Compile an OBJ file once:

```python
from gui_utils import compile_obj_to_cache

cache_path, manifest_path = compile_obj_to_cache(
    "rocket.obj",
    ".cache/rocket_mesh",
)
```

This writes:

- an `.npz` cache with `vertices`, `normals`, and `indices`
- a TOML manifest that records the discovered mesh parts and source metadata

Bind names come from OBJ groups and objects:

- `g Body1788` becomes `g_Body1788`
- `o Nosecone` becomes `o_Nosecone`

If a group is active, group naming wins over object naming.

## Stream Contracts

### Body Streams

Body streams use the same shape as the graph stack:

```text
shape = (2, rows)
row 0 = timestamps
row 1 = values
dtype = float32 or float64
```

The viewer drains all currently available batches and keeps only the latest valid sample for each configured body binding.

### Pose Stream

The canonical pose stream is:

```text
shape = (13, rows)
row 0 = timestamps
rows 1:4 = x / y / z position
rows 4:13 = row-major 3x3 rotation matrix
dtype = float32 or float64
```

Current runtime behavior:

- position is accepted and preserved in the pose snapshot
- orientation is the only pose component applied to the rendered model
- slightly noisy matrices are re-orthonormalized before rendering
- grossly invalid matrices are rejected and the last good pose is retained

For compatibility, the runtime also accepts the older orientation-only shape:

```text
shape = (10, rows)
row 0 = timestamps
rows 1:10 = row-major 3x3 rotation matrix
```

Helper constructors are available when callers want to normalize upstream pose data before binding:

- `build_pose_batch_from_matrices(...)`
- `build_pose_batch_from_direction_vectors(...)`

## Configuration Surface

The most important `ModelViewerConfig` fields are:

| Field | Meaning |
| --- | --- |
| `viewer_id` | unique viewer identifier |
| `title` | window-local title shown above the viewport |
| `mesh_cache_path` | path to the compiled `.npz` mesh cache |
| `manifest_path` | path to the TOML mesh manifest |
| `pose_stream_name` | reader key for the pose stream |
| `model_alignment_matrix` | explicit 3x3 alignment matrix applied before rendering |
| `default_body_color_rgba` | fallback color for every unbound mesh body |
| `other_body_alpha` | shared alpha applied to all unbound mesh bodies |
| `camera_distance` | initial orbit-camera distance before fit adjustments |
| `camera_azimuth_deg` | initial orbit azimuth |
| `camera_elevation_deg` | initial orbit elevation |
| `backpressure_mode` | `latest_only` or `blocking` |
| `show_labels` | whether the built-in legend uses binding ids |
| `show_legend` | whether the built-in text legend is rendered |
| `show_axes` | whether xyz axes are rendered in the viewport |

Each `ModelBodyBinding` defines one highlighted mesh body:

| Field | Meaning |
| --- | --- |
| `binding_id` | unique binding identifier |
| `mesh_part_name` | exact mesh body name from the manifest |
| `value_stream_name` | scalar reader key |
| `low_value` | lower bound of the explicit telemetry range |
| `low_color_rgba` | color at `low_value` |
| `high_value` | upper bound of the explicit telemetry range |
| `high_color_rgba` | color at `high_value` |
| `default_color_rgba` | color used before the first valid sample arrives |

The viewer does not auto-range telemetry in v1.
All body color ranges are explicit.

## Basic Example

```python
from gui_utils import ModelBodyBinding, ModelViewerConfig

body_20 = ModelBodyBinding(
    binding_id="body_20",
    mesh_part_name="g_Body1:20",
    value_stream_name="oxidizer_level",
    low_value=0.0,
    low_color_rgba=(0.18, 0.27, 0.42, 1.0),
    high_value=100.0,
    high_color_rgba=(0.24, 0.72, 0.98, 1.0),
    default_color_rgba=(0.18, 0.27, 0.42, 1.0),
)

config = ModelViewerConfig(
    viewer_id="flight_view",
    title="Flight Viewer",
    mesh_cache_path=str(cache_path),
    manifest_path=str(manifest_path),
    pose_stream_name="pose",
    model_alignment_matrix=(
        1.0, 0.0, 0.0,
        0.0, 0.0, -1.0,
        0.0, 1.0, 0.0,
    ),
    default_body_color_rgba=(0.18, 0.205, 0.24, 1.0),
    other_body_alpha=0.34,
    body_bindings=(body_20,),
)

viewer = config.build_viewer()
viewer.bind(readers)
```

The `readers` mapping must contain:

- one reader for `pose_stream_name`
- one reader per `ModelBodyBinding.value_stream_name`

Reader expectations match the rest of `gui_utils`:

- `shape`
- `dtype`
- `read()` or `look()/increment()`

## Rendering Behavior

`ModelViewer.render()` renders into the current ImGui window.

The OpenGL scene is drawn into an offscreen framebuffer and then displayed with `imgui.image(...)`.
This keeps the viewer consistent with the rest of the ImGui-native desktop stack.

The built-in camera controls are:

- left-drag: orbit
- right-drag: pan
- mouse wheel: zoom
- toolbar button: reset camera

The runtime also performs a fit-distance adjustment so the full mesh stays in view after initial load or reset.

## Common Failure Cases

- `Expected exactly one asset ...`
  `gui_assets/cad/` contains zero or more than one matching asset. The runnable intentionally fails early here.
- `mesh_part_name ... does not exist in ...`
  A `ModelBodyBinding.mesh_part_name` does not match the manifest. Check the compiled manifest, not the raw OBJ text, because names are normalized during compile.
- `Reader ... must have shape ...`
  A bound reader does not match the required scalar or pose frame contract.
- `manifest_path ... does not match loaded manifest ...`
  The viewer config and the manifest disagree about which cache is being loaded.
- `PyOpenGL is required ...`
  The viewer is being rendered without the OpenGL dependency installed.

## Current Performance Boundaries

The current implementation is meant for operator-desk tooling, not a general-purpose scene engine.

Practical limits to keep in mind:

- cache size is strongly affected by the current OBJ compile strategy
- render cost scales with the number of manifest parts because each part is drawn separately
- the runtime is optimized for a single model viewer inside an ImGui tool, not a large multi-view scene graph

These limits are documented in more detail for engineers in [3D Viewer Maintainer Notes](3d-viewer-maintainer.md).

## Runnable Summary

`tests/gui_runnables/rocket_viewer_lab.py` demonstrates:

- discovery of exactly one OBJ asset under `gui_assets/cad/`
- compile-or-reuse cache resolution under `gui_assets/compiled/`
- two live highlighted bodies: `g_Body1:20` and `g_Body1:21`
- one live pose stream using row-major 3x3 matrices
- default-body styling for every unbound mesh body
- visible model rotation inside a standalone ImGui desktop window
