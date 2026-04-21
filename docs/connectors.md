# Connectors

If you are trying to add a stream, wire a sender/receiver pair, or understand what
shape your telemetry batch should have, start with
[Build With VIVIIan -> Telemetry](build-telemetry.md).
This page is the transport reference.

This page documents the connector runtime that exists in the repo today.
It fits the `deviceinterface` / `orchestrator` architecture, but it is not the
full future transport story described in [Architecture](architecture.md).

## What Exists Now

The current connector runtime lives in:

- `packages/viviian_core/src/viviian/connector_utils/connectors.py`

It provides three public objects:

- `StreamSpec`
- `SendConnector`
- `ReceiveConnector`

The current design is intentionally small:

- `SendConnector` is the Arrow Flight server
- `ReceiveConnector` is the Arrow Flight client
- both sides are latest-only
- there is no queue
- the live path is freshest-wins by design

The mental model is:

```text
producer -> sender.latest_batch -> do_get stream -> receiver.latest_batch -> consumer
```

## Core Contract

### StreamSpec

`StreamSpec` defines the connector contract for one stream:

- `stream_id`
- Arrow schema
- fixed 2D shape `(rows, columns)`
- optional local mirror target `stream`

The shape is strict:

- batches must be 2D NumPy arrays
- batch width must match the schema field count
- all transport data is sent as `float64`

The runtime treats the NumPy side as a fixed row-major batch:

```text
[
  [field_0, field_1, field_2, ...],
  [field_0, field_1, field_2, ...],
  ...
]
```

`stream` is optional.
If provided, it is a local receive-side mirror target, not part of the Arrow
transport contract.
The connector treats it as any object with `write(np.ndarray)` semantics.

### SendConnector

`SendConnector` is the publisher side and hosts the Flight server.

Its behavior is:

- keep one latest batch buffer
- `send_numpy(batch)` validates and copies into that buffer
- each new batch overwrites the previous latest batch
- connected receivers read from one long-lived `do_get` stream

The sender does not build a queue.
If a producer runs faster than receivers can observe updates, intermediate
batches can be lost.

### ReceiveConnector

`ReceiveConnector` is the subscriber side and acts as the Flight client.

Its behavior is:

- run a background reader thread
- call `do_get(...)` against the sender
- read streamed record batches
- copy the newest observed batch into one local latest buffer
- reconnect automatically if the stream is unavailable or drops

Like the sender, the receiver is latest-only.
It does not retain batch history.

## How Data Moves

The current runtime is pull-driven over `do_get`:

1. a producer calls `sender.send_numpy(batch)`
2. the sender overwrites its latest batch buffer
3. the receiver's background thread stays connected through `do_get`
4. the sender serves new batches onto that stream
5. the receiver overwrites its local latest batch buffer
6. application code reads the latest receiver buffer

This is intentionally different from a queued transport.
The runtime optimizes for current state, not lossless live replay.

In the broader VIVIIan architecture, these connectors may sit:

- between a `deviceinterface` and an `orchestrator`
- between two orchestrators
- between an orchestrator and a remote operator tool when an explicit remote
  boundary is required

## Reading From A Receiver

The public read surface is:

- `receiver.has_batch`
- `receiver.batch`

Typical usage:

```python
if receiver.has_batch:
    batch = receiver.batch.copy()
```

Use `.copy()` if you want a stable snapshot for downstream work.
Reading `receiver.batch` directly gives you the current live buffer.

## Optional Local Mirror Stream

`ReceiveConnector` now has two distinct output paths:

- the Arrow transport path, which fills `receiver.batch`
- the optional local mirror path, which writes one appended frame into
  `StreamSpec.stream`

The mental model is:

```text
producer -> sender.latest_batch -> Flight stream -> receiver.batch
                                             \
                                              -> optional local mirror stream
                                                 [payload | time_received | connection_alive]
```

The important boundary is:

- `receiver.batch` keeps the original `(rows, columns)` shape
- the Arrow schema does not change
- the mirror path is local to the receiving process

If `StreamSpec.stream` is `None`, nothing changes from the original connector
behavior.

### Mirror Frame Contract

When `StreamSpec.stream` is present, every successfully received batch is also
written once to that local mirror target as one `float64` frame with shape:

- `(rows, original_columns + 2)`

The columns are:

- `[:, :original_columns]`
  The received payload copied from `receiver.batch`
- `[:, original_columns]`
  `time_received`, written from local `time.time_ns()` on the receiver host and
  repeated across every row in that mirrored frame
- `[:, original_columns + 1]`
  `connection_alive`, repeated across every row in that mirrored frame

`connection_alive` values are:

- `1.0` for normal live received frames
- `0.0` for the synthetic disconnect frame described below

Important precision note:

- the mirror frame stays `float64`
- `time_received` therefore behaves like a wall-clock receive marker, not an
  exact `uint64` storage contract

### Disconnect Semantics

The mirror path emits state transitions, not a heartbeat stream.

If the Flight stream was alive and then drops, the receiver writes one extra
synthetic mirrored frame with:

- payload columns filled with `NaN`
- `time_received` refreshed at disconnect time
- `connection_alive` set to `0.0`

That disconnect frame is written once per alive-to-dead transition.
The connector does not continuously publish `0.0` while retrying.

### Mirror Example

```python
from __future__ import annotations

import numpy as np
import pyarrow as pa

from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


class MirrorWriter:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []

    def write(self, frame: np.ndarray) -> bool:
        self.frames.append(np.array(frame, copy=True))
        return True


mirror = MirrorWriter()
spec = StreamSpec(
    stream_id="telemetry",
    schema=pa.schema(
        [
            pa.field("timestamp", pa.float64()),
            pa.field("value", pa.float64()),
        ]
    ),
    shape=(4, 2),
    stream=mirror,
)

with SendConnector(spec, port=0) as sender, ReceiveConnector(spec, port=sender.port) as receiver:
    batch = np.zeros((4, 2), dtype=np.float64)
    batch[:, 0] = np.arange(4, dtype=np.float64)
    batch[:, 1] = 1.25
    sender.send_numpy(batch)

    latest = receiver.batch.copy()
    mirrored = mirror.frames[-1]

    print(latest.shape)    # (4, 2)
    print(mirrored.shape)  # (4, 4)
```

In that mirrored frame:

- columns `0:2` are the original payload
- column `2` is `time_received`
- column `3` is `connection_alive`

## Minimal Example

```python
from __future__ import annotations

import numpy as np
import pyarrow as pa

from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


spec = StreamSpec(
    stream_id="telemetry",
    schema=pa.schema(
        [
            pa.field("timestamp", pa.float64()),
            pa.field("value", pa.float64()),
        ]
    ),
    shape=(1024, 2),
)

with SendConnector(spec, port=0) as sender, ReceiveConnector(spec, port=sender.port) as receiver:
    batch = np.zeros((1024, 2), dtype=np.float64)
    batch[:, 0] = np.arange(1024, dtype=np.float64)
    batch[:, 1] = 1.25
    sender.send_numpy(batch)

    if receiver.has_batch:
        latest = receiver.batch.copy()
        print(latest.shape)
```

## Operational Semantics

The important live-path semantics are:

- latest-only on the sender
- latest-only on the receiver
- freshest-wins under pressure
- receiver auto-reconnect
- no queue and no replay guarantee

That means:

- published throughput can be higher than observed throughput
- intermediate live batches may be overwritten before a receiver sees them
- this transport is a current-state feed, not an archive
- the optional mirror stream is also latest-driven, not a replay log

## What This Is Not

The current connector runtime is not:

- a lossless queue
- a replay store
- a command acknowledgement protocol
- the full orchestrated runtime described in [Architecture](architecture.md)

The optional local mirror stream is also not:

- an Arrow schema extension
- a second network payload
- a heartbeat feed with continuous disconnect updates
- an exact `uint64` timestamp channel

## Benchmark

The repo now includes a connector benchmark runner:

- `benchmarks/connector_throughput_benchmark.py`

Run it from the repo root:

```bash
.venv/bin/python benchmarks/connector_throughput_benchmark.py \
  --graph \
  --graph-out benchmarks/results/connector-heatmaps.png \
  --json-out benchmarks/results/connector-matrix.json \
  --no-show
```

The benchmark reports:

- published throughput
- observed throughput
- overwrite fraction
- mean latency
- p95 latency
- p99 latency

The heatmaps are useful because the connector is latest-only.
Published and observed rates diverge when producers overwrite batches faster
than receivers observe them.

### Benchmark Metadata Columns

The benchmark reserves the first two columns of each batch for:

- `sequence`
- `sent_at_ns`

That is benchmark instrumentation only.
It is not a required connector runtime convention for normal application use.

## What To Read Next

- [Telemetry](build-telemetry.md) — task-driven guide for defining and wiring streams
- [Backend](build-backend.md) — where connector pairs usually get composed in an app
- [Device Interfaces](build-device-interfaces.md) — when the producer is a board, socket, or simulator
