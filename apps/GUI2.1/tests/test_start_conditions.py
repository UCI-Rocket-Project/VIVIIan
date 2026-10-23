"""Starting configuration display, using board snapshots without a GUI window."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "GUI2.1"))

from tests.test_state_machine import FakeEffector, make_context, set_board
from gui_state_machine import StateMachinePanel
from state_machine.procedures import pressure_decay
from state_machine.state_machine import Dispatcher


class TestStartConditions(unittest.TestCase):
    def setUp(self):
        self.context = make_context()
        self.effector = FakeEffector()
        self.panel = StateMachinePanel(Dispatcher(
            pressure_decay.build_machine(), self.context, self.effector,
        ))
        set_board(self.context, **self.panel.dispatcher.current.expected_state)
        self.context.begin_cycle(1000.0)

    def render_text(self):
        imgui = Mock()
        self.panel._start_conditions(imgui)
        self.assertEqual(self.effector.staged, [])
        self.assertEqual(self.effector.tables, [])
        return "\n".join(
            call.args[1] for call in imgui.text_colored.call_args_list
        )

    def test_physical_positions_and_mismatch(self):
        set_board(self.context, pv1=True)
        self.context.begin_cycle(1001.0)
        text = self.render_text()
        self.assertIn("PV 2: expected OPEN | board OPEN | MATCH", text)
        self.assertIn("LOX VV & LNG VV: expected OPEN | board OPEN | MATCH", text)
        self.assertIn("GN2 Fill 1: expected CLOSED | board CLOSED | MATCH", text)
        self.assertIn("PV 1: expected CLOSED | board OPEN | MISMATCH", text)

    def test_stale_and_missing_feedback_are_unknown(self):
        self.context.gse_server.latest.pop("internal_pv1")
        self.context.begin_cycle(1001.0)
        self.assertIn("PV 1: expected CLOSED | board UNKNOWN", self.render_text())
        self.context.gse_server._fresh = False
        self.context.begin_cycle(1002.0)
        text = self.render_text()
        self.assertEqual(text.count("board UNKNOWN"), 9)
        self.assertNotIn("| MATCH", text)

    def test_selection_updates_unchecked_valves(self):
        self.panel.select_procedure("tank_pressurization")
        text = self.render_text()
        self.assertIn("LOX VV & LNG VV: not checked", text)
        self.assertNotIn("LOX VV & LNG VV: expected", text)

    def test_advanced_procedure_keeps_start_reference_without_mismatches(self):
        dispatcher = self.panel.dispatcher
        dispatcher.current = dispatcher.machine.states["PD_03_COPV_FILL"]
        set_board(self.context, sol_gn2_fill_1=True)
        self.context.begin_cycle(1001.0)
        text = self.render_text()
        self.assertIn("Starting configuration for reference", text)
        self.assertIn("GN2 Fill 1: expected CLOSED", text)
        self.assertNotIn("| board", text)
