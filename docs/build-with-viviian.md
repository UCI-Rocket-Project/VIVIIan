# Build With VIVIIan

This section is the task-oriented path through the repo.
Use it when you are trying to ship a feature, not when you need exhaustive API
details.

The reference pages remain the source of record for module-level behavior.
The pages in this section are the shortest path from "I need to build X" to
"I know which files, contracts, and runtime loops I have to touch."

## Quick Start

### Environment

- Python 3.12+
- macOS or Linux shell

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/pythusa" -e ".[gui]"
```

Install `packages/pythusa` first so `from pythusa import ...` resolves to the in-repo runtime, not a missing optional extra.

### Run a working desk

From repo root:

```bash
python tests/gui_runnables/signal_graph_lab.py
python tests/gui_runnables/frontend_lab.py
python tests/gui_runnables/rocket_viewer_lab.py
```

Expected outcome:

- the signal lab renders graphs and buttons against simulated spectra
- the frontend lab runs the shared frontend runtime
- the model viewer renders the checked-in OBJ under `packages/viviian_core/src/gui_assets/cad/`

## Repo Mental Model

| Layer | What it owns | Typical files |
| --- | --- | --- |
| Telemetry contracts | stream IDs, Arrow schemas, port assignments, batch shapes | `viviian.connector_utils` |
| Device interfaces | schema, batching, and transport into a sender | `viviian.deviceinterface` |
| Backend | latest-batch ingestion, derived streams, persistence | `viviian.orchestrator`, `viviian.datastorage_utils` |
| Frontend | widgets, readers, output-state snapshots, GLFW/headless runtime | `viviian.frontend`, `viviian.gui_utils` |
| Reusable primitives | connectors, frontend runtime, shared widgets, storage helpers | `packages/viviian_core/src/viviian/` |
| Local runtime substrate | in-process pipeline and shared-memory helpers | `packages/pythusa/` |

Use `packages/` when the behavior is reusable across apps.
Use `apps/` when the behavior is deployment-specific.

## Most Common Tasks

| I need to... | Start here | Then use the reference for... |
| --- | --- | --- |
| define a stream and move batches between processes | [Telemetry](build-telemetry.md) | [Connectors](connectors.md) |
| derive new dashboard-facing values | [Backend](build-backend.md) | [Orchestrator](orchestrator.md), [Connectors](connectors.md) |
| add a graph, gauge, or control | [Frontend](build-frontend.md) | [Frontend](frontend.md), [GUI Utils](gui-utils.md) |
| integrate a board, socket feed, or simulator | [Device Interfaces](build-device-interfaces.md) | [Device Interface](deviceinterface.md), [Connectors](connectors.md) |
| see a working operator desk | [Examples](examples.md) | [GUI Utils](gui-utils.md), [3D Viewer](3d-viewer.md) |

## Working Examples

- `tests/gui_runnables/signal_graph_lab.py`: graphs, buttons, and spectral simulators
- `tests/gui_runnables/frontend_lab.py`: shared frontend runtime
- `tests/gui_runnables/rocket_viewer_lab.py`: OBJ compile and model viewer
- `tests/gui_runnables/gauge_lab.py`: analog and LED gauges
- `packages/viviian_core/src/viviian/`: reusable primitives
- `apps/GUI2.1/`: local GSE scripts that sit beside those primitives

## Recommended Workflow

When you add a feature, work in this order unless there is a strong reason not
to:

1. Define the telemetry contract in app config.
2. Decide whether the source belongs in a device interface or the backend.
3. Publish the derived stream the frontend actually needs.
4. Bind that stream into widgets or controls.
5. Add focused tests for the modified layer.
6. Run the app entrypoint that exercises the full path.

This order prevents a common failure mode in telemetry systems: building the UI
first, then discovering later that stream IDs, shapes, or update semantics are
not stable enough to support it cleanly.

## Local Validation

### Runtime

```bash
python tests/gui_runnables/frontend_lab.py
```

### Tests

```bash
python -m unittest tests.test_frontend tests.test_gui_utils tests.test_connector_utils
```

### Docs

```bash
mkdocs serve
```

## Read These Next

- [Telemetry](build-telemetry.md) for stream contracts and sender/receiver wiring
- [Backend](build-backend.md) for derived stream runtimes
- [Frontend](build-frontend.md) for dashboards and output-state bindings
- [Device Interfaces](build-device-interfaces.md) for board and simulator integration
- [Reference](reference.md) when you need to know exactly where code belongs
