from ..state_machine import (
    ABORT_PRIORITY, Call, Machine, MismatchPolicy, Operation, SetValve, State, WaitUntil,
)
from ..operations import (
    apply_table, auto_operation, close_valve,
    manual_gate, manual_operation, open_valve, panic_operation,
)
from ..table_states import ALL_OFF, ABORT, FINAL, unchecked, valves

TANK_ABORT_PSI = 650.0
COPV_MINIMUM_PSI = 2000.0
COPV_TARGET_PSI = 4000.0
# Same hung-fill allowance as pressure decay; not a stabilization criterion.
FILL_WATCHDOG_SECONDS = 900.0

# Table 20 does not specify the tank vents. Pin them only after commanding close.
READY = unchecked(ALL_OFF, "tank_vent") # type: ignore
FILL_1 = valves(READY, sol_gn2_fill_1=True)
TANKS_CLOSED = valves(FILL_1, tank_vent=True)
FILL_2 = valves(TANKS_CLOSED, sol_gn2_fill_2=True)
PV2_CLOSED = valves(FILL_2, pv2=True)
PRESSURIZING = valves(PV2_CLOSED, pv1=True)
FILL_3 = valves(PRESSURIZING, sol_gn2_fill_3=True)


def build_machine() -> Machine:
    aborted: set[str] = set()
    return Machine.build(
        "tank_pressurization",
        [
            State(
                "TP_00_CLEAR_STAND",
                manual_gate(
                    "All personnel clear; crew in bunkers; directors in control room",
                    "TP_01_VERIFY_READY",
                    description="Test Director and Program Manager notify FAR of terminal count. "
                    "No personnel at the stand. Announce that systems will be pressurized.",
                ),
                expected_state=READY, start=True,
                description="Monitor pressures and temperatures continuously; be ready to sound the alarm.",
            ),
            State(
                "TP_01_VERIFY_READY",
                manual_gate(
                    "Verify Table 20, MVAS CLOSED, COPV 500 psig, both tanks 0-50 psig",
                    "TP_02_ANNOUNCE",
                    description="Test Director and Avionics Test Controller verify Table 21. "
                    "MVAS position is a physical confirmation, not a momentary-command echo.",
                ),
                expected_state=READY,
            ),
            State(
                "TP_02_ANNOUNCE",
                manual_operation(
                    "FAR announcement complete — open GN2 Fill 1", "TP_03_FILL_1",
                    (open_valve("sol_gn2_fill_1"),),
                    description="Announce: pressurization of the test stand will commence. "
                    "Continuously read COPV, LOX/LNG tank pressures and COPV temperature during fill.",
                ),
                expected_state=READY,
            ),
            State(
                "TP_03_FILL_1",
                manual_operation(
                    "Vent PT verified 300 psig — close LOX and LNG vents", "TP_04_VERIFY_VENTS",
                    (close_valve("tank_vent"),),
                ),
                expected_state=FILL_1,
            ),
            State(
                "TP_04_VERIFY_VENTS",
                manual_gate("Test Leads visually confirm LOX and LNG vents CLOSED", "TP_05_STABILIZE_1"),
                expected_state=TANKS_CLOSED,
            ),
            State(
                "TP_05_STABILIZE_1",
                manual_operation(
                    "COPV pressure stabilized — open GN2 Fill 2", "TP_06_FILL_2",
                    (open_valve("sol_gn2_fill_2"),),
                ),
                expected_state=TANKS_CLOSED,
            ),
            State(
                "TP_06_FILL_2",
                auto_operation(
                    "COPV at minimum pressure", "TP_07_STABILIZE_2", (),
                    guard=lambda ctx: ctx.psi("COPV") >= COPV_MINIMUM_PSI,
                    guard_text="COPV >= 2000 psig; stabilization still requires confirmation",
                ),
                auto_operation(
                    "Abort on timing limit", "TP_ABORTED",
                    actions=(apply_table("abort"), SetValve("alarm", True)),
                    guard=lambda ctx: ctx.in_state_for() > FILL_WATCHDOG_SECONDS,
                    guard_text="Timing limit exceeded",
                    priority=ABORT_PRIORITY, verify_dest=False,
                ),
                expected_state=FILL_2,
            ),
            State(
                "TP_07_STABILIZE_2",
                manual_operation(
                    "COPV stabilized at minimum 2000 psig — close PV 2", "TP_08_PV2_CLOSED",
                    (WaitUntil(lambda ctx: ctx.psi("COPV") >= COPV_MINIMUM_PSI,
                               label="COPV must still be at least 2000 psig"),
                     close_valve("pv2")),
                    description="Recheck the minimum pressure before commanding PV 2 closed.",
                ),
                expected_state=FILL_2,
            ),
            State(
                "TP_08_PV2_CLOSED",
                manual_operation("Open PV 1", "TP_09_STABILIZE_PV1", (open_valve("pv1"),)),
                expected_state=PV2_CLOSED,
            ),
            State(
                "TP_09_STABILIZE_PV1",
                manual_operation(
                    "COPV pressure stabilized — open GN2 Fill 3", "TP_10_STABILIZE_3",
                    (open_valve("sol_gn2_fill_3"),),
                ),
                expected_state=PRESSURIZING,
            ),
            State(
                "TP_10_STABILIZE_3",
                manual_operation(
                    "COPV pressure stabilized — open GN2 Fill 4", "TP_11_FILL_4",
                    (open_valve("sol_gn2_fill_4"),),
                ),
                expected_state=FILL_3,
            ),
            State(
                "TP_11_FILL_4",
                auto_operation(
                    "COPV at 4000 psig — close all GN2 fills", "TP_12_VERIFY_PRESSURES",
                    tuple(close_valve(f"sol_gn2_fill_{number}") for number in range(1, 5)),
                    guard=lambda ctx: ctx.psi("COPV") >= COPV_TARGET_PSI,
                    guard_text="COPV >= 4000 psig",
                    description="Read Table 22 pressures during fill; close fills at target without "
                    "waiting for operator acknowledgement.",
                ),
                auto_operation(
                    "Abort on timing limit", "TP_ABORTED",
                    actions=(apply_table("abort"), SetValve("alarm", True)),
                    guard=lambda ctx: ctx.in_state_for() > FILL_WATCHDOG_SECONDS,
                    guard_text="Timing limit exceeded",
                    priority=ABORT_PRIORITY, verify_dest=False,
                ),
                expected_state=valves(FILL_3, sol_gn2_fill_4=True),
            ),
            State(
                "TP_12_VERIFY_PRESSURES",
                manual_operation(
                    "Verify COPV 4000, LOX 400–550, LNG 400–500 psig — open GN2 VV",
                    "TP_13_VERIFY_FINAL", (open_valve("sol_gn2_vent"),),
                    description="Test Director and Avionics Test Controller confirm Table 22.",
                ),
                expected_state=valves(tank_vent=True, pv2=True, pv1=True),
            ),
            State(
                "TP_13_VERIFY_FINAL",
                manual_gate(
                    "Verify Table 23, manual fill valves CLOSED, MVAS CLOSED — Go/No-Go",
                    "TP_14_COMPLETE",
                    description="Verify all pressures and final valve positions before proceeding.",
                ),
                expected_state=FINAL,
            ),
            State("TP_14_COMPLETE", expected_state=FINAL,
                  description="Tank pressurization complete; awaiting Go/No-Go. Overpressure watch remains active."),
            State(
                "TP_ABORTED",
                manual_operation(
                    "Acknowledge abort — ALL OFF", "TP_00_CLEAR_STAND",
                    (apply_table("all_off"), SetValve("alarm", False),
                     Call(lambda ctx, effector: aborted.clear(), "clear abort latch")),
                ),
                expected_state=ABORT, on_mismatch=MismatchPolicy.WARN,
            ),
        ],
        default_panic=panic_operation("TP_ABORTED"),
        global_transitions=(Operation(
            actions=(apply_table("abort"), SetValve("alarm", True),
                     Call(lambda ctx, effector: aborted.add("overpressure"), "latch abort")),
            dest_state="TP_ABORTED", auto=True, name="ABORT — tank overpressure",
            guard=lambda ctx: not aborted and (
                ctx.psi("LOXTANK") >= TANK_ABORT_PSI or ctx.psi("LNGTANK") >= TANK_ABORT_PSI
            ),
            guard_text="LOX or LNG tank >= 650 psig", priority=ABORT_PRIORITY,
            overrides_abort=True, verify_dest=False,
        ),),
    )
