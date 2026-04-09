# VIVIIan

VIVIIan is an early-stage rocket ground-station codebase.
The working top-level modules today are:

- `gui_utils` for ImGui graph and button primitives
- `gui_utils/3dmodel.py` for the compact OBJ-backed 3D viewer runtime
- `simulation_utils` for deterministic repeating signal simulators in NumPy `rfft` space
- `tests/gui_runnables/signal_graph_lab.py` and `tests/gui_runnables/rocket_viewer_lab.py` for manual end-to-end GUI examples

The larger app shell is not implemented yet.
`main.py`, `connector_utils`, and `datastorage_utils` are still stubs.

## Documentation

MkDocs content lives under `docs/`.

Local docs commands:

```bash
python -m pip install mkdocs
mkdocs serve
mkdocs build
```

## Quick Start

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install numpy imgui
```

Optional desktop dependencies for the GUI example:

```bash
python -m pip install glfw PyOpenGL
```

## Run Tests

```bash
python -m unittest tests.test_3dmodel tests.test_gui_utils tests.test_simulation_utils tests.test_signal_graph_lab tests.test_rocket_viewer_lab
```

## Run The Signal Lab

```bash
python tests/gui_runnables/signal_graph_lab.py
```

The signal-lab runnable opens one ImGui window with:

- one `SensorGraph`
- one one-shot button to create 8 random signals
- eight `signal_1` to `signal_8` toggles that control which signals currently feed the graph

## Run The Rocket Viewer Lab

```bash
python tests/gui_runnables/rocket_viewer_lab.py
```

The rocket-viewer runnable opens one ImGui window with:

- the single `.obj` file discovered under `gui_assets/cad/`
- a compiled mesh cache under `gui_assets/compiled/`
- named rocket parts bound to scalar telemetry streams, including `g_Body1788` and `g_Body1844`
- one live orientation stream built from repeating roll/pitch/yaw signals
- orbit, pan, and zoom controls inside the ImGui-hosted viewport

The viewer lab expects exactly one CAD file in:

```text
gui_assets/cad/
```

## Current Status

Implemented now:

- graph configuration, rendering, TOML export, and reconstruction
- generic state buttons for ImGui desks
- deterministic signal generation from sparse `rfft` coefficients
- a compact OBJ-backed 3D viewer runtime and example
- working manual GUI examples and regression tests

Not implemented yet:

- connectors
- storage
- the full ground-station app shell
