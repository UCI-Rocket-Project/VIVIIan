"""Pressure Decay Test — MOCH4 Cold Flow procedure, section 3.

    close GN2 Fill 1 at COPV 350 psig                      (§3 step 6)
    fall back if Vent PT drops below 150 psig              (§3 step 8.2)
    close GN2 Fill 1 at COPV 200 / LOX or LNG 250 psig     (§3 step 10)
    pass/fail the 3 psi/min decay criterion                (§3 step 15)
    return to ALL OFF once depressurised through MVAS      (§3 step 22)
    abort on tank overpressure, in any state               (§12.1)

Pressures are read in psig through the context, which applies the calibration
frontendv2 already owns.
"""
from __future__ import annotations

import math

from state_machine import (
    ABORT_PRIORITY,
    Call,
    MismatchPolicy,
    ControlContext,
    DECAY_WINDOW_SECONDS,
    Effector,
    Machine,
    Operation,
    Pulse,
    ResetSlopeWindow,
    SetValve,
    State,
)

from . import table_states
from .operations import (
    TABLE_LEAD_TIME_SECONDS,
    apply_table,
    auto_operation,
    close_valve,
    countdown,
    manual_gate,
    manual_operation,
    open_valve,
    panic_operation,
)
from .table_states import unchecked, valves

# The state marked start here must also be marked within the state
START_STATE = "PD_00_ALL_OFF"

COPV = "COPV"
LOX_TANK = "LOXTANK"
LNG_TANK = "LNGTANK"
VENT = "VENT"

# Sections checked against the 3 psi/min criterion (procedure Table 15).
DECAY_SECTIONS = (COPV, LOX_TANK, LNG_TANK, VENT)

# --- Thresholds, all psig ---------------------------------------------------
COPV_FILL_TARGET_PSI = 350.0         # §3 step 4: pressurise COPV to 350 psig
COPV_OVERPRESSURE_LIMIT_PSI = 400.0  # margin above target; falls to panic
VENT_RECOVERY_TARGET_PSI = 300.0     # §3 step 8.2: repressurise the vent line
SYSTEM_CHARGE_TARGET_PSI = 200.0     # §3 step 10.1
TANK_CHARGE_LIMIT_PSI = 250.0        # §3 step 10.2
VENT_MINIMUM_PSI = 150.0             # §3 step 8.1: Vent PT must stay above this
TANK_OVERPRESSURE_LIMIT_PSI = 650.0  # §12.1: abort if LOX or LNG reach 650
DEPRESSURISED_LIMIT_PSI = 20.0       # §3 step 21: confirm the system is empty
DECAY_LIMIT_PSI_PER_MIN = 3.0        # §3 step 15: no section above 3 psi/min

# --- Timing -----------------------------------------------------------------
PRESSURE_SETTLE_SECONDS = 15.0       # §3 step 9: "once pressure has stabilized"
# The vent line reads ambient at the instant PV 1 opens and takes a moment to
# come up, so the "Vent PT stays above 150 psig" watch cannot apply to the
# initial transient — §3 step 8.1 is a criterion for the remainder of the test,
# not for the transition itself.
VENT_WATCH_DELAY_SECONDS = 10.0
MVAS_COUNTDOWN_SECONDS = 5.0         # §3 step 20.1: "Opening MVAS in 5...4...3...2...1"

# Watchdogs. A guard reading a dead sensor evaluates NaN, which is False, so a
# state with automatic exits would otherwise wait forever. These are generous:
# they exist to catch a hung procedure, not to pace one.
FILL_WATCHDOG_SECONDS = 900.0
SETTLE_WATCHDOG_SECONDS = 900.0
DECAY_WATCHDOG_SECONDS = DECAY_WINDOW_SECONDS + 600.0
VENT_WATCHDOG_SECONDS = 600.0

# --- Valve configurations ---------------------------------------------------
# §3 step 11: tanks isolated, PV 1 feeding them.
TANKS_PRESSURIZING = valves(tank_vent=True, pv2=True, pv1=True)

# §3 step 16: GN2 VV and GN2 Fill 1 opened to vent the GSE. Nothing moves
# again until MVAS in §3 step 24.
GSE_VENTED = valves(
    tank_vent=True,
    pv2=True,
    pv1=True,
    sol_gn2_vent=True,
    sol_gn2_fill_1=True,
)

# What the decay measurement and everything after it sit in.
# @TODO Ask prop what these values should be, unclear from procedure
GSE_VENTED_HOLD = unchecked(GSE_VENTED, "sol_gn2_vent", "sol_gn2_fill_1")


# --- Recorded results -------------------------------------------------------
# Filled in at the moment the pass/fail decision is taken, so the number in the
# readout is the number that made the decision (procedure Table 15).
DECAY_RESULT: dict[str, float | str] = {}


class OneShotLatch:
    """A true guard only fires once"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.used = False

    def set(self, ctx: ControlContext, effector: Effector) -> None:
        self.used = True

    def reset(self) -> None:
        self.used = False


VENT_RECOVERY_LATCH = OneShotLatch("vent recovery")   # §3 step 8.2: "not more than once"
OVERPRESSURE_LATCH = OneShotLatch("overpressure abort")


def reset_latches() -> None:
    VENT_RECOVERY_LATCH.reset()
    OVERPRESSURE_LATCH.reset()
    DECAY_RESULT.clear()


def _record_decay(verdict: str):
    def write_result(ctx: ControlContext, effector: Effector) -> None:
        DECAY_RESULT.clear()
        DECAY_RESULT["verdict"] = verdict
        for section in DECAY_SECTIONS:
            DECAY_RESULT[section] = ctx.slope_psi_per_min(section)

    return write_result


def _decay_measurement_ready(ctx: ControlContext) -> bool:
    """A full window has accumulated and the slopes are readable."""
    return ctx.slope_ready() and math.isfinite(ctx.worst_slope(DECAY_SECTIONS))


def build_machine() -> Machine:
    reset_latches()

    # -- §12.1, evaluated in every state ------------------------------------
    # No destination check: the abort has to land, and a failed check could
    # only abort again.
    overpressure_abort = Operation(
        actions=(apply_table("abort"), Call(OVERPRESSURE_LATCH.set, "latch abort")),
        dest_state="PD_ABORTED",
        auto=True,
        name="ABORT — tank overpressure",
        guard=lambda ctx: (
            not OVERPRESSURE_LATCH.used
            and (
                ctx.psi(LOX_TANK) >= TANK_OVERPRESSURE_LIMIT_PSI
                or ctx.psi(LNG_TANK) >= TANK_OVERPRESSURE_LIMIT_PSI
            )
        ),
        guard_text=f"LOX or LNG TANK PT >= {TANK_OVERPRESSURE_LIMIT_PSI:.0f} psig",
        priority=ABORT_PRIORITY,
        overrides_abort=True,
        description="Procedure §12.1 Tank Overpressurization Event.",
        lead_time_s=TABLE_LEAD_TIME_SECONDS,
        verify_dest=False,
    )

    states = [
        # ------------------------------------------------------------------
        State(
            "PD_00_ALL_OFF",
            (
                manual_gate(
                    "Safety Officer: PPE on, perimeter established",
                    "PD_01_AREA_SECURE",
                    description="§3 steps 1-2. Pressurized systems PPE: safety glasses, r < 30 ft.",
                ),
            ),
            table_states.ALL_OFF,
            panic_operation("PD_ABORTED"),
            description="§3 step 3: valve states verified ALL OFF (Table 13).",
            start=True,
        ),
        State(
            "PD_01_AREA_SECURE",
            (
                manual_gate(
                    "Test Director: alert personnel — COPV to 350 psig",
                    "PD_02_BOTTLE_READY",
                    description="§3 step 4.",
                ),
            ),
            table_states.ALL_OFF,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_02_BOTTLE_READY",
            (
                manual_operation(
                    "Open GN2 Fill 1 (bottle open, regulator at 800 psig)",
                    "PD_03_COPV_FILL",
                    (open_valve("sol_gn2_fill_1"),),
                    description=(
                        "§3 steps 5-7. Bottle Operator confirms regulator closed, GN2 Bottle 1 "
                        "fully open, then regulator slowly opened to 800 psig."
                    ),
                ),
            ),
            table_states.ALL_OFF,
            panic_operation("PD_ABORTED"),
        ),
        # ------------------------------------------------------------------
        State(
            "PD_03_COPV_FILL",
            (
                auto_operation(
                    "Close GN2 Fill 1 at 350 psig",
                    "PD_04_COPV_350",
                    (close_valve("sol_gn2_fill_1"),),
                    guard=lambda ctx: ctx.psi(COPV) >= COPV_FILL_TARGET_PSI,
                    guard_text=f"COPV PT >= {COPV_FILL_TARGET_PSI:.0f} psig",
                    description="§3 step 8: close GN2 Fill 1 once 350 psig is attained.",
                ),
                auto_operation(
                    "Overpressure — close fill and panic",
                    "PD_ABORTED",
                    (apply_table("abort"),),
                    guard=lambda ctx: ctx.psi(COPV) >= COPV_OVERPRESSURE_LIMIT_PSI,
                    guard_text=f"COPV PT >= {COPV_OVERPRESSURE_LIMIT_PSI:.0f} psig",
                    priority=500,
                    # Same reasoning as the global abort: it has to land.
                    lead_time_s=TABLE_LEAD_TIME_SECONDS,
                    verify_dest=False,
                ),
            ),
            valves(sol_gn2_fill_1=True),
            panic_operation("PD_ABORTED"),
            max_seconds=FILL_WATCHDOG_SECONDS,
            description="§3 steps 7-8: pressurising COPV, crew leak-checking fittings.",
        ),
        State(
            "PD_04_COPV_350",
            (
                manual_operation(
                    "Close LOX VV & LNG VV (leaks mitigated)",
                    "PD_05_TANKS_ISOLATED",
                    (close_valve("tank_vent"),),
                    description=(
                        "§3 steps 7.1, 9. Test Stand Crew spray fittings with soap and torque "
                        "until bubbles subside or max torque is reached."
                    ),
                ),
            ),
            valves(),
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_05_TANKS_ISOLATED",
            (
                manual_gate(
                    "Test Director: verify LOX FV & LNG FV are closed",
                    "PD_06_READY_TANK_PRESS",
                    description="§3 step 10. Manual fill valves, not electrically controlled.",
                ),
            ),
            valves(tank_vent=True),
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_06_READY_TANK_PRESS",
            (
                manual_operation(
                    "Close PV 2, then open PV 1",
                    "PD_07_TANK_PRESSURIZING",
                    (close_valve("pv2"), open_valve("pv1")),
                    description="§3 step 11: order matters — PV 2 closes before PV 1 opens.",
                ),
            ),
            valves(tank_vent=True),
            panic_operation("PD_ABORTED"),
        ),
        # ------------------------------------------------------------------
        State(
            "PD_07_TANK_PRESSURIZING",
            (
                auto_operation(
                    "Pressures stabilised",
                    "PD_08_TANKS_STABLE",
                    (),
                    guard=lambda ctx: (
                        ctx.in_state_for() >= PRESSURE_SETTLE_SECONDS
                        and math.isfinite(ctx.psi(LOX_TANK))
                        and math.isfinite(ctx.psi(LNG_TANK))
                    ),
                    guard_text=f"settled for {PRESSURE_SETTLE_SECONDS:.0f}s with tank PTs reading",
                    description="§3 step 12: read out all PTs once pressure has stabilised.",
                ),
                auto_operation(
                    "Vent line low — recover",
                    "PD_07R_VENT_RECOVER",
                    (
                        close_valve("pv1"),
                        open_valve("pv2"),
                        open_valve("sol_gn2_fill_1"),
                        Call(VENT_RECOVERY_LATCH.set, "latch vent recovery"),
                    ),
                    guard=lambda ctx: (
                        not VENT_RECOVERY_LATCH.used
                        and ctx.in_state_for() >= VENT_WATCH_DELAY_SECONDS
                        and ctx.psi(VENT) < VENT_MINIMUM_PSI
                    ),
                    guard_text=(
                        f"Vent PT < {VENT_MINIMUM_PSI:.0f} psig after "
                        f"{VENT_WATCH_DELAY_SECONDS:.0f}s (once only)"
                    ),
                    priority=500,
                    description=(
                        "§3 step 11.2: close PV 1, open PV 2, repressurise the vent line. "
                        "Do not do this more than once — it could bring tank pressures above "
                        "safe levels."
                    ),
                ),
            ),
            TANKS_PRESSURIZING,
            panic_operation("PD_ABORTED"),
            max_seconds=SETTLE_WATCHDOG_SECONDS,
        ),
        State(
            "PD_07R_VENT_RECOVER",
            (
                auto_operation(
                    "Vent line repressurised — close GN2 Fill 1",
                    "PD_06_READY_TANK_PRESS",
                    (close_valve("sol_gn2_fill_1"),),
                    guard=lambda ctx: ctx.psi(COPV) >= VENT_RECOVERY_TARGET_PSI,
                    guard_text=f"COPV PT >= {VENT_RECOVERY_TARGET_PSI:.0f} psig",
                    description="§3 step 11.2, then repeat tank press.",
                ),
            ),
            valves(tank_vent=True, sol_gn2_fill_1=True),
            panic_operation("PD_ABORTED"),
            max_seconds=VENT_WATCHDOG_SECONDS,
        ),
        # ------------------------------------------------------------------
        State(
            "PD_08_TANKS_STABLE",
            (
                manual_operation(
                    "Open GN2 Fill 1 — fill system to 200 psig",
                    "PD_09_FILL_200",
                    (open_valve("sol_gn2_fill_1"),),
                    description=(
                        "§3 step 13. Table 14 readout first: COPV, Vent PT ~280 psig, "
                        "LOX and LNG TANK PT 30-70 psig."
                    ),
                ),
            ),
            TANKS_PRESSURIZING,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_09_FILL_200",
            (
                auto_operation(
                    "Close GN2 Fill 1 at target",
                    "PD_10_SYSTEM_CHARGED",
                    (close_valve("sol_gn2_fill_1"),),
                    guard=lambda ctx: (
                        ctx.psi(COPV) >= SYSTEM_CHARGE_TARGET_PSI
                        or ctx.psi(LOX_TANK) >= TANK_CHARGE_LIMIT_PSI
                        or ctx.psi(LNG_TANK) >= TANK_CHARGE_LIMIT_PSI
                    ),
                    guard_text=(
                        f"COPV >= {SYSTEM_CHARGE_TARGET_PSI:.0f} or LOX/LNG >= {TANK_CHARGE_LIMIT_PSI:.0f} psig"
                    ),
                    description="§3 steps 13.1-13.2: whichever limit is reached first.",
                ),
            ),
            valves(tank_vent=True, pv2=True, pv1=True, sol_gn2_fill_1=True),
            panic_operation("PD_ABORTED"),
            max_seconds=FILL_WATCHDOG_SECONDS,
        ),
        State(
            "PD_10_SYSTEM_CHARGED",
            (
                manual_gate(
                    "Bottle Operator: close GN2 Bottle 1",
                    "PD_11_BOTTLE_CLOSED",
                    description=(
                        "§3 steps 14-15. Crew continues leak checks and marks any fitting "
                        "still leaking after torque."
                    ),
                ),
            ),
            TANKS_PRESSURIZING,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_11_BOTTLE_CLOSED",
            (
                manual_operation(
                    "Open GN2 VV & GN2 Fill 1 — depressurise GSE",
                    "PD_12_GSE_DEPRESSURIZED",
                    (open_valve("sol_gn2_vent"), open_valve("sol_gn2_fill_1")),
                    description="§3 step 16.",
                ),
            ),
            TANKS_PRESSURIZING,
            panic_operation("PD_ABORTED"),
        ),
        # ------------------------------------------------------------------
        State(
            "PD_12_GSE_DEPRESSURIZED",
            (
                manual_operation(
                    "Start Automated Pressure Decay Test",
                    "PD_13_DECAY_MEASURING",
                    (ResetSlopeWindow(),),
                    description=(
                        "§3 step 17. Resets the measurement window so the slope means decay "
                        "since this moment, not decay including the fill transient."
                    ),
                ),
            ),
            GSE_VENTED,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_13_DECAY_MEASURING",
            (
                auto_operation(
                    "Decay within limit — PASS",
                    "PD_14_DECAY_PASS",
                    (Call(_record_decay("PASS"), "record decay result"),),
                    guard=lambda ctx: (
                        _decay_measurement_ready(ctx)
                        and ctx.worst_slope(DECAY_SECTIONS) <= DECAY_LIMIT_PSI_PER_MIN
                    ),
                    guard_text=f"all sections <= {DECAY_LIMIT_PSI_PER_MIN:.0f} psi/min",
                    mutually_exclusive_with=("Decay exceeds limit — FAIL",),
                    description="§3 step 18: no section may lose more than 3 psi/minute.",
                ),
                auto_operation(
                    "Decay exceeds limit — FAIL",
                    "PD_15_DECAY_FAIL",
                    (Call(_record_decay("FAIL"), "record decay result"),),
                    guard=lambda ctx: (
                        _decay_measurement_ready(ctx)
                        and ctx.worst_slope(DECAY_SECTIONS) > DECAY_LIMIT_PSI_PER_MIN
                    ),
                    guard_text=f"a section exceeds {DECAY_LIMIT_PSI_PER_MIN:.0f} psi/min",
                    mutually_exclusive_with=("Decay within limit — PASS",),
                ),
            ),
            # Holding: nothing moves, so the configuration is known and worth
            # watching for drift.
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
            max_seconds=DECAY_WATCHDOG_SECONDS,
            description=(
                f"§3 steps 17-18. Measuring over a {DECAY_WINDOW_SECONDS:.0f}s window; "
                "the verdict is taken from the slope at that moment."
            ),
        ),
        State(
            "PD_14_DECAY_PASS",
            (
                manual_gate(
                    "Test Director: alert personnel — depressurising through MVAS",
                    "PD_16_DEPRESS_READY",
                    description="§3 step 19.",
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
            description="§3 step 18, Table 15: decay within 3 psi/min on every section.",
        ),
        State(
            "PD_15_DECAY_FAIL",
            (
                manual_gate(
                    "Test Director: alert personnel — depressurising through MVAS",
                    "PD_16_DEPRESS_READY",
                    description="§3 step 19. Note the failing section before depressurising.",
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
            description="§3 step 18, Table 15: a section exceeded 3 psi/min.",
        ),
        # ------------------------------------------------------------------
        State(
            "PD_16_DEPRESS_READY",
            (
                manual_operation(
                    "Turn on Alarm",
                    "PD_17_ALARM_ON",
                    (SetValve("alarm", True),),
                    description="§3 step 20.",
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_17_ALARM_ON",
            (
                manual_gate(
                    "Stand clear, compressor at 120 psi, MVAS verified closed",
                    "PD_18_COUNTDOWN",
                    description="§3 steps 21-23.",
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_18_COUNTDOWN",
            (
                manual_operation(
                    "Open MVAS — 5...4...3...2...1...EARS",
                    "PD_19_DEPRESSURIZING",
                    (
                        countdown(MVAS_COUNTDOWN_SECONDS, "countdown to MVAS"),
                        Pulse("mvas_open"),
                    ),
                    description="§3 step 24.1.",
                    timeout_s=MVAS_COUNTDOWN_SECONDS + 15.0,
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
        ),
        State(
            "PD_19_DEPRESSURIZING",
            (
                auto_operation(
                    "Depressurised — set ALL OFF",
                    "PD_20_COMPLETE",
                    (apply_table("all_off"),),
                    guard=lambda ctx: (
                        ctx.psi(COPV) <= DEPRESSURISED_LIMIT_PSI
                        and ctx.psi(LOX_TANK) <= DEPRESSURISED_LIMIT_PSI
                        and ctx.psi(LNG_TANK) <= DEPRESSURISED_LIMIT_PSI
                    ),
                    guard_text=f"COPV, LOX and LNG all <= {DEPRESSURISED_LIMIT_PSI:.0f} psig",
                    description="§3 steps 25-26: confirm depressurised, then ALL OFF (Table 16).",
                    lead_time_s=TABLE_LEAD_TIME_SECONDS,
                ),
            ),
            GSE_VENTED_HOLD,
            panic_operation("PD_ABORTED"),
            max_seconds=VENT_WATCHDOG_SECONDS,
        ),
        State(
            "PD_20_COMPLETE",
            (
                manual_gate(
                    "Restart procedure",
                    "PD_00_ALL_OFF",
                    description=(
                        "§3 steps 27-28: Test Director depressurises the compressor outlet and "
                        "declares the stand safe to approach."
                    ),
                ),
            ),
            table_states.ALL_OFF,
            panic_operation("PD_ABORTED"),
            description="Pressure decay complete.",
        ),
        # ------------------------------------------------------------------
        State(
            "PD_ABORTED",
            (
                manual_operation(
                    "Acknowledge — return to ALL OFF",
                    "PD_00_ALL_OFF",
                    (
                        apply_table("all_off"),
                        SetValve("alarm", False),
                        Call(lambda ctx, effector: reset_latches(), "clear latches"),
                    ),
                    description=(
                        "Clears the abort and vent-recovery latches and restarts the procedure "
                        "from the top. Without this the overpressure watch, having fired once, "
                        "would stay latched for the rest of the session."
                    ),
                    lead_time_s=TABLE_LEAD_TIME_SECONDS,
                ),
            ),
            table_states.ABORT,
            None,
            # WARN, not ABORT. The safe-out from here is the abort table, which
            # is what we are already in, so aborting on a mismatch would panic
            # into this state once a cycle forever. Surface it and let the
            # operator decide.
            on_mismatch=MismatchPolicy.WARN,
            description="Abort configuration: vents open, MVAS closed, alarm on.",
        ),
    ]

    return Machine.build(
        "pressure_decay",
        states,
        START_STATE,
        default_panic=panic_operation("PD_ABORTED"),
        global_transitions=(overpressure_abort,),
    )
