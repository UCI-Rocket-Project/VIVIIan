# 3D Viewer Maintainer Notes

This page is for engineers maintaining `gui_utils/3dmodel.py`.
It is not a user guide.
Its purpose is to make the current implementation legible, explain why it is shaped the way it is today, and capture the important constraints before this module grows further.

## Current Structure

The viewer currently lives in one large module and contains five distinct concerns:

1. public config and snapshot dataclasses
2. scalar and pose ingestion/runtime state
3. mesh manifest/cache loading and OBJ compile helpers
4. OpenGL resource creation and draw submission
5. camera math, pose normalization, and compatibility aliases

That file shape is workable for a first implementation, but it is already large enough that future changes should preserve clear conceptual boundaries even before any refactor happens.

## Lifecycle

The runtime path is:

```text
discover asset -> resolve or compile cache -> load manifest -> build config -> build viewer
-> bind readers -> consume streams -> render viewport -> close OpenGL resources
```

The important invariants are:

- the runtime does not parse OBJ on the hot path
- `ModelViewerConfig` is the declarative source of truth for viewer behavior
- `ModelViewer` owns live runtime state
- the manifest is the source of truth for mesh body names
- scalar telemetry is explicit and per-body
- pose validation is conservative: keep the last good matrix rather than rendering bad attitude data

## Module Map

### Public Surface

The supported primary API is:

- `ModelBodyBinding`
- `ModelViewerConfig`
- `ModelViewer`
- `ModelBodySnapshot`
- `ModelPoseSnapshot`
- `build_pose_batch_from_matrices(...)`
- `build_pose_batch_from_direction_vectors(...)`
- `compile_obj_to_cache(...)`
- `resolve_compiled_obj_assets(...)`

### Compatibility Surface

The module still exports:

- `RocketPartBinding`
- `RocketPartSnapshot`
- `RocketViewer`
- `RocketViewerConfig`

These are compatibility aliases only.
They should not be used as the naming model for new code or new docs.

## Stream and State Model

### Body State

Each `ModelBodyBinding` maps one mesh body to one scalar stream.

`_BodyRuntime` stores:

- last accepted timestamp
- last accepted scalar value
- currently resolved display color

The current behavior on timestamp restart is reset-and-replace.
That matches the graph stack’s bias toward preserving coherent time rather than attempting to merge restarted streams into old state.

### Pose State

`_PoseRuntime` stores only the latest accepted pose snapshot.

The canonical pose contract is `(13, rows)`:

- timestamp
- position `x/y/z`
- row-major 3x3 rotation matrix

Legacy `(10, rows)` orientation-only input is still accepted.
Position is retained in the snapshot but is not applied to the rendered transform in the current viewer.

### Reader Contract

The viewer follows the same reader contract used elsewhere in `gui_utils`:

- expose `shape`
- expose `dtype`
- expose either `read()` or `look()/increment()`

That consistency is important.
Do not introduce a viewer-only reader abstraction unless the rest of the GUI stack is moving the same way.

## Render Path

`ModelViewer.render()` is intentionally thin.
It delegates the heavy work to `_OpenGLMeshRenderer`.

The renderer currently owns:

- shader compilation
- VBO / VAO / EBO creation
- framebuffer creation and resize
- per-frame projection/view/model matrix assembly
- one draw submission per manifest part

OpenGL resources are lazy-created on first render and must be released through `ModelViewer.close()`.

## Current Performance Constraints

These are the main current risks and scaling limits.
They should be understood before adding more capability.

### 1. Cache Generation Is Not Vertex-Shared

The OBJ compile path expands triangles into fully duplicated vertex and normal arrays and then emits:

```text
indices = arange(vertex_count)
```

Implications:

- large assets produce very large `.npz` caches
- memory bandwidth and GPU upload cost are higher than necessary
- the cache format is simpler than it should be for production-scale meshes

This is the highest-value future performance fix.
If the viewer needs to handle materially larger CAD assets, start here.

### 2. Render Cost Scales With Manifest Part Count

The current render loop:

- looks up uniforms every frame
- iterates through every manifest part
- submits one draw call per part

This is acceptable for the current operator-desk use case, but it is not a scene-engine architecture.
If frame time becomes unstable with highly fragmented CAD, the next likely work items are:

- cache uniform locations once
- reduce per-part state churn
- consider batched body ranges or material groups where that does not break telemetry coloring requirements

### 3. One File Owns Too Many Responsibilities

The module is now large enough that maintenance cost is rising faster than feature complexity.
The preferred future split is:

1. `model_types.py`
   public config and snapshot dataclasses
2. `model_streams.py`
   scalar/pose normalization and runtime state
3. `model_assets.py`
   manifest loading, OBJ compile, cache resolution
4. `model_render.py`
   OpenGL resources and draw path
5. `model_viewer.py`
   user-facing runtime wrapper

That split order is recommended because it separates the least coupled code first.

## Configurability Policy

The viewer should remain explicit rather than “smart.”

Keep these principles:

- explicit low/high value ranges instead of auto-ranging body colors
- explicit mesh-part binding names instead of fuzzy matching
- explicit model alignment matrices instead of hidden heuristics
- explicit fallback body styling instead of per-frame inference

This code lives closer to an operator system than a consumer graphics layer.
Predictability matters more than convenience defaults.

## Documentation and Naming Policy

When updating docs or examples:

- teach `Model*` names first
- mention `Rocket*` aliases only as backward compatibility
- keep the public guide focused on usage
- keep architectural and performance detail on this maintainer page

Avoid split-brain documentation where examples use legacy names but the guide teaches generic ones.

## Review Summary

The current viewer is a credible v1 for an ImGui operator desk:

- runtime contracts are explicit
- the bind/consume/render lifecycle is understandable
- the pose normalization path is defensively implemented
- config export/reconstruct fits the rest of `gui_utils`

The main issues are not correctness blockers.
They are maintainability and scale risks:

- oversized cache generation
- per-part draw submission cost
- a single file owning too many subsystems

Those should be the lens for future work.
