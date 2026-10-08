"""Procedure authoring and shared safe-out regressions, without hardware."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import math

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "GUI2.1"))

from state_machine.state_machine import (
    Dispatcher, Machine, MismatchPolicy, Operation, OpStatus, State,
    VERIFY_FEED_GRACE_SECONDS,
)
from tests.test_state_machine import FakeEffector, make_context
from state_machine.procedures.pressure_decay import build_machine
from state_machine.procedures import pressure_decay
from state_machine.operations import close_valve, open_valve


class TestProcedureAPI(unittest.TestCase):
    def test_inline_exits_link_and_execute_in_order(self):
        first = Operation((), "B", name="continue", lead_time_s=0)
        alternate = Operation((), "C", name="alternate", lead_time_s=0)
        machine = Machine.build("inline", [
            State.define("A", first, alternate, start=True),
            State.define("B", Operation((), "A")),
            State.define("C", Operation((), "A")),
        ])
        self.assertEqual(machine.validate(), [])
        dispatcher = Dispatcher(machine, make_context(), FakeEffector())
        self.assertEqual(dispatcher.current.manual_operations(), (first, alternate))
        dispatcher.request(first)
        dispatcher.tick(1.0)
        self.assertIs(dispatcher.current, machine.states["B"])

    def test_default_panic_is_a_reachable_exit_and_runs(self):
        panic = Operation((), "SAFE", name="panic", verify_dest=False, lead_time_s=0)
        machine = Machine.build("shared panic", [
            State.define("A", expected_state={"valve_a": True}, start=True,
                         mismatch_grace_s=0),
            State.define("SAFE", Operation((), "A"), on_mismatch=MismatchPolicy.WARN),
        ], default_panic=panic)
        self.assertEqual(machine.validate(), [])
        dispatcher = Dispatcher(machine, make_context(), FakeEffector())
        dispatcher.arm()
        dispatcher.tick(1.0)
        self.assertEqual(dispatcher.current.name, "SAFE")
        self.assertFalse(dispatcher.armed)

    def test_state_panic_overrides_default_in_reachability_and_runtime(self):
        machine = Machine.build("override", [
            State.define("A", start=True, panic=Operation((), "LOCAL", lead_time_s=0)),
            State.define("LOCAL", Operation((), "A")),
            State.define("DEFAULT", Operation((), "A")),
        ], default_panic=Operation((), "DEFAULT", lead_time_s=0))
        self.assertEqual(machine.validate(), [])
        dispatcher = Dispatcher(machine, make_context(), FakeEffector())
        dispatcher.panic()
        dispatcher.tick(1.0)
        self.assertEqual(dispatcher.current.name, "LOCAL")

    def test_stale_feed_grace_expires_when_actions_finish_at_zero(self):
        destination = State.define("B", expected_state={"valve_a": True})
        operation = Operation((), destination, lead_time_s=0)
        context = make_context(healthy=False)
        context.begin_cycle(0.0)
        operation.start(context)
        self.assertIs(operation.step(context, FakeEffector()), OpStatus.RUNNING)
        context.begin_cycle(VERIFY_FEED_GRACE_SECONDS + 0.1)
        self.assertIs(operation.step(context, FakeEffector()), OpStatus.FAILED)
        self.assertEqual(operation.feedback.unreadable, ("valve_a",))

    def test_pressure_decay_validates_with_shared_panic(self):
        machine = build_machine()
        self.assertEqual(machine.validate(), [])
        self.assertEqual(machine.initial, "PD_00_ALL_OFF")
        self.assertTrue(all(state.panic is None for state in machine.states.values()))
        self.assertEqual(machine.default_panic.dest_name(), "PD_ABORTED")

    def test_pressure_target_and_overpressure_remain_distinct(self):
        state = build_machine().states["PD_03_COPV_FILL"]
        for pressure, count in ((math.nan, 0), (349.0, 0), (350.0, 1), (400.0, 2)):
            with self.subTest(pressure=pressure):
                context = make_context(nidaq={"COPV": pressure}, scales={"COPV": (1.0, 0.0)})
                context.begin_cycle(1.0)
                exits = state.evaluate(context)
                self.assertEqual(len(exits), count)
                if count == 2:
                    self.assertEqual(max(exits, key=lambda op: op.priority).dest_name(), "PD_ABORTED")

    def test_documented_example_builds_and_validates(self):
        document = (ROOT / "docs" / "state-machine-api.md").read_text(encoding="utf-8")
        example = document.split("```python\n", 1)[1].split("```", 1)[0]
        namespace = {}
        exec(compile(example, "state-machine-api.md", "exec"), namespace)
        self.assertEqual(namespace["build_machine"]().validate(), [])

    def test_physical_valve_commands_preserve_normally_open_polarity(self):
        for valve in ("pv2", "tank_vent"):
            with self.subTest(valve=valve):
                self.assertFalse(open_valve(valve).state)
                self.assertTrue(close_valve(valve).state)
        self.assertTrue(open_valve("pv1").state)
        self.assertFalse(close_valve("pv1").state)


class TestPressureDecayBookkeeping(unittest.TestCase):
    def setUp(self):
        self.machine = build_machine()
        self.addCleanup(pressure_decay.reset_latches)

    def test_recovery_and_abort_latch_independently_until_acknowledged(self):
        context = SimpleNamespace(
            psi=lambda field: 700.0 if field in ("LOXTANK", "LNGTANK") else 100.0,
            in_state_for=lambda: pressure_decay.VENT_WATCH_DELAY_SECONDS + 1,
        )
        recovery = self.machine.states["PD_07_TANK_PRESSURIZING"].operations[1]
        abort = self.machine.global_transitions[0]
        self.assertTrue(recovery.guard(context))
        self.assertTrue(abort.guard(context))

        recovery.actions[-1].begin(context, FakeEffector())
        self.assertFalse(recovery.guard(context))
        self.assertTrue(abort.guard(context))
        abort.actions[-1].begin(context, FakeEffector())
        self.assertFalse(abort.guard(context))

        pressure_decay.DECAY_RESULT["verdict"] = "FAIL"
        acknowledge = self.machine.states["PD_ABORTED"].operations[0]
        acknowledge.actions[-1].begin(context, FakeEffector())
        self.assertTrue(recovery.guard(context))
        self.assertTrue(abort.guard(context))
        self.assertEqual(pressure_decay.DECAY_RESULT, {})

    def test_decay_requires_a_full_finite_window_and_preserves_boundary(self):
        state = self.machine.states["PD_13_DECAY_MEASURING"]
        for ready, slope, destination in (
            (False, 0.0, None),
            (True, math.nan, None),
            (True, math.inf, None),
            (True, -math.inf, None),
            (True, 3.0, "PD_14_DECAY_PASS"),
            (True, 3.01, "PD_15_DECAY_FAIL"),
        ):
            with self.subTest(ready=ready, slope=slope):
                context = SimpleNamespace(slope_ready=lambda: ready, worst_slope=lambda fields: slope)
                self.assertEqual(
                    [op.dest_name() for op in state.evaluate(context)],
                    [] if destination is None else [destination],
                )

    def test_both_verdict_actions_record_current_signed_decay(self):
        rates = dict(zip(pressure_decay.DECAY_SECTIONS, (1.0, -2.0, 3.0, 4.0)))
        context = SimpleNamespace(decay_psi_per_min=rates.__getitem__)
        for operation, verdict in zip(
            self.machine.states["PD_13_DECAY_MEASURING"].operations, ("PASS", "FAIL")
        ):
            with self.subTest(verdict=verdict):
                pressure_decay.DECAY_RESULT["old section"] = 999.0
                operation.actions[0].begin(context, FakeEffector())
                self.assertEqual(pressure_decay.DECAY_RESULT, {"verdict": verdict, **rates})


if __name__ == "__main__":
    unittest.main()
