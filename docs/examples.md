# Examples

The current top-level examples are:

```bash
python tests/gui_runnables/signal_graph_lab.py
python tests/gui_runnables/rocket_viewer_lab.py
```

The signal desk exercises graphs, buttons, and scalar signal simulators.
The model viewer exercises the new 3D mesh cache runtime, per-body gradients,
default-body fading, and timestamped pose matrices.

## Model Viewer Lab

The 3D viewer lab is the current end-to-end example for the viewer stack:

- `gui_utils.compile_obj_to_cache`
- `gui_utils.ModelViewerConfig`
- `gui_utils.ModelViewer`
- `simulation_utils.RotationMatrixSignalGenerator`

Use [3D Viewer](3d-viewer.md) for the public API and runtime contracts.
Use [3D Viewer Maintainer Notes](3d-viewer-maintainer.md) for internal architecture and performance limits.
The runnable name remains `rocket_viewer_lab.py` for compatibility, but the primary API it exercises is the generic `Model*` surface.

Run it from the repo root:

```bash
python tests/gui_runnables/rocket_viewer_lab.py
```

Before running it, place exactly one `.obj` file in:

```text
gui_assets/cad/
```

What it demonstrates:

- discovery of exactly one CAD asset under `gui_assets/cad/`
- a compile step that produces or reuses a mesh cache and manifest under `gui_assets/compiled/`
- named mesh bodies with explicit low/high color mappings, including `g_Body1:20` and `g_Body1:21`
- one live pose stream using row-major 3x3 matrices
- default-body styling for every unbound mesh part with a shared transparency
- a pure ImGui/PyOpenGL viewer window with orbit, pan, and zoom
- visible model rotation within the first few seconds of runtime

If you only need the graph/button/signal path, use the signal lab below.
This page documents the signal lab in the same spirit as `pythusa`’s showcase docs:

- what it demonstrates
- how the data flows
- what the operator controls do
- the exact commands to run it

## Signal Graph Lab

The signal lab is a small standalone ImGui application that exercises the current repo’s working primitives together:

- `gui_utils.SensorGraph`
- `gui_utils.ToggleButton`
- `gui_utils.MomentaryButton`
- `simulation_utils.random_sparse_spectrum_generator`

It is intentionally simple, but it is the best current top-level demonstration of the repo’s direction.

## What It Demonstrates

- **ImGui-native controls**: the desk is built entirely from the current top-level button and graph classes.
- **Deterministic telemetry-like signals**: each signal is a repeating spectral construction, not an ad hoc random walk.
- **Timestamped graph ingestion**: the graph consumes explicit `(2, rows)` batches.
- **Shared graph clock windowing**: disabled signals freeze immediately, then age out once newer timestamps arrive from live signals.
- **One-shot signal bank creation**: the operator can create one seeded 8-signal bank for the session and then selectively feed those signals into the graph.

## Architecture

The example has four moving parts:

1. `SignalGraphLabApp`
2. `BufferedFrameReader`
3. one `SensorGraph`
4. eight `SpectralSignalGenerator`s

The flow is:

```text
SpectralSignalGenerator -> BufferedFrameReader -> SensorGraph.consume() -> SensorGraph.render()
```

The button layer sits beside that path:

- one `MomentaryButton` generates the 8-signal bank
- eight `ToggleButton`s decide which generators currently feed the graph

## Controls

### Generate 8 Random Signals

This is a one-shot `MomentaryButton`.
On the first click, the app:

- creates 8 seeded `SpectralSignalGenerator`s
- stores the session seed
- enables the eight signal toggles
- disables the generate button for the rest of the session

### `signal_1` to `signal_8`

Each of these is a `ToggleButton`.

When a signal toggle is ON:

- the generator advances
- its new batch is primed into the reader
- the graph consumes the batch

When a signal toggle is OFF:

- the generator still advances internally
- no new batch is fed to the graph
- the existing trace freezes in place
- that frozen trace ages out once other live series advance the graph clock far enough

This is important.
The example does **not** pause the simulated signal’s notion of time when you toggle it off.
That is the behavior that keeps re-enabled signals aligned with current graph time.

## Run Commands

From the repo root:

```bash
python tests/gui_runnables/signal_graph_lab.py
```

With an explicit seed:

```bash
python tests/gui_runnables/signal_graph_lab.py --seed 42
```

If you do not already have the desktop dependencies:

```bash
python -m pip install glfw PyOpenGL
```

## What You Should See

On launch:

- one window titled `Signal Graph Lab`
- one graph
- one generate button
- eight disabled signal toggles

After clicking generate:

- the session seed appears in the header
- the generate button becomes disabled
- the eight signal toggles become active

After enabling one or more signals:

- their traces appear in the graph
- turning one OFF freezes it immediately
- keeping other signals ON eventually pushes the frozen trace out of the graph window

## Relevant Code Path

The example lives in:

- `tests/gui_runnables/signal_graph_lab.py`

The most important runtime methods are:

- `SignalGraphLabApp.generate_signal_bank()`
- `SignalGraphLabApp.advance()`
- `BufferedFrameReader.read()`
- `SensorGraph.consume()`

If you want to understand how the graph window behaves under active and inactive signals, start there.

## Useful Companion Commands

Run the regression tests for the example and the shared graph behavior:

```bash
python -m unittest tests.test_gui_utils tests.test_signal_graph_lab
```

That covers:

- bank generation
- toggle behavior
- resume-at-current-time behavior
- stale-series expiry from the graph window
