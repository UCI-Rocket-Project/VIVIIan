# Orchestrator

This page documents `orchestrator` as the VIVIIan runtime composition root.

The key rules are:

- `Orchestrator` should be a `pythusa.Pipeline` subclass
- public composition stays code-first and explicit
- private topology bookkeeping is an implementation detail, not user API

## What The Orchestrator Owns

The `orchestrator` layer is responsible for:

- defining deployment-local structure in code
- wiring streams, tasks, events, and connector boundaries to explicit endpoints
- assembling local tool collections around the pipeline runtime
- tracking private structural metadata for validation, reconstruction, and
  topology export
- keeping deployment logic out of the hot path

It does **not** own:

- per-frame scheduling inside a `pythusa` task
- a second worker model above `Pipeline`
- global live routing of every message
- reinterpretation of local shared-memory buffers across unit boundaries

The executable dataflow remains the local `pythusa` graph.
The orchestrator may also maintain a private topology graph describing how
streams, tasks, and connector boundaries relate.
That topology graph exists for composition, validation, reconstruction, and
export.
It is not part of the supported public surface and it does not change runtime
scheduling semantics.

## Tool Collections, Not Runtime Roles

VIVIIan no longer models processing and UI as mandatory separate architectural
roles.
An orchestrator deployment may instead compose whatever local tool collections
it needs, for example:

- processing tools that add typed transforms and derivation tasks
- storage tools that persist selected streams
- GUI tools that render desks, graphs, gauges, and 3D views
- connector tools that bridge Arrow boundaries

That means connector setup, endpoint wiring, private topology bookkeeping, and
lifecycle scaffolding belong in the orchestrator layer, while domain logic stays
in the tool modules that the orchestrator wires together.

## Relationship To `pythusa.Pipeline`

`Orchestrator` should be implemented as a `Pipeline` subclass.

That means:

- `add_stream(...)`, `add_task(...)`, `add_event(...)`, `compile()`, and
  `run()` keep their normal `pythusa` semantics
- VIVIIan-specific helpers add deployment structure around those primitives
- the orchestrator should not hide the underlying runtime behind a second
  scheduler or worker model

The composition root should stay thin and explicit.
If a capability is really a reusable processing primitive or GUI primitive, it
belongs in a tool module, not in the orchestrator base class.

## Intended `VIVIIan` API Shape

The repo does not yet expose a finished top-level `VIVIIan` package API, but
the intended public calling pattern should remain explicit while the lower-level
orchestrator surface is still being built out.

The target shape is:

```python
from __future__ import annotations

import numpy as np

from viviian import VIVIIan


def source(samples) -> None:
    samples.write(np.arange(64, dtype=np.float32))


def sink(samples) -> None:
    while True:
        frame = samples.read()
        if frame is None:
            continue
        return


with VIVIIan as VIVII:
    VIVII.add_stream("samples", shape=(64,), dtype=np.float32)
    VIVII.add_task("source", fn=source, writes={"samples": "samples"})
    VIVII.add_task("sink", fn=sink, reads={"samples": "samples"})
    VIVII.run()
```

Connector composition follows the same explicit pattern:

- register an ingress connector boundary for the stream contract you want
- add local tasks that consume those streams and produce derived outputs
- register egress connector boundaries for any outputs you want to publish

The exact helper names for those connector-registration APIs are still under
design.
What is already fixed is the ownership model: those boundaries belong to the
orchestrator layer, not to hidden brokers or a separate runtime role.

## Private Bookkeeping

The orchestrator may keep private structural metadata so it can:

- validate how streams, tasks, and connectors relate before compile time
- reconstruct the deployment shape deterministically
- export topology information for inspection or rebuild

Users should compose deployments through the public stream, task, event, and
connector surface.
They should not call private graph-registration helpers directly, even if
temporary scaffolding code exposes them during development.

## Structural Contract vs Local Read Window

VIVIIan keeps two different concerns separate:

- the stream definition is the structural contract
- the local read or write window is a task-level runtime choice

That separation matters because the architectural stream contract needs to stay
predictable and reconstructable, while one task may still need to aggregate or
split local `pythusa` frames differently for DSP or processing convenience.

The endorsed current procedure is:

- keep the underlying stream definition normal
- override `frame_nbytes` on the local binding that wants a different local size
- use `look()` and `increment()` on that resized side
- manually reinterpret the returned bytes with `np.frombuffer(...)` and the
  local shape you want

One side must own that regrouping logic internally.
The ring only moves bytes.
It does not preserve a higher-level "writer frame" boundary for a different
reader shape.
If neither side owns regrouping, you can consume the same byte stream at the
wrong local size and lose logical frame alignment.

This is a local-runtime rule only.
It does **not** change:

- the declared stream structure in the deployment
- the Arrow connector contract between deployments
- the role of the orchestrator composition layer

## Minimal Local Read-Window Example

This is the smallest current pattern for one normal stream with a larger local
reader window:

```python
from __future__ import annotations

import numpy as np

from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding


writer = make_writer_binding(raw_writer_ring, name="samples", shape=(64,), dtype=np.float32)
reader = make_reader_binding(raw_reader_ring, name="samples", shape=(64,), dtype=np.float32)

reader.frame_nbytes = 256 * np.dtype(np.float32).itemsize

for offset in (0, 64, 128, 192):
    writer.write(np.arange(offset, offset + 64, dtype=np.float32))

view = reader.look()
if view is not None:
    try:
        block = np.frombuffer(view, dtype=np.float32).reshape((256,)).copy()
    finally:
        view.release()
        reader.increment()
```

Why this works:

- `look()` asks the ring for `frame_nbytes`
- `increment()` advances by `frame_nbytes`
- the ring is byte-oriented, so four normal 64-sample writes can be consumed as
  one 256-sample local read

Why this stays local:

- `read()` and `read_into()` still assume the binding's declared `shape` and
  `frame_size`
- so the resized side should use `look()` / `increment()` and own regrouping
  explicitly

In practice:

- processing tools may use this pattern inside a local `pythusa` DAG
- GUI tools may use the same pattern inside a local view-layer runtime if they
  have a legitimate need
- the orchestrator layer still does not own that decision

## Relationship To The Spike

The concrete proof for this pattern lives at:

- `spikes/elastic_stream_sizes/`

That spike is a demonstration artifact.
This page is the canonical reference for how the pattern fits into the broader
VIVIIan system model.
