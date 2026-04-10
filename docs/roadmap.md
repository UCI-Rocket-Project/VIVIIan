# Roadmap

This page is intentionally short.
It exists to separate **implemented code** from **planned system shape**.

## Implemented Today

The current top-level repo has working code in:

- `gui_utils`
- `simulation_utils`
- `deviceinterface`
- `tests/gui_runnables/signal_graph_lab.py`
- `tests/gui_runnables/rocket_viewer_lab.py`

Those parts are documented in detail because they exist, run, and are covered by tests.

## Planned But Not Implemented Yet

These areas are still incomplete relative to the architecture:

- generic connector abstractions
- backend-owned storage and archival
- orchestrated deployment topology
- the full backend processing DAG and republishing flow
- the end-to-end multi-unit system described in [Architecture](architecture.md)

That means there is **not yet** a documented finished API for:

- the full connector layer
- durable backend storage
- orchestrated multi-process launch
- the complete telemetry/control runtime

## Intended Direction

The current primitives suggest a likely future shape:

- connectors publish strict versioned numeric payloads
- a backend runtime handles ingestion, processing, storage, and republishing
- ImGui desks consume snapshots and time-series batches through the current graph, gauge, button, and model-viewer layers
- frontends emit one-way typed commands directly to device interfaces
- deterministic simulators remain available for development, test, and operator-desk bring-up

That direction is now specified formally in [Architecture](architecture.md), not just implied by the existing modules.

## Documentation Policy For Now

Until the missing subsystems exist in code, this docs site will:

- document working modules in detail
- document the target architecture explicitly
- avoid inventing concrete runtime APIs that are not implemented yet
