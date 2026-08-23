"""Tests for the GSE control state machine engine.

Pure Python: fake servers, fake effector, no GUI and no Flight. These cover the
safety properties docs/state-machine.md actually asks for, not just the happy
path.

    PYTHONPATH=apps/GUI2.1 python -m unittest tests.test_state_machine -v
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "GUI2.1"))

from state_machine import (  # noqa: E402
    ABORT_PRIORITY,
    VERIFY_FEED_GRACE_SECONDS,
    Action,
    ControlContext,
    DONT_CARE,
    Dispatcher,
    DispatcherMode,
    InterruptPolicy,
    Machine,
    MismatchPolicy,
    OpPhase,
    Operation,
    SetValve,
    SlopeTracker,
    StartStateError,
    State,
    UnknownStateError,
    WaitUntil,
    safe_guard,
    table_signature,
)


# --- Fakes ------------------------------------------------------------------


class FakeServer:
    def __init__(self, latest=None, fresh=True):
        self.latest = latest
        self.latest_generation = 0
        self._fresh = fresh

    def is_fresh(self, _timeout):
        return self._fresh and self.latest is not None

    def push(self, latest):
        self.latest = latest
        self.latest_generation += 1


class FakeValveMap:
    def __init__(self):
        self.states: dict[str, bool] = {}

    def button_ids(self):
        return tuple(self.states)

    def commanded(self, button_id):
        return bool(self.states.get(button_id, False))

    def status_fields(self, button_id):
        return (f"internal_{button_id}",)

    def display_name(self, button_id):
        return button_id


class FakeEffector:
    def __init__(self):
        self.staged: list[tuple[str, bool]] = []
        self.tables: list[dict] = []
        self.flushes = 0
        self._dirty = False
        self.abort = False

    def stage_button(self, button_id, state):
        self.staged.append((button_id, bool(state)))
        self._dirty = True

    def stage_table(self, states):
        self.tables.append(dict(states))
        self._dirty = True

    def is_dirty(self):
        return self._dirty

    def flush(self):
        self.flushes += 1
        self._dirty = False
        return True

    def abort_active(self):
        return self.abort


def make_context(nidaq=None, *, healthy=True, scales=None, gse=None):
    gse_server = FakeServer(
        {"internal_valve_a": 0.0} if gse is None else dict(gse),
        fresh=healthy,
    )
    echo = FakeServer({"connected": 1.0 if healthy else 0.0}, fresh=healthy)
    nidaq_server = FakeServer(nidaq if nidaq is not None else {"COPV": 1.0}, fresh=healthy)
    return ControlContext(
        gse_server=gse_server,
        echo_server=echo,
        nidaq_server=nidaq_server,
        scales=scales if scales is not None else {"COPV": (100.0, 0.0)},
        valves=FakeValveMap(),
        window_seconds=10.0,
    )


def set_board(ctx, **positions):
    """What the fake board reports back about its valves."""
    latest = dict(ctx.gse_server.latest or {})
    for button_id, state in positions.items():
        latest[f"internal_{button_id}"] = 1.0 if state else 0.0
    ctx.gse_server.latest = latest


def run_ticks(dispatcher, count, *, start=1000.0, step=1.0):
    for i in range(count):
        dispatcher.tick(start + i * step)


class Counter(Action):
    """Action that records how many times it was begun."""

    def __init__(self):
        super().__init__()
        self.begun = 0
        self.name = "counter"

    def begin(self, ctx, effector):
        self.begun += 1


# --- Determinism ------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    def _tie_machine(self):
        a = State(
            "A",
            (
                Operation((), "B", True, name="left", guard=lambda ctx: True,
                          mutually_exclusive_with=("right",)),
                Operation((), "C", True, name="right", guard=lambda ctx: True,
                          mutually_exclusive_with=("left",)),
            ),
            None,
            None,
            max_seconds=1000.0,
        )
        b = State("B", (Operation((), "A", False, name="back"),))
        c = State("C", (Operation((), "A", False, name="back"),))
        return Machine.build("tie", [a, b, c], "A")

    def test_equal_priority_transitions_stop_and_ask(self):
        eff = FakeEffector()
        d = Dispatcher(self._tie_machine(), make_context(), eff)
        d.armed = True
        run_ticks(d, 3)

        self.assertIs(d.mode, DispatcherMode.AMBIGUOUS)
        self.assertEqual({op.name for op in d.tie_candidates}, {"left", "right"})
        self.assertEqual(d.current.name, "A", "must not move while ambiguous")
        self.assertEqual(eff.staged, [], "must stage nothing while ambiguous")

    def test_operator_resolves_the_tie(self):
        d = Dispatcher(self._tie_machine(), make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 3)
        chosen = next(op for op in d.tie_candidates if op.name == "right")

        d.choose(chosen)
        run_ticks(d, 3, start=2000.0)

        self.assertEqual(d.current.name, "C")

    def test_higher_priority_wins_without_asking(self):
        a = State(
            "A",
            (
                Operation((), "B", True, name="normal", guard=lambda ctx: True),
                Operation((), "SAFE", True, name="abort", guard=lambda ctx: True, priority=500),
            ),
            None,
            None,
            max_seconds=1000.0,
        )
        machine = Machine.build(
            "priority",
            [a, State("B", (Operation((), "A", False, name="back"),)),
             State("SAFE", (Operation((), "A", False, name="back"),))],
            "A",
        )
        d = Dispatcher(machine, make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 4)

        self.assertIs(d.mode, DispatcherMode.IDLE)
        self.assertEqual(d.current.name, "SAFE")

    def test_persistent_global_criterion_does_not_restart_its_own_operation(self):
        # An overpressure watch does not stop being true just because the abort
        # has started, so the global must not re-fire on top of itself.
        first, second = Counter(), Counter()
        a = State("A", (Operation((), "B", False, name="manual"),))
        machine = Machine.build(
            "persistent",
            [a, State("B", (Operation((), "A", False, name="back"),)),
             State("SAFE", (Operation((), "A", False, name="back"),))],
            "A",
        )
        machine.global_transitions = (
            Operation((first, second), "SAFE", True, name="watch",
                      guard=lambda ctx: True, priority=ABORT_PRIORITY),
        )
        machine.link()

        d = Dispatcher(machine, make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 6)

        self.assertEqual(d.current.name, "SAFE")
        self.assertEqual(first.begun, 1, "the abort must run once, not restart every cycle")

    def test_abort_priority_global_breaks_an_ambiguous_freeze(self):
        machine = self._tie_machine()
        machine.states["SAFE"] = State("SAFE", (Operation((), "A", False, name="back"),))
        fired = {"value": False}
        machine.global_transitions = (
            Operation((), "SAFE", True, name="global abort",
                      guard=lambda ctx: fired["value"], priority=ABORT_PRIORITY),
        )
        machine.link()

        d = Dispatcher(machine, make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 3)
        self.assertIs(d.mode, DispatcherMode.AMBIGUOUS)

        fired["value"] = True
        run_ticks(d, 4, start=2000.0)

        self.assertEqual(d.current.name, "SAFE")


# --- Guard safety -----------------------------------------------------------


class TestGuards(unittest.TestCase):
    def test_nan_reading_does_not_satisfy_a_threshold(self):
        ctx = make_context(nidaq={})           # COPV absent -> NaN
        ctx.begin_cycle(1000.0)
        self.assertTrue(math.isnan(ctx.psi("COPV")))
        self.assertFalse(ctx.psi("COPV") <= 20.0)
        self.assertFalse(ctx.psi("COPV") >= 350.0)

    def test_missing_sensor_reads_nan_not_zero(self):
        ctx = make_context(nidaq={"COPV": 2.5})
        ctx.begin_cycle(1000.0)
        self.assertAlmostEqual(ctx.psi("COPV"), 250.0)

        ctx.nidaq_server._fresh = False
        ctx.begin_cycle(1001.0)
        self.assertTrue(math.isnan(ctx.psi("COPV")), "stale data must not read as 0 psi")

    def test_raising_guard_is_treated_as_do_not_transition(self):
        def boom(ctx):
            raise RuntimeError("sensor exploded")

        ctx = make_context()
        ctx.begin_cycle(1000.0)
        self.assertFalse(safe_guard(boom, ctx, "boom"))

    def test_only_the_current_states_criteria_are_evaluated(self):
        calls = {"a": 0, "b": 0}

        def guard_a(ctx):
            calls["a"] += 1
            return False

        def guard_b(ctx):
            calls["b"] += 1
            return False

        a = State("A", (Operation((), "B", True, name="a->b", guard=guard_a),), None, None,
                  max_seconds=1000.0)
        b = State("B", (Operation((), "A", True, name="b->a", guard=guard_b),), None, None,
                  max_seconds=1000.0)
        d = Dispatcher(Machine.build("scoped", [a, b], "A"), make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 5)

        self.assertGreater(calls["a"], 0)
        self.assertEqual(calls["b"], 0, "B's math must not run while we are in A")


# --- Cycle discipline -------------------------------------------------------


class TestCycle(unittest.TestCase):
    def test_arrival_cycle_does_not_also_exit(self):
        b_guard_calls = {"count": 0}

        def b_guard(ctx):
            b_guard_calls["count"] += 1
            return True

        a = State(
            "A",
            (Operation((Counter(), Counter()), "B", True, name="a->b", guard=lambda ctx: True),),
            None, None, max_seconds=1000.0,
        )
        b = State("B", (Operation((), "C", True, name="b->c", guard=b_guard),),
                  None, None, max_seconds=1000.0)
        c = State("C", (Operation((), "A", False, name="restart"),))
        d = Dispatcher(Machine.build("chain", [a, b, c], "A"), make_context(), FakeEffector())
        d.armed = True

        d.tick(1000.0)                     # start a->b, first action
        self.assertIs(d.mode, DispatcherMode.RUNNING)
        self.assertEqual(d.current.name, "A")

        d.tick(1001.0)                     # second action done, lead time starts
        self.assertEqual(d.current.name, "A")
        self.assertIs(d.active_op.phase, OpPhase.SETTLING)

        d.tick(1002.0)                     # lead time up, enter B
        self.assertEqual(d.current.name, "B")
        self.assertIsNone(d.active_op)
        self.assertEqual(b_guard_calls["count"], 0,
                         "B's criteria must not be evaluated on the cycle that entered B")

        d.tick(1003.0)                     # now B gets evaluated, b->c starts
        self.assertGreater(b_guard_calls["count"], 0)
        d.tick(1004.0)                     # lead time up, enter C
        self.assertEqual(d.current.name, "C")

    def test_one_flush_per_cycle(self):
        eff = FakeEffector()
        a = State(
            "A",
            (Operation((SetValve("v1", True), SetValve("v2", True)), "B", True,
                       name="two valves", guard=lambda ctx: True),),
            None, None, max_seconds=1000.0,
        )
        machine = Machine.build("flush", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        d = Dispatcher(machine, make_context(), eff)
        d.armed = True
        run_ticks(d, 5)

        self.assertEqual(eff.flushes, 2, "one flush per cycle that staged something")
        self.assertEqual(eff.staged, [("v1", True), ("v2", True)])

    def test_control_cycle_is_rate_limited(self):
        counter = Counter()
        a = State("A", (Operation((counter,), "B", True, name="go", guard=lambda ctx: True),),
                  None, None, max_seconds=1000.0)
        machine = Machine.build("rate", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        d = Dispatcher(machine, make_context(), FakeEffector(), period_s=1.0)

        d.armed = True
        for i in range(10):
            d.tick(1000.0 + i * 0.05)      # 0.5s of frames at 20fps

        self.assertEqual(counter.begun, 1, "a fast frame rate must not speed up control")

    def test_state_watchdog_panics_rather_than_hanging(self):
        panic = Operation((SetValve("vent", True),), "SAFE", False, name="panic")
        a = State("A", (Operation((), "B", True, name="never", guard=lambda ctx: False),),
                  None, panic, max_seconds=5.0)
        machine = Machine.build(
            "watchdog",
            [a, State("B", (Operation((), "A", False, name="back"),)),
             State("SAFE", (Operation((), "A", False, name="back"),))],
            "A",
        )
        eff = FakeEffector()
        d = Dispatcher(machine, make_context(), eff)
        d.armed = True

        d.tick(1000.0)
        d.tick(1010.0)                     # past max_seconds
        d.tick(1011.0)

        self.assertEqual(d.current.name, "SAFE")
        self.assertIn(("vent", True), eff.staged)
        self.assertFalse(d.armed, "a panic must disarm automation")


# --- Manual control ---------------------------------------------------------


class TestManualControl(unittest.TestCase):
    def _slow_machine(self):
        never = WaitUntil(lambda ctx: False, label="never")
        a = State(
            "A",
            (Operation((SetValve("v1", True), never), "B", True, name="slow",
                       guard=lambda ctx: True, timeout_s=None),),
            None, None, max_seconds=1000.0,
        )
        machine = Machine.build("slow", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        return machine

    def test_manual_click_suspends_and_resume_keeps_progress(self):
        d = Dispatcher(self._slow_machine(), make_context(), FakeEffector())
        d.armed = True
        run_ticks(d, 3)
        self.assertIs(d.mode, DispatcherMode.RUNNING)
        index_before = d.active_op._index

        d.note_manual_command("v9")
        self.assertIs(d.mode, DispatcherMode.SUSPENDED)

        run_ticks(d, 3, start=2000.0)
        self.assertIs(d.mode, DispatcherMode.SUSPENDED, "must stay frozen until resumed")
        self.assertEqual(d.active_op._index, index_before, "progress must be preserved")

        d.resume()
        d.tick(3000.0)
        self.assertIs(d.mode, DispatcherMode.RUNNING)

    def test_abandon_policy_discards_the_operation(self):
        d = Dispatcher(
            self._slow_machine(),
            make_context(),
            FakeEffector(),
            interrupt_policy=InterruptPolicy.ABANDON,
        )
        d.armed = True
        run_ticks(d, 3)

        d.note_manual_command("v9")

        self.assertIsNone(d.active_op)
        self.assertIs(d.mode, DispatcherMode.HALTED)
        self.assertFalse(d.armed)

    def test_effector_refuses_to_stage_while_abort_is_latched(self):
        eff = FakeEffector()
        eff.abort = True
        a = State("A", (Operation((SetValve("v1", True),), "B", True, name="go",
                                  guard=lambda ctx: True),), None, None, max_seconds=1000.0)
        machine = Machine.build("abort", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        d = Dispatcher(machine, make_context(), eff)
        d.armed = True
        run_ticks(d, 4)

        self.assertEqual(eff.staged, [], "abort must not be decorative w.r.t. automation")
        self.assertEqual(d.current.name, "A")

    def test_unarmed_machine_runs_no_automatic_transitions(self):
        d = Dispatcher(self._slow_machine(), make_context(), FakeEffector())
        run_ticks(d, 5)
        self.assertEqual(d.current.name, "A")
        self.assertIsNone(d.active_op)


# --- Linking and validation -------------------------------------------------


class TestMachineAudit(unittest.TestCase):
    def test_unknown_destination_raises_at_link_time(self):
        a = State("A", (Operation((), "TYPO", False, name="go"),))
        with self.assertRaises(UnknownStateError):
            Machine.build("bad", [a], "A")

    def test_validate_flags_equal_priority_automatic_exits(self):
        a = State(
            "A",
            (
                Operation((), "B", True, name="left", guard=lambda ctx: True),
                Operation((), "B", True, name="right", guard=lambda ctx: True),
            ),
            None, None, max_seconds=10.0,
        )
        machine = Machine.build("dup", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        problems = machine.validate()
        self.assertTrue(any("share priority" in p for p in problems), problems)

    def test_declared_mutual_exclusion_silences_the_tie_warning(self):
        a = State(
            "A",
            (
                Operation((), "B", True, name="left", guard=lambda ctx: True,
                          mutually_exclusive_with=("right",)),
                Operation((), "B", True, name="right", guard=lambda ctx: True,
                          mutually_exclusive_with=("left",)),
            ),
            None, None, max_seconds=10.0,
        )
        machine = Machine.build("declared", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        self.assertEqual([p for p in machine.validate() if "share priority" in p], [])

    def test_validate_requires_a_watchdog_on_automatic_states(self):
        a = State("A", (Operation((), "B", True, name="go", guard=lambda ctx: True),))
        machine = Machine.build("nowatchdog", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        self.assertTrue(any("max_seconds" in p for p in machine.validate()))

    def test_validate_flags_unreachable_states(self):
        a = State("A", (Operation((), "B", False, name="go"),))
        b = State("B", (Operation((), "A", False, name="back"),))
        orphan = State("ORPHAN", (Operation((), "A", False, name="back"),))
        machine = Machine.build("orphan", [a, b, orphan], "A")
        self.assertTrue(any("unreachable" in p for p in machine.validate()))

    def test_validate_flags_an_operation_that_misses_its_destination_table(self):
        # Actions open valve_a, the destination expects it closed. Catch that
        # at build time rather than at 350 psig.
        a = State(
            "A",
            (Operation((SetValve("valve_a", True),), "B", False, name="open a"),),
            {"valve_a": False},
        )
        b = State("B", (Operation((), "A", False, name="back"),), {"valve_a": False})
        machine = Machine.build("tables", [a, b], "A")

        problems = machine.validate()

        self.assertTrue(any("but B expects False" in p for p in problems), problems)

    def test_validate_accepts_an_operation_that_reaches_its_destination_table(self):
        a = State(
            "A",
            (Operation((SetValve("valve_a", True),), "B", False, name="open a"),),
            {"valve_a": False},
        )
        b = State(
            "B",
            (Operation((SetValve("valve_a", False),), "A", False, name="back"),),
            {"valve_a": True},
        )
        machine = Machine.build("tables", [a, b], "A")

        self.assertEqual([p for p in machine.validate() if "expects" in p], [])

    def test_validate_flags_a_safe_out_that_re_enters_its_own_state(self):
        # PD_ABORTED shaped: a state that aborts on mismatch, whose safe-out
        # lands back in itself. That is an infinite panic, not a safe state.
        loop = Operation((), "ABORTED", False, name="safe-out")
        aborted = State(
            "ABORTED",
            (Operation((), "A", False, name="ack"),),
            {"valve_a": True},
            loop,
        )
        a = State("A", (Operation((), "ABORTED", False, name="go"),))
        machine = Machine.build("loop", [a, aborted], "A")

        problems = machine.validate()

        self.assertTrue(any("re-enters ABORTED" in p for p in problems), problems)

    def test_panic_into_the_current_state_halts_instead_of_looping(self):
        loop = Operation((), "ABORTED", False, name="safe-out")
        aborted = State(
            "ABORTED",
            (Operation((), "A", False, name="ack"),),
            {"valve_a": True},
            loop,
            on_mismatch=MismatchPolicy.WARN,
        )
        a = State("A", (Operation((), "ABORTED", False, name="go"),))
        machine = Machine.build("loop", [a, aborted], "A")
        ctx = make_context()
        set_board(ctx, valve_a=False)
        d = Dispatcher(machine, ctx, FakeEffector())
        d.force_state("ABORTED")
        d.tick(1000.0)

        d.panic()
        run_ticks(d, 5, start=1001.0)

        self.assertIs(d.mode, DispatcherMode.HALTED)
        self.assertEqual(d.current.name, "ABORTED")
        self.assertLessEqual(
            sum("PANIC" in line for line in d.log), 2,
            "must halt after one panic, not repeat it every cycle",
        )

    def test_validate_flags_a_timeout_below_the_lead_time(self):
        a = State(
            "A",
            (Operation((), "B", False, name="go", lead_time_s=2.0, timeout_s=1.0),),
        )
        machine = Machine.build(
            "timeout", [a, State("B", (Operation((), "A", False, name="back"),))], "A"
        )
        self.assertTrue(any("lead time" in p for p in machine.validate()))

    def test_dispatcher_refuses_to_arm_with_outstanding_problems(self):
        a = State("A", (Operation((), "B", True, name="go", guard=lambda ctx: True),))
        machine = Machine.build("unsafe", [a, State("B", (Operation((), "A", False, name="back"),))], "A")
        d = Dispatcher(machine, make_context(), FakeEffector())

        d.arm()

        self.assertFalse(d.armed)


# --- Expected valve configuration -------------------------------------------


def expect_machine(expected, *, on_mismatch=MismatchPolicy.ABORT):
    """A machine whose start state asserts *expected* and can bail to SAFE."""
    safe_out = Operation((SetValve("vent", True),), "SAFE", False, name="panic")
    start = State(
        "A",
        (Operation((), "B", False, name="go"),),
        expected,
        safe_out,
        on_mismatch=on_mismatch,
    )
    return Machine.build(
        "expect",
        [
            start,
            State("B", (Operation((), "A", False, name="back"),)),
            State("SAFE", (Operation((), "A", False, name="back"),)),
        ],
        "A",
    )


class TestExpectedState(unittest.TestCase):
    def test_mismatch_aborts_by_default(self):
        ctx = make_context()
        set_board(ctx, valve_a=False)
        d = Dispatcher(expect_machine({"valve_a": True}), ctx, FakeEffector())
        d.armed = True

        run_ticks(d, 4)

        self.assertEqual(d.current.name, "SAFE", "a valve mismatch must abort")
        self.assertFalse(d.armed)

    def test_mismatch_does_not_abort_while_disarmed(self):
        # Disarmed means the operator is hand-flying. Slamming the abort table
        # onto someone deliberately moving a valve breaks "never take manual
        # control away".
        ctx = make_context()
        set_board(ctx, valve_a=False)
        d = Dispatcher(expect_machine({"valve_a": True}), ctx, FakeEffector())

        run_ticks(d, 4)

        self.assertEqual(d.current.name, "A", "a disarmed machine must not abort")
        self.assertEqual([tuple(i) for i in d.warnings], [("valve_a", True, False)],
                         "but it must still show the mismatch")

    def test_warn_policy_keeps_the_state(self):
        ctx = make_context()
        set_board(ctx, valve_a=False)
        machine = expect_machine({"valve_a": True}, on_mismatch=MismatchPolicy.WARN)
        d = Dispatcher(machine, ctx, FakeEffector())

        run_ticks(d, 4)

        self.assertEqual([tuple(item) for item in d.warnings], [("valve_a", True, False)])
        self.assertEqual(d.current.name, "A", "WARN reports the mismatch and holds")

    def test_mismatch_check_waits_for_the_grace_time(self):
        # Valves take time to move, so checking the instant we arrive would
        # abort on entry to every state.
        ctx = make_context()
        set_board(ctx, valve_a=False)
        d = Dispatcher(expect_machine({"valve_a": True}), ctx, FakeEffector())

        d.tick(1000.0)

        self.assertEqual(d.warnings, [])
        self.assertEqual(d.current.name, "A")

    def test_dont_care_valve_is_never_checked(self):
        ctx = make_context()
        set_board(ctx, valve_a=False, valve_b=True)
        machine = expect_machine({"valve_a": DONT_CARE, "valve_b": True})
        d = Dispatcher(machine, ctx, FakeEffector())

        run_ticks(d, 4)

        self.assertEqual(d.warnings, [])
        self.assertEqual(d.current.name, "A", "an unchecked valve must not abort")

    def test_dont_care_has_no_boolean_value(self):
        # bool(DONT_CARE) would silently mean "expect open", so it must not be
        # possible to write it by accident.
        with self.assertRaises(TypeError):
            bool(DONT_CARE)

    def test_dont_care_does_not_change_the_hash(self):
        pinned = table_signature({"valve_a": True})
        with_spare = table_signature({"valve_a": True, "valve_b": DONT_CARE})
        self.assertEqual(pinned, with_spare)

    def test_a_different_table_gives_a_different_hash(self):
        self.assertNotEqual(
            table_signature({"valve_a": True}),
            table_signature({"valve_a": False}),
        )


# --- Operation feedback and the destination table check ---------------------


def move_machine(dest_expected, *, lead_time_s=1.0, verify_dest=True):
    """One operation that opens valve_a on the way into B."""
    move = Operation(
        (SetValve("valve_a", True),),
        "B",
        False,
        name="open valve_a",
        lead_time_s=lead_time_s,
        verify_dest=verify_dest,
        timeout_s=None,
    )
    return Machine.build(
        "move",
        [
            State("A", (move,), None, Operation((), "SAFE", False, name="panic")),
            State(
                "B",
                (Operation((), "A", False, name="back"),),
                dest_expected,
                on_mismatch=MismatchPolicy.WARN,
            ),
            State("SAFE", (Operation((), "A", False, name="back"),)),
        ],
        "A",
    )


class TestOperationFeedback(unittest.TestCase):
    def _request_move(self, machine, ctx):
        d = Dispatcher(machine, ctx, FakeEffector())
        d.tick(1000.0)
        d.request(machine.states["A"].operations[0])
        d.tick(1001.0)
        return d

    def test_operation_waits_the_lead_time_before_it_transitions(self):
        ctx = make_context()
        d = self._request_move(move_machine(None, lead_time_s=5.0), ctx)

        self.assertIs(d.active_op.phase, OpPhase.SETTLING)
        self.assertEqual(d.current.name, "A")

        d.tick(1010.0)
        self.assertEqual(d.current.name, "B")

    def test_board_agreement_confirms_the_move(self):
        ctx = make_context()
        d = self._request_move(move_machine({"valve_a": True}), ctx)
        set_board(ctx, valve_a=True)          # board confirms the new position

        d.tick(1003.0)

        self.assertEqual(d.current.name, "B")
        self.assertTrue(d.last_feedback.verified)
        self.assertEqual(d.last_feedback.expected_hash, d.last_feedback.observed_hash)

    def test_board_disagreement_fails_the_operation(self):
        ctx = make_context()
        d = self._request_move(move_machine({"valve_a": True}), ctx)
        set_board(ctx, valve_a=False)         # the valve did not move

        run_ticks(d, 4, start=1003.0)         # check fails, panic runs

        self.assertEqual(d.current.name, "SAFE", "a failed table check must panic")
        failure = d.last_failure
        self.assertIs(failure.phase, OpPhase.FAILED)
        self.assertFalse(failure.verified)
        self.assertEqual(
            [tuple(item) for item in failure.mismatches],
            [("valve_a", True, False)],
        )
        self.assertNotEqual(failure.expected_hash, failure.observed_hash)

    def test_a_stale_feed_holds_instead_of_failing(self):
        # A telemetry blip means we cannot see the valves, not that they moved.
        # Holding rides it out; failing would panic a perfectly good procedure.
        ctx = make_context()
        d = self._request_move(move_machine({"valve_a": True}), ctx)
        ctx.gse_server._fresh = False

        run_ticks(d, 3, start=1003.0)

        self.assertEqual(d.current.name, "A", "must hold, not panic")
        self.assertIs(d.active_op.phase, OpPhase.VERIFYING)

        ctx.gse_server._fresh = True                  # feed comes back
        set_board(ctx, valve_a=True)
        d.tick(1007.0)

        self.assertEqual(d.current.name, "B", "and then carry on")

    def test_a_feed_that_never_returns_still_fails(self):
        ctx = make_context()
        d = self._request_move(move_machine({"valve_a": True}), ctx)
        ctx.gse_server._fresh = False

        run_ticks(d, 12, start=1003.0)                # past the feed grace

        self.assertEqual(d.current.name, "SAFE")
        self.assertEqual(d.last_failure.unreadable, ("valve_a",))

    def test_missing_board_feedback_fails_the_operation(self):
        # Silence is not agreement. The feed here is healthy and the status
        # field is simply absent, which is a wiring fault, not a blip — so it
        # fails immediately rather than waiting out the feed grace.
        ctx = make_context(gse={})
        d = self._request_move(move_machine({"valve_a": True}), ctx)

        run_ticks(d, 4, start=1003.0)

        self.assertEqual(d.current.name, "SAFE")
        self.assertEqual(d.last_failure.unreadable, ("valve_a",))

    def test_verify_dest_false_skips_the_check(self):
        ctx = make_context()
        d = self._request_move(move_machine({"valve_a": True}, verify_dest=False), ctx)
        set_board(ctx, valve_a=False)

        d.tick(1003.0)

        self.assertEqual(d.current.name, "B")
        self.assertIsNone(d.last_feedback.verified)

    def test_feedback_reports_each_phase(self):
        ctx = make_context()
        machine = move_machine({"valve_a": True}, lead_time_s=5.0)
        d = Dispatcher(machine, ctx, FakeEffector())
        d.tick(1000.0)
        d.request(machine.states["A"].operations[0])

        d.tick(1001.0)
        self.assertIs(d.active_op.feedback.phase, OpPhase.SETTLING)
        self.assertIn("settling", d.status_line())

        set_board(ctx, valve_a=True)
        d.tick(1010.0)
        self.assertIs(d.last_feedback.phase, OpPhase.DONE)
        self.assertIn("confirmed", d.feedback_line())


# --- Start state ------------------------------------------------------------


class TestStartState(unittest.TestCase):
    def _states(self, **flags):
        return [
            State("A", (Operation((), "B", False, name="go"),), start=flags.get("a", False)),
            State("B", (Operation((), "A", False, name="back"),), start=flags.get("b", False)),
        ]

    def test_declared_start_state_needs_no_initial_argument(self):
        machine = Machine.build("start", self._states(a=True))
        self.assertEqual(machine.initial, "A")

    def test_two_start_states_are_rejected(self):
        with self.assertRaises(StartStateError):
            Machine.build("start", self._states(a=True, b=True))

    def test_no_start_state_is_rejected(self):
        with self.assertRaises(StartStateError):
            Machine.build("start", self._states())

    def test_initial_argument_must_match_the_flag(self):
        with self.assertRaises(StartStateError):
            Machine.build("start", self._states(a=True), "B")

    def test_naming_the_start_state_marks_it(self):
        machine = Machine.build("start", self._states(), "B")
        self.assertEqual(machine.initial, "B")
        self.assertTrue(machine.states["B"].start)
        self.assertEqual(machine.validate(), [])


# --- Slope tracking ---------------------------------------------------------


class TestSlopeTracker(unittest.TestCase):
    def test_slope_is_nan_until_a_full_window_is_available(self):
        tracker = SlopeTracker(("COPV",), window_seconds=10.0)
        for i in range(5):
            tracker.sample({"COPV": 100.0}, i, 1000.0 + i)
        self.assertFalse(tracker.is_ready())
        self.assertTrue(math.isnan(tracker.slope_per_min("COPV")))

    def test_known_decay_rate_is_recovered(self):
        tracker = SlopeTracker(("COPV",), window_seconds=10.0)
        # 3 psi/min falling, sampled at 1 Hz for 12 s
        for i in range(13):
            tracker.sample({"COPV": 200.0 - 3.0 * (i / 60.0)}, i, 1000.0 + i)

        self.assertTrue(tracker.is_ready())
        self.assertAlmostEqual(tracker.slope_per_min("COPV"), -3.0, places=6)

    def test_reset_discards_the_previous_transient(self):
        tracker = SlopeTracker(("COPV",), window_seconds=10.0)
        for i in range(13):
            tracker.sample({"COPV": 10.0 * i}, i, 1000.0 + i)
        self.assertTrue(tracker.is_ready())

        tracker.reset()

        self.assertFalse(tracker.is_ready())
        self.assertTrue(math.isnan(tracker.slope_per_min("COPV")))


if __name__ == "__main__":
    unittest.main()
