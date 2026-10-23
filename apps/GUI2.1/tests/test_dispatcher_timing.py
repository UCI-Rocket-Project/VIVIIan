"""Dispatcher clock lifecycle and context-driven procedure timing."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "GUI2.1"))

from tests.test_state_machine import FakeEffector, make_context
from gui_state_machine import StateMachinePanel
from state_machine.procedures import pressure_decay, tank_pressurization
from state_machine.operations import auto_operation, manual_operation
from state_machine.state_machine import (
    ABORT_PRIORITY, ApplyTable, Dispatcher, DispatcherMode, DispatcherTiming, InterruptPolicy,
    Machine, Operation, OpPhase, SetValve, State, WaitUntil,
)


class TestDispatcherTiming(unittest.TestCase):
    def make_dispatcher(self, *, timing=None, safety_guard=None):
        self.context = make_context()
        self.effector = FakeEffector()
        self.operation = Operation(
            (WaitUntil(lambda ctx: ctx.in_operation_for() >= 5.0),
             SetValve("valve_a", True)), "B",
        )
        safety = (() if safety_guard is None else (Operation(
            (), "B", auto=True, guard=safety_guard, priority=ABORT_PRIORITY,
        ),))
        machine = Machine.build("timing", [
            State("A", self.operation, *safety, start=True),
            State("B", Operation((), "A")),
        ])
        return Dispatcher(machine, self.context, self.effector, timing=timing)

    def test_clocks_start_at_zero_and_reset_independently(self):
        d = self.make_dispatcher()
        ctx = self.context
        d.tick(0.0)
        self.assertEqual((ctx.since_startup(), ctx.in_procedure_for(),
                          ctx.in_state_for(), ctx.in_operation_for()), (0, 0, 0, 0))
        d.tick(4.0)
        d.request(self.operation)
        d.tick(5.0)
        self.assertEqual((ctx.since_startup(), ctx.in_procedure_for(),
                          ctx.in_state_for(), ctx.in_operation_for()), (5, 5, 5, 0))
        d.tick(9.0)
        self.assertEqual(ctx.in_operation_for(), 4)
        self.assertEqual(self.effector.staged, [])
        d.tick(10.0)
        d.tick(11.0)
        self.assertEqual(self.effector.staged, [("valve_a", True)])
        d.tick(12.0)
        self.assertEqual(d.current.name, "B")
        self.assertEqual((ctx.since_startup(), ctx.in_procedure_for(),
                          ctx.in_state_for(), ctx.in_operation_for()), (12, 12, 0, 0))
        d.force_state("A")
        d.tick(13.0)
        self.assertEqual((ctx.since_startup(), ctx.in_procedure_for(), ctx.in_state_for()), (13, 13, 1))

    def test_procedure_selection_preserves_startup_and_runtime_policy(self):
        context = make_context()
        timing = DispatcherTiming(lead_time_s=2.0)
        panel = StateMachinePanel(Dispatcher(
            pressure_decay.build_machine(), context, FakeEffector(), timing=timing,
        ))
        panel.dispatcher.tick(0.0)
        panel.dispatcher.tick(8.0)
        self.assertTrue(panel.select_procedure("tank_pressurization"))
        panel.dispatcher.tick(10.0)
        panel.dispatcher.tick(12.0)
        self.assertEqual(context.since_startup(), 12)
        self.assertEqual(context.in_procedure_for(), 2)
        self.assertEqual(context.in_state_for(), 2)
        self.assertIs(context.timing, timing)
        panel.dispatcher.arm()
        panel.dispatcher.disarm()
        panel.dispatcher.tick(13.0)
        self.assertEqual(context.in_procedure_for(), 3)

    def test_timing_guard_interrupts_suspension_before_next_command(self):
        d = self.make_dispatcher(safety_guard=lambda ctx: ctx.in_operation_for() > 6.0)
        d.tick(0.0)
        d.request(self.operation)
        d.tick(1.0)
        d.tick(6.0)  # Countdown completes; command is next.
        d.note_manual_command("valve_a")
        d.tick(7.0)
        self.assertEqual(d.mode, DispatcherMode.SUSPENDED)
        self.assertEqual(self.context.in_operation_for(), 6)
        d.tick(10.0)
        self.assertEqual(d.mode, DispatcherMode.RUNNING)
        d.tick(11.0)
        self.assertEqual(d.current.name, "B")
        self.assertEqual(self.effector.staged, [])
        self.assertEqual(self.context.in_operation_for(), 0)

    def test_abandoned_operation_clears_its_clock(self):
        d = self.make_dispatcher()
        d.interrupt_policy = InterruptPolicy.ABANDON
        d.tick(0.0)
        d.request(self.operation)
        d.tick(1.0)
        d.tick(2.0)
        d.note_manual_command("valve_a")
        self.assertEqual(self.context.in_operation_for(), 0)
        self.assertEqual(self.context.in_state_for(), 2)

    def test_table_settling_uses_dispatcher_context(self):
        ctx = make_context()
        timing = DispatcherTiming(lead_time_s=1.0, table_lead_time_s=3.0)
        move = Operation((ApplyTable("test", {"valve_a": True}),), "B")
        machine = Machine.build("table", [State("A", move, start=True),
                                            State("B", Operation((), "A"))])
        d = Dispatcher(machine, ctx, FakeEffector(), timing=timing)
        d.request(move)
        d.tick(0.0)
        d.tick(2.0)
        self.assertEqual(move.phase, OpPhase.SETTLING)
        d.tick(3.0)
        self.assertEqual(d.current.name, "B")

    def test_numeric_constructor_timing_is_rejected(self):
        for kwargs in ({"max_seconds": 10}, {"mismatch_grace_s": 1}, {"expired": lambda ctx: True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                State("A", **kwargs)
        for kwargs in ({"timeout_s": 10}, {"lead_time_s": 1}, {"expired": lambda ctx: True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                Operation((), "A", **kwargs)
        with self.assertRaises(TypeError):
            WaitUntil(lambda ctx: False, timeout_s=1)
        with self.assertRaises(TypeError):
            SetValve("a", True, confirm_timeout_s=1)
        with self.assertRaises(TypeError):
            manual_operation("go", "B", (), expired=lambda ctx: True)
        with self.assertRaises(TypeError):
            auto_operation("go", "B", (), guard=lambda ctx: True, expired=lambda ctx: True)

    def test_local_and_global_safety_guards_use_priority_and_tie_checks(self):
        d = self.make_dispatcher(safety_guard=lambda ctx: ctx.in_state_for() > 1)
        global_abort = Operation((), d.machine.states["B"], auto=True,
                                 guard=lambda ctx: ctx.in_state_for() > 1,
                                 priority=ABORT_PRIORITY)
        d.machine.global_transitions = (global_abort,)
        d.tick(0.0)
        d.tick(2.0)
        self.assertEqual(d.mode, DispatcherMode.AMBIGUOUS)
        self.assertEqual(len(d.tie_candidates), 2)
        self.assertEqual(self.effector.staged, [])
        global_abort.priority += 1
        d.tick(3.0)
        self.assertIs(d.active_op, global_abort)
        d.tick(4.0)
        self.assertEqual(d.current.name, "B")

    def test_both_procedure_fill_watchdogs_preserve_the_boundary(self):
        for procedure, state_name in (
            (pressure_decay, "PD_03_COPV_FILL"),
            (tank_pressurization, "TP_06_FILL_2"),
        ):
            state = procedure.build_machine().states[state_name]
            watchdog = next(op for op in state.operations if op.name == "Abort on timing limit")
            for elapsed, expired in ((900.0, False), (900.1, True)):
                with self.subTest(procedure=procedure.__name__, elapsed=elapsed):
                    ctx = SimpleNamespace(in_state_for=lambda: elapsed)
                    self.assertEqual(watchdog.guard(ctx), expired)

    def test_no_implicit_operation_timeout(self):
        d = self.make_dispatcher()
        self.operation.actions = (WaitUntil(lambda ctx: False),)
        d.request(self.operation)
        d.tick(0.0)
        d.tick(60.0)
        self.assertIs(d.active_op, self.operation)
        self.assertEqual(self.context.in_operation_for(), 60)

    def test_mvas_countdown_uses_operation_elapsed_time(self):
        machine = pressure_decay.build_machine()
        operation = next(
            op for state in machine.states.values() for op in state.operations
            if any(action.describe() == "countdown to MVAS" for action in op.actions)
        )
        wait = operation.actions[0]
        duration = pressure_decay.MVAS_COUNTDOWN_SECONDS
        for elapsed, done in ((duration - 0.1, False), (duration, True)):
            ctx = SimpleNamespace(in_operation_for=lambda: elapsed)
            self.assertEqual(wait.poll(ctx), done)
