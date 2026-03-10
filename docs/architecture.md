# Architecture for the [VIVIIan Client](./)

## Overview

The basic design of the [VIVIIan](./) client consists of:

1. A network reader
2. A data-processing pipeline
3. A display/GUI layer

Data in [VIVIIan](./) is primarily handled using [Apache Arrow](https://arrow.apache.org/) / `pyarrow`.
Time-series data maps naturally to Arrow's columnar model, and the preferred design assumes data arrives over the network already serialized in an Arrow-compatible format (`pyarrow` on the producer side).

Display for the VIVIIan client is handled through [Dear ImGui](https://github.com/ocornut/imgui) (via Python bindings).
It is a low-overhead, high-performance C++ UI library, and the Python bindings allow relatively simple UI creation with low maintenance cost.

DSP and general data processing for VIVIIan is handled through [NumPy](https://numpy.org/) and [SciPy](https://scipy.org/), with optional acceleration using [Numba](https://numba.pydata.org/) (`njit`) on supported systems.

## QuestDB

[QuestDB](https://questdb.io/) should be treated as the long-term storage and query engine for historical telemetry, not as the primary in-memory transport format for live rendering.

Recommended role of QuestDB in the client architecture:

1. Store persisted time-series data for replay, analysis, and export
2. Serve historical query windows (for example, "last 10 minutes" or a selected time range)
3. Support backfill when a user opens a chart or changes the viewed range
4. Provide a durable source of truth for data that has already been recorded

Design constraints:

1. The GUI should not block on database reads
2. Live rendering should continue even if QuestDB is temporarily slow or unavailable
3. Historical fetches should be paged/windowed and merged into the visualization pipeline asynchronously

## Target Dataflow (Proposed)

This section describes the preferred dataflow for a better version of the client and intentionally does not mirror the current implementation.

### High-Level Flow

1. **Acquisition/Ingress**
   Receive live telemetry from the network source(s) already encoded in `pyarrow` format.
2. **Arrow Deserialize + Validate**
   Deserialize Arrow payloads, validate schema/version compatibility, and reject malformed or unsupported batches.
3. **Columnar Buffering**
   Append validated Arrow batches (or selected columns) into bounded in-memory columnar buffers.
4. **Processing Pipeline**
   Run optional transforms (unit conversion, filtering, derived channels, DSP).
5. **Fan-out**
   Publish processed data to:
   - the live UI renderer
   - recording/persistence workers (including QuestDB)
   - optional logging/export sinks
6. **UI Presentation**
   Render charts, tables, and status widgets from a snapshot/read model, not directly from the ingress thread.
7. **Historical Backfill**
   Query QuestDB asynchronously and merge historical windows into the same UI read model used by live data.

### Dataflow Principles

1. **Single-write, multi-read**
   One ingestion path writes to canonical buffers; downstream consumers read snapshots or queued batches.
2. **Bounded memory**
   Live buffers should be size-limited by time window and/or sample count.
3. **Backpressure-aware**
   If processing or rendering falls behind, degrade gracefully (drop frames, decimate, or coalesce updates) instead of blocking ingestion.
4. **Schema-first**
   Channels/fields should be defined by a versioned Arrow schema so ingress validation, transforms, and UI widgets can validate assumptions.
5. **Async persistence**
   Database writes must not sit on the rendering path.
6. **Deterministic transforms**
   Derived values should be computed in a well-defined stage so replay and live modes produce equivalent results.

### Suggested Pipeline Stages

1. **Ingress Thread / Task**
   Handles socket IO and packet framing only.
2. **Arrow Deserialize/Validate Stage**
   Deserializes network payloads into Arrow `RecordBatch`/table objects and rejects malformed or incompatible batches with metrics.
3. **Canonical Batch Adapter**
   Reorders/selects columns and applies schema-version adapters as needed for efficient Arrow/NumPy processing.
4. **Transform Stage**
   Applies scaling, calibration, filtering, and derived signal generation.
5. **Publish Stage**
   Emits immutable batches/snapshots to UI and storage queues.
6. **Persistence Worker**
   Writes batches to QuestDB and handles retry/reconnect logic.
7. **UI State Aggregator**
   Maintains chart-ready windows, decimated views, and user-selected channel state.

### Live + Historical Merge Strategy

1. Live data and historical data should converge into the same chart data model.
2. Historical ranges should load in chunks to avoid freezing the UI.
3. A chart can render immediately from live memory, then improve as historical backfill arrives.
4. Merge logic should de-duplicate by `(timestamp, channel)` (or the canonical primary key used by the stream).

### Failure Handling

1. **Network failure**
   Show connection status, retain last buffered data, and allow automatic reconnect.
2. **QuestDB failure**
   Continue live viewing if possible; mark persistence status degraded.
3. **Processing overload**
   Prefer update decimation and reduced render frequency over data-path stalls.
4. **Schema mismatch**
   Fail the affected stream/channel clearly and surface diagnostics in the UI.

## Current Dataflow (Legacy Note)

The current system may differ from the target architecture above.
This document's dataflow section is the preferred design direction: network-delivered `pyarrow` data, Arrow-native validation/buffering, asynchronous persistence, and a UI fed from snapshots instead of direct process coupling.








## Ammends

the manager for the netoworked and processor shared queues is going to be a thread on the display thread, should have minimal interaction until the gui requests a change
this way they can care the same space and will both refresh upon rendered frames or gui interactions 
