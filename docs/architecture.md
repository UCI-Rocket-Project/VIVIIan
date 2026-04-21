# Architecture

VIVIIan uses a simple rule: **fast local runtime inside an orchestrator, explicit Arrow contracts between units**.

## Core Units

- `deviceinterface`: owns hardware semantics and boundary normalization.
- `orchestrator`: deployment composition root built around `pythusa` runtime.
- `connector_utils`: strict typed transport (`SendConnector`/`ReceiveConnector`).
- `gui_utils`: operator visualization and controls.
- `datastorage_utils`: append-oriented archival and replay support.

## Runtime Boundaries

- Inside a deployment: `pythusa` stream execution and processing DAG.
- Across deployments/processes: Arrow schemas with explicit stream contracts.

## Why This Shape

- Keeps hot-path processing efficient.
- Prevents hidden coupling between apps and transport.
- Supports deterministic rebuild of structural topology.

For concrete paths and ownership, see [Repository Map](reference.md).
