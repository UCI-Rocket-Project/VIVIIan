"""
GSE control state machine.

Vocabulary follows docs/state-machine.md:

- **Action** — one atomic thing done to the system (set a valve, wait, pulse).
- **Operation** — a reusable sequence of Actions that takes the system from one
  state to another. Names its destination explicitly.
- **State** — a condition the system is *in*, between operations. Owns its own
  exit criteria; only the current state's criteria are evaluated each cycle.

The engine deliberately knows nothing about imgui or about the GSE2V1 button
config. It talks to hardware through the ``Effector`` protocol and reads valve
identity through the ``ValveMap`` protocol; ``procedures/operations.py``
implements both against the existing ``gui_gse2v1`` layer, so commands go out
through the same buttons the operator uses and the GUI stays in sync.

Guard-writing rules (they matter, and the engine enforces what it can):

1. Every sensor read goes through the per-cycle snapshot. Missing or stale data
   reads as NaN or None, never 0.0 — a dead transducer must not look like
   "0 psi" and satisfy a "pressure is below X" criterion.
2. Write guards positively. ``slope <= 3.0`` is False for NaN (safe);
   ``not (slope > 3.0)`` is True for NaN (a NaN-passes-the-test bug).
3. Because a NaN guard never fires, any state with automatic exits must set
   ``max_seconds``. ``Machine.validate()`` rejects states that don't.

An operation is not finished when its last action is staged, only when the
board agrees it happened: actions, then ``lead_time_s`` for the valves to
actually move and be reported, then a comparison against the destination
state's ``expected_state``, and only then the transition. The comparison is a
hash of the valves that state pins against a hash of what the board reports;
a difference fails the operation, and a failed operation panics. Missing
feedback fails too.

Valves the destination does not pin are written ``DONT_CARE``, which
keeps them out of both the hash and the mismatch checks. Never leave them out of
the table instead: an omitted key reads as "expect closed".
"""
from __future__ import annotations

import hashlib
import math
import os
import time
import traceback
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, NamedTuple, Protocol, Union

import numpy as np

# --- Priorities -------------------------------------------------------------
# "if the cycle produced both a hot fire transition and an abort transition,
#  abort wins. Anything equally likely does not happen."
ABORT_PRIORITY = 1000
PANIC_PRIORITY = 900
DEFAULT_PRIORITY = 0

# --- Control cycle ----------------------------------------------------------
# The imgui frame loop runs uncapped (glfw.swap_interval(0)), so the control
# cycle is gated to a fixed rate. Control behaviour must not depend on frame
# rate.
CONTROL_PERIOD_SECONDS = 0.05

# Decay-slope window. 180 s matches NidaqGraph.WINDOW_SECONDS so the number on
# screen and the number the machine decides on describe the same interval.
# run_sim.py shortens it so a full procedure walkthrough is watchable.
DECAY_WINDOW_SECONDS = float(os.environ.get("GSE_DECAY_WINDOW", "180.0"))

LOG_LINES = 400

# --- Lead time --------------------------------------------------------------
# How long an operation waits after its last action before it believes what the
# board reports. A solenoid takes time to move and the board takes time to
# report it, so checking immediately would fail every operation. Override
# `lead_time_s` per operation for a slow valve or a whole-table command.
DEFAULT_LEAD_TIME_SECONDS = 0.75

# The same allowance for a state nobody drove an operation into — a forced state
# or the first state after startup — since no lead time was applied on the way in.
MISMATCH_GRACE_SECONDS = DEFAULT_LEAD_TIME_SECONDS

# How long past the lead time an operation will wait for a telemetry feed that
# has gone stale before it gives up. A blip in the GSE feed is not a reason to
# dump the tanks: the valves have not moved, we just cannot see them. Long
# enough to ride out a reconnect, short enough that a dead feed still fails.
VERIFY_FEED_GRACE_SECONDS = 5.0


class OpStatus(Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class OpPhase(Enum):
    """Where an operation is in its lifecycle, for the operator panel."""

    IDLE = "idle"
    ACTING = "acting"          # staging and polling actions
    SETTLING = "settling"      # waiting out the lead time
    VERIFYING = "verifying"    # comparing the board against the destination table
    DONE = "done"
    FAILED = "failed"


class VerifyResult(Enum):
    """Outcome of comparing the board against a destination table."""

    OK = "ok"
    WAIT = "wait"      # cannot see the board yet; hold, do not judge
    FAILED = "failed"


class MismatchPolicy(Enum):
    """What a state does when the board disagrees with its expected valves."""

    ABORT = "abort"    # run this state's safe-out; the default
    WARN = "warn"      # surface it and stay put
    IGNORE = "ignore"  # don't even look


class DispatcherMode(Enum):
    IDLE = "idle"            # sitting in a state, evaluating its exit criteria
    RUNNING = "running"      # an operation is executing
    SUSPENDED = "suspended"  # manual interrupt; automation frozen, resumable
    AMBIGUOUS = "ambiguous"  # two equally valid transitions; waiting on operator
    HALTED = "halted"        # panic ran; automation off until re-armed


class InterruptPolicy(Enum):
    """What a manual valve click does to a running operation."""

    SUSPEND = "suspend"    # freeze in place, keep progress, offer Resume
    ABANDON = "abandon"    # discard the operation, halt automation
    IGNORE = "ignore"      # log only (automation will fight the operator)


class UnknownStateError(LookupError):
    pass


class StartStateError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Expected valve tables
# ---------------------------------------------------------------------------


class _DontCare:
    """Marker for a valve a state deliberately does not pin.

    ``bool()`` raises, and that is the whole point. ``bool(DONT_CARE)`` would
    quietly be ``True``, and ``True`` means "expect open" — a wrong assertion
    about a valve, invisible in review. Better to blow up on the first run.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "DONT_CARE"

    def __bool__(self) -> bool:
        raise TypeError("DONT_CARE has no truth value; test it with 'is DONT_CARE'.")


DONT_CARE = _DontCare()

# A valve table maps button id -> True, False, or DONT_CARE.
ValveState = Union[bool, _DontCare]
ExpectedTable = Mapping[str, ValveState]


class Mismatch(NamedTuple):
    """One valve where the board disagrees with the table."""

    button_id: str
    expected: bool
    actual: bool | None


def pinned_valves(table: ExpectedTable | None) -> tuple[tuple[str, bool], ...]:
    """The entries a table actually asserts, in a stable order.

    ``DONT_CARE`` entries are dropped: they are in the table to document that
    nobody checks them, not to be checked.
    """
    if not table:
        return ()
    return tuple(
        (button_id, bool(state))
        for button_id, state in sorted(table.items())
        if state is not DONT_CARE
    )


def table_signature(table: ExpectedTable | None) -> str:
    """Short hash of the valves a table pins, for comparing configurations.

    Equal signatures mean equal tables over everything either one asserts, so
    an operation can confirm it arrived by comparing two of these rather than
    walking pairs of booleans.
    """
    payload = ";".join(f"{name}={int(state)}" for name, state in pinned_valves(table))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=6).hexdigest()


def observe_table(ctx: "ControlContext", table: ExpectedTable | None) -> tuple[
    dict[str, ValveState], tuple[Mismatch, ...], tuple[str, ...]
]:
    """What the board says about the valves *table* pins.

    Returns the observed table (an unreadable valve becomes ``DONT_CARE``), the
    valves that disagree, and the valves the board is not reporting at all.

    An unreadable valve never counts as a match. Silence from a status field is
    not agreement, and treating it as agreement is how a stuck valve gets
    confirmed as moved.
    """
    observed: dict[str, ValveState] = {}
    mismatches: list[Mismatch] = []
    unreadable: list[str] = []
    for button_id, expected in pinned_valves(table):
        actual = ctx.actual(button_id)
        if actual is None:
            observed[button_id] = DONT_CARE
            unreadable.append(button_id)
            continue
        observed[button_id] = actual
        if actual != expected:
            mismatches.append(Mismatch(button_id, expected, actual))
    return observed, tuple(mismatches), tuple(unreadable)


# ---------------------------------------------------------------------------
# Ports the engine talks through
# ---------------------------------------------------------------------------


class Effector(Protocol):
    """Stages valve commands and flushes them as one send per control cycle."""

    def stage_button(self, button_id: str, state: bool) -> None: ...
    def stage_table(self, states: Mapping[str, bool]) -> None: ...
    def is_dirty(self) -> bool: ...
    def flush(self) -> bool: ...
    def abort_active(self) -> bool: ...


class ValveMap(Protocol):
    """Identity of the valves, so the engine needn't know the GSE2V1 config."""

    def button_ids(self) -> tuple[str, ...]: ...
    def commanded(self, button_id: str) -> bool: ...
    def status_fields(self, button_id: str) -> tuple[str, ...]: ...
    def display_name(self, button_id: str) -> str: ...


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


class SlopeTracker:
    """Least-squares slope per field, in units/minute, over a moving window.

    Fed once per control cycle from the nidaq snapshot, and only when the
    server's generation counter has moved — so it samples at the telemetry
    rate, not the frame rate.
    """

    def __init__(self, fields: Sequence[str], window_seconds: float = DECAY_WINDOW_SECONDS) -> None:
        self.fields = tuple(fields)
        self.window_seconds = float(window_seconds)
        self._index = {name: i for i, name in enumerate(self.fields)}
        self._times: deque[float] = deque()
        self._rows: deque[list[float]] = deque()
        self._last_generation = -1
        self._started_at: float | None = None

    def reset(self) -> None:
        self._times.clear()
        self._rows.clear()
        self._started_at = None

    def sample(self, scaled: Mapping[str, float] | None, generation: int, now: float) -> None:
        if scaled is None or generation == self._last_generation:
            return
        self._last_generation = generation
        if self._started_at is None:
            self._started_at = now
        self._times.append(now)
        self._rows.append([float(scaled.get(name, math.nan)) for name in self.fields])
        cutoff = now - self.window_seconds
        while self._times and self._times[0] < cutoff:
            self._times.popleft()
            self._rows.popleft()

    def span_seconds(self) -> float:
        if len(self._times) < 2:
            return 0.0
        return self._times[-1] - self._times[0]

    def elapsed_seconds(self, now: float) -> float:
        if self._started_at is None:
            return 0.0
        return now - self._started_at

    def is_ready(self) -> bool:
        return len(self._times) >= 2 and self.span_seconds() >= self.window_seconds

    def slope_per_min(self, name: str) -> float:
        """units/minute. NaN until a full window of data has accumulated."""
        column = self._index.get(name)
        if column is None or not self.is_ready():
            return math.nan
        x = np.asarray(self._times, dtype=np.float64)
        y = np.asarray([row[column] for row in self._rows], dtype=np.float64)
        finite = np.isfinite(y)
        if int(finite.sum()) < 2:
            return math.nan
        x = (x[finite] - x[finite][0]) / 60.0
        y = y[finite]
        x = x - x.mean()
        denominator = float(np.dot(x, x))
        if denominator == 0.0:
            return math.nan
        return float(np.dot(x, y - y.mean()) / denominator)


@dataclass(frozen=True)
class Snapshot:
    """One consistent read of every feed, taken at the top of a control cycle.

    LatestServer.do_put rebinds `.latest` to a brand new dict from a Flight
    thread, so a single attribute read is a safe snapshot but two reads are
    not. Everything downstream reads this, never the servers.
    """

    now: float
    gse: Mapping[str, float] | None
    echo: Mapping[str, float] | None
    nidaq: Mapping[str, float] | None
    gse_fresh: bool
    echo_connected: bool
    nidaq_fresh: bool
    nidaq_generation: int


_STALE_SECONDS = 2.0


class ControlContext:
    """What exit criteria read. Handed to every guard and action."""

    def __init__(
        self,
        *,
        gse_server: Any,
        echo_server: Any,
        nidaq_server: Any,
        scales: Mapping[str, tuple[float, float]],
        valves: ValveMap,
        window_seconds: float = DECAY_WINDOW_SECONDS,
    ) -> None:
        self.gse_server = gse_server
        self.echo_server = echo_server
        self.nidaq_server = nidaq_server
        # psi = raw * scale + offset. Passed in rather than imported so
        # frontendv2 keeps ownership of the calibration table.
        self.scales = dict(scales)
        self.valves = valves
        self.slopes = SlopeTracker(tuple(self.scales.keys()), window_seconds)
        self.state_entered_at = time.monotonic()
        self.op_started_at: float | None = None
        self.snap = Snapshot(
            now=time.monotonic(),
            gse=None,
            echo=None,
            nidaq=None,
            gse_fresh=False,
            echo_connected=False,
            nidaq_fresh=False,
            nidaq_generation=-1,
        )

    # -- lifecycle ----------------------------------------------------------

    def begin_cycle(self, now: float) -> None:
        gse = self.gse_server.latest
        echo = self.echo_server.latest
        nidaq = self.nidaq_server.latest
        echo_connected = (
            echo is not None
            and self.echo_server.is_fresh(_STALE_SECONDS)
            and float(echo.get("connected", 0.0)) > 0.5
        )
        self.snap = Snapshot(
            now=now,
            gse=gse,
            echo=echo,
            nidaq=nidaq,
            gse_fresh=self.gse_server.is_fresh(_STALE_SECONDS),
            echo_connected=echo_connected,
            nidaq_fresh=self.nidaq_server.is_fresh(_STALE_SECONDS),
            nidaq_generation=int(self.nidaq_server.latest_generation),
        )
        self.slopes.sample(self.scaled_pressures(), self.snap.nidaq_generation, now)

    def enter_state(self, now: float) -> None:
        self.state_entered_at = now
        self.op_started_at = None

    def start_operation(self, now: float) -> None:
        self.op_started_at = now

    def reset_slopes(self) -> None:
        self.slopes.reset()

    # -- pressures ----------------------------------------------------------

    def raw(self, field_name: str) -> float:
        nidaq = self.snap.nidaq
        if nidaq is None or not self.snap.nidaq_fresh:
            return math.nan
        value = nidaq.get(field_name)
        return math.nan if value is None else float(value)

    def psi(self, field_name: str) -> float:
        scale, offset = self.scales.get(field_name, (1.0, 0.0))
        return self.raw(field_name) * scale + offset

    def scaled_pressures(self) -> dict[str, float] | None:
        if self.snap.nidaq is None:
            return None
        return {name: self.psi(name) for name in self.scales}

    def slope_psi_per_min(self, field_name: str) -> float:
        return self.slopes.slope_per_min(field_name)

    def slope_ready(self) -> bool:
        return self.slopes.is_ready()

    def worst_slope(self, field_names: Sequence[str]) -> float:
        """Largest absolute decay rate across the named sections. NaN-safe."""
        values = [abs(self.slope_psi_per_min(name)) for name in field_names]
        finite = [v for v in values if math.isfinite(v)]
        return max(finite) if finite else math.nan

    # -- valves -------------------------------------------------------------

    def commanded(self, button_id: str) -> bool:
        return self.valves.commanded(button_id)

    def actual(self, button_id: str) -> bool | None:
        """Board-reported valve position, from the gse telemetry (not the echo)."""
        gse = self.snap.gse
        if gse is None or not self.snap.gse_fresh:
            return None
        fields = self.valves.status_fields(button_id)
        if not fields:
            return None
        states = []
        for name in fields:
            value = gse.get(name)
            if value is None:
                return None
            states.append(float(value) > 0.5)
        return all(states)

    # -- timing / health ----------------------------------------------------

    def in_state_for(self) -> float:
        return self.snap.now - self.state_entered_at

    def in_operation_for(self) -> float:
        if self.op_started_at is None:
            return 0.0
        return self.snap.now - self.op_started_at

    def healthy(self) -> bool:
        return self.snap.gse_fresh and self.snap.echo_connected and self.snap.nidaq_fresh


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class Action:
    """One atomic thing done to the system.

    Actions never block: ``begin`` stages, ``poll`` is called once per control
    cycle until it returns True. All staging within a cycle is batched into a
    single send by the dispatcher.
    """

    name: str = "action"

    def __init__(self) -> None:
        self.failed = False
        self.failure_reason = ""

    def begin(self, ctx: ControlContext, effector: Effector) -> None:  # noqa: B027
        return None

    def poll(self, ctx: ControlContext) -> bool:
        return True

    def buttons(self) -> tuple[str, ...]:
        """Buttons this action commands, for manual-override discrimination."""
        return ()

    def describe(self) -> str:
        return self.name

    def _fail(self, reason: str) -> bool:
        self.failed = True
        self.failure_reason = reason
        return True


class SetValve(Action):
    def __init__(
        self,
        button_id: str,
        state: bool,
        *,
        confirm: bool = False,
        confirm_timeout_s: float = 2.0,
    ) -> None:
        super().__init__()
        self.button_id = button_id
        self.state = bool(state)
        self.confirm = confirm
        self.confirm_timeout_s = confirm_timeout_s
        self.name = f"{'open' if state else 'close'} {button_id}"
        self._started_at = 0.0

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        self._started_at = ctx.snap.now
        effector.stage_button(self.button_id, self.state)

    def poll(self, ctx: ControlContext) -> bool:
        if not self.confirm:
            return True
        if ctx.actual(self.button_id) is self.state:
            return True
        if ctx.snap.now - self._started_at > self.confirm_timeout_s:
            return self._fail(f"{self.button_id} did not reach {self.state} within {self.confirm_timeout_s}s")
        return False

    def buttons(self) -> tuple[str, ...]:
        return (self.button_id,)

    def describe(self) -> str:
        return self.name


class ApplyTable(Action):
    """Apply a named valve table as an absolute configuration."""

    def __init__(self, table_name: str, states: Mapping[str, bool]) -> None:
        super().__init__()
        self.table_name = table_name
        self.states = dict(states)
        self.name = f"apply table {table_name}"

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        effector.stage_table(self.states)

    def buttons(self) -> tuple[str, ...]:
        return tuple(self.states)


class Pulse(Action):
    """Momentary command. Release is owned by the button's momentary timer."""

    def __init__(self, button_id: str) -> None:
        super().__init__()
        self.button_id = button_id
        self.name = f"pulse {button_id}"

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        effector.stage_button(self.button_id, True)

    def buttons(self) -> tuple[str, ...]:
        return (self.button_id,)


class Dwell(Action):
    def __init__(self, seconds: float, label: str = "") -> None:
        super().__init__()
        self.seconds = float(seconds)
        self.name = label or f"wait {seconds:g}s"
        self._started_at = 0.0

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        self._started_at = ctx.snap.now

    def poll(self, ctx: ControlContext) -> bool:
        return ctx.snap.now - self._started_at >= self.seconds

    def remaining(self, ctx: ControlContext) -> float:
        return max(0.0, self.seconds - (ctx.snap.now - self._started_at))


class WaitUntil(Action):
    def __init__(
        self,
        predicate: Callable[[ControlContext], bool],
        *,
        timeout_s: float | None = None,
        label: str = "wait for condition",
    ) -> None:
        super().__init__()
        self.predicate = predicate
        self.timeout_s = timeout_s
        self.name = label
        self._started_at = 0.0

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        self._started_at = ctx.snap.now

    def poll(self, ctx: ControlContext) -> bool:
        if safe_guard(self.predicate, ctx, self.name):
            return True
        if self.timeout_s is not None and ctx.snap.now - self._started_at > self.timeout_s:
            return self._fail(f"timed out waiting for {self.name}")
        return False


class ResetSlopeWindow(Action):
    """Restart the decay window so the slope means 'since this moment'."""

    name = "reset decay window"

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        ctx.reset_slopes()


class Call(Action):
    def __init__(self, fn: Callable[[ControlContext, Effector], None], label: str = "call") -> None:
        super().__init__()
        self.fn = fn
        self.name = label

    def begin(self, ctx: ControlContext, effector: Effector) -> None:
        self.fn(ctx, effector)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def safe_guard(guard: Callable[[ControlContext], bool] | None, ctx: ControlContext, label: str) -> bool:
    """Evaluate a predicate so that anything unexpected means 'do not transition'."""
    if guard is None:
        return False
    try:
        return bool(guard(ctx))
    except Exception:
        print(f"[state machine] guard {label!r} raised:\n{traceback.format_exc()}")
        return False


# ---------------------------------------------------------------------------
# Operation feedback
# ---------------------------------------------------------------------------


@dataclass
class OperationFeedback:
    """What one attempt at an operation actually did, and what the board said.

    The dispatcher keeps the most recent one for the panel, so "it moved on"
    and "it moved on and the valves were confirmed" are distinguishable after
    the fact rather than only in the log.
    """

    operation: str
    dest: str
    phase: OpPhase = OpPhase.IDLE
    detail: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    verified: bool | None = None          # None means nothing was checked
    expected_hash: str = ""
    observed_hash: str = ""
    mismatches: tuple[Mismatch, ...] = ()
    unreadable: tuple[str, ...] = ()

    def duration(self, now: float) -> float:
        return (self.ended_at if self.ended_at is not None else now) - self.started_at

    def summary(self) -> str:
        """One line, for the panel and the log."""
        if self.phase is OpPhase.FAILED:
            return f"{self.operation}: FAILED — {self.detail}"
        if self.phase is OpPhase.DONE:
            if self.verified is True:
                return f"{self.operation}: done, valves confirmed ({self.observed_hash})"
            return f"{self.operation}: done, no valve check"
        return f"{self.operation}: {self.phase.value}"


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


@dataclass
class Operation:
    actions: Sequence[Action]
    dest_state: str | "State"
    auto: bool = False
    name: str = ""
    guard: Callable[[ControlContext], bool] | None = None
    priority: int = DEFAULT_PRIORITY
    overrides_abort: bool = False
    requires_captcha: bool = False
    timeout_s: float | None = None
    description: str = ""
    guard_text: str = ""
    mutually_exclusive_with: tuple[str, ...] = ()
    # Settling time between the last action and the valve check; see
    # DEFAULT_LEAD_TIME_SECONDS.
    lead_time_s: float = DEFAULT_LEAD_TIME_SECONDS
    # Confirm the destination table before transitioning. Abort paths turn this
    # off: they have to land, and a failed check would only panic again.
    verify_dest: bool = True

    _index: int = field(default=0, init=False, repr=False)
    _begun: bool = field(default=False, init=False, repr=False)
    _actions_done_at: float | None = field(default=None, init=False, repr=False)
    _verify_failure: str = field(default="", init=False, repr=False)
    _verify_detail: str = field(default="", init=False, repr=False)
    phase: OpPhase = field(default=OpPhase.IDLE, init=False, repr=False)
    feedback: OperationFeedback | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.actions = tuple(self.actions)
        if not self.name:
            self.name = self.actions[0].describe() if self.actions else "operation"

    # -- execution ----------------------------------------------------------

    def start(self, ctx: ControlContext) -> None:
        self._index = 0
        self._begun = False
        self._actions_done_at = None
        self._verify_failure = ""
        self._verify_detail = ""
        self.phase = OpPhase.ACTING
        for action in self.actions:
            action.failed = False
            action.failure_reason = ""
        ctx.start_operation(ctx.snap.now)
        self.feedback = OperationFeedback(
            operation=self.name,
            dest=self.dest_name(),
            phase=OpPhase.ACTING,
            started_at=ctx.snap.now,
            expected_hash=table_signature(self.dest_table()),
        )

    def step(self, ctx: ControlContext, effector: Effector) -> OpStatus:
        # 1. Actions, one per cycle at most, so staging stays batched and a
        #    long chain can't monopolise a frame.
        while self._index < len(self.actions):
            action = self.actions[self._index]
            if not self._begun:
                action.begin(ctx, effector)
                self._begun = True
            done = action.poll(ctx)
            if action.failed:
                return self._finish(ctx, OpStatus.FAILED, action.failure_reason)
            if not done:
                return self._report(ctx, OpPhase.ACTING, action.describe())
            self._index += 1
            self._begun = False
            if self._index < len(self.actions):
                return self._report(ctx, OpPhase.ACTING, action.describe())

        # 2. Give the valves time to move and the board time to report them.
        if self._actions_done_at is None:
            self._actions_done_at = ctx.snap.now
        remaining = self.lead_time_s - (ctx.snap.now - self._actions_done_at)
        if remaining > 0.0:
            return self._report(ctx, OpPhase.SETTLING, f"settling {remaining:.1f}s")

        # 3. Only now is the operation allowed to claim it happened.
        verdict = self._check_destination(ctx)
        if verdict is VerifyResult.WAIT:
            return self._report(ctx, OpPhase.VERIFYING, self._verify_detail)
        if verdict is VerifyResult.FAILED:
            return self._finish(ctx, OpStatus.FAILED, self._verify_failure)
        return self._finish(ctx, OpStatus.DONE, "")

    def _check_destination(self, ctx: ControlContext) -> VerifyResult:
        """Whether the board agrees we reached the destination configuration.

        Three outcomes, not two. Not being able to see the board is different
        from the board disagreeing: the first means wait, the second means the
        valves are not where the procedure needs them.
        """
        feedback = self.feedback
        table = self.dest_table()
        if not self.verify_dest or not pinned_valves(table):
            if feedback is not None:
                feedback.verified = None
            return VerifyResult.OK

        observed, mismatches, unreadable = observe_table(ctx, table)
        observed_hash = table_signature(observed)
        expected_hash = table_signature(table)
        if feedback is not None:
            feedback.observed_hash = observed_hash
            feedback.expected_hash = expected_hash
            feedback.mismatches = mismatches
            feedback.unreadable = unreadable
            feedback.verified = observed_hash == expected_hash

        if unreadable:
            # A stale feed is a "we are blind" problem, not a valve problem.
            # Hold and keep looking; the operation's timeout still bounds it.
            waited = ctx.snap.now - (self._actions_done_at or ctx.snap.now)
            if not ctx.snap.gse_fresh and waited < self.lead_time_s + VERIFY_FEED_GRACE_SECONDS:
                self._verify_detail = "waiting for board telemetry"
                if feedback is not None:
                    feedback.verified = None
                return VerifyResult.WAIT
            self._verify_failure = (
                f"no board feedback for {', '.join(unreadable)} after "
                f"{waited:.1f}s"
            )
            return VerifyResult.FAILED
        if observed_hash != expected_hash:
            detail = ", ".join(
                f"{item.button_id} expected {item.expected}, board reports {item.actual}"
                for item in mismatches
            )
            self._verify_failure = (
                f"{self.dest_name()} table check failed "
                f"({expected_hash} != {observed_hash}): {detail}"
            )
            return VerifyResult.FAILED
        return VerifyResult.OK

    def _report(self, ctx: ControlContext, phase: OpPhase, detail: str) -> OpStatus:
        self.phase = phase
        if self.feedback is not None:
            self.feedback.phase = phase
            self.feedback.detail = detail
        return OpStatus.RUNNING

    def _finish(self, ctx: ControlContext, status: OpStatus, detail: str) -> OpStatus:
        self.phase = OpPhase.DONE if status is OpStatus.DONE else OpPhase.FAILED
        if self.feedback is not None:
            self.feedback.phase = self.phase
            self.feedback.detail = detail
            self.feedback.ended_at = ctx.snap.now
        return status

    def failure_reason(self) -> str:
        for action in self.actions:
            if action.failed:
                return action.failure_reason
        return self._verify_failure

    # -- introspection ------------------------------------------------------

    def progress(self) -> tuple[int, int]:
        return (min(self._index + 1, len(self.actions)), len(self.actions))

    def current_action(self) -> Action | None:
        if 0 <= self._index < len(self.actions):
            return self.actions[self._index]
        return None

    def owned_buttons(self) -> frozenset[str]:
        owned: set[str] = set()
        for action in self.actions:
            owned.update(action.buttons())
        return frozenset(owned)

    def dest_name(self) -> str:
        return self.dest_state.name if isinstance(self.dest_state, State) else str(self.dest_state)

    def dest_table(self) -> ExpectedTable | None:
        """The destination state's expected valves, once ``Machine.link()`` ran.

        Before linking the destination is still a name, so there is no table to
        return yet.
        """
        if isinstance(self.dest_state, State):
            return self.dest_state.expected_state
        return None

    def predict_table(self, source: ExpectedTable | None) -> dict[str, ValveState]:
        """Where this operation leaves the valves, starting from *source*.

        Valves the actions leave unknowable come back as ``DONT_CARE``.
        ``Machine.validate()`` compares this against the destination table, so
        an operation that cannot reach the state it names is caught at build
        time rather than at 350 psig.
        """
        predicted: dict[str, ValveState] = dict(source or {})
        for action in self.actions:
            if isinstance(action, SetValve):
                if action.button_id in predicted:
                    predicted[action.button_id] = action.state
                continue
            if isinstance(action, ApplyTable):
                # A table is absolute: a valve it omits is driven closed.
                for button_id in predicted:
                    predicted[button_id] = bool(action.states.get(button_id, False))
                continue
            # Anything else that touches a valve leaves it unpredictable.
            for button_id in action.buttons():
                if button_id in predicted:
                    predicted[button_id] = DONT_CARE
        return predicted


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class State:
    """A condition the system is in, between operations.

    Exactly one state per machine carries ``start=True``; ``Machine.build``
    refuses anything else.

    ``on_mismatch`` defaults to ABORT, because a valve disagreeing with
    ``expected_state`` means the system is not where the procedure thinks it
    is, and continuing to run a procedure from a false premise is the failure
    mode worth being loudest about.
    """

    name: str
    operations: Sequence[Operation] = ()
    expected_state: ExpectedTable | None = None
    panic: Operation | None = None
    max_seconds: float | None = None
    on_mismatch: MismatchPolicy | str = MismatchPolicy.ABORT
    description: str = ""
    entry_from: frozenset[str] | None = None
    start: bool = False
    # Grace period after entry before the mismatch check bites. An operation
    # already waited out its own lead time; this covers the ways into a state
    # that skip one, like force_state() and startup.
    mismatch_grace_s: float = MISMATCH_GRACE_SECONDS

    def __post_init__(self) -> None:
        self.operations = tuple(self.operations)
        if not isinstance(self.on_mismatch, MismatchPolicy):
            # Procedures written against the old string form said "warn" or
            # "panic"; accept both rather than break them.
            text = str(self.on_mismatch).lower()
            if text == "panic":
                text = "abort"
            self.on_mismatch = MismatchPolicy(text)

    def evaluate(self, ctx: ControlContext) -> list[Operation]:
        """Automatic exits whose criteria are met. Only this state's criteria run."""
        return [
            op
            for op in self.operations
            if op.auto and safe_guard(op.guard, ctx, f"{self.name}:{op.name}")
        ]

    def manual_operations(self) -> tuple[Operation, ...]:
        return tuple(op for op in self.operations if not op.auto)

    def signature(self) -> str:
        """Hash of this state's expected valve configuration."""
        return table_signature(self.expected_state)

    def mismatches(self, ctx: ControlContext) -> list[Mismatch]:
        """Valves where the board disagrees with this state.

        This is the drift watch for a state we are sitting in, so an unreadable
        valve is not counted — demanding feedback belongs to the operation
        check, which knows a command was just sent and has a lead time to wait.
        """
        if self.on_mismatch is MismatchPolicy.IGNORE:
            return []
        _observed, mismatches, _unreadable = observe_table(ctx, self.expected_state)
        return list(mismatches)


# ---------------------------------------------------------------------------
# Machine
# ---------------------------------------------------------------------------


@dataclass
class Machine:
    name: str
    states: dict[str, State]
    initial: str
    default_panic: Operation | None = None
    global_transitions: tuple[Operation, ...] = ()

    @classmethod
    def build(
        cls,
        name: str,
        states: Iterable[State],
        initial: str | None = None,
        *,
        default_panic: Operation | None = None,
        global_transitions: Sequence[Operation] = (),
    ) -> "Machine":
        """Assemble a machine and resolve every destination name to a State.

        Declare where the procedure begins with ``State(..., start=True)``.
        *initial* is optional and only says the same thing twice; if it
        disagrees with the flag, or no state claims to be the start, that is a
        ``StartStateError`` rather than a machine that quietly begins wherever.
        """
        by_name: dict[str, State] = {}
        for state in states:
            if state.name in by_name:
                raise ValueError(f"duplicate state name {state.name!r}")
            by_name[state.name] = state
        machine = cls(
            name=name,
            states=by_name,
            initial=cls._resolve_start(by_name, initial),
            default_panic=default_panic,
            global_transitions=tuple(global_transitions),
        )
        machine.link()
        return machine

    @staticmethod
    def _resolve_start(states: dict[str, State], initial: str | None) -> str:
        """The single declared start state, cross-checked against *initial*."""
        declared = [state.name for state in states.values() if state.start]
        if len(declared) > 1:
            raise StartStateError(
                f"more than one start state: {', '.join(sorted(declared))}. "
                "Set start=True on exactly one state."
            )
        if declared:
            if initial is not None and initial != declared[0]:
                raise StartStateError(
                    f"initial={initial!r} does not match the declared start state "
                    f"{declared[0]!r}"
                )
            return declared[0]
        if initial is None:
            raise StartStateError(
                "no start state. Set start=True on the state that the procedure "
                "begins from."
            )
        # Naming the start state in the call is explicit too, and it is what
        # the small test machines do. Mark it so the flag and the name cannot
        # drift apart afterwards.
        state = states.get(initial)
        if state is not None:
            state.start = True
        return initial

    # -- linking ------------------------------------------------------------

    def all_operations(self) -> list[Operation]:
        ops: list[Operation] = list(self.global_transitions)
        if self.default_panic is not None:
            ops.append(self.default_panic)
        for state in self.states.values():
            ops.extend(state.operations)
            if state.panic is not None:
                ops.append(state.panic)
        return ops

    def resolve(self, dest: str | State) -> State:
        if isinstance(dest, State):
            return dest
        try:
            return self.states[dest]
        except KeyError:
            raise UnknownStateError(f"unknown destination state {dest!r}") from None

    def link(self) -> None:
        """Replace destination names with State objects; raise on any miss."""
        for op in self.all_operations():
            op.dest_state = self.resolve(op.dest_state)
        if self.initial not in self.states:
            raise UnknownStateError(f"unknown initial state {self.initial!r}")

    # -- static audit -------------------------------------------------------

    def validate(self) -> list[str]:
        """Problems that must be fixed before automation may be armed."""
        problems: list[str] = []

        declared_start = [state.name for state in self.states.values() if state.start]
        if declared_start != [self.initial]:
            problems.append(
                f"start state: expected exactly {self.initial!r}, found "
                f"{', '.join(sorted(declared_start)) or 'none'}"
            )

        for state in self.states.values():
            auto_ops = [op for op in state.operations if op.auto]

            for op in state.operations:
                if op.auto and op.guard is None:
                    problems.append(f"{state.name}: automatic operation {op.name!r} has no exit criterion")
                dest = op.dest_state
                if isinstance(dest, State) and dest.entry_from is not None:
                    if state.name not in dest.entry_from:
                        problems.append(
                            f"{state.name}: operation {op.name!r} enters {dest.name} "
                            f"which does not accept entry from {state.name}"
                        )
                if op.lead_time_s < 0.0:
                    problems.append(
                        f"{state.name}: operation {op.name!r} has a negative lead time"
                    )
                # A timeout inside the lead time kills the operation before the
                # valve check ever gets a chance to run.
                if op.timeout_s is not None and op.timeout_s <= op.lead_time_s:
                    problems.append(
                        f"{state.name}: operation {op.name!r} has timeout_s "
                        f"{op.timeout_s:g}s at or below its lead time {op.lead_time_s:g}s"
                    )

            if state.on_mismatch is MismatchPolicy.ABORT and state.expected_state:
                safe_out = state.panic or self.default_panic
                if safe_out is None:
                    problems.append(
                        f"{state.name}: aborts on valve mismatch but has no safe-out"
                    )
                elif self.resolve(safe_out.dest_state) is state:
                    # This is how you get a machine that panics into itself
                    # once a cycle, forever.
                    problems.append(
                        f"{state.name}: aborts on valve mismatch but its safe-out "
                        f"re-enters {state.name}; use MismatchPolicy.WARN there"
                    )

            problems.extend(self._table_problems(state))

            if auto_ops and state.max_seconds is None:
                # A NaN guard never fires, so an auto state without a watchdog
                # can hang silently on a dead sensor.
                problems.append(f"{state.name}: has automatic exits but no max_seconds watchdog")

            if not state.operations and state.panic is None:
                problems.append(f"{state.name}: dead end (no operations, no panic)")

            # Two automatic exits at equal priority is a tie that *can* happen,
            # even if today's thresholds happen to be disjoint.
            by_priority: dict[int, list[Operation]] = {}
            for op in auto_ops:
                by_priority.setdefault(op.priority, []).append(op)
            for priority, ops in by_priority.items():
                if len(ops) < 2:
                    continue
                names = {op.name for op in ops}
                declared = all(
                    names - {op.name} <= set(op.mutually_exclusive_with) for op in ops
                )
                if not declared:
                    problems.append(
                        f"{state.name}: {len(ops)} automatic exits share priority {priority} "
                        f"({', '.join(sorted(names))}); give them distinct priorities or declare "
                        f"mutually_exclusive_with"
                    )

        reachable = self._reachable()
        for name in self.states:
            if name not in reachable:
                problems.append(f"{name}: unreachable from {self.initial}")

        return problems

    def _table_problems(self, state: State) -> list[str]:
        """Operations of *state* whose actions cannot reach the state they name.

        The build-time half of the destination check: the runtime half compares
        hashes once the board has reported, this one compares the destination
        table against where the actions would leave the valves. Same mistake,
        found before anyone arms the machine.
        """
        problems: list[str] = []
        if state.expected_state is None:
            return problems
        for op in state.operations:
            if not op.verify_dest:
                continue
            dest = op.dest_state
            if not isinstance(dest, State) or dest.expected_state is None:
                continue
            predicted = op.predict_table(state.expected_state)
            for button_id, expected in pinned_valves(dest.expected_state):
                actual = predicted.get(button_id)
                if actual is None or actual is DONT_CARE:
                    continue  # unpinned upstream, or an action made it unknowable
                if actual != expected:
                    problems.append(
                        f"{state.name}: operation {op.name!r} leaves {button_id} "
                        f"{actual}, but {dest.name} expects {expected}"
                    )
        return problems

    def _reachable(self) -> set[str]:
        seen = {self.initial}
        queue = [self.initial]
        while queue:
            state = self.states[queue.pop()]
            targets = [op.dest_state for op in state.operations]
            if state.panic is not None:
                targets.append(state.panic.dest_state)
            for op in self.global_transitions:
                targets.append(op.dest_state)
            for dest in targets:
                dest_name = dest.name if isinstance(dest, State) else str(dest)
                if dest_name not in seen:
                    seen.add(dest_name)
                    queue.append(dest_name)
        return seen


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Owns the current state and runs one control cycle at a fixed rate.

    Transitions are evaluated only at the end of a cycle, from a single
    snapshot, and only for the state the machine is actually in.
    """

    def __init__(
        self,
        machine: Machine,
        ctx: ControlContext,
        effector: Effector,
        *,
        period_s: float = CONTROL_PERIOD_SECONDS,
        interrupt_policy: InterruptPolicy = InterruptPolicy.SUSPEND,
    ) -> None:
        self.machine = machine
        self.ctx = ctx
        self.effector = effector
        self.period_s = period_s
        self.interrupt_policy = interrupt_policy

        self.current: State = machine.states[machine.initial]
        self.mode = DispatcherMode.IDLE
        self.active_op: Operation | None = None
        self.pending_manual: Operation | None = None
        self.tie_candidates: list[Operation] = []
        self.warnings: list[Mismatch] = []
        self.problems: list[str] = machine.validate()
        self.armed = False
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.history: list[str] = [self.current.name]
        self.results: dict[str, Any] = {}
        self.last_feedback: OperationFeedback | None = None
        # Kept separately because a failure is immediately followed by a panic,
        # and the panic's own (successful) report would otherwise bury the
        # thing that actually went wrong.
        self.last_failure: OperationFeedback | None = None

        self._panic_requested = False
        self._resume_requested = False
        self._last_tick_at = 0.0
        self._staging = False
        self._clock_started = False
        self._panicking = False

        self._log(f"initial state {self.current.name}")
        for problem in self.problems:
            self._log(f"VALIDATE: {problem}")

    # -- logging ------------------------------------------------------------

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {message}")
        print(f"[state machine] {message}")

    # -- operator input -----------------------------------------------------

    def arm(self) -> None:
        if self.problems:
            self._log("refusing to arm: validation problems outstanding")
            return
        self.armed = True
        if self.mode is DispatcherMode.HALTED:
            self.mode = DispatcherMode.IDLE
        self._log("automation ARMED")

    def disarm(self) -> None:
        self.armed = False
        self._log("automation disarmed")

    def request(self, op: Operation) -> None:
        """Queue a manual operation; it still goes through the determinism check."""
        self.pending_manual = op
        self._log(f"operator requested {op.name!r}")

    def panic(self) -> None:
        self._panic_requested = True

    def resume(self) -> None:
        self._resume_requested = True

    def choose(self, op: Operation) -> None:
        """Resolve an AMBIGUOUS cycle with the operator's pick."""
        if self.mode is not DispatcherMode.AMBIGUOUS:
            return
        self._log(f"operator resolved tie in favour of {op.name!r}")
        self.tie_candidates = []
        self.mode = DispatcherMode.IDLE
        self._start_operation(op)

    def force_state(self, state_name: str) -> None:
        """Operator override: jump to a state without running an operation."""
        state = self.machine.states.get(state_name)
        if state is None:
            self._log(f"cannot force unknown state {state_name!r}")
            return
        self._log(f"OPERATOR FORCED state {self.current.name} -> {state_name}")
        self.active_op = None
        self.mode = DispatcherMode.IDLE
        self._enter_state(state)

    def note_manual_command(self, button_id: str) -> None:
        """Called when the operator clicks a raw valve button."""
        if self._staging:
            return
        if self.active_op is None or self.mode is not DispatcherMode.RUNNING:
            return
        if self.interrupt_policy is InterruptPolicy.IGNORE:
            self._log(f"manual command {button_id!r} during {self.active_op.name!r} (ignored)")
            return
        if self.interrupt_policy is InterruptPolicy.ABANDON:
            self._log(f"manual command {button_id!r} abandoned {self.active_op.name!r}")
            self.active_op = None
            self.mode = DispatcherMode.HALTED
            self.armed = False
            return
        self.mode = DispatcherMode.SUSPENDED
        self._log(f"manual command {button_id!r} suspended {self.active_op.name!r} — Resume to continue")

    # -- state changes ------------------------------------------------------

    def _enter_state(self, state: State) -> None:
        self.current = state
        self.history.append(state.name)
        self.ctx.enter_state(self.ctx.snap.now)
        self.warnings = []
        self._log(f"-> {state.name}")

    def _start_operation(self, op: Operation, *, forced: bool = False) -> None:
        if not forced and self.effector.abort_active() and not op.overrides_abort:
            self._log(f"refusing {op.name!r}: abort is latched")
            return
        self.active_op = op
        self.mode = DispatcherMode.RUNNING
        op.start(self.ctx)
        self._log(f"operation {op.name!r} -> {op.dest_name()}")
        # Advance in the same cycle it was committed, so a command reaches the
        # board now rather than one control period from now.
        self._advance()

    def _advance(self) -> None:
        """Step the in-flight operation once."""
        op = self.active_op
        if op is None:
            return

        status = op.step(self.ctx, self.effector)

        if status is OpStatus.RUNNING:
            if op.timeout_s is not None and self.ctx.in_operation_for() > op.timeout_s:
                self.active_op = None
                self.last_feedback = self.last_failure = op.feedback
                self._panic_now(f"operation {op.name!r} timed out")
            return

        self.last_feedback = op.feedback

        if status is OpStatus.FAILED:
            reason = op.failure_reason()
            self.last_failure = op.feedback
            self.active_op = None
            self._panic_now(f"operation {op.name!r} failed: {reason}")
            return

        self.active_op = None
        if op.feedback is not None and op.feedback.verified:
            self._log(
                f"{op.name!r} verified {op.dest_name()} table "
                f"({op.feedback.observed_hash})"
            )
        self._enter_state(self.machine.resolve(op.dest_state))
        self.mode = DispatcherMode.IDLE

    def _panic_now(self, reason: str) -> None:
        op = self.current.panic or self.machine.default_panic
        self._log(f"PANIC ({reason})")
        self.armed = False
        # A safe-out that lands where we already are cannot fix anything, and
        # re-running it every cycle is an infinite loop rather than a safe
        # state. Halt instead and leave it to the operator.
        if op is not None and self.machine.resolve(op.dest_state) is self.current:
            self._log(
                f"{self.current.name} is already its own safe-out; halting "
                "rather than repeating the panic"
            )
            self.active_op = None
            self.mode = DispatcherMode.HALTED
            return
        if op is None or self._panicking:
            if self._panicking:
                self._log("panic operation itself failed; halting without further action")
            else:
                self._log("no panic operation defined; halting without action")
            self.active_op = None
            self.mode = DispatcherMode.HALTED
            return
        self._panicking = True
        try:
            self._start_operation(op, forced=True)
        finally:
            self._panicking = False

    def _global_applies(self, op: Operation) -> bool:
        """Whether a global transition is eligible this cycle.

        A global criterion typically stays true for as long as the condition
        that tripped it — an overpressure watch does not stop being true just
        because the abort has started. Without these two exclusions such an
        operation would restart itself every cycle and never reach its
        destination. A *different* global at abort priority still preempts.
        """
        if op is self.active_op:
            return False
        return self.machine.resolve(op.dest_state) is not self.current

    # -- the control cycle --------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if not self._clock_started:
            # The dispatcher may be constructed long before the first tick, and
            # a caller may drive it from its own clock. Anchor state timing to
            # the first tick rather than to construction.
            self._clock_started = True
            self.ctx.enter_state(now)
        elif now - self._last_tick_at < self.period_s:
            return
        self._last_tick_at = now

        self.ctx.begin_cycle(now)
        self._staging = True
        try:
            self._cycle()
        finally:
            if self.effector.is_dirty():
                self.effector.flush()
            self._staging = False

    def _cycle(self) -> None:
        ctx = self.ctx

        # 0. Explicit operator panic outranks everything, including a tie.
        if self._panic_requested:
            self._panic_requested = False
            self._panic_now("operator")
            return

        # Abort-priority global criteria are evaluated before the AMBIGUOUS
        # early-return, so an abort can always break a tie.
        for op in self.machine.global_transitions:
            if op.priority < ABORT_PRIORITY or not self._global_applies(op):
                continue
            if safe_guard(op.guard, ctx, f"global:{op.name}"):
                self.tie_candidates = []
                self._start_operation(op, forced=True)
                return

        if self.mode is DispatcherMode.AMBIGUOUS:
            return  # frozen: stage nothing, wait for choose()

        if self._resume_requested:
            self._resume_requested = False
            if self.mode is DispatcherMode.SUSPENDED:
                self.mode = DispatcherMode.RUNNING if self.active_op else DispatcherMode.IDLE
                self._log("resumed")

        if self.mode is DispatcherMode.SUSPENDED:
            return  # automation frozen; the operator's own buttons still work

        # 1. Advance the in-flight operation. No transition evaluation while
        #    an operation is running, and the cycle that arrives in a state
        #    never also leaves it.
        if self.active_op is not None:
            self._advance()
            return

        # 2. Invariants of the state we are actually sitting in. The grace
        #    period keeps a valve still travelling from reading as drift.
        settled = ctx.in_state_for() >= self.current.mismatch_grace_s
        self.warnings = self.current.mismatches(ctx) if settled else []
        # Only while armed. Disarmed means the operator is hand-flying, and
        # slamming the abort table onto someone deliberately moving a valve is
        # exactly the "never take manual control away" rule being broken.
        if self.warnings and self.armed and self.current.on_mismatch is MismatchPolicy.ABORT:
            detail = ", ".join(
                f"{item.button_id} expected {item.expected}, board reports {item.actual}"
                for item in self.warnings
            )
            self._panic_now(f"{self.current.name} valve mismatch: {detail}")
            return
        if self.current.max_seconds is not None and ctx.in_state_for() > self.current.max_seconds:
            self._panic_now(f"{self.current.name} exceeded {self.current.max_seconds:g}s")
            return

        # 3. Gather candidates from the current state only, plus globals and
        #    at most one queued manual request.
        candidates: list[Operation] = []
        for op in self.machine.global_transitions:
            if op.priority >= ABORT_PRIORITY or not self._global_applies(op):
                continue
            if safe_guard(op.guard, ctx, f"global:{op.name}"):
                candidates.append(op)
        if self.armed and ctx.healthy():
            candidates.extend(self.current.evaluate(ctx))
        if self.pending_manual is not None:
            candidates.append(self.pending_manual)
            self.pending_manual = None

        if not candidates:
            return

        # 4. Determinism check.
        top = max(op.priority for op in candidates)
        winners = [op for op in candidates if op.priority == top]
        if len(winners) > 1:
            self.mode = DispatcherMode.AMBIGUOUS
            self.tie_candidates = winners
            self._log(
                f"AMBIGUOUS in {self.current.name}: "
                f"{', '.join(op.name for op in winners)} — operator must choose"
            )
            return

        # 5. Commit.
        self._start_operation(winners[0])

    # -- introspection for the GUI -----------------------------------------

    def status_line(self) -> str:
        op = self.active_op
        if op is None:
            return self.mode.value
        index, total = op.progress()
        if op.phase is OpPhase.ACTING:
            action = op.current_action()
            detail = action.describe() if action else ""
            return f"{op.name} [{index}/{total}] {detail}"
        detail = op.feedback.detail if op.feedback is not None else ""
        return f"{op.name} [{op.phase.value}] {detail}"

    def feedback_line(self) -> str:
        """One line about the operation that ran last, or empty if none has."""
        feedback = self.last_feedback
        return feedback.summary() if feedback is not None else ""
