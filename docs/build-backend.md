# Backend

This guide is for engineers building the app-side runtime that turns raw
telemetry into the data products a frontend or storage layer actually needs.

In this repo, "backend" usually means:

- consume one or more raw streams
- derive values or combine sources
- publish frontend-facing streams
- optionally persist selected outputs

Use this guide for the workflow.
Use [Orchestrator](orchestrator.md) and [Connectors](connectors.md) for lower-level
runtime details.

## Backend Responsibilities

A good backend does four things well:

1. treat stream IDs and schemas as contracts
2. keep derivation logic explicit and local
3. publish streams shaped for consumers, not for internal convenience
4. degrade predictably when upstream data is temporarily missing

UCIRPLGUI is a good example of this.
Its backend reads raw GSE, ECU, EXTR_ECU, and loadcell telemetry, then produces
dashboard-specific streams like tank pressures, line pressures, FFT magnitude,
and scalar summaries.

## Smallest Working Example

This is the smallest backend loop that reads one raw stream and publishes one
derived stream:

```python
from __future__ import annotations

import time

import numpy as np

from myapp import config
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


def _stream_spec(stream_id: str) -> StreamSpec:
    return StreamSpec(
        stream_id=stream_id,
        schema=config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
    )


class BackendRuntime:
    def __init__(self) -> None:
        self.rx_raw = ReceiveConnector(
            _stream_spec(config.RAW_PRESSURE_STREAM_ID),
            port=config.CONNECTOR_PORTS["raw_pressure"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_dashboard = SendConnector(
            _stream_spec(config.FRONTEND_PRESSURE_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_pressure"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )

    def run_forever(self) -> None:
        self.rx_raw.open()
        self.tx_dashboard.open()

        while True:
            timestamp_s = time.time()

            raw_row = self.rx_raw.batch[0].copy() if self.rx_raw.has_batch else None
            pressure_psi = float(raw_row[1]) if raw_row is not None else 0.0
            pressure_kpa = pressure_psi * 6.894757

            out = np.array([[timestamp_s, pressure_kpa]], dtype=np.float64)
            self.tx_dashboard.send_numpy(out)
            time.sleep(0.05)
```

The key pattern is simple:

- open connectors once
- read `batch[0]` for single-row streams
- compute derived values locally
- publish a new fixed-shape batch
- sleep or otherwise pace the loop explicitly

## The UCIRPLGUI Pattern

The reference backend in `apps/ucirplgui/src/ucirplgui/backend/pipeline.py`
follows the same structure, just with more streams:

- `ReceiveConnector` for raw board feeds
- `SendConnector` for frontend-facing outputs
- one long-lived runtime object
- one loop that:
  - checks the latest raw batches
  - derives dashboard-ready values
  - publishes multiple output streams
  - appends selected values to CSV storage

That design is worth preserving.
It keeps the UI from having to know about raw packet formats, smoothing rules,
or FFT windows.

## Common Tasks

### 1) Add a New Derived Stream

The safest order is:

1. add the new stream ID, columns, schema, and port in app config
2. create a `SendConnector` for that stream in the backend runtime
3. derive the new values inside the backend loop
4. publish a fixed-shape NumPy batch
5. bind the new stream on the frontend side

The app contract lives in config first.
Do not invent new stream IDs ad hoc inside the backend file.

### 2) Combine Multiple Raw Sources

UCIRPLGUI already does this when it smooths line-pressure channels with
secondary EXTR_ECU data:

```python
inj_lox = float(ecu_row[4]) if ecu_row is not None else 0.0
inj_lng = float(ecu_row[5]) if ecu_row is not None else 0.0

if extr_row is not None:
    inj_lox = (inj_lox + float(extr_row[3])) / 2.0
    inj_lng = (inj_lng + float(extr_row[4])) / 2.0
```

The important design choice is not the math.
It is the placement:

- raw streams stay raw
- fusion happens once in the backend
- the frontend receives a single clean stream to render

### 3) Persist Data Without Polluting the Frontend Contract

The current backend appends selected values to CSV in the same loop that
publishes frontend-facing streams.
That is acceptable when persistence is a side effect of the derived runtime,
not a UI concern.

Keep that persistence path separate from the connector contract:

- write storage files locally
- publish only the data the live consumer actually needs

If persistence grows more complex, move it into a dedicated storage utility or
storage-side task rather than bloating the frontend stream payload.

### 4) Expose a Clean Script Entry Point

Every backend should have a deterministic script entrypoint such as:

```python
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    BackendRuntime().run_forever()


if __name__ == "__main__":
    main()
```

This is not cosmetic.
It gives you a stable path for:

- local debugging
- process supervision
- user instructions
- future integration tests

### 5) Pick Predictable Missing-Data Behavior

The current app generally falls back to `0.0` when an upstream batch has not
arrived yet:

```python
copv = float(ecu_row[1]) if ecu_row is not None else 0.0
```

That is a valid choice if:

- the UI can tolerate it
- the consumer understands that "no data yet" and "zero" are conflated

If that is not acceptable for your app, make the choice explicit.
Do not let it emerge accidentally.

Reasonable alternatives are:

- publish `NaN` for unknown values
- hold the last good value
- gate downstream consumers until the first valid batch arrives

Choose one policy per stream and document it.

## UCIRPLGUI File Map

When you are extending the reference app, the backend-relevant files are:

- `apps/ucirplgui/src/ucirplgui/config.py`
- `apps/ucirplgui/src/ucirplgui/backend/pipeline.py`
- `apps/ucirplgui/scripts/run_backend.py`
- `apps/ucirplgui/scripts/run_all.py`
- `apps/ucirplgui/tests/test_dashboard_runtime.py`

That is the path for most dashboard-facing data work.
If the source is an actual board or simulator protocol, start one layer earlier
in [Device Interfaces](build-device-interfaces.md).

## Failure Modes Worth Designing For

The backend failures that matter in practice are:

- a receiver never gets data because the raw sender is on the wrong port
- a derived sender publishes the wrong shape
- a loop spins too fast and wastes CPU
- a loop assumes queued transport and silently drops intermediate states
- the frontend has to reconstruct raw protocol knowledge because the backend contract is underdesigned

A useful review rule is:

"Could a frontend engineer use this stream correctly without reading the board
decode logic?"

If the answer is no, the backend contract is still too raw.

## What To Read Next

- [Telemetry](build-telemetry.md) for stream contract definitions
- [Frontend](build-frontend.md) for binding backend outputs into widgets
- [Orchestrator](orchestrator.md) for broader runtime composition rules
