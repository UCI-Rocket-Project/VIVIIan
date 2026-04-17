# Repository Guidelines

## Project Structure & Module Organization

The active root libraries are `gui_utils/` for ImGui widgets, `simulation_utils/` for deterministic signal generators, `connector_utils/` for Arrow transport helpers, `datastorage_utils/` for Parquet persistence, `deviceinterface/` for hardware-boundary publishing, and `orchestrator/` for the `pythusa.Pipeline`-based composition layer. `tests/` mirrors those modules with unit coverage, while `tests/gui_runnables/` holds manual ImGui lab apps such as `signal_graph_lab.py` and `rocket_viewer_lab.py`.

`docs/` is the MkDocs source tree. `/site`, `imgui.ini`, `*.obj`, and `gui_assets/compiled/` are generated or local artifacts; avoid committing changes there unless the task explicitly requires it. `clients/` and `benchmarking/` contain examples and performance scripts. `pythusa/` and `rocket2-webservice-gui/` are semi-independent subprojects with their own tooling and should be treated as separate scopes unless a change intentionally spans them.

## Build, Test, and Development Commands

`python3 -m venv .venv && source .venv/bin/activate` creates the root environment.

`python -m pip install -r requirements.txt numpy imgui glfw PyOpenGL mkdocs` installs the core dependencies plus the optional GUI and docs tools used in this repo.

`PYTHONPATH=src python -m unittest tests.test_gui_utils tests.test_simulation_utils tests.test_signal_graph_lab tests.test_rocket_viewer_lab tests.test_3dmodel tests.test_gauge_lab tests.test_connector_utils tests.test_datastorage_utils tests.test_deviceinterface_utils tests.test_orchestrator` runs the root regression suite.

`python tests/gui_runnables/signal_graph_lab.py` and `python tests/gui_runnables/rocket_viewer_lab.py` launch the manual GUI labs.

`mkdocs serve` previews documentation locally; `mkdocs build` rebuilds the static docs output.

## Coding Style & Naming Conventions

Use 4-space indentation, explicit type hints, and `from __future__ import annotations` in new Python modules where practical. Follow existing naming patterns: `snake_case` for modules and functions, `PascalCase` for classes, and dataclasses for configuration-style records.

The root project does not define a formatter or linter. Match the surrounding import grouping, line wrapping, and docstring style instead of introducing a new tool mid-change.

## Testing Guidelines

Root tests use `unittest`, not `pytest`. Name files `tests/test_*.py`, keep assertions in focused `unittest.TestCase` classes, and add coverage next to the subsystem you changed. For GUI-visible behavior, run the relevant lab in `tests/gui_runnables/` and note any manual verification in your PR.

## Commit & Pull Request Guidelines

Recent commits use short, imperative subjects such as `Added an artificial signal generator` and `Updated the API implementation`. Keep commit titles concise, specific, and action-oriented.

Pull requests should describe the affected subsystem, list the commands you ran, link any related issue, and include screenshots or short clips for GUI changes. Call out follow-up work or known gaps directly in the PR description.
