# Data Storage

`ParquetDatabase` is an append-only columnar store for numeric telemetry.
It writes Snappy-compressed Parquet files in fixed-size chunks and maintains a
manifest for time-range queries.
It does not expose a query language — it exposes `store` and `retrieve`.

## Install

```bash
pip install -e .
```

```python
from viviian.datastorage_utils import ParquetDatabase
```

## Schema Contract

`ParquetDatabase` accepts numeric Arrow schemas only.
Allowed field types: `int8`, `int16`, `int32`, `int64`, `uint8`, `uint16`,
`uint32`, `uint64`, `float32`, `float64`.

String, binary, list, timestamp, and nested types are rejected at construction.

```python
import pyarrow as pa

schema = pa.schema([
    pa.field("pressure_kpa", pa.float64()),
    pa.field("temperature_c", pa.float32()),
])
```

## Shape Contract

`shape=(rows, cols)` describes the exact shape of every batch passed to `store`.

- `rows` — number of rows per batch
- `cols` — must equal `len(schema)`

Every call to `store` must supply a NumPy array with exactly this shape.
The database does not accept ragged or variable-length batches.

`rows_per_file` controls how many rows accumulate before the buffer flushes to
a Parquet file.
It must be a positive multiple of `shape[0]`.
If omitted, it defaults to `shape[0] * 1024`.

## Basic Usage

```python
import numpy as np
import pyarrow as pa
from viviian.datastorage_utils import ParquetDatabase

schema = pa.schema([
    pa.field("pressure_kpa", pa.float64()),
    pa.field("temperature_c", pa.float64()),
])

with ParquetDatabase("./telemetry_db", schema, shape=(16, 2)) as db:
    for _ in range(10):
        batch = np.random.default_rng().standard_normal((16, 2))
        db.store(batch)
```

Use `ParquetDatabase` as a context manager.
Exiting the context flushes any buffered rows before closing.

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `root` | `str \| Path` | required | Directory for all database files. Created if it does not exist. |
| `schema` | `pa.Schema` | required | Numeric-only Arrow schema. |
| `shape` | `tuple[int, int]` | required | `(rows_per_batch, columns)`. Must match schema field count. |
| `rows_per_file` | `int \| None` | `shape[0] * 1024` | Rows to accumulate before writing a Parquet file. Must be a multiple of `shape[0]`. |
| `compression` | `str` | `"snappy"` | Parquet compression codec. |

## Writing Data

```python
batch = np.array([[1.0, 20.5], [1.1, 20.6]], dtype=np.float64)
db.store(batch)
```

`store` accepts a 2D NumPy array with shape exactly matching `shape`.
Rows are buffered in memory.
The buffer flushes to a Parquet file when it reaches `rows_per_file` rows.
Calling `close()` (or exiting the context manager) flushes any partial buffer.

All input dtypes are cast to `float64` internally before writing.

## Reading Data

```python
table = db.retrieve()                             # all rows
table = db.retrieve(start_ns=t0, end_ns=t1)      # time range [t0, t1)
```

`retrieve` returns a `pa.Table`.
The table schema prepends a `database_timestamp_ns: int64` column to your
declared schema:

```
database_timestamp_ns | pressure_kpa | temperature_c
```

Time bounds are integer nanoseconds (`int64`).
The range is half-open: `[start_ns, end_ns)`.
Both bounds are optional — omit either to leave that end unbounded.

If `retrieve` is called while data sits in the buffer, the buffer is flushed
first so the result is always complete.

## Timestamps

`ParquetDatabase` generates its own `database_timestamp_ns` values using
`time.time_ns()`.
These are wall-clock nanosecond timestamps assigned at flush time, not at
`store` call time.
Timestamps are strictly monotonically increasing across all rows within a
session and across session boundaries.

These timestamps are the storage index.
They are not the device-level timestamps in your schema fields — those are
stored verbatim in your declared columns.

## File Layout

```
./telemetry_db/
  metadata.json          — schema, shape, rows_per_file, compression
  manifest.jsonl         — one JSON line per Parquet file with time bounds and row count
  part-00000001.parquet
  part-00000002.parquet
  ...
```

### metadata.json

Written once at database creation.
Re-opening an existing database validates all fields against this file and
raises `ValueError` on any mismatch.

### manifest.jsonl

Append-only.
Each line is a JSON object:

```json
{
  "file_name": "part-00000001.parquet",
  "row_count": 1024,
  "start_database_timestamp_ns": 1713000000000000000,
  "end_database_timestamp_ns":   1713000000000001023
}
```

`retrieve` uses the manifest to prune which files need to be opened for a
given time range.

### Part Files

Each part file contains exactly `rows_per_file` rows, except the final
part file written on close which may contain fewer.
Part files are numbered from 1 and are never rewritten after being written.

## Reopening a Database

Opening the same `root` directory again with matching constructor arguments
appends to the existing database.
The next part file continues numbering from where the previous session left off,
and timestamps are guaranteed to be greater than the last written timestamp.

Opening with mismatched `schema`, `shape`, `rows_per_file`, or `compression`
raises `ValueError`.

```python
with ParquetDatabase("./telemetry_db", schema, shape=(16, 2)) as db:
    db.store(first_batch)

# resume in a later session
with ParquetDatabase("./telemetry_db", schema, shape=(16, 2)) as db:
    db.store(second_batch)
    table = db.retrieve()  # contains all rows from both sessions
```

## What This Is Not

`ParquetDatabase` is not:

- a query engine — use PyArrow or DuckDB directly on the part files for complex queries
- a streaming sink — batches must match the declared shape exactly
- a time-series database — timestamps are storage indices, not physical time guarantees

## What To Read Next

- [Device Interface](deviceinterface.md) — feed device output into a database
- [Connectors](connectors.md) — live transport for current-state distribution
- [Architecture](architecture.md) — how storage fits the full system
