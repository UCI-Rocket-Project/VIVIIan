# Orchestrator

This page documents the orchestrator role in the VIVIIan architecture as it
exists today: mostly as a structural and deployment concern, not as a finished
runtime subsystem.

The key rule is simple:

- the orchestrator owns topology
- the local runtime owns hot-path stream consumption details

## What The Orchestrator Owns

The orchestrator is responsible for:

- defining deployment topology in code
- wiring units to explicit endpoints
- materializing reconstructable deployment descriptions
- launching and supervising units
- keeping deployment logic out of the hot path

It does **not** own:

- per-frame scheduling inside a `pythusa` task
- global live routing of every message
- reinterpretation of local shared-memory buffers across unit boundaries

The orchestrator should wire a coherent structural stream contract. It should
not reach into a worker’s inner loop and decide how many local bytes one task
consumes per `look()`.

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

One side must own that regrouping logic internally. The ring only moves bytes.
It does not preserve a higher-level “writer frame” boundary for a different
reader shape. If neither side owns regrouping, you can consume the same byte
stream at the wrong local size and lose logical frame alignment.

This is a local-runtime rule only. It does **not** change:

- the declared stream structure in the deployment topology
- the Arrow connector contract between deployment units
- the orchestrator’s job as the topology compiler

## Minimal Example

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

- `read()` and `read_into()` still assume the binding’s declared `shape` and
  `frame_size`
- so the resized side should use `look()` / `increment()` and own regrouping
  explicitly

## Relationship To The Spike

The concrete proof for this pattern lives at:

- `spikes/elastic_stream_sizes/`

That spike is a demonstration artifact. This page is the canonical reference
for how the pattern fits into the broader VIVIIan system model.
