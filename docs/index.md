# VIVIIan

VIVIIan is a Python-first telemetry and control monorepo for operator tooling, hardware interfaces, and streaming runtime systems.

![VIVIIan architecture overview](assets/vivian_architecture.svg)

## Start Here

- [Build With VIVIIan](build-with-viviian.md): task-driven guide set for running the repo, defining telemetry, building backends, building frontends, and wiring device interfaces.
- [Reference](reference.md): module map and decision guide for where to implement each capability.
- [Architecture](architecture.md): runtime boundaries, data flow, and system contracts.

## What This Repo Gives You

- Reusable libraries in `packages/viviian_core/src/viviian` for frontend/runtime, connectors, storage, and device integration.
- A production app baseline in `apps/ucirplgui` you can run, inspect, and fork into your own deployment.
- In-repo pipeline/runtime support via `packages/pythusa` for deterministic stream processing workflows.

## Working Model

1. Build features in packages.
2. Wire them into an app under `apps/`.
3. Validate behavior with runtime scripts and tests.
4. Document contracts in the reference pages.

## Most Common Build Tasks

- [Run the reference app and choose the right layer](build-with-viviian.md)
- [Define or move telemetry streams](build-telemetry.md)
- [Add backend processing and derived streams](build-backend.md)
- [Build an operator frontend](build-frontend.md)
- [Integrate hardware or simulators](build-device-interfaces.md)
