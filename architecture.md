# VIVIIan Architecture

This repository-root copy is preserved as a mirror.
The canonical docs page lives at `docs/architecture.md`.

## Purpose

VIVIIan is a Python-first architecture for hardware-agnostic lab and research systems that need to:

- acquire typed numeric telemetry from arbitrary devices
- process that telemetry with predictable behavior
- persist raw and derived outputs
- drive operator-facing frontends
- remain reconstructable, testable, and maintainable as systems grow

This document describes the target architecture of VIVIIan.
It is intentionally prescriptive about system shape, contracts, and runtime boundaries.

## Design Principles

### 1. Pure Python, cross-platform, practical performance

VIVIIan is designed to be implemented in Python `3.12+` and to run cross-platform.
The goal is not to chase absolute peak throughput at any cost.
The goal is to build systems that are easy to reason about, easy to extend, and fast enough for real lab and research telemetry workloads.

### 2. Simplicity and maintainability before cleverness

The architecture favors:

- explicit typed contracts over implicit conventions
- bounded behavior over unbounded queues
- deterministic reconstruction over opaque runtime state
- narrow module responsibilities over large multi-role components
- code that fails clearly over code that tries to guess

If a design choice improves benchmark numbers but makes the system harder to understand, operate, or recover, it is not the default choice.

### 3. Numeric telemetry first

The primary optimization target is typed numeric time-series data and typed numeric control/setpoint data.
The core system is not a general-purpose message bus for arbitrary Python objects.

That means the architecture assumes:

- explicit schemas
- predictable frame shapes
- columnar transport and storage

### 4. Toolkit, not platform

VIVIIan is a composable systems toolkit.
It provides primitives, contracts, lifecycle patterns, and reconstruction mechanisms.
It does not prescribe one mandatory application topology beyond the boundaries defined in this document.

Users should be able to write their own device logic, processing logic, frontend composition, and deployment wiring while staying inside a coherent architecture.

### 5. Code-first system definition

The primary authoring model is Python code.
TOML export and reconstruction are convenience mechanisms for reproducibility, inspection, and rebuildability.
They are not the primary setup path for serious deployments.

## System Model

VIVIIan systems are composed from separately deployable units.
The common production shape is:

- many `deviceinterface` deployments
- one or more `backend` deployments
- one or more `frontend` deployments
- an `orchestrator` that compiles and launches the topology

These units may run on the same host, across a lab LAN, or across a wider trusted network.
The code should not assume co-location.
The architecture should still remain valid when each role is deployed independently.

The v1 trust model assumes trusted internal or lab environments.
Authentication, authorization, and transport hardening are valid future layers, but they are not first-class architectural requirements here.

## Runtime Boundary Rule

VIVIIan uses two different data movement models for two different jobs:

- `pythusa` is the hot path inside a deployment unit where fixed-shape numeric streams need low-overhead movement and processing.
- `pyarrow` is the transport and schema boundary between deployment units.

This boundary is fundamental.
It keeps the high-rate local runtime efficient while keeping the inter-unit interface explicit, rebuildable, and language-agnostic enough for long-term maintenance.

In short:

- inside a unit: optimize for efficient internal execution
- between units: optimize for stable contracts and transport clarity

Inside one deployment unit, the structural stream contract should remain
predictable, but one local `pythusa` task may still consume a different byte
window when it explicitly owns regrouping. The current endorsed local procedure
is to keep the stream definition normal, override binding-local `frame_nbytes`
on the side that wants a different local size, and use `look()` /
`increment()` there. This is a local runtime adaptation only. It does not alter
the cross-unit Arrow connector contract.

## Deployable Roles

### Device Interface

`deviceinterface` is the boundary between user-owned hardware logic and the rest of VIVIIan.

It is responsible for:

- acquiring data from hardware or hardware-adjacent user code
- normalizing that data into versioned typed payloads
- publishing outbound telemetry/state over Arrow-based connectors
- receiving inbound command/setpoint tables over Arrow-based connectors
- interpreting those command tables and deciding what device-side action to take

It is intentionally hardware-specific internally.
VIVIIan does not care how a user talks to a DAQ, serial device, CAN bus, FPGA bridge, lab instrument, or custom control stack.
The contract begins at the point where data enters or leaves the device interface through a typed connector.

The device interface is therefore not just an adapter.
It is the authoritative owner of:

- hardware communication details
- device-local command interpretation
- acquisition-time stamping
- device-local safety or validation logic

If a control message says "set valve target to 0.35" or "set motor speed target to 1200 rpm", the device interface decides what hardware operations that implies.

### Backend

`backend` is the aggregation, processing, persistence, and republishing layer.

It is responsible for:

- ingesting telemetry/state from one or more device interfaces
- running user-defined processing DAGs over typed streams
- persisting configurable raw streams, derived streams, and session metadata
- republishing selected outputs for frontend consumption
- maintaining a clean separation between live processing and durable archival

The backend is not required to publish a fixed universal API to the frontend.
Different deployments may expose different derived products, summaries, alerts, raw streams, or downsampled outputs.
That choice is deployment-defined, not architecture-defined.

Internally, the backend uses `pythusa` for local stream movement and processing DAG execution when high-rate paths matter.

### Frontend

`frontend` is the operator-facing consumer and control surface.

It is responsible for:

- consuming Arrow-exposed streams from one or more backends
- rendering operator desks, graphs, 3D views, buttons, gauges, and other controls
- performing small local view-layer computations when needed
- emitting one-way typed command/setpoint payloads directly to device interfaces

The frontend is not the source of truth for whether a command "worked."
The source of truth is subsequent device state telemetry.

This is deliberate.
It avoids building a second semantic control protocol beside the data plane.
The frontend sends desired-state or setpoint updates, and the operator observes their effect through the next published state.

### Orchestrator

`orchestrator` is the topology compiler and launcher.

It is responsible for:

- defining system topology in code
- wiring units to explicit endpoints
- generating or materializing reconstructable deployment descriptions
- launching and supervising deployment units
- keeping deployment logic out of the hot path

The orchestrator is not a central runtime brain.
It should not sit inline on the data path or act as a live routing coordinator for every message.

Its job is to turn system definition into running units with coherent contracts.
That includes wiring the structural stream contract, not deciding every
task-local read window inside a deployment. If a backend task aggregates or
splits local `pythusa` frames by overriding binding-local `frame_nbytes`, that
remains an internal runtime concern of that unit rather than an orchestrator
topology concern.

## Core Architectural Objects

The detailed implementation may use different class names, but the architecture assumes the following logical objects exist.

### Connector

`Connector` is the generic inter-unit transport abstraction. There is one connector type in VIVIIan — not separate primitives for data, commands, and health. All cross-unit traffic moves through the same abstraction, bound to different schemas for different purposes.

A connector is defined by:

- a `StreamSpec` describing the payload schema and version
- a direction (inbound or outbound)
- transport endpoint information
- bounded buffering behavior
- reconstruction metadata

This covers everything: telemetry from device interfaces to backends, processed outputs from backends to frontends, and commands from frontends directly to device interfaces. The connector does not know or care what the payload means — that is the `StreamSpec`'s job. The connector's job is to move typed PyArrow tables between units reliably and within defined bounds.

### StreamSpec

`StreamSpec` defines a versioned schema contract for any typed tabular payload.

It should capture at minimum:

- stream identity
- schema version
- field definitions
- optional units or domain metadata
- compatibility expectations

The architecture assumes telemetry and state are structured, typed, and versioned.
Loose dynamic payloads are not the default.

### Structural Representation

All structural objects should support deterministic structural representation and reconstruction.

This applies to things like:

- connector definitions
- stream specs
- processing graph definitions
- frontend composition structures
- deployment topology structures

This does not apply to ephemeral runtime state like:

- queue contents
- socket handles
- active threads
- process-local caches
- instantaneous metrics snapshots

The goal is that a structural object can describe itself in a rebuildable way and that parent objects can recursively compose representations from their children.

This is the same general pattern already used in the GUI utilities:

- child object returns structural data
- parent object builds its own representation using child structure
- a full topology can be exported or reconstructed without serializing live runtime internals

## Data Plane

The default telemetry flow is:

1. hardware or user device code produces raw measurements
2. `deviceinterface` converts them into typed versioned payloads 
3. a generic Arrow connector sends those payloads to `backend`
4. `backend` ingests, processes, and optionally persists them
5. `backend` may publish selected outputs through Arrow connectors to one or more `frontend` deployments
6. `frontend` renders those outputs for operators

The backend-to-frontend payload shape is intentionally not fixed at the architecture level.
One deployment may publish mostly raw streams.
Another may publish only derived views.
Another may publish a mix of raw, filtered, aggregated, and event-like summaries.

The architecture should enable all of those without changing the core API.

## Control Plane

The default control flow is:

1. `frontend` emits a typed Arrow table bound to a `StreamSpec`
2. the command travels directly to the relevant `deviceinterface`
3. `deviceinterface` interprets the payload and applies the corresponding device-local behavior
4. the next outbound telemetry/state from that device reflects the new device state

The control path is intentionally direct from frontend to device interface.
The backend is not the required command authority.

This reduces architectural coupling and allows a device interface to remain the single source of hardware-side truth.
It also means the system should not assume backend-mediated validation for all commands.

The architecture treats command effects as observable through state.
It does not require a second reply protocol that returns a different semantic message type back to the frontend.

## Processing Model

Backend processing is defined as a user-composed DAG over typed streams.

The architecture does not enforce one universal ordered chain like:

- ingest
- normalize
- derive
- alert
- persist
- serve

Those stages may exist in a particular deployment, but the default architecture is more general:

- users compose the graph they need
- the runtime provides strict stream contracts and efficient local execution
- the orchestrator wires the pieces together

This fits research systems better because real pipelines diverge quickly:

- some systems need simple pass-through archival
- some need DSP or filtering
- some need fusion across many devices
- some need alerting or trigger derivation
- some need only a curated frontend feed

The architecture should support all of these as compositions over the same primitives.

## Storage Model

Durable storage is backend-owned.

The backend should persist:

- raw telemetry when the deployment requests it
- derived streams when the deployment requests it
- session metadata needed for replay, audit, and reconstruction

The default physical storage model is append-only columnar storage.
In practice this means Arrow/Parquet-style storage is the architectural default.

This choice fits the rest of the system because:

- the live wire format is already Arrow-oriented
- the data is typed and columnar
- offline analysis benefits from columnar storage
- append-oriented archival is operationally simple

The architecture does not require every deployment to persist every stream.
Persistence scope is deployment-defined.
But storage ownership still belongs to the backend layer.

## Delivery And Backpressure Semantics

VIVIIan prefers liveness and bounded behavior over unbounded lossless queues.

The default live-path delivery semantics are freshest-wins.
This means:

- connectors are bounded
- slow consumers do not force unbounded memory growth
- under pressure, older payloads may be discarded in favor of newer payloads
- the system remains live and responsive rather than stalling globally

This is acceptable for the target workload because:

- telemetry is primarily observational
- frontends usually need current state more than perfect live replay
- command traffic is primarily desired-state or setpoint oriented rather than fire-once imperative transactions

If the latest target value is "0.35", losing intermediate values while keeping the newest target can still preserve useful semantics.

The architecture therefore assumes:

- the live path is not the authoritative replay path
- durability is handled by backend-owned storage when replay matters
- stream schemas should be designed with bounded, freshest-wins behavior in mind

Designs that require guaranteed delivery of every live command and every live frame are outside the default architecture and should be treated as specialized cases.

## Failure And Recovery Model

The architecture should treat the following as normal conditions, not exceptional system collapse:

- a device interface disconnecting and reconnecting
- a backend restarting
- a frontend reconnecting
- a connector temporarily losing its peer

A robust VIVIIan system should recover through explicit lifecycle behavior:

- connectors reconnect cleanly
- schema validation reruns on re-establishment
- bounded queues reset to known states
- frontends resume consuming current state when streams return
- device state is re-observed through telemetry rather than guessed

The architecture should not depend on hidden mutable state that only exists in one long-lived process.

## Endpoint And Topology Model

Deployment units find each other through explicit configured endpoints.

The architecture does not assume:

- service registries
- dynamic discovery meshes
- hidden runtime brokers

This keeps topology understandable.
An operator or developer should be able to inspect a deployment definition and know:

- which unit binds where
- which unit connects to which peer
- which schemas are expected on each boundary

The common topology is many devices feeding one or more backends with one or more frontends attached.
The architecture should support other shapes, but this is the shape the document should optimize for conceptually.

## Schema Discipline

All cross-unit payloads must be strict and versioned.

That means:

- schema mismatches fail clearly
- silent field drift is unacceptable
- compatibility rules must be defined per schema family
- reconstruction must know what versioned object it is rebuilding

This is essential for maintainability.
Without schema discipline, distributed Python systems turn into undocumented conventions very quickly.

Strict schemas are especially important here because:

- devices and frontends are separately deployable
- research systems evolve quickly
- data needs to remain analyzable after acquisition
- operators need confidence that plotted data means what it claims to mean

## Reconstruction And Rebuildability

VIVIIan should make structural rebuildability a first-class architectural property.

Every major structural object should be able to answer, in a deterministic way:

- what am I
- what are my parameters
- what are my child objects
- how can I be reconstructed

This supports:

- reproducible deployments
- offline inspection
- topology export
- auditability
- simpler testing

The intended pattern is recursive:

1. leaf objects expose structural representation
2. parent objects request child structure
3. parent objects embed or reference that structure in their own representation
4. the full object graph can be reconstructed without serializing live process state

TOML is a good export format for this because it is readable and versionable.
But the architecture remains code-first.
Users should be able to define a system in Python and export its structure, not be forced to author everything as configuration files.

## Observability Philosophy

The architecture should keep observability aligned with the data model instead of inventing separate bespoke control channels everywhere.

In practice this means:

- device state telemetry is the primary way to observe command effect
- backend persistence is the primary way to support replay and audit
- structural representations are the primary way to inspect configuration intent
- metrics and health reporting should be additive, not replacements for typed state streams

Observability is important, but it should not fracture the system into many unrelated protocols.

## What The Architecture Optimizes For

This architecture is optimized for:

- hardware heterogeneity
- typed numeric telemetry
- efficient local processing
- separately deployable units
- deterministic reconstruction
- bounded live behavior
- maintainable long-lived systems

It is not optimized first for:

- arbitrary object transport
- globally lossless live delivery
- centralized command mediation
- framework-enforced application topology
- dynamic discovery-heavy infrastructure

## Canonical End-To-End Example

A representative VIVIIan deployment looks like this:

1. a device interface talks to a DAQ and a serial controller
2. it timestamps each acquisition at the device boundary
3. it emits Arrow telemetry/state tables to a backend
4. the backend ingests those tables and runs a `pythusa` DAG that filters, derives, and republishes selected outputs
5. the backend stores raw streams and selected derived streams in append-only columnar archives
6. a frontend subscribes to backend-published streams and renders graphs, buttons, gauges, and 3D views
7. the frontend sends a typed desired-state command directly to the device interface
8. the device interface applies the new target and the next state telemetry reflects the resulting device state

This is the intended steady-state model.

## Final Position

VIVIIan should be understood as a strict, reconstructable, Python-first systems architecture for telemetry and control.

Its central design commitments are:

- `pythusa` for efficient local stream processing
- `pyarrow` for explicit inter-unit boundaries
- frontend-to-device one-way typed commands
- backend-owned storage
- code-first topology with deterministic reconstruction
- bounded freshest-wins live behavior

If those constraints are preserved, the system can remain efficient, maintainable, and evolvable even as individual devices, processing graphs, and frontends become much more complex.
