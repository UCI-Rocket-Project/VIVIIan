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
pip install -e .[gui]
```

### Run the reference app

From repo root:

```bash
python apps/ucirplgui/scripts/run_all.py
```

Expected outcome:

- device-interface processes publish raw board telemetry
- backend runtime derives frontend-facing streams
- frontend window renders the operator dashboard
- device-link status files update under `apps/ucirplgui/data/device_link/`

## Repo Mental Model

| Layer | What it owns | Typical files |
| --- | --- | --- |
| Telemetry contracts | stream IDs, Arrow schemas, port assignments, batch shapes | `apps/<app>/src/<app>/config.py` |
| Device interfaces | sockets, simulator links, protocol decode/encode, command echo | `apps/<app>/src/<app>/device_interfaces/` |
| Backend | latest-batch ingestion, derived streams, persistence, process-local coordination | `apps/<app>/src/<app>/backend/` |
| Frontend | widgets, readers, output-state snapshots, GLFW/headless runtime | `apps/<app>/src/<app>/frontend/`, `apps/<app>/src/<app>/components/` |
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
| build an app like UCIRPLGUI | this page, then [Backend](build-backend.md) and [Frontend](build-frontend.md) | [Reference](reference.md) |

## Reference App Layout

If you are building a new app, the cleanest starting point is the current
UCIRPLGUI layout:

- `apps/ucirplgui/scripts/run_all.py`: starts the end-to-end app
- `apps/ucirplgui/scripts/run_backend.py`: backend-only entrypoint
- `apps/ucirplgui/scripts/run_frontend.py`: frontend-only entrypoint
- `apps/ucirplgui/src/ucirplgui/config.py`: stream IDs, schemas, ports, UI constants
- `apps/ucirplgui/src/ucirplgui/device_interfaces/device_interfaces.py`: board-facing runtime
- `apps/ucirplgui/src/ucirplgui/backend/pipeline.py`: raw -> derived stream processing
- `apps/ucirplgui/src/ucirplgui/frontend/frontend.py`: connector reads -> widget readers -> frontend task
- `apps/ucirplgui/src/ucirplgui/components/dashboard.py`: actual dashboard composition
- `apps/ucirplgui/tests/`: app-level regression tests

That split is deliberate:

- config defines contracts
- device interfaces normalize hardware boundaries
- backend derives data products
- frontend renders and emits operator state
- scripts provide deterministic launch paths

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
python apps/ucirplgui/scripts/run_all.py
```

### App tests

```bash
PYTHONPATH="packages/viviian_core/src:apps/ucirplgui/src" \
python -m unittest apps.ucirplgui.tests.test_dashboard_runtime
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
