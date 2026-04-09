# Getting Started

This page assumes you are in the repo root.
The current top-level repo is not packaged as an installable application yet, so the working pattern today is:

- create a virtual environment
- install the few Python dependencies directly into that environment
- run tests and examples from the repo root

## Environment Setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install numpy imgui
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

The useful top-level code today is:

- `gui_utils/buttons.py`
- `gui_utils/graphs.py`
- `simulation_utils/simulators.py`
- `tests/gui_runnables/signal_graph_lab.py`

The following are currently empty stubs and should not be treated as active APIs:

- `main.py`
- `configure.py`
- `connector_utils/connectors.py`
- `datastorage_utils/database.py`

## Run The Tests

Run the current working regression suite:

```bash
python -m unittest tests.test_gui_utils tests.test_simulation_utils tests.test_signal_graph_lab
```

If you want a syntax check without executing the tests:

```bash
python -m py_compile \
  gui_utils/buttons.py \
  gui_utils/graphs.py \
  simulation_utils/simulators.py \
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

1. read [GUI Utils](gui-utils.md)
2. read [Simulation Utils](simulation-utils.md)
3. run the signal-lab example from [Examples](examples.md)

That path covers the working operator-desk primitives and the current simulator stack without forcing assumptions about the rest of the planned ground station.
