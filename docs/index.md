# VIVIIan Docs

VIVIIan is an early-stage rocket ground-station codebase.
At the top level, the implementation that exists today is concentrated in two areas:

- `gui_utils` for ImGui-native operator-desk primitives
- `simulation_utils` for deterministic, repeating telemetry-like signals defined in NumPy `rfft` space

The larger application shell is not implemented yet.
`main.py`, `connector_utils`, `datastorage_utils`, and top-level `configure.py` are still stubs, so this first docs set stays tightly grounded in the working modules instead of speculating about future APIs.

## What Exists Now

The current top-level system can already do a few concrete things:

- render multi-series time graphs with explicit timestamped numeric input
- render generic state buttons for operator workflows
- render OBJ-backed 3D telemetry models with per-body coloring and pose-driven orientation
- export and reconstruct graph and button config from TOML
- generate exact repeating signals from sparse `rfft` coefficients
- run a manual ImGui signal desk example that exercises the graph and simulator stack together

The mental model is:

- `simulation_utils` produces deterministic `(timestamp, value)` batches
- a small reader adapter feeds those batches into `gui_utils.SensorGraph`
- `gui_utils` handles windowing, plotting, and operator controls

## Start Here

- [Getting Started](getting-started.md)
  Local environment setup, test commands, docs commands, and the first runnable.
- [GUI Utils](gui-utils.md)
  `SensorGraph`, `GraphSeries`, and the button classes used to build an ImGui operator desk.
- [3D Viewer](3d-viewer.md)
  The public guide for the OBJ-backed model viewer, its stream contracts, and its configuration surface.
- [3D Viewer Maintainer Notes](3d-viewer-maintainer.md)
  Internal architecture, lifecycle, compatibility policy, and current performance constraints for the viewer stack.
- [Simulation Utils](simulation-utils.md)
  Sparse spectral signal configs, exact repeating cycles, and the seeded random helpers.
- [Examples](examples.md)
  A walkthrough of the current manual GUI example and the exact commands to run it.
- [Roadmap](roadmap.md)
  Short notes on the parts of the ground station that are planned but not implemented yet.

## Current Design Direction

The current code is written in a style that fits a high-rate telemetry desk:

- signals are fixed-shape numeric frames, not arbitrary Python objects
- graphs consume explicit timestamps rather than assuming wall-clock plotting
- simulators are deterministic and reconstructable from compact config
- the GUI layer is ImGui-first rather than web-first

That makes the current modules good building blocks for a future `pythusa`-backed ground-station runtime, even though that full system does not exist at the top level yet.

## Quick Example

The simplest useful composition today is:

1. build a spectral signal generator
2. generate `(2, rows)` batches
3. feed those batches into a graph reader
4. let `SensorGraph` manage display history and window expiry

```python
from __future__ import annotations

import numpy as np

from gui_utils.graphs import GraphSeries, SensorGraph
from simulation_utils import random_sparse_spectrum_generator


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

This example does not open a GUI window by itself, but it shows the actual data contract that the current graph runtime expects.
