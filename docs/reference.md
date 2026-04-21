# Reference

Use this page as your implementation index: find the right module first, then implement in the correct layer.

## Build Tasks -> Where To Work

### I need to build an operator app like UCIRPLGUI

- Start with [Build With VIVIIan](build-with-viviian.md)
- Then read [Backend](build-backend.md), [Frontend](build-frontend.md), and [Device Interfaces](build-device-interfaces.md) as needed
- Use `apps/ucirplgui` as your baseline layout
- Keep reusable logic in `packages/viviian_core/src/viviian`

### I need to move telemetry between processes/machines

- Start with [Telemetry](build-telemetry.md)
- Read [Connectors](connectors.md)
- Implement in `packages/viviian_core/src/viviian/connector_utils`
- Respect latest-only semantics and schema/shape contracts

### I need orchestration or runtime wiring

- Start with [Backend](build-backend.md) if you are building an app runtime
- Read [Orchestrator](orchestrator.md)
- Use pipeline/runtime composition patterns from `packages/pythusa`

### I need device integration

- Start with [Device Interfaces](build-device-interfaces.md)
- Read [Device Interface Reference](deviceinterface.md)
- Implement adapters under app-local `device_interfaces` and shared interfaces in `viviian/deviceinterface`

### I need frontend runtime and dashboard behavior

- Start with [Frontend](build-frontend.md)
- Read [Frontend Reference](frontend.md)
- For UCIRPL-style dashboards, inspect:
  - `apps/ucirplgui/src/ucirplgui/frontend/frontend.py`
  - `apps/ucirplgui/src/ucirplgui/components/dashboard.py`

### I need storage/persistence work

- Read [Data Storage](datastorage.md)
- Implement via `packages/viviian_core/src/viviian/datastorage_utils`

### I need examples and baseline patterns

- Read [Examples](examples.md)
- Mirror structure from `apps/ucirplgui` and adapt stream config, pipeline, and UI surfaces

## Repository Map

### Packages

- `packages/viviian_core/src/viviian`
  - `connector_utils`: transport contracts and runtime connectors
  - `datastorage_utils`: persistence/database utilities
  - `deviceinterface`: device-facing abstractions
  - `frontend`: frontend runtime and component integration
  - `gui_utils`: common UI/graphics primitives
  - `orchestrator`: orchestration and runtime coordination
  - `simulation_utils`: simulation helpers for non-hardware runs
- `packages/pythusa`: pipeline runtime and worker/process machinery

### Apps

- `apps/ucirplgui`
  - `scripts`: launch entrypoints
  - `src/ucirplgui`: app runtime, backend, frontend, components, config
  - `src/device_simulations`: simulation-side device producers
  - `tests`: app runtime and integration tests

### Docs

- `docs/`: architecture, how-to guidance, and API/runtime reference

## Engineering Rules

- Put reusable code in `packages/`; keep app-specific composition in `apps/`.
- Treat stream IDs, schemas, and shapes as explicit contracts.
- Prefer deterministic startup paths via scripts under `apps/<app>/scripts`.
- Add tests next to the layer you modify (`apps/<app>/tests` or package tests).
