# Roadmap

This page is intentionally short.
It exists to separate **implemented code** from **planned system shape**.

## Implemented Today

The current top-level repo has working code in:

- `gui_utils`
- `simulation_utils`
- `connector_utils`
- `datastorage_utils`
- `deviceinterface`
- `frontend`
- `orchestrator`
- `tests/gui_runnables/signal_graph_lab.py`
- `tests/gui_runnables/rocket_viewer_lab.py`
- `tests/gui_runnables/frontend_lab.py`

Those parts are documented in detail because they exist, run, and are covered
by tests.

## Planned But Not Implemented Yet

These areas are still incomplete relative to the architecture:

- richer orchestrator composition helpers beyond the current scaffold
- reusable processing tool collections on top of `pythusa`
- tighter storage integration into orchestrated runtimes
- the end-to-end multi-deployment system described in [Architecture](architecture.md)

That means there is **not yet** a documented finished API for:

- the full connector/runtime story beyond the current latest-only Arrow Flight
  transport
- higher-level processing tool libraries
- orchestrated multi-process or multi-host launch helpers
- the complete telemetry/control runtime

## Intended Direction

The current primitives suggest a likely future shape:

- connectors publish strict versioned numeric payloads
- connectors already support a working latest-only live transport for
  current-state distribution
- `orchestrator` acts as the `pythusa.Pipeline`-based composition root
- local tool collections provide processing, storage, GUI, and simulation
  capabilities
- storage remains explicit and append oriented
- ImGui desks consume snapshots and time-series batches through the current
  graph, gauge, button, and model-viewer layers
- operator tools emit one-way typed commands directly to device interfaces
- deterministic simulators remain available for development, test, and
  operator-desk bring-up

That direction is now specified formally in [Architecture](architecture.md),
not just implied by the existing modules.

## Documentation Policy For Now

Until the missing subsystems exist in code, this docs site will:

- document working modules in detail
- document the target architecture explicitly
- avoid inventing concrete runtime APIs that are not implemented yet
