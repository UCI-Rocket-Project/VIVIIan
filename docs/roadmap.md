# Roadmap

This page is intentionally short.
It exists to separate **implemented code** from **planned system shape**.

## Implemented Today

The current top-level repo has working code in:

- `gui_utils`
- `simulation_utils`
- `tests/gui_runnables/signal_graph_lab.py`

Those parts are documented in detail because they exist, run, and are covered by tests.

## Planned But Not Implemented Yet

These top-level areas are still stubs:

- `main.py`
- `configure.py`
- `connector_utils/connectors.py`
- `datastorage_utils/database.py`

That means there is **not yet** a documented top-level API for:

- telemetry connectors
- storage or archival
- the full application shell
- the end-to-end ground-station runtime

## Intended Direction

The current primitives suggest a likely future shape:

- connectors publish typed numeric telemetry frames
- a backend runtime handles ingestion, storage, and control logic
- ImGui desks consume snapshots and time-series batches through the current graph and button layer
- deterministic simulators remain available for development, test, and operator-desk bring-up

But that is direction, not current implementation.

## Documentation Policy For Now

Until the missing subsystems exist in code, this docs site will:

- document working modules in detail
- mention stubbed areas only as short roadmap notes
- avoid inventing future API contracts prematurely
