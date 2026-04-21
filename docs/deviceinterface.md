# Device Interface

If you are starting from zero, read
[Build With VIVIIan -> Device Interfaces](build-device-interfaces.md) first.
This page is the class reference for the shared `DeviceInterface` utility.

`DeviceInterface` is the repo's generic batching boundary for code that already
has `pyarrow.Table` telemetry in process.
It does not know anything about board protocols, CRCs, socket reconnect policy,
or command formats.

Those behaviors belong in app-local adapters.

## What `DeviceInterface` Does

`DeviceInterface` accepts Arrow tables, validates them against a declared schema,
batches them locally, and publishes them through an internal
`SendConnector`.

The important architectural split is:

- ingress side: Arrow tables with schema checking and local batching
- transport side: fixed-shape `float64` NumPy batches published through the connector runtime

That means `DeviceInterface` is not a lossless history store.
Once data leaves the local queue and enters the connector runtime, transport is
latest-only.

## Install

```bash
pip install -e .
```

```python
from viviian.deviceinterface import DeviceInterface
```

## Constructor

```python
DeviceInterface(
    schema,
    tx_timeout=1.0,
    max_rows=1000,
    publish_host="127.0.0.1",
    publish_port=6767,
    stream_id="device",
    sender=None,
)
```

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `schema` | `pa.Schema` | required | Declared ingress schema. Must include at least one `pa.time64("ns")` field. |
| `tx_timeout` | `float` | `1.0` | Flush queued rows when this many seconds have elapsed since the last send. |
| `max_rows` | `int` | `1000` | Maximum rows per published chunk and the fixed transport row count. |
| `publish_host` | `str` | `"127.0.0.1"` | Host used when `DeviceInterface` constructs its own `SendConnector`. |
| `publish_port` | `int` | `6767` | Port used when `DeviceInterface` constructs its own `SendConnector`. |
| `stream_id` | `str` | `"device"` | Stream name used in the internally created `StreamSpec`. |
| `sender` | `SendConnector \| None` | `None` | Optional injected sender, useful for tests or custom transport ownership. |

If `sender` is provided, `publish_host`, `publish_port`, and `stream_id` are no
longer the active transport owner; the injected sender is.

## Schema Contract

The declared schema must contain at least one field typed `pa.time64("ns")`.
Construction fails otherwise.

```python
import pyarrow as pa

schema = pa.schema(
    [
        pa.field("time_ns", pa.time64("ns")),
        pa.field("pressure_kpa", pa.float64()),
        pa.field("temperature_c", pa.float64()),
    ]
)
```

Why the timestamp requirement exists:

- device telemetry without an explicit time basis is usually not usable downstream
- the shared utility needs one strong invariant across applications

The schema is also used as the cast target for every ingress table.
If a table cannot be cast to the declared schema, the table is dropped and the
error is logged; the call does not raise.

## Minimal Example

```python
from __future__ import annotations

import pyarrow as pa

from viviian.deviceinterface import DeviceInterface


schema = pa.schema(
    [
        pa.field("time_ns", pa.time64("ns")),
        pa.field("signal", pa.float64()),
    ]
)

with DeviceInterface(
    schema,
    max_rows=4,
    tx_timeout=0.25,
    publish_port=6767,
    stream_id="demo.device",
) as di:
    table = pa.table(
        {
            "time_ns": pa.array([0, 1_000_000, 2_000_000], type=pa.time64("ns")),
            "signal": pa.array([1.0, 2.0, 3.0], type=pa.float64()),
        }
    )
    di.ingress_table(table)
```

Entering the context opens the sender and starts the background sender thread.
Exiting the context:

- signals the sender loop to stop
- joins the thread
- flushes remaining queued tables
- closes the sender

## How Ingress, Batching, and Transport Work

### 1) Ingress

`ingress_table(table)`:

- casts the table to the declared schema
- appends it to the internal pending list
- increments `stats["queued_rows"]`

Ingress is fast and local.
Network publication happens on the background thread.

### 2) Flush Triggers

The sender loop checks two conditions:

- `queued_rows >= max_rows`
- `queued_rows > 0 and elapsed > tx_timeout`

If either condition is true, pending tables are flushed.

### 3) Chunking

On flush:

1. pending tables are concatenated
2. the result is sliced into chunks of at most `max_rows`
3. each chunk is converted to a fixed-shape NumPy batch

### 4) Fixed Transport Shape

The transport batch shape is always:

- `(max_rows, number_of_schema_fields)`

This is true even when the final chunk has fewer than `max_rows` rows.
Unused rows are filled with `NaN`.

That fixed shape exists because the current connector runtime requires exact
batch shapes.

### 5) Transport Normalization

The internally created `StreamSpec` uses:

- the provided `stream_id`
- a `float64` transport schema derived from the Arrow schema
- `shape=(max_rows, len(schema))`

All transport columns are therefore published as `float64`, including the
timestamp column.

## Transport Semantics

`DeviceInterface` publishes through `SendConnector`, so the cross-process
semantics after flush are the connector semantics:

- the newest batch is retained
- transport is latest-only
- history is not replayed
- a newly connected receiver can observe the latest batch, not every batch that was sent before it connected

That distinction matters:

- local ingress queueing is batched
- network transport is not a queue

If you need full replay or archival, pair device output with a storage path such
as [Data Storage](datastorage.md).

## Stats

`di.stats` is a mutable dict updated under the class lock.

| Key | Meaning |
| --- | --- |
| `sent_batches` | Number of fixed-shape transport batches sent |
| `sent_rows` | Number of actual table rows sent before padding |
| `drops` | Reserved; not currently incremented |
| `queued_rows` | Current number of rows waiting to be flushed |

Example:

```python
print(di.stats)
# {'sent_batches': 3, 'sent_rows': 5, 'drops': 0, 'queued_rows': 0}
```

## What `DeviceInterface` Is Not

`DeviceInterface` is not:

- a board protocol adapter
- a command bridge
- a reconnect manager for sockets or serial devices
- a lossless history or replay system
- a frontend-facing visualization API

If your integration needs protocol decode, command sendback, or link-state
publishing, build an app-local adapter instead of forcing those concerns into
this shared class.

## What To Read Next

- [Device Interfaces](build-device-interfaces.md) — task-driven guide for choosing between the generic class and app-local adapters
- [Telemetry](build-telemetry.md) — stream contract and sender/receiver wiring
- [Connectors](connectors.md) — transport-layer semantics
