"""The view model: everything the panels draw, derived once per frame.

This is the counterpart of ``renderVals()`` in the source design. The design was
drawn against a hotfire mock with its own simulated state; here every field is
resolved from the real dispatcher, control context and telemetry servers, so the
panels stay dumb and all of the mapping lives in one readable place.

Where the design named a hotfire concept that the pressure-decay procedure does
not have, the slot is filled with the equivalent real thing rather than left
empty or faked -- see ``README.md`` for the full mapping.
"""
from __future__ import annotations

import math

import theme as T

from state_machine import DispatcherMode, MismatchPolicy, OpPhase, safe_guard
from procedures import table_states
from procedures.pressure_decay import (
    COPV_OVERPRESSURE_LIMIT_PSI,
    DECAY_LIMIT_PSI_PER_MIN,
    DECAY_SECTIONS,
    TANK_OVERPRESSURE_LIMIT_PSI,
    VENT_MINIMUM_PSI,
)

# --- Channels --------------------------------------------------------------
# NIDAQ display names, from nidaq_gse.NIDAQ_FIELD_NAME_MAP.
COPV = "COPV"
LOX_TANK = "LOXTANK"
LNG_TANK = "LNGTANK"
VENT = "VENT"
LOX_ING = "LOXING"
LNG_ING = "LNGING"
LOX_POT = "LOXPOT"
LNG_POT = "LNGPOT"
PT10 = "PT10"
THRUST = "Thrust"

TREND_CHANNELS = (COPV, LOX_TANK, LNG_TANK, VENT, LOX_ING, LNG_ING,
                  LOX_POT, LNG_POT, PT10, THRUST)

# Full-scale for each tank gauge, chosen so the procedure's abort limit sits
# clearly inside the bar rather than at its very end.
GAUGE_FULL_SCALE = {
    COPV: 500.0,
    LOX_TANK: 700.0,
    LNG_TANK: 700.0,
}
REDLINE = {
    COPV: COPV_OVERPRESSURE_LIMIT_PSI,
    LOX_TANK: TANK_OVERPRESSURE_LIMIT_PSI,
    LNG_TANK: TANK_OVERPRESSURE_LIMIT_PSI,
}
# Caution sits at 85% of the abort limit: enough warning to act, late enough
# that a nominal fill does not sit amber for the whole procedure.
CAUTION = {name: limit * 0.85 for name, limit in REDLINE.items()}

DISPLAY_NAME = {
    COPV: "COPV",
    LOX_TANK: "LOX TANK",
    LNG_TANK: "LNG TANK",
    VENT: "VENT",
    LOX_ING: "LOX ING",
    LNG_ING: "LNG ING",
    LOX_POT: "LOX POT",
    LNG_POT: "LNG POT",
    PT10: "PT 10",
    THRUST: "THRUST",
}

SERIES_COLOR = {
    COPV: T.ACID,
    LOX_TANK: T.OX,
    LNG_TANK: T.BLUE,
    VENT: T.INK3,
    LOX_ING: T.PURPLE,
    LNG_ING: T.PINK,
    LOX_POT: T.LIME,
    LNG_POT: T.WARN,
    PT10: T.INK2,
    THRUST: T.INK2,
}

# Valves shown on the P&ID and in the command grid, in procedure order.
GSE_VALVES = (
    "sol_gn2_fill_1",
    "sol_gn2_fill_2",
    "sol_gn2_fill_3",
    "sol_gn2_fill_4",
    "sol_gn2_vent",
    "copv_vent",
    "pv1",
    "pv2",
    "tank_vent",
)
# Momentary commands. MVAS actuators hold their last position, which is why
# open and close are separate pulses rather than one toggle.
ECU_COMMANDS = ("mvas_open", "mvas_close", "igniter_0", "igniter_1")

NORMALLY_OPEN = ("pv2", "tank_vent")

HOLD_ARM_SECONDS = 1.5
HOLD_ABORT_SECONDS = 1.0


def valve_is_open(commanded: bool, button_id: str) -> bool:
    """Energised is not open for PV 2 and the tank vents."""
    return (not commanded) if button_id in NORMALLY_OPEN else bool(commanded)


class Alarm:
    def __init__(self, title: str, detail: str, color: int = T.CRIT) -> None:
        self.title = title
        self.detail = detail
        self.color = color


class OpsModel:
    """Live state the panels read. Rebuilt cheaply every frame."""

    def __init__(self, dispatcher, ctx, history, event_log, captcha) -> None:
        self.dispatcher = dispatcher
        self.ctx = ctx
        self.history = history
        self.events = event_log
        self.captcha = captcha

        self.selected = COPV
        self.scroll: dict[str, float] = {}
        self.acknowledged: str = ""
        # Which page the top-bar tabs are showing.
        self.tab = "ops"

    # -- per-frame ----------------------------------------------------------

    def update(self, gse_server, nidaq_server) -> None:
        self.history.sample(self.ctx, nidaq_server.latest_generation)

    # -- telemetry ----------------------------------------------------------

    def value(self, channel: str) -> float:
        try:
            return self.ctx.psi(channel)
        except Exception:
            return float("nan")

    def severity_color(self, channel: str) -> int:
        value = self.value(channel)
        if not math.isfinite(value):
            return T.INK3
        redline = REDLINE.get(channel)
        if redline is None:
            return T.INK
        if value >= redline:
            return T.CRIT
        if value >= CAUTION[channel]:
            return T.ALERT
        return T.ACID

    def commanded(self, button_id: str) -> bool:
        return bool(self.ctx.commanded(button_id))

    def actual(self, button_id: str):
        return self.ctx.actual(button_id)

    def valve_open(self, button_id: str) -> bool:
        return valve_is_open(self.commanded(button_id), button_id)

    def valve_agrees(self, button_id: str):
        """True / False / None when the board is not reporting the valve."""
        actual = self.actual(button_id)
        if actual is None:
            return None
        return bool(actual) == self.commanded(button_id)

    def any_mismatch(self) -> bool:
        """Is any valve reporting something other than what we commanded?

        The command tiles say this on their own faces, but they are a tab away,
        so the tab itself has to carry the flag.
        """
        return bool(self.dispatcher.warnings)

    def valve_label(self, button_id: str) -> str:
        return table_states.DISPLAY_NAMES.get(button_id, button_id)

    # -- dispatcher ---------------------------------------------------------

    @property
    def mode(self):
        return self.dispatcher.mode

    @property
    def aborted(self) -> bool:
        return self.dispatcher.current.name == "PD_ABORTED"

    def phase_colors(self) -> tuple[int, int]:
        """(foreground, background) for the mode chip."""
        if self.aborted or self.mode is DispatcherMode.HALTED:
            return T.CRIT, T.CRIT_BG
        if self.mode is DispatcherMode.AMBIGUOUS:
            return T.CRIT, T.CRIT_BG
        if self.mode is DispatcherMode.SUSPENDED:
            return T.WARN, T.ALERT_BG
        if self.mode is DispatcherMode.RUNNING:
            return T.ALERT, T.ALERT_BG
        return T.ACID, T.ACID_BG

    def phase_name(self) -> str:
        if self.aborted:
            return "ABORTED - SAFING"
        return self.mode.value.upper()

    def sequence_rows(self) -> list[dict]:
        """One row per state, in declaration order.

        DONE means the machine has actually been through it, which is not the
        same as being earlier in the list: the procedure has a recovery branch
        that goes backwards, and an aborted run never reaches most of them.
        """
        current = self.dispatcher.current.name
        visited = set(self.dispatcher.history[:-1])
        rows = []
        for index, (name, state) in enumerate(self.dispatcher.machine.states.items()):
            if name == current:
                status, status_fg = "ACTIVE", (T.CRIT if self.aborted else T.ALERT)
                bar, fg = status_fg, T.INK
            elif name in visited:
                status, status_fg = "DONE", T.ACID
                bar, fg = T.ACID, T.INK2
            else:
                status, status_fg = "PENDING", T.INK3
                bar, fg = T.BORDER, T.INK3
            rows.append(
                {
                    "index": index,
                    "name": name,
                    "state": state,
                    "label": pretty_state(name),
                    "status": status,
                    "status_fg": status_fg,
                    "bar": bar,
                    "fg": fg,
                    "active": name == current,
                }
            )
        return rows

    # -- the active step ----------------------------------------------------

    def watched(self) -> list[dict]:
        """Guards the machine is evaluating right now, with their live result."""
        dispatcher = self.dispatcher
        operations = list(dispatcher.machine.global_transitions)
        operations += [op for op in dispatcher.current.operations if op.auto]
        rows = []
        for op in operations:
            met = safe_guard(op.guard, dispatcher.ctx, "panel:" + op.name)
            rows.append(
                {
                    "text": op.guard_text or op.name,
                    "dest": op.dest_name(),
                    "met": met,
                    "description": op.description,
                }
            )
        return rows

    def manual_operations(self) -> tuple:
        return self.dispatcher.current.manual_operations()

    def can_run_manual(self) -> bool:
        return self.mode in (DispatcherMode.IDLE, DispatcherMode.HALTED)

    def watchdog_remaining(self) -> float:
        limit = self.dispatcher.current.max_seconds
        if limit is None:
            return float("nan")
        return limit - self.ctx.in_state_for()

    # -- alarms -------------------------------------------------------------

    def alarm(self) -> Alarm | None:
        """The one thing most worth interrupting the operator about."""
        dispatcher = self.dispatcher

        if dispatcher.problems:
            key = "validate:" + dispatcher.problems[0]
            if key != self.acknowledged:
                return Alarm(
                    "MACHINE FAILED VALIDATION",
                    dispatcher.problems[0] + "  -  automation cannot be armed",
                )

        if self.mode is DispatcherMode.AMBIGUOUS:
            return Alarm(
                "AMBIGUOUS TRANSITION",
                "Two transitions are equally valid. The machine will not choose "
                "for you -- pick one on the Manual tab.",
            )

        failure = dispatcher.last_failure
        if failure is not None and failure.phase is OpPhase.FAILED:
            key = "fail:" + failure.summary()
            if key != self.acknowledged:
                return Alarm("OPERATION FAILED", failure.summary())

        if dispatcher.warnings:
            will_abort = (
                dispatcher.current.on_mismatch is MismatchPolicy.ABORT and dispatcher.armed
            )
            item = dispatcher.warnings[0]
            detail = "%s expected %s, board reports %s" % (
                self.valve_label(item.button_id),
                item.expected,
                item.actual,
            )
            if will_abort:
                detail += "  -  this state aborts on a valve mismatch"
            elif dispatcher.current.on_mismatch is MismatchPolicy.ABORT:
                detail += "  -  disarmed, so this only warns; arming here would abort"
            key = "mismatch:" + detail
            if key != self.acknowledged:
                return Alarm("VALVE STATE MISMATCH", detail,
                             T.CRIT if will_abort else T.ALERT)

        if not self.ctx.healthy():
            snapshot = self.ctx.snap
            missing = [
                label
                for label, ok in (
                    ("GSE", snapshot.gse_fresh),
                    ("ECHO", snapshot.echo_connected),
                    ("NIDAQ", snapshot.nidaq_fresh),
                )
                if not ok
            ]
            if missing:
                detail = ", ".join(missing) + " not reporting"
                if ("feed:" + detail) != self.acknowledged:
                    return Alarm("TELEMETRY FEED LOST", detail, T.ALERT)

        return None

    def acknowledge(self) -> None:
        alarm = self.alarm()
        if alarm is None:
            return
        dispatcher = self.dispatcher
        if dispatcher.problems and alarm.title == "MACHINE FAILED VALIDATION":
            self.acknowledged = "validate:" + dispatcher.problems[0]
        elif alarm.title == "OPERATION FAILED" and dispatcher.last_failure is not None:
            self.acknowledged = "fail:" + dispatcher.last_failure.summary()
        elif alarm.title == "VALVE STATE MISMATCH":
            self.acknowledged = "mismatch:" + alarm.detail
        elif alarm.title == "TELEMETRY FEED LOST":
            self.acknowledged = "feed:" + alarm.detail
        self.events.add("operator acknowledged: " + alarm.title, source="UI",
                        severity="info")

    # -- header -------------------------------------------------------------

    def feed_links(self, gse_server, echo_server, nidaq_server) -> list[tuple[str, str, int]]:
        """Per-feed (name, age, colour), the design's link chips.

        The age is kept to at most four digits so the top bar can reserve a
        fixed slot for it: a feed that has been gone for a minute reads as STALE
        rather than as a number that grows a character wider every second.
        """
        out = []
        for label, server, limit in (
            ("GSE", gse_server, 0.5),
            ("ECHO", echo_server, 2.0),
            ("NIDAQ", nidaq_server, 0.5),
        ):
            age = server.latest_age()
            if age is None:
                out.append((label, "--", T.CRIT))
            elif age >= 10.0:
                out.append((label, "STALE", T.CRIT))
            elif age > limit:
                out.append((label, "%.0fms" % (age * 1000), T.CRIT))
            else:
                color = T.ACID if age <= limit * 0.5 else T.WARN
                out.append((label, "%.0fms" % (age * 1000), color))
        return out

    # -- decay --------------------------------------------------------------

    def decay_rows(self) -> list[dict]:
        from procedures.pressure_decay import DECAY_RESULT

        ready = self.ctx.slope_ready()
        rows = []
        for section in DECAY_SECTIONS:
            live = self.ctx.decay_psi_per_min(section)
            recorded = DECAY_RESULT.get(section)
            failing = math.isfinite(live) and live > DECAY_LIMIT_PSI_PER_MIN
            rows.append(
                {
                    "name": DISPLAY_NAME.get(section, section),
                    "channel": section,
                    "psi": self.value(section),
                    "slope": live,
                    "recorded": recorded if isinstance(recorded, float) else None,
                    "color": T.CRIT if failing else (T.INK if ready else T.INK3),
                }
            )
        return rows

    def decay_verdict(self) -> tuple[str, int]:
        from procedures.pressure_decay import DECAY_RESULT

        verdict = DECAY_RESULT.get("verdict")
        if not verdict:
            return "", T.INK3
        return str(verdict), T.ACID if verdict == "PASS" else T.CRIT

    def decay_progress(self) -> tuple[float, float]:
        return self.ctx.slopes.span_seconds(), self.ctx.slopes.window_seconds


def pretty_state(name: str) -> str:
    """PD_07R_VENT_RECOVER -> 07R VENT RECOVER."""
    if name.startswith("PD_"):
        name = name[3:]
    return name.replace("_", " ")


def vent_floor_text() -> str:
    return "vent floor %.0f psig" % VENT_MINIMUM_PSI


def decay_limit_text() -> str:
    return "limit %.0f psi/min" % DECAY_LIMIT_PSI_PER_MIN
