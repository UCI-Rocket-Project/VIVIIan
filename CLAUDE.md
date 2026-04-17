# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VIVIIan is a rocket ground-station library providing ImGui-based operator-desk primitives for real-time telemetry visualization. All library code lives under `src/viviian/` and is importable as `from viviian.<subpackage> import <name>` after `pip install -e .`.

Two semi-independent subsystems exist as subdirectories:
- `pythusa/` — shared-memory multiprocess DSP pipeline runtime (separate package)
- `rocket2-webservice-gui/` — Docker-based Flask + React telemetry web UI

## Commands

### Core Library (root)

```bash
# Install (requires Python 3.12+)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui]"  # adds imgui glfw PyOpenGL for interactive labs

# Run all tests (no PYTHONPATH needed)
python -m unittest tests.test_gui_utils tests.test_simulation_utils tests.test_signal_graph_lab tests.test_rocket_viewer_lab tests.test_3dmodel tests.test_gauge_lab tests.test_connector_utils tests.test_datastorage_utils tests.test_deviceinterface_utils

# Run a single test class
python -m unittest tests.test_gui_utils.SensorGraphTests

# Run a single test method
python -m unittest tests.test_gui_utils.SensorGraphTests.test_incremental_mode_expiry

# Run interactive demos (requires glfw + PyOpenGL)
python tests/gui_runnables/signal_graph_lab.py [--seed SEED]
python tests/gui_runnables/rocket_viewer_lab.py
python tests/gui_runnables/gauge_lab.py

# Docs
pip install mkdocs
mkdocs serve   # http://localhost:8000
mkdocs build
```

### PYTHUSA (`pythusa/`)

```bash
cd pythusa
pip install -e ".[test,examples]"
python -m pytest -q
```

### Rocket2 Web UI (`rocket2-webservice-gui/`)

```bash
cd rocket2-webservice-gui
make build     # Build Docker images
make run_dev   # Start dev environment with fake_rocket
make test      # Run Playwright e2e tests
```

## Architecture

### Data Contract

All telemetry data moves as NumPy arrays with shape `(2, N)` — row 0 is timestamps, row 1 is values. dtype is `float32` or `float64`. This fixed-shape frame contract is enforced by `gui_utils/_streaming.py` (`validate_numeric_reader`, `normalize_numeric_batch`) before any widget consumes data.

### viviian.gui_utils

ImGui operator-desk widgets. Each widget is self-contained and TOML-persistent via `gui_utils/configure.py`.

- **`graphs.py`** — `SensorGraph` / `GraphSeries`: multi-series time graph with windowed history and explicit timestamp handling. Consumers push `(timestamp, value)` batches; the widget manages display history and window expiry.
- **`buttons.py`** — `StateButton`, `ToggleButton`, `MomentaryButton`: operator workflow state machines with gate/interlock logic.
- **`gauges.py`** — `SensorGauge`, `AnalogNeedleGauge`, `LedBarGauge`: single-value displays with damped animation and color-range mapping.
- **`3dmodel.py`** — `ModelViewer` / `RocketViewer`: OBJ-backed 3D viewer (~2000 lines). Handles vertex/mesh caching in `gui_assets/compiled/`, per-body scalar bindings for telemetry coloring, pose-driven orientation from rotation matrix streams, and camera orbit/pan/zoom.
- **`_streaming.py`** — Reader validation and batch normalization (the boundary layer).
- **`configure.py`** — TOML read/write helpers. Persistence files carry `format_version` and `kind` fields.

### viviian.simulation_utils

Deterministic, seeded signal generation for testing and demos.

- **`SpectralSignalGenerator`** — RFFT-based signal synthesis from `SpectralSignalConfig` (sparse frequency components). Produces fixed-shape `(2, N)` frames.
- **`RotationMatrixSignalGenerator`** — generates orientation streams consumed by `RocketViewer`.
- **`configure.py`** — TOML persistence for `SpectralSignalConfig`/`SpectralTerm`.

### tests/gui_runnables/

Interactive ImGui demos that double as integration harnesses. `_support.py` provides the shared OpenGL/GLFW setup. The lab apps (`SignalGraphLabApp`, `RocketViewerLabApp`) wire `simulation_utils` generators directly into `gui_utils` widgets.

### pythusa/ (separate package)

High-performance shared-memory DSP pipeline runtime. Public API: `Pipeline`, `Manager`, `Worker`, ring buffer primitives. Internal hot path uses `SharedRingBuffer` with zero-copy reads. Requires Python ≥ 3.12. Has its own CI (pytest on Ubuntu + Windows).

### Asset Pipeline

OBJ mesh files live in `gui_assets/cad/` (gitignored). Parsed meshes are cached to `gui_assets/compiled/` (also gitignored). `ModelViewer` checks the compiled cache before parsing OBJ on startup.

## Key Design Decisions

- **No wall-clock assumptions** — graphs consume explicit timestamps from the data stream, not `time.now()`.
- **TOML persistence** — widget state (graph window size, button states, gauge ranges, 3D body bindings) is saved/restored via TOML, not a database.
- **unittest, not pytest** — the root test suite uses `unittest`. Only `pythusa/` uses pytest.
- **No linting config** — no ruff/black/mypy/flake8 at root level.
