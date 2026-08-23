# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.12 is a hard requirement: `pythusa` publishes wheels for `>=3.12,<3.13` only.

### Run the GUI2.0 app

```bash
python apps/GUI2.0/scripts/setup_and_run.py
```

Builds `.venv`, installs what is missing, clears leftover processes, then hands
off to `run_all.py`. `--setup-only`, `--recreate`, `--skip-setup` and
`--keep-stale` change what it does; everything else is forwarded.

`run_all.py` starts the simulator, four device interfaces, the backend and the
frontend. The stale-process sweep is not optional: `pythusa` spawns
multiprocessing workers that outlive `run_all.py`, and a leftover worker keeps
holding the shared-memory ring, so the next backend dies with
`FileExistsError: [WinError 183] File exists: 'gse_stream'` before printing
anything useful.

### Run the GUI2.1 app against the simulator

```bash
python apps/GUI2.1/run_sim.py                 # the decay test passes
python apps/GUI2.1/run_sim.py --profile fail  # the decay exceeds 3 psi/min
python apps/GUI2.1/run_sim.py --real-window   # a 180 s decay window, not 20 s
```

No hardware needed: it starts the fake board, the frontend, the connector, the
backend and the scripted pressure playback.

### Tests

`unittest`, not `pytest`. There is no CI config and no linter config.

`viviian` is not installed into `.venv`, so every test command needs
`PYTHONPATH` — and **on Windows the separator is `;`, not `:`**. Getting that
wrong produces a `ModuleNotFoundError` that reads like a missing dependency.

```bash
# Repository tests
PYTHONPATH="packages/viviian_core/src" python -m unittest discover -s tests -t . -p "test_*.py"

# GUI2.0 app tests
PYTHONPATH="packages/viviian_core/src;apps/GUI2.0/src" python -m unittest discover -s apps/GUI2.0/tests -p "test_*.py"

# One test, one class, or one method
python -m unittest tests.test_state_machine
python -m unittest tests.test_state_machine.TestExpectedState
python -m unittest tests.test_state_machine.TestExpectedState.test_mismatch_aborts_by_default -v
```

`tests/test_state_machine.py` puts `apps/GUI2.1` on `sys.path` itself, so it
needs no `PYTHONPATH`.

A lot of `tests/test_gui_utils.py`, `tests/test_frontend.py` and
`tests/test_3dmodel.py` fails today because the `FakeImgui` stub has fallen
behind the widget code. Check whether a failure predates your change before
chasing it.

### Docs and benchmarks

```bash
mkdocs serve
python benchmarks/connector_throughput_benchmark.py
python benchmarks/datastorage_benchmark.py
```

## Path traps

- `docs/` and the root `README.md` say `apps/ucirplgui`; the real path is
  `apps/GUI2.0`. The runtime package inside it is still called `ucirplgui`.
- `packages/pythusa` is a git submodule and is usually not checked out —
  `pythusa` comes from PyPI, so there is no runtime source in the tree to read.
- `apps/GUI2.1/data/` holds thousands of gitignored parquet files. Exclude it
  from recursive searches.

## Architecture

One reusable toolkit and two operator applications. The two applications are
built completely differently, so read the one you are actually working in.

### `packages/viviian_core/src/viviian`

The reusable toolkit. The rule is a fast local runtime inside an orchestrator,
with explicit Arrow contracts between units.

- `orchestrator`: a thin `pythusa.Pipeline` subclass, the composition root. It
  owns stream, task and connector wiring, and no scheduling.
- `connector_utils`: `StreamSpec`, `SendConnector`, `ReceiveConnector` over
  Arrow Flight. Latest-only, no queue, freshest-wins by design. Batches are 2D
  `float64` arrays whose width must match the schema.
- `deviceinterface`, `datastorage_utils`, `gui_utils`, `frontend`,
  `simulation_utils`: device abstraction, storage, widgets, and simulation.

Two runtimes side by side: `pythusa` shared-memory rings inside a deployment,
Arrow Flight between processes.

### `apps/GUI2.0` (UCIRPLGUI)

A multi-process application that is built on `viviian`.

- `src/ucirplgui/config.py` owns every stream id, Arrow schema, port and UI
  constant. Start here — it is the contract the other layers share.
- `src/device_simulations/device_simulator.py` fakes the boards on TCP ports
  10002, 10004, 10006, and 10069.
- `src/ucirplgui/device_interfaces/device_interfaces.py` decodes one board and
  publishes raw telemetry. Run it with `--board gse|ecu|extr_ecu|loadcell`.
- `src/ucirplgui/backend/pipeline.py` reads the raw streams and derives the
  frontend streams: tank pressures, line pressures, load cell, and the FFTs.
- `src/ucirplgui/frontend/frontend.py` and `components/dashboard.py` render the
  operator dashboard.

Each layer is its own process; they meet only at the connector ports in
`config.py`.

### `apps/GUI2.1` (GSE2V1)

The flight application for the GSE2V1 board: flat modules in one directory,
run directly, and **not** built on `viviian`.

Data flow:

```
GSE2V1 board (TCP 10001) <-> gse21connector.py <-> Arrow Flight <-> frontendv2.py
                                                        |
                                                   backend.py -> parquet in data/
```

- `generic_connector.py`: the Flight server classes. `LatestServer` keeps the
  newest row plus a generation counter; `CommandServer` and `StorageServer`
  handle commands and storage.
- `gse21connector.py`: the wire format (`GSE2V1_DATA_FORMAT`,
  `GSE2V1_COMMAND_FORMAT`), telemetry TCP -> Flight and commands Flight -> TCP.
- `frontendv2.py`: hosts the Flight servers the connector and backend write
  into, and owns `PT_SCALES`, the pressure calibration.
- `gui_gse2v1.py`: the command buttons and the named valve tables in
  `TABLE_BUTTONS`. Every valve command goes through a `Button`.
- `state_machine.py` and `procedures/`: the GSE control state machine.

### The GSE control state machine

`docs/state-machine.md` is the design record — read it before changing the
engine or a procedure.

- `state_machine.py` is the engine, and knows nothing about imgui or the
  GSE2V1 buttons. It commands hardware through the `Effector` protocol and
  reads valve identity through `ValveMap`.
- `procedures/operations.py` implements both against `gui_gse2v1`, so
  automation moves the same buttons the operator does.
- `procedures/table_states.py` reads the named tables out of
  `gui_gse2v1.TABLE_BUTTONS` rather than restating them.
- `procedures/pressure_decay.py` is the first procedure, following section 3 of
  the MOCH4 Cold Flow document.

What the engine enforces:

1. Every sensor read goes through the per-cycle `Snapshot`; missing data reads
   as NaN, never 0.0.
2. Guards are written positively. `slope <= 3.0` is False for NaN (safe);
   `not (slope > 3.0)` is True for NaN (a bug).
3. A state with automatic exits must set `max_seconds`.
4. Two automatic exits at equal priority stop the machine and ask the operator.
5. An operation is actions, then lead time, then the destination table check,
   then the transition. A failed check panics; a stale feed holds instead,
   since being blind is not the same as being wrong. A mismatch only aborts
   while armed — disarmed, the operator is hand-flying.
6. `State.expected_state` names every steady valve — an absent key means
   "expect closed". `DONT_CARE` is how you say a valve is not pinned.
7. Exactly one state carries `start=True`.

Valve polarity is the trap: `pv2` and `tank_vent` are normally open, so
solenoid `False` means the valve is OPEN.

## Working rules

- Reusable code goes in `packages/`, deployment-specific composition in
  `apps/`.
- Treat stream ids, Arrow schemas and batch shapes as contracts.
- Add tests beside the layer you changed: `apps/<app>/tests` for an app,
  `tests/` for a package.
- Comments here explain *why*, and usually name the failure mode being guarded
  against. Match that rather than restating what the line does.
