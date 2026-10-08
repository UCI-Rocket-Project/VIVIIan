# Writing GSE procedures

The GUI2.1 engine is in `apps/GUI2.1/state_machine.py`. Procedure definitions
live in `apps/GUI2.1/procedures/`; `pressure_decay.py` is the complete example.
The engine has no ImGui dependency. Hardware commands go through an `Effector`,
and valve feedback goes through a `ValveMap`.

## A procedure in Python

A **state** describes a stable valve configuration. An **operation** names a
destination and the actions needed to get there. A **guard** decides when an
automatic operation may start. Manual operations wait for an operator request.

Use `State.define(name, *operations, **settings)` to put exits directly inside
their state. All state settings are keyword-only; no surrounding exit tuple or
positional `None` placeholders are needed. The original `State(...)` constructor
remains supported.

This small authoring example uses an illustrative pressure target, not an
approved operating procedure:

```python
from state_machine import Machine, MismatchPolicy, State
from procedures import table_states
from procedures.operations import (
    apply_table, auto_operation, close_valve, manual_gate,
    manual_operation, open_valve, panic_operation,
)


def build_machine():
    return Machine.build(
        "fill_example",
        [
            State.define(
                "READY",
                manual_operation(
                    "Begin fill", dest="FILLING",
                    actions=[open_valve("sol_gn2_fill_1")],
                ),
                expected_state=table_states.ALL_OFF,
                start=True,
            ),
            State.define(
                "FILLING",
                auto_operation(
                    "Close fill at target", dest="DONE",
                    actions=[close_valve("sol_gn2_fill_1")],
                    guard=lambda ctx: ctx.psi("COPV") >= 200.0,
                    guard_text="COPV >= 200 psig",
                ),
                expected_state=table_states.valves(sol_gn2_fill_1=True),
                max_seconds=900.0,
            ),
            State.define(
                "DONE",
                manual_gate("Restart", dest="READY"),
                expected_state=table_states.ALL_OFF,
            ),
            State.define(
                "ABORTED",
                manual_operation(
                    "Return to ALL OFF", dest="READY",
                    actions=[apply_table("all_off")],
                    lead_time_s=1.5,
                ),
                expected_state=table_states.ABORT,
                on_mismatch=MismatchPolicy.WARN,
            ),
        ],
        default_panic=panic_operation("ABORTED"),
    )
```

Create fresh states, operations and actions inside each build: operations and
actions carry mutable execution state. `Machine.build` links destination names
to states and rejects duplicate names, unknown destinations and conflicting
start declarations. Mark exactly one state `start=True`. For compatibility,
`initial="READY"` can instead mark the start; if both are given they must agree.

## State settings

| Keyword | Meaning |
| --- | --- |
| `expected_state` | Solenoid feedback table; `None` disables table verification for this state. |
| `start=False` | Marks the single initial state. |
| `panic=None` | Per-state safe-out; omission inherits `Machine.default_panic`. `None` does not disable that default. |
| `max_seconds=None` | Time allowed in this state. Required for local automatic exits. Expiry requests panic, including while disarmed. |
| `on_mismatch=MismatchPolicy.ABORT` | `ABORT`, `WARN` or `IGNORE`; automatic mismatch panic occurs only while armed. |
| `mismatch_grace_s=0.75` | Entry grace before checking steady-state valve disagreement. |
| `entry_from=None` | Optional set of permitted source names, checked for local operations by the static audit. |
| `description=""` | Operator-facing explanation. |

The abort destination normally uses `WARN`: an abort-on-mismatch state whose
safe-out returns to itself is rejected by validation. Panic into the current
state halts rather than repeatedly commanding the same safe-out.

## Operation and action reference

Factories in `procedures.operations` return ordinary engine `Operation` objects:

| Factory | Use |
| --- | --- |
| `manual_gate(label, dest=...)` | Operator acknowledgement with no actuator actions. Still verifies the destination. |
| `manual_operation(label, dest=..., actions=[...])` | Operator-requested sequence of actions. |
| `auto_operation(label, dest=..., actions=[...], guard=...)` | Sensor-driven exit; `guard_text` explains its criterion in the panel. |
| `panic_operation(dest)` | Abort table plus alarm; bypasses destination verification and the abort latch. |

Manual and automatic operations accept `description`, `timeout_s` (default
30 seconds; `None` disables it), `lead_time_s` (default 0.75 seconds), and
`verify_dest` (default `True`). The timeout covers the operation, including
actions, lead time and verification. Whole-table moves should use
`TABLE_LEAD_TIME_SECONDS` (1.5 seconds). Gates accept description and lead time.

Manual operations also accept `requires_captcha`; the GUI implements the
confirmation gate. Automatic operations accept `priority` (higher wins) and
`mutually_exclusive_with`, a sequence of other operation labels. Declaring
mutual exclusion silences a static tie warning; it never bypasses the runtime
tie check. If equally ranked exits are both true, automation stops for a choice.

For exceptional needs, construct `Operation(actions=..., dest_state=...,
auto=..., name=..., ...)` directly. `Machine.build(global_transitions=[...])`
adds guards checked across states. Abort-priority globals can interrupt an
operation or unresolved tie; use `overrides_abort=True` only for commands that
must be allowed while the hardware abort is latched.

Actions execute in order, at most one per control cycle:

- `open_valve(id)` / `close_valve(id)` express physical valve position and handle
  normally-open polarity. `confirm=True` waits for individual valve feedback.
- `apply_table(name)` applies a named operator table, such as `"all_off"` or
  `"abort"`.
- `pulse(id)` stages a momentary command; the hardware adapter handles release.
- `countdown(seconds, label)` waits while displaying a label.
- Engine `WaitUntil(predicate, ...)` waits for a condition; use a bounded timeout.
- Engine `ResetSlopeWindow()` starts a fresh decay measurement.
- Engine `Call(callback, label)` calls `callback(ctx, effector)` for bookkeeping.
- Engine `SetValve(id, bool)` uses raw solenoid state, not physical open/closed.

An operation completes only after actions, lead time and destination verification.
Disagreement fails the operation and invokes the source state's safe-out. Stale
board telemetry holds verification for up to five additional seconds (subject
to the operation timeout); a missing field on a healthy feed fails immediately.
Abort operations use `verify_dest=False` so failed feedback cannot prevent landing.

## Valve expectations and sensor guards

Use named tables or `table_states.valves(...)` to describe every steady solenoid.
`True` means energized. **PV 2 and tank vents are normally open**: `False` means
physically open for these valves. ALL OFF is not the same configuration as ABORT.

`valves(...)` starts from ALL OFF, or an explicit base table, then applies named
overrides. Use `unchecked(table, "valve_id")` or `DONT_CARE` to explicitly leave
a valve unconstrained. The low-level engine only checks entries supplied in a
mapping; use the full table helpers instead of sparse dictionaries in procedures.
Unspecified solenoids in the full ALL OFF table are de-energized, which is not
necessarily physically closed. `bool(DONT_CARE)` deliberately raises an error.

Every guard receives a `ControlContext` backed by one snapshot per control cycle:

| Read | Result |
| --- | --- |
| `ctx.psi("COPV")` | Calibrated pressure; missing/stale data is NaN. |
| `ctx.actual("pv1")` | Reported solenoid state, or `None` if unavailable. |
| `ctx.in_state_for()` | Seconds since state entry. |
| `ctx.in_operation_for()` | Seconds since operation start. |
| `ctx.slope_ready()` | Whether the decay tracker has a full measurement window. |
| `ctx.slope_psi_per_min(field)` | Signed rate of pressure change. |
| `ctx.decay_psi_per_min(field)` | Pressure loss rate (negative of signed slope). |
| `ctx.worst_slope(fields)` | Largest absolute slope; NaN if any requested section is unreadable. |
| `ctx.settled(fields, limit)` | Whether all sections are within the separate settling-window limit. |

Write positive comparisons: `value <= limit` is false for NaN;
`not (value > limit)` incorrectly passes NaN. Guards should only read data;
put mutations in actions. Exceptions in guards are logged and treated as false.

## Validation and runtime integration

Call `machine.validate()` and fix every returned message before use. It checks
watchdogs, possible priority ties, valve-table contradictions, safe-outs and
reachability. `Dispatcher.arm()` refuses outstanding problems. Validation is
a static audit, not proof that arbitrary Python guards or callbacks are safe.

The frontend supplies `ControlContext`, `Effector` and `ValveMap`, constructs
`Dispatcher(machine, ctx, effector)`, then calls `tick()` each frame. The
dispatcher caps control cycles at 50 ms. Its public controls include `arm()`,
`disarm()`, `request(operation)`, `panic()`, `choose(operation)` for a tie,
and `resume()` after suspension. Raw valve commands notify
`note_manual_command(id)` and follow the configured interrupt policy.
`force_state(name)` is an operator override that skips operation actions and
destination verification. Disarming prevents ordinary automatic exits; it
does not cancel an already-running operation.

`Operation.feedback`, `Dispatcher.last_feedback`, and `last_failure` expose
phase, timing, verification hashes, mismatches and unreadable fields for the UI.

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe -m unittest tests.test_state_machine
.venv/Scripts/python.exe -m unittest discover -s apps/GUI2.1/tests -p 'test_*.py'
```

See [the design record](state-machine.md) for rationale and unresolved propulsion
decisions. Procedure thresholds and hardware timing still require engineering
review; tests establish software behavior, not physical valve response.
