# VIVIIan Docs

VIVIIan is a Python-first architecture for hardware-agnostic telemetry and
control systems.
The architecture centers on an explicit `deviceinterface` boundary, a
`pythusa`-powered `orchestrator` composition root, and local tool collections
for processing, storage, GUI, simulation, and transport concerns.

The repo is an in-progress implementation of that architecture.
The strongest working code today is concentrated in a few areas:

- `gui_utils` for ImGui-native operator-desk primitives
- `simulation_utils` for deterministic, repeating telemetry-like signals
  defined in NumPy `rfft` space
- `deviceinterface` for an early Arrow-based streaming boundary
- `connector_utils` for a latest-only Arrow Flight connector runtime
  with an optional receive-side appended mirror stream
- `datastorage_utils` for append-only Parquet persistence helpers
- `orchestrator` for the in-progress `Pipeline`-subclass composition layer

The full orchestrator/tool-collection runtime described in
[Architecture](architecture.md) is still being built, so the docs separate
target architecture from current code carefully.

## Start Here

- [Architecture](architecture.md)
  The target system model, runtime boundaries, and modular composition rules.
- [Getting Started](getting-started.md)
  Local environment setup, runnable commands, and the current repo surface.
- [Orchestrator](orchestrator.md)
  The intended `Pipeline`-subclass composition root, its private topology
  bookkeeping role, and the boundary between structural stream wiring and local
  runtime adaptation.
- [Connectors](connectors.md)
  The current latest-only Flight connector runtime, read/write semantics, and
  optional local mirror-stream behavior.
- [GUI Utils](gui-utils.md)
  `SensorGraph`, `GraphSeries`, buttons, and gauges for operator desks.
- [3D Viewer](3d-viewer.md)
  The public guide for the OBJ-backed model viewer and its stream contracts.
- [Simulation Utils](simulation-utils.md)
  Sparse spectral signal configs, exact repeating cycles, and seeded helpers.

## What Exists Now

The current codebase can already do a few concrete things:

- render multi-series time graphs with explicit timestamped numeric input
- render generic state buttons for operator workflows
- render OBJ-backed 3D telemetry models with per-body coloring and pose-driven
  orientation
- export and reconstruct graph and button config from TOML
- generate exact repeating signals from sparse `rfft` coefficients
- batch and transmit typed Arrow tables from the device-interface boundary
- publish and subscribe latest-only numeric batches through Arrow Flight
  connectors
- mirror receive-side connector data into one appended local frame when a local
  downstream consumer needs transport state inline
- persist fixed-shape numeric batches into append-only Parquet partitions
- run a manual ImGui signal desk example that exercises the graph and simulator
  stack together

The mental model is:

- `simulation_utils` produces deterministic numeric batches
- `deviceinterface` and `connector_utils` define explicit Arrow boundaries
- `orchestrator` is where a deployment-local `pythusa` pipeline will be
  assembled
- `gui_utils` handles windowing, plotting, and operator controls
- `datastorage_utils` persists typed numeric output when a deployment needs
  archival

## Current Design Direction

The current code is written in a style that fits a high-rate telemetry desk:

- signals are fixed-shape numeric frames, not arbitrary Python objects
- connectors keep current state rather than building lossless live queues
- graphs consume explicit timestamps rather than assuming wall-clock plotting
- simulators are deterministic and reconstructable from compact config
- storage is columnar and append oriented
- the GUI layer is ImGui-first rather than web-first

That makes the current modules good building blocks for the broader VIVIIan
architecture, even though the full composed runtime described in
[Architecture](architecture.md) does not exist yet.

## Quick Example

The simplest useful composition today is:

1. build a spectral signal generator
2. generate `(2, rows)` batches
3. feed those batches into a graph reader
4. let `SensorGraph` manage display history and window expiry

```python
from __future__ import annotations

import numpy as np

from viviian.gui_utils.graphs import GraphSeries, SensorGraph
from viviian.simulation_utils import random_sparse_spectrum_generator


class Reader:
    def __init__(self) -> None:
        self.shape = (2, 8)
        self.dtype = np.dtype(np.float64)
        self._pending = None

    def set_blocking(self, _blocking: bool) -> None:
        return None

    def prime(self, frame: np.ndarray) -> None:
        self._pending = np.asarray(frame, dtype=np.float64)

    def read(self) -> np.ndarray | None:
        frame = self._pending
        self._pending = None
        return frame


reader = Reader()
graph = SensorGraph(
    "demo",
    title="Demo Signal",
    series=(
        GraphSeries(
            series_id="signal_1",
            label="signal_1",
            stream_name="signal_1",
            color_rgba=(0.16, 0.73, 0.78, 1.0),
        ),
    ),
    window_seconds=10.0,
)
graph.bind({"signal_1": reader})

generator = random_sparse_spectrum_generator(
    signal_id="signal_1",
    sample_rate_hz=128.0,
    samples_per_cycle=1024,
    seed=7,
    nonzero_terms=4,
    coefficient_scale=2.0,
)

reader.prime(generator.next_batch(8))
graph.consume()
print(graph.series_snapshot("signal_1"))
```

This example does not open a GUI window by itself, but it shows the actual data
contract that the current graph runtime expects.
