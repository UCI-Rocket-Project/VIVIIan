# Getting Started

This page assumes you are in the repo root.
The current repo is not packaged as a finished end-to-end application yet, so the working pattern today is:

- create a virtual environment
- install the dependencies directly into that environment
- run tests and manual examples from the repo root
- treat the architecture doc as the target system model and the runnable modules as the currently implemented surface

## Environment Setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt numpy imgui
```

### Optional Desktop Dependencies

The manual ImGui example also needs GLFW and OpenGL bindings:

```bash
python -m pip install glfw PyOpenGL
```

### Documentation Dependencies

The docs site uses plain MkDocs:

```bash
python -m pip install mkdocs
```

## Current Repo Surface

The most useful working modules today are:

- `src/gui_utils/buttons.py`
- `src/gui_utils/graphs.py`
- `src/gui_utils/3dmodel.py`
- `src/simulation_utils/simulators.py`
- `src/deviceinterface/deviceinterface.py`
- `tests/gui_runnables/signal_graph_lab.py`
- `tests/gui_runnables/rocket_viewer_lab.py`

The following areas exist, but should still be treated as incomplete or placeholder-level relative to the architecture document:

- `src/connector_utils/connectors.py`
- `src/datastorage_utils/database.py`
- `src/orchestrator/orchestrator.py`
- the backend processing and deployment topology described in [Architecture](architecture.md)

## Run The Tests

Run the current working regression suite:

```bash
python -m unittest \
  tests.test_gui_utils \
  tests.test_simulation_utils \
  tests.test_signal_graph_lab \
  tests.test_rocket_viewer_lab \
  tests.test_3dmodel \
  tests.test_gauge_lab \
  tests.test_deviceinterface_utils
```

If you want a syntax check without executing the tests:

```bash
python -m py_compile \
  src/gui_utils/buttons.py \
  src/gui_utils/graphs.py \
  src/gui_utils/3dmodel.py \
  src/simulation_utils/simulators.py \
  src/deviceinterface/deviceinterface.py \
  tests/gui_runnables/signal_graph_lab.py
```

## Run The Manual GUI Example

The current operator-desk example is:

```bash
python tests/gui_runnables/signal_graph_lab.py
```

It opens one window with:

- one graph
- one one-shot button that creates an 8-signal bank
- eight toggle buttons named `signal_1` through `signal_8`

Once a signal is enabled, the app feeds new batches into the graph.
Once a signal is disabled, its history freezes and then ages out according to the graph window once newer timestamps arrive from other live series.

You can also override the base seed:

```bash
python tests/gui_runnables/signal_graph_lab.py --seed 42
```

## Run The Docs Locally

Serve the docs site:

```bash
mkdocs serve
```

Build the static site:

```bash
mkdocs build
```

MkDocs reads the root `mkdocs.yml` and the pages under `docs/`.

## First Things To Read

If you are new to this repo, the most useful sequence is:

1. read [Architecture](architecture.md)
2. read [GUI Utils](gui-utils.md) and [Simulation Utils](simulation-utils.md)
3. run the signal-lab example from [Examples](examples.md)

That path covers the target architecture first and then the most mature working modules without overstating what is already implemented.
