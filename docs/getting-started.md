# Getting Started

This page assumes you are in the repo root.
The current repo is not packaged as a finished end-to-end application yet, so
the working pattern today is:

- create a virtual environment
- install the dependencies directly into that environment
- run tests and manual examples from the repo root
- treat the architecture doc as the target system model and the runnable modules
  as the currently implemented surface

## Environment Setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
```

### Documentation Dependencies

The docs site uses plain MkDocs:

```bash
pip install mkdocs
```

## Current Repo Surface

The most useful working modules today are:

- `src/viviian/gui_utils/buttons.py`
- `src/viviian/gui_utils/graphs.py`
- `src/viviian/gui_utils/3dmodel.py`
- `src/viviian/simulation_utils/simulators.py`
- `src/viviian/connector_utils/connectors.py`
- `src/viviian/datastorage_utils/database.py`
- `src/viviian/deviceinterface/deviceinterface.py`
- `src/viviian/orchestrator/__init__.py`
- `src/viviian/orchestrator/orchestrator.py`
- `tests/gui_runnables/signal_graph_lab.py`
- `tests/gui_runnables/rocket_viewer_lab.py`

The following areas exist, but should still be treated as incomplete relative
to the architecture document:

- the orchestrator composition layer beyond the current scaffold
- richer processing tool collections
- tighter end-to-end storage integration
- the full multi-deployment runtime described in [Architecture](architecture.md)

### Connectors

The current connector runtime is documented in [Connectors](connectors.md).

The short version is:

- `SendConnector` is the Flight server
- `ReceiveConnector` is the Flight client
- both sides are latest-only
- the runtime uses one long-lived `do_get` stream
- the public receive surface is `receiver.has_batch` plus `receiver.batch`
- `StreamSpec` can also mirror receive-side data into one local appended frame
  through its optional `stream` target
- the exact mirror-frame shape and disconnect behavior are documented in
  [Connectors](connectors.md)

All transport data is sent as `float64`.

### Orchestrator And Local Stream Adaptation

The orchestrator role and the current guidance for local mixed frame sizes are
documented in [Orchestrator](orchestrator.md).

The short version is:

- `orchestrator` extends `pythusa.Pipeline`
- the intended top-level entry is `with VIVIIan as VIVII:`
- it is the composition root for local processing, storage, GUI, and connector
  tool collections
- it owns topology and lifecycle structure, not a second worker model
- any internal topology graph is private orchestration metadata, not a user API
- task-local `pythusa` consumption size is still a local runtime concern
- if one task needs to consume a different local byte window, the current
  endorsed path is to override `frame_nbytes` on that local binding and use
  `look()` / `increment()`
- one side must own regrouping internally or logical frame alignment can be
  lost

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
  tests.test_connector_utils \
  tests.test_datastorage_utils \
  tests.test_deviceinterface_utils \
  tests.test_orchestrator
```

If you want a syntax check without executing the tests:

```bash
python -m py_compile \
  src/viviian/gui_utils/buttons.py \
  src/viviian/gui_utils/graphs.py \
  src/viviian/gui_utils/3dmodel.py \
  src/viviian/simulation_utils/simulators.py \
  src/viviian/connector_utils/connectors.py \
  src/viviian/datastorage_utils/database.py \
  src/viviian/deviceinterface/deviceinterface.py \
  src/viviian/orchestrator/orchestrator.py \
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
Once a signal is disabled, its history freezes and then ages out according to
the graph window once newer timestamps arrive from other live series.

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

## Run The Connector Benchmark

The connector benchmark lives at:

- `benchmarks/connector_throughput_benchmark.py`

Run it from the repo root:

```bash
.venv/bin/python benchmarks/connector_throughput_benchmark.py \
  --graph \
  --graph-out benchmarks/results/connector-heatmaps.png \
  --json-out benchmarks/results/connector-matrix.json \
  --no-show
```

It writes:

- structured JSON results
- throughput and latency heatmaps

## First Things To Read

If you are new to this repo, the most useful sequence is:

1. read [Architecture](architecture.md)
2. read [Orchestrator](orchestrator.md)
3. read [GUI Utils](gui-utils.md), [Simulation Utils](simulation-utils.md), and
   [Connectors](connectors.md)
4. run the signal-lab example from [Examples](examples.md)

That path covers the target architecture first and then the most mature working
modules without overstating what is already implemented.
