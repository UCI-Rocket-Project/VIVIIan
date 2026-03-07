# Manager/Worker v2 Architecture Plan (Speed First)

## Goals
1. Preserve and improve throughput/latency on the hot path.
2. Make runtime ownership and cleanup deterministic.
3. Provide a simple import/use API without hiding expert controls.

## Current Behavior (What the code is doing now)
1. `Manager` is a control-plane registry/factory for:
   - shared memory specs and instances
   - worker events
   - worker definitions
2. `AbstractWorker` is mostly a worker spec container (`proc_func`, memory specs, events, args).
3. Execution loops are implemented outside core modules (benchmark/playground), where processes:
   - open shared memory handles
   - wait/reset events
   - run `worker.proc_func(...)`
   - signal events
4. `AbstractWorker` contains duplicated bootstrap-style loops (`_bootstrap`, `_make_bootstrap`) that overlap in responsibility.

## Problems to Fix
1. Runtime logic is duplicated across scripts.
2. Public usage currently depends on private attributes (for example `_workers` and `_worker_events`).
3. Control plane and runtime plane are not clearly separated.
4. There is no single obvious "start/stop/join" API for users.

## Proposed Architecture
1. `specs` layer (pure configuration):
   - `RingSpec` (name, size, readers, cache settings)
   - `WorkerSpec` (name, proc func, IO bindings, wait/signal bindings, args/kwargs)
   - optional `PipelineSpec`/`GraphSpec` for compiled topology
2. `runtime` layer (single canonical execution model):
   - one worker entry loop (the only process target)
   - one process supervisor for spawn/start/stop/join
   - one cleanup strategy for all shared resources
3. `api` layer (ergonomic facade):
   - simple builder for common usage
   - explicit expert API for full control

## Canonical Runtime Loop (Single Source of Truth)
1. Child process receives only serializable worker spec + runtime context.
2. Open all input/output shared memory once at startup.
3. Build/resolve runtime args once.
4. Loop:
   - wait on required events
   - reset wait events
   - call `proc_func`
   - signal downstream events
5. On stop/error:
   - close/release all memory views and rings
   - return metrics/status to parent

## Public API Shape
1. Expert API:
   - `Manager.create_ring(...)`
   - `Manager.create_event(...)`
   - `Manager.create_worker(...)`
   - `Manager.start()`, `Manager.stop()`, `Manager.join()`, `Manager.close()`
2. Simple API:
   - `Pipeline().ring(...).worker(...).connect(...).run()`
3. Avoid exposing private fields as part of normal usage.

## Performance-First Rules
1. Keep hot-path branch count low in the worker loop.
2. Avoid allocations in the loop; pre-allocate and reuse where possible.
3. Open handles once per process; avoid repeated init in the loop.
4. Prefer batch processing over tiny message granularity.
5. Keep synchronization count minimal per cycle.
6. Keep metrics optional and cheap (sampling or lightweight counters).
7. Use a single multiprocessing context strategy consistently (`spawn` or explicit configurable context).

## Usability Rules (After speed constraints are met)
1. One obvious way to run a pipeline (`start/stop/join` lifecycle).
2. Safe defaults for ring/event creation.
3. Clear error messages for invalid topology or mismatched bindings.
4. Typed signatures and docs for common workflow.

## Similar Design Strategies to Follow
1. Control plane vs data plane separation (common in stream/dataflow systems).
2. Graph compilation before execution (resolve bindings once).
3. Deterministic ownership model (creator vs opener, explicit cleanup).
4. Single runtime primitive for workers (no duplicate bootstrap variants).
5. Fast path and debug path split (debug checks do not pollute hot path).

## Migration Plan
1. Phase 1: Introduce specs (`RingSpec`, `WorkerSpec`) without breaking current scripts.
2. Phase 2: Add runtime supervisor and canonical worker loop in core module.
3. Phase 3: Move benchmark/playground to use core runtime API only.
4. Phase 4: Remove duplicated bootstrap logic and private-field dependencies.
5. Phase 5: Add docs/examples for simple and expert APIs.

## Acceptance Criteria
1. Benchmark/playground run with no direct access to manager private fields.
2. Exactly one canonical worker runtime loop in core code.
3. Equal or better throughput than current benchmark baseline.
4. Deterministic teardown with no shared memory leaks on normal and error exits.
5. New user can build and run a basic pipeline with minimal code.
