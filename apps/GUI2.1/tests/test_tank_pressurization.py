"""Tank procedure and GUI selection tests; no hardware or GUI window."""
from pathlib import Path
import sys
import math
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "GUI2.1"))

from tests.test_state_machine import FakeEffector, make_context, set_board
from gui_state_machine import StateMachinePanel
from gui_elements import Button
from state_machine.operations import attach_manual_listener
from state_machine.procedures import pressure_decay, tank_pressurization
from state_machine.state_machine import Dispatcher, DispatcherMode, MachineValidationError


class TestTankPressurization(unittest.TestCase):
    def setUp(self):
        self.machine = tank_pressurization.build_machine()
        self.context = make_context(
            nidaq={"COPV": 500.0, "LOXTANK": 25.0, "LNGTANK": 25.0, "VENT": 300.0},
            scales={name: (1.0, 0.0) for name in ("COPV", "LOXTANK", "LNGTANK", "VENT")},
        )
        set_board(self.context, **tank_pressurization.ALL_OFF)

    def test_complete_sequence_with_board_echo(self):
        context = self.context

        class EchoEffector(FakeEffector):
            def stage_button(self, button_id, state):
                super().stage_button(button_id, state)
                set_board(context, **{button_id: state})

            def stage_table(self, states):
                super().stage_table(states)
                set_board(context, **states)

        effector = EchoEffector()
        dispatcher = Dispatcher(self.machine, context, effector)
        dispatcher.arm()
        for tick in range(250):
            name = dispatcher.current.name
            if name == "TP_06_FILL_2":
                context.nidaq_server.latest["COPV"] = 2000.0
            if name == "TP_11_FILL_4":
                context.nidaq_server.latest.update(COPV=4000.0, LOXTANK=450.0, LNGTANK=450.0)
            if name == "TP_14_COMPLETE":
                break
            if dispatcher.active_op is None and dispatcher.current.manual_operations():
                dispatcher.request(dispatcher.current.manual_operations()[0])
            dispatcher.tick(1000.0 + tick * 0.25)
        self.assertEqual(dispatcher.current.name, "TP_14_COMPLETE")
        self.assertIsNone(dispatcher.last_failure)
        self.assertEqual(effector.staged, [
            ("sol_gn2_fill_1", True), ("tank_vent", True),
            ("sol_gn2_fill_2", True), ("pv2", True), ("pv1", True),
            ("sol_gn2_fill_3", True), ("sol_gn2_fill_4", True),
            ("sol_gn2_fill_1", False), ("sol_gn2_fill_2", False),
            ("sol_gn2_fill_3", False), ("sol_gn2_fill_4", False),
            ("sol_gn2_vent", True),
        ])
        self.assertEqual(dispatcher.current.expected_state, tank_pressurization.FINAL)

    def test_pressure_guards_and_nan(self):
        for state, threshold in (("TP_06_FILL_2", 2000.0), ("TP_11_FILL_4", 4000.0)):
            for pressure, expected in ((math.nan, False), (threshold - 1, False), (threshold, True)):
                with self.subTest(state=state, pressure=pressure):
                    self.context.nidaq_server.latest["COPV"] = pressure
                    self.context.begin_cycle(1.0)
                    self.assertEqual(bool(self.machine.states[state].evaluate(self.context)), expected)

    def test_pv2_close_waits_if_pressure_drops_after_minimum_gate(self):
        operation = self.machine.states["TP_07_STABILIZE_2"].operations[0]
        effector = FakeEffector()
        self.context.begin_cycle(1.0)
        operation.start(self.context)
        operation.step(self.context, effector)
        self.assertEqual(effector.staged, [])
        self.context.nidaq_server.latest["COPV"] = 2000.0
        self.context.begin_cycle(2.0)
        operation.step(self.context, effector)
        operation.step(self.context, effector)
        self.assertEqual(effector.staged, [("pv2", True)])

    def test_either_tank_at_650_aborts_during_an_operation(self):
        for field in ("LOXTANK", "LNGTANK"):
            with self.subTest(field=field):
                self.setUp()
                effector = FakeEffector()
                dispatcher = Dispatcher(self.machine, self.context, effector)
                dispatcher.request(dispatcher.current.operations[0])
                dispatcher.tick(1000.0)
                self.assertIsNotNone(dispatcher.active_op)
                self.context.nidaq_server.latest[field] = 650.0
                for tick in range(1, 10):
                    dispatcher.tick(1000.0 + tick)
                self.assertEqual(dispatcher.current.name, "TP_ABORTED")
                self.assertIn(("alarm", True), effector.staged)
                self.assertEqual(len(effector.tables), 1)


class TestProcedureSelection(unittest.TestCase):
    def setUp(self):
        self.effector = FakeEffector()
        self.panel = StateMachinePanel(
            Dispatcher(pressure_decay.build_machine(), make_context(), self.effector),
            decay_sections=pressure_decay.DECAY_SECTIONS,
        )

    def test_switch_is_fresh_disarmed_and_does_not_move_valves(self):
        old = self.panel.dispatcher
        self.assertTrue(self.panel.select_procedure("tank_pressurization"))
        self.assertIsNot(self.panel.dispatcher, old)
        self.assertEqual(self.panel.dispatcher.current.name, "TP_00_CLEAR_STAND")
        self.assertFalse(self.panel.dispatcher.armed)
        self.assertEqual(self.panel.decay_sections, ())
        self.assertEqual(self.effector.staged, [])
        self.assertEqual(self.effector.tables, [])
        self.assertTrue(self.panel.select_procedure("pressure_decay"))
        self.assertEqual(self.panel.decay_sections, pressure_decay.DECAY_SECTIONS)

    def test_armed_pending_running_and_confirmation_block_switch(self):
        dispatcher = self.panel.dispatcher
        dispatcher.arm()
        self.assertFalse(self.panel.select_procedure("tank_pressurization"))
        dispatcher.disarm()
        dispatcher.request(dispatcher.current.operations[0])
        self.assertFalse(self.panel.select_procedure("tank_pressurization"))
        dispatcher.pending_manual = None
        for mode in (DispatcherMode.RUNNING, DispatcherMode.SUSPENDED, DispatcherMode.AMBIGUOUS):
            dispatcher.mode = mode
            self.assertFalse(self.panel.select_procedure("tank_pressurization"))
        dispatcher.mode = DispatcherMode.IDLE
        dispatcher.panic()
        self.assertFalse(self.panel.select_procedure("tank_pressurization"))
        dispatcher._panic_requested = False
        self.panel.captcha.require("pending", lambda: None)
        self.assertFalse(self.panel.select_procedure("tank_pressurization"))
        self.assertIs(self.panel.dispatcher, dispatcher)

    def test_failed_build_keeps_current_procedure(self):
        old = self.panel.dispatcher
        build = Mock(side_effect=MachineValidationError("broken", ["invalid procedure"]))
        with patch.dict("gui_state_machine.PROCEDURES", {"broken": ("Broken", build, ())}):
            self.assertFalse(self.panel.select_procedure("broken"))
        self.assertIs(self.panel.dispatcher, old)
        self.assertIn("invalid procedure", self.panel.selection_error)

    def test_selector_changes_procedure_from_gui(self):
        imgui = Mock()
        imgui.combo.return_value = (True, 1)
        self.panel._header(imgui)
        self.assertEqual(self.panel.dispatcher.machine.name, "tank_pressurization")
        imgui.combo.assert_called_once_with("Procedure", 0, ["Pressure decay", "Tank pressurization"])

    def test_raw_valve_listener_follows_selection(self):
        button = Button(button_id="pv1", text="Valve")
        original = self.panel.dispatcher
        original.note_manual_command = Mock()
        with patch.dict("state_machine.operations.GSE2V1_COMMAND_BUTTONS",
                        {"pv1": {"button": button}}, clear=True):
            attach_manual_listener(lambda valve: self.panel.dispatcher.note_manual_command(valve))
        self.panel.select_procedure("tank_pressurization")
        self.panel.dispatcher.note_manual_command = Mock()
        button.on_click(button)
        self.panel.dispatcher.note_manual_command.assert_called_once_with("pv1")
        original.note_manual_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
