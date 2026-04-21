# Device Interfaces

This guide is for engineers integrating hardware, socket feeds, or simulators
into a VIVIIan app.

There are two supported patterns in the repo today:

1. `DeviceInterface` for generic Arrow-table ingestion and batching
2. app-local board adapters for protocol-aware telemetry and command loops

Choosing the right one matters.
If you pick the generic abstraction for a protocol-heavy board, you will end up
fighting it.
If you build a custom board adapter when a generic table publisher would do, you
will create unnecessary surface area.

## Choose the Right Pattern

| Use case | Start with | Why |
| --- | --- | --- |
| you already have Arrow tables in process | `DeviceInterface` | it handles schema validation, batching, chunking, and publication |
| you need to read from a socket, decode binary packets, or send commands back | app-local adapter | protocol decode/encode is app-specific and belongs near the board boundary |
| you need device-link status files or reconnect logic | app-local adapter | those are deployment behaviors, not generic ingestion primitives |

## Pattern 1: Generic `DeviceInterface`

`DeviceInterface` is the shared batching utility in
`packages/viviian_core/src/viviian/deviceinterface/deviceinterface.py`.

Use it when your source code can already produce `pyarrow.Table` objects with a
stable schema.

### Smallest Working Example

```python
from __future__ import annotations

import pyarrow as pa

from viviian.deviceinterface import DeviceInterface


schema = pa.schema(
    [
        pa.field("time_ns", pa.time64("ns")),
        pa.field("pressure_kpa", pa.float64()),
    ]
)

with DeviceInterface(
    schema,
    max_rows=4,
    tx_timeout=0.25,
    publish_port=6767,
    stream_id="demo.device",
) as device:
    table = pa.table(
        {
            "time_ns": pa.array([0, 1_000_000, 2_000_000], type=pa.time64("ns")),
            "pressure_kpa": pa.array([101.3, 101.7, 102.1], type=pa.float64()),
        }
    )
    device.ingress_table(table)
```

What this does:

- validates that the schema includes at least one `pa.time64("ns")` field
- casts ingress tables to the declared schema
- batches rows in memory
- flushes when either:
  - queued rows reach `max_rows`
  - queued rows are non-zero and `tx_timeout` elapses
- publishes through an internal `SendConnector`

### Important Contract: Fixed Transport Shape

`DeviceInterface` publishes through `SendConnector`, which requires a fixed
shape.
That means its transport shape is:

- `(max_rows, number_of_schema_fields)`

When the final chunk has fewer than `max_rows` rows, the remaining rows are
filled with `NaN`.

That behavior is not incidental.
It is how the current shared connector contract stays fixed-width while still
accepting variable-sized Arrow tables at ingress.

### Common Generic Tasks

#### Define the Schema Carefully

The schema must include a nanosecond timestamp field.
That requirement is enforced at construction time.

Good schema example:

```python
pa.schema(
    [
        pa.field("time_ns", pa.time64("ns")),
        pa.field("temperature_c", pa.float64()),
    ]
)
```

Bad schema example:

```python
pa.schema(
    [
        pa.field("temperature_c", pa.float64()),
    ]
)
```

The second schema is rejected because it has no nanosecond timestamp field.

#### Choose `max_rows` and `tx_timeout` Deliberately

- lower `max_rows` lowers batching latency
- higher `max_rows` improves batch amortization
- lower `tx_timeout` forces more frequent partial flushes

The right choice depends on whether the consumer values low latency or larger
coalesced batches.

#### Inject a Fake Sender in Tests

The class supports a `sender=` override.
The repo's tests use that to validate batching logic without bringing up a real
connector server.

That is the right pattern for unit tests that care about:

- chunking
- schema rejection
- shutdown flush behavior

## Pattern 2: App-Local Board Adapters

When the boundary is an actual board protocol, the reference pattern is the
UCIRPLGUI code in:

- `apps/ucirplgui/src/ucirplgui/device_interfaces/device_interfaces.py`

That module does work `DeviceInterface` should not try to own:

- TCP connection management
- binary packet reads
- CRC checks
- protocol decode into `np.ndarray`
- command sendback through a second connector
- device-link snapshot publishing

### Minimal Shape of a Board Adapter

The essential loop looks like this:

```python
from __future__ import annotations

import socket
import time

import numpy as np

from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


class ChamberBoardInterface:
    def __init__(self) -> None:
        self.telemetry = SendConnector(telemetry_spec, port=7101, host="127.0.0.1")
        self.commands = ReceiveConnector(command_spec, port=7201, host="127.0.0.1")

    def run_forever(self) -> None:
        self.telemetry.open()
        self.commands.open()

        while True:
            try:
                with socket.create_connection(("127.0.0.1", 10002), timeout=2.0) as sock:
                    sock.settimeout(0.2)
                    while True:
                        packet = _read_exact(sock, TELEMETRY_LEN)
                        if packet is None:
                            break

                        row = self._decode_packet(packet)
                        if row is not None:
                            self.telemetry.send_numpy(row)

                        if self.commands.has_batch:
                            self._send_command(sock, self.commands.batch[0].copy())
            except OSError:
                time.sleep(0.5)
```

The important design choice is placement:

- protocol decode lives here
- command encode lives here
- connector publication starts here
- the backend should receive normalized telemetry, not raw wire packets

### Common Board-Adapter Tasks

#### Publish Raw Telemetry as Normalized Numeric Rows

After protocol decode, publish a NumPy batch whose shape matches the raw stream
contract exactly.

UCIRPLGUI uses one-row batches for board telemetry:

```python
row = np.array([[packet_time_ms, pressure_psi, temperature_c]], dtype=np.float64)
self.send_connector.send_numpy(row)
```

Keep the output numeric and boring.
Do not leak binary protocol details into downstream layers.

#### Echo Commands Back to the Device

If the board accepts commands, pair a telemetry `SendConnector` with a command
`ReceiveConnector`.

The pattern is:

1. backend or frontend writes command state onto a command stream
2. device interface reads the latest command batch
3. device interface encodes that batch into the board's command format
4. socket loop sends the encoded command packet

That keeps the protocol-specific command shape at the hardware boundary where it
belongs.

#### Publish Connectivity State Separately

UCIRPLGUI writes device-link snapshots to JSON files consumed by the frontend.
That is app-specific state, so it belongs in the board adapter, not in the
generic `DeviceInterface`.

If your app needs operator-visible link state, keep it as a separate contract
from the raw telemetry stream.

## Failure Modes Worth Designing For

Device-interface bugs are expensive because they contaminate everything
downstream.
The failures worth handling explicitly are:

- bad packets or CRC failures
- silent disconnects
- a command stream that repeats the same value unnecessarily
- shape drift between decoded rows and the stream schema
- confusing "no telemetry yet" with valid zero-valued telemetry

The right review question is:

"Does the rest of the app see a clean numeric contract, or is hardware protocol
knowledge leaking upward?"

If raw protocol knowledge is leaking upward, the boundary is in the wrong place.

## What To Read Next

- [Device Interface](deviceinterface.md) for the exact shared-class reference
- [Telemetry](build-telemetry.md) for stream contract rules
- [Backend](build-backend.md) for the next stage after raw device publication
