# Telemetry

This guide is for engineers who need to define a stream, publish batches onto
it, and consume the latest data from another process without digging through the
transport reference first.

Use this page for workflow and examples.
Use [Connectors](connectors.md) when you need the precise transport semantics.

## What a Stream Contract Includes

Before you write any runtime code, decide four things:

1. `stream_id`: the stable name of the stream
2. Arrow schema: field names and column count
3. fixed batch shape: `(rows, columns)`
4. port assignment: where the sender will publish

Those are contracts, not suggestions.
If the sender and receiver disagree on any of them, the runtime will fail fast
or silently give you the wrong data shape.

The current connector runtime also has two architectural rules:

- transport batches are always normalized to `float64`
- live transport is latest-only, not queued

That means the connector path is good for current state, not replay or lossless
history.

## Smallest Working Example

This is the smallest real sender/receiver pair in the repo's current API:

```python
from __future__ import annotations

import time

import numpy as np
import pyarrow as pa

from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


spec = StreamSpec(
    stream_id="demo.telemetry",
    schema=pa.schema(
        [
            pa.field("timestamp_s", pa.float64()),
            pa.field("pressure_kpa", pa.float64()),
        ]
    ),
    shape=(4, 2),
)

with SendConnector(spec, port=0) as sender, ReceiveConnector(spec, port=sender.port) as receiver:
    batch = np.array(
        [
            [0.00, 101.3],
            [0.05, 102.1],
            [0.10, 102.7],
            [0.15, 103.0],
        ],
        dtype=np.float64,
    )
    sender.send_numpy(batch)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if receiver.has_batch:
            latest = receiver.batch.copy()
            print(latest)
            break
        time.sleep(0.01)
```

Important details:

- `shape=(4, 2)` is strict; the sent NumPy batch must match it exactly
- `port=0` lets the OS choose a free port for demos and tests
- use `receiver.batch.copy()` when downstream code needs a stable snapshot
- the receiver thread reconnects automatically if the sender disappears and returns later

## The UCIRPLGUI Pattern

The app code does not inline `StreamSpec` definitions everywhere.
It centralizes the contracts in `config.py`, then builds specs from those
contracts.

```python
from viviian.connector_utils import StreamSpec
from ucirplgui import config


def _stream_spec(stream_id: str) -> StreamSpec:
    return StreamSpec(
        stream_id=stream_id,
        schema=config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
    )
```

That pattern is worth copying because it keeps four failure-prone values aligned:

- stream ID
- schema
- row count
- port mapping

If you spread those across the codebase, drift is nearly guaranteed.

## Common Tasks

### 1) Define a New Stream in App Config

Start in `apps/<app>/src/<app>/config.py`.
Keep stream ID, columns, schema, and port assignment together.

```python
import pyarrow as pa

ROWS_PER_FRAME = 1

CONNECTOR_PORTS = {
    "frontend_temperature": 7310,
}

FRONTEND_TEMPERATURE_STREAM_ID = "myapp.frontend.temperature"
FRONTEND_TEMPERATURE_COLUMNS = (
    "timestamp_s",
    "temperature_c",
)


def make_schema(columns: tuple[str, ...]) -> pa.Schema:
    return pa.schema([(name, pa.float64()) for name in columns])


SCHEMAS = {
    FRONTEND_TEMPERATURE_STREAM_ID: make_schema(FRONTEND_TEMPERATURE_COLUMNS),
}
```

For app-level telemetry, the repo's current convention is:

- one constant for the stream ID
- one tuple for the column names
- one schema entry in `SCHEMAS`
- one connector port entry in `CONNECTOR_PORTS`

### 2) Publish a Derived Batch

In a backend or device-interface loop, build a fixed-shape NumPy batch and
publish it through a `SendConnector`.

```python
temperature_sender = SendConnector(
    _stream_spec(config.FRONTEND_TEMPERATURE_STREAM_ID),
    port=config.CONNECTOR_PORTS["frontend_temperature"],
    host=config.DEFAULT_CONNECTOR_HOST,
)

temperature_sender.open()

out = np.array([[timestamp_s, temperature_c]], dtype=np.float64)
temperature_sender.send_numpy(out)
```

The contract is strict:

- row count must match the `StreamSpec`
- column count must match the Arrow schema
- numeric data is coerced to contiguous `float64`

### 3) Consume the Latest Batch Safely

Receiver-side code normally follows this pattern:

```python
rx_temperature = ReceiveConnector(
    _stream_spec(config.FRONTEND_TEMPERATURE_STREAM_ID),
    port=config.CONNECTOR_PORTS["frontend_temperature"],
    host=config.DEFAULT_CONNECTOR_HOST,
)
rx_temperature.open()

if rx_temperature.has_batch:
    row = rx_temperature.batch[0].copy()
    timestamp_s = float(row[0])
    temperature_c = float(row[1])
```

Use `.copy()` when you are going to retain or mutate the data.
`receiver.batch` is the live buffer owned by the connector.

### 4) Mirror Received Data into a Local Stream

`StreamSpec.stream` is optional and local to the receiving process.
If you provide it, each successfully received batch is also written to that
object with two extra columns:

- `time_received`
- `connection_alive`

```python
class MirrorWriter:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []

    def write(self, frame: np.ndarray) -> bool:
        self.frames.append(np.array(frame, copy=True))
        return True


mirror = MirrorWriter()
spec = StreamSpec(
    stream_id="demo.telemetry",
    schema=pa.schema(
        [
            pa.field("timestamp_s", pa.float64()),
            pa.field("value", pa.float64()),
        ]
    ),
    shape=(4, 2),
    stream=mirror,
)
```

The mirrored frame shape becomes `(rows, columns + 2)`.
This is useful when you want a receiver process to feed a local ring buffer or
shared-memory stream without changing the network payload.

### 5) Debug the Failures You Will Actually See

The common connector mistakes in this repo are:

- wrong shape: sender built `(1, 3)` but the `StreamSpec` says `(4, 3)`
- wrong schema width: schema has 5 fields but batch has 4 columns
- wrong stream ID: sender and receiver use different names
- wrong port: code compiles, but sender and receiver never meet
- queue assumptions: receiver misses intermediate updates because transport is latest-only

A fast debugging checklist:

1. print the `stream_id`, shape, and port on both sides
2. check `receiver.has_batch` before touching `receiver.batch`
3. copy the batch before downstream mutation
4. confirm the producer is not relying on queue semantics

## UCIRPLGUI Equivalent

The reference app uses telemetry in three different roles:

- `device_interfaces/device_interfaces.py`: receives socket telemetry and publishes raw streams
- `backend/pipeline.py`: reads raw streams and publishes frontend-facing streams
- `frontend/frontend.py`: reads frontend-facing streams and primes widget readers

That layering is intentional:

- device interfaces normalize board protocols
- the backend emits the data products the UI wants
- the frontend should not need to understand raw board packet structure

## When To Read the Reference

Go to [Connectors](connectors.md) when you need:

- precise mirror-stream semantics
- disconnect-frame behavior
- benchmark behavior under overwrite pressure
- the exact meaning of latest-only transport
