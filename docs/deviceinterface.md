# Device Interface

`DeviceInterface` is the hardware boundary layer.
It accepts Arrow tables from device drivers and publishes them to downstream
consumers over a local TCP stream — one live connection, Arrow IPC framing,
no queue.

## Install

```bash
pip install -e .
```

```python
from viviian.deviceinterface import DeviceInterface
```

## Schema Contract

The schema passed to `DeviceInterface` must contain at least one field typed
`pa.time64("ns")`.
Every ingressed table is cast to the declared schema on entry.
Tables that fail the cast are dropped with an error log; they do not raise.

```python
import pyarrow as pa
from viviian.deviceinterface import DeviceInterface

schema = pa.schema([
    pa.field("time_ns", pa.time64("ns")),
    pa.field("pressure_kpa", pa.float64()),
    pa.field("temperature_c", pa.float32()),
])
```

## Basic Usage

Use `DeviceInterface` as a context manager.
Entering the context starts the background sender thread.
Exiting flushes remaining queued data, joins the thread, and closes the socket.

```python
import pyarrow as pa
import numpy as np
from viviian.deviceinterface import DeviceInterface

schema = pa.schema([
    pa.field("time_ns", pa.time64("ns")),
    pa.field("value", pa.float64()),
])

with DeviceInterface(schema, publish_port=6767) as di:
    table = pa.table({
        "time_ns": pa.array([0, 1_000_000, 2_000_000], type=pa.time64("ns")),
        "value": pa.array([1.0, 2.0, 3.0], type=pa.float64()),
    })
    di.ingress_table(table)
```

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `schema` | `pa.Schema` | required | Schema for incoming tables. Must include a `time64("ns")` field. |
| `tx_timeout` | `float` | `1.0` | Seconds before a non-empty queue flushes, even if `max_rows` is not reached. |
| `max_rows` | `int` | `1000` | Row count that triggers an immediate flush. |
| `publish_host` | `str` | `"127.0.0.1"` | TCP bind address. |
| `publish_port` | `int` | `6767` | Data port. The metadata port is always `publish_port + 1`. |

## How Data Moves

```text
driver code
    │
    │  di.ingress_table(table)
    ▼
 thread-safe queue   ◄─── background sender thread
    │                         │
    │   flush when:           │  concat + chunk
    │   - max_rows reached    │
    │   - tx_timeout elapsed  │
    ▼                         ▼
 ArrowBatchStreamServer ──► TCP client (one connection)
   port N  (data)            Arrow IPC frames, 4-byte length prefix
   port N+1 (metadata)
```

The sender thread wakes every millisecond and checks two conditions:

- `queued_rows >= max_rows` — flush immediately
- `queued_rows > 0 and elapsed > tx_timeout` — flush on timeout

On flush, pending tables are concatenated and chunked to `max_rows` before
being sent.

## Flush and Batching

`ingress_table` is non-blocking.
The calling thread appends to an internal list and returns.
The background thread owns all network I/O.

If no client is connected when a flush fires, the batch is silently dropped.
The server does not buffer for absent clients.

## Port Layout

`ArrowBatchStreamServer` opens two ports:

| Port | Purpose |
|------|---------|
| `publish_port` | Arrow IPC data stream |
| `publish_port + 1` | Metadata (reserved, not yet consumed by built-in receivers) |

Both ports accept one client connection.
Reconnections after a disconnect are accepted automatically on the next send.

## Stats

`di.stats` is a plain dict updated under the internal lock:

| Key | Meaning |
|-----|---------|
| `sent_batches` | Number of Arrow tables sent to the stream server |
| `sent_rows` | Total rows sent |
| `drops` | (reserved — not currently incremented) |
| `queued_rows` | Rows currently in the pending queue |

```python
print(di.stats)
# {'sent_batches': 12, 'sent_rows': 4800, 'drops': 0, 'queued_rows': 0}
```

## Wire Format

Each sent frame is:

```
[4 bytes big-endian length] [Arrow IPC stream bytes]
```

The Arrow IPC stream includes the schema on every send — receivers do not need
to maintain schema state across reconnects.

## What This Is Not

`DeviceInterface` is not:

- a queue or replay store — if no client is connected, data is dropped
- a multi-client broadcaster — one live connection at a time
- a lossless transport — intermediate batches can be overwritten before flush

For persistent storage, pair it with
[`ParquetDatabase`](datastorage.md).
For in-process transport between components, use
[connectors](connectors.md).

## What To Read Next

- [Data Storage](datastorage.md) — persist device output to Parquet files
- [Connectors](connectors.md) — Arrow Flight transport between processes
- [Architecture](architecture.md) — how device interfaces fit the full system
