"""Headless render tests for the GUI3.0 ops view.

ImGui renders fine without a GPU, so every panel can be exercised for real:
these build the actual pressure-decay machine, drive it into the states an
operator would care about, and paint a full frame each time.

That matters more here than it would for an ordinary widget layout. GUI3.0
draws itself with absolute rectangles rather than stacked ImGui widgets, so
there is no library between a bad number and a crash -- a NaN pressure, a
missing valve echo or a zero-width panel reaches the draw call directly.

    python -m unittest tests.test_gui3_ops_view -v
"""
from __future__ import annotations

import math
import time
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _entry in (os.path.join(ROOT, "apps", "GUI3.0"), os.path.join(ROOT, "apps", "GUI2.1")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from imgui_bundle import imgui  # noqa: E402

import events as E  # noqa: E402
import model as M  # noqa: E402
import theme as T  # noqa: E402
from draw import Hold, Painter, Rect, fmt  # noqa: E402
from history import History  # noqa: E402
from main import build_layout  # noqa: E402
from panels import (  # noqa: E402
    commands, pid, sequence, strips, telemetry, topbar,
)

from captcha import Captcha67Gate  # noqa: E402
from state_machine import ControlContext, Dispatcher, DispatcherMode  # noqa: E402
from gui_gse2v1 import GseCommandClient, make_gse2v1_command_buttons  # noqa: E402
from procedures.operations import GseValveMap  # noqa: E402
from procedures.pressure_decay import build_machine  # noqa: E402

# Mirrors frontendv2.PT_SCALES. Imported there in the app; restated here so the
# test does not need nidaqmx, which frontendv2 pulls in.
SCALES = {
    "LNGTANK": (402.45048, -0.471844),
    "LOXTANK": (402.45048, -0.471844),
    "VENT": (402.45048, 0),
    "COPV": (1255.98144, 0),
    "LOXING": (402.45048, 0),
    "LNGING": (402.45048, 0),
    "LOXPOT": (402.45048, 0),
    "LNGPOT": (402.45048, 0),
    "PT10": (1, 0),
    "Thrust": (1, 0),
}


class FakeServer:
    def __init__(self, name, fields, latest=None, fresh=True):
        self.name = name
        self.fields = fields
        self.latest = latest
        self.latest_generation = 0
        self._fresh = fresh

    def is_fresh(self, _timeout):
        return self._fresh and self.latest is not None

    def latest_age(self):
        return 0.01 if self.latest is not None else None

    def push(self, latest):
        self.latest = latest
        self.latest_generation += 1


class FakeEffector:
    """Accepts everything and records nothing. No board, no sockets."""

    def __init__(self):
        self.dirty = False

    def stage_button(self, button_id, state):
        self.dirty = True

    def stage_table(self, states):
        self.dirty = True

    def is_dirty(self):
        return self.dirty

    def flush(self):
        self.dirty = False
        return True

    def abort_active(self):
        return False


def _nidaq_frame(**overrides):
    """Raw counts that scale to the given psi values."""
    frame = {}
    for name, (scale, offset) in SCALES.items():
        psi = overrides.get(name, 0.0)
        frame[name] = (psi - offset) / scale if scale else 0.0
    return frame


class OpsViewHarness:
    """A full ops view, painted into a headless ImGui context."""

    def __init__(self):
        self.gse = FakeServer("gse", ("solenoidInternalState0",), {})
        self.echo = FakeServer("echo", ("connected",), {"connected": 1.0})
        self.nidaq = FakeServer("nidaq", tuple(SCALES), _nidaq_frame())

        client = GseCommandClient()
        make_gse2v1_command_buttons(client, self.gse)

        self.ctx = ControlContext(
            gse_server=self.gse,
            echo_server=self.echo,
            nidaq_server=self.nidaq,
            scales=SCALES,
            valves=GseValveMap(),
        )
        self.dispatcher = Dispatcher(build_machine(), self.ctx, FakeEffector())
        self.log = E.EventLog()
        E.attach(self.dispatcher, self.log)
        self.model = M.OpsModel(
            dispatcher=self.dispatcher,
            ctx=self.ctx,
            history=History(M.TREND_CHANNELS),
            event_log=self.log,
            captcha=Captcha67Gate(),
        )
        self.hold = Hold()
        # The dispatcher paces itself to a 50 ms control period, so frames drive
        # a virtual clock rather than the wall clock. Otherwise a reading pushed
        # between two fast frames never reaches a control cycle, and the test
        # asserts against a snapshot that predates it.
        self.clock = time.monotonic()
        # ImGui's first frame for a window is a sizing pass that emits no
        # geometry, so warm one up here and let the assertions mean something.
        self.frame()

    def frame(self, width=1920.0, height=1080.0, advance=0.1):
        """Paint one frame. Returns the draw data ImGui produced."""
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, height)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures

        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(width, height))
        imgui.begin("##ops_root", flags=imgui.WindowFlags_.no_decoration)

        self.clock += advance
        self.dispatcher.tick(self.clock)
        self.model.update(self.gse, self.nidaq)

        p = Painter(imgui, _FONTS, T.scale_for(width, height), self.hold)
        alarm = self.model.alarm()
        layout = build_layout(p, width, height, alarm is not None)

        topbar.draw(p, layout["top"], self.model, (self.gse, self.echo, self.nidaq))
        if layout["alarm"] is not None:
            topbar.draw_alarm(p, layout["alarm"], self.model)
        if self.model.tab == "commands":
            commands.draw_page(p, layout["body"], self.model)
        else:
            sequence.draw(p, layout["left"], self.model)
            pid.draw(p, layout["centre"], self.model)
            telemetry.draw(p, layout["right"], self.model)
            strips.draw(p, layout["strips"], self.model)
            commands.draw_controls(p, layout["controls"], self.model)
        self.model.captcha.render(imgui)

        imgui.end()
        imgui.render()
        return imgui.get_draw_data()


class _NullFonts:
    """The built-in font for both roles; no TTF loading in tests."""

    display = None
    mono = None
    base_size = 16.0


_FONTS = _NullFonts()


def setUpModule():
    imgui.create_context()


class TestOpsViewRenders(unittest.TestCase):
    def setUp(self):
        self.harness = OpsViewHarness()

    def _assert_painted(self, draw_data):
        self.assertTrue(draw_data.valid)
        self.assertGreater(draw_data.total_vtx_count, 0,
                           "the frame produced no geometry at all")

    def test_cold_start_with_no_telemetry(self):
        """Every reading is NaN before the first frame arrives.

        This is the state the view is actually in when it opens, and the one
        most likely to divide by zero or format a NaN into a crash.
        """
        self.harness.nidaq.latest = None
        self.harness.gse.latest = None
        self.harness.echo.latest = None
        self._assert_painted(self.harness.frame())

    def test_nominal_telemetry(self):
        self.harness.nidaq.push(_nidaq_frame(COPV=350.0, LOXTANK=120.0,
                                             LNGTANK=118.0, VENT=300.0))
        for _ in range(4):
            draw_data = self.harness.frame()
        self._assert_painted(draw_data)

    def test_armed(self):
        self.harness.nidaq.push(_nidaq_frame(COPV=200.0))
        self.harness.dispatcher.arm()
        self._assert_painted(self.harness.frame())

    def test_aborted_state(self):
        self.harness.dispatcher.force_state("PD_ABORTED")
        self.harness.nidaq.push(_nidaq_frame(COPV=410.0, LOXTANK=660.0))
        self.assertTrue(self.harness.model.aborted)
        self._assert_painted(self.harness.frame())

    def test_over_redline_paints_crit(self):
        """A tank past its abort limit must resolve to the crit color."""
        self.harness.nidaq.push(_nidaq_frame(LOXTANK=700.0))
        self.harness.frame()
        self.assertEqual(self.harness.model.severity_color(M.LOX_TANK), T.CRIT)
        self._assert_painted(self.harness.frame())

    def test_ambiguous_offers_the_choice(self):
        """A tie stops the machine; the operations block renders the choice."""
        state = self.harness.dispatcher.current
        self.harness.dispatcher.mode = DispatcherMode.AMBIGUOUS
        self.harness.dispatcher.tie_candidates = list(state.operations)[:2]
        self._assert_painted(self.harness.frame())

    def test_commands_page_renders(self):
        """The raw command grids are a page of their own now."""
        self.harness.model.tab = "commands"
        self._assert_painted(self.harness.frame())
        self._assert_painted(self.harness.frame(1280.0, 720.0))

    def test_missing_valve_echo(self):
        """The board reporting nothing is a third outcome, not a failure."""
        self.harness.gse.push({})
        self.assertIsNone(self.harness.model.valve_agrees("pv1"))
        self._assert_painted(self.harness.frame())

    def test_captcha_modal(self):
        self.harness.model.captcha.require("force state", lambda: None)
        self._assert_painted(self.harness.frame())

    def test_small_window(self):
        """The layout must survive a window far below the design size."""
        self._assert_painted(self.harness.frame(1280.0, 720.0))
        self._assert_painted(self.harness.frame(900.0, 600.0))

    def test_every_state_renders(self):
        """Walk the whole procedure. Each state owns its own panel content.

        States differ in what they offer -- one operation, or none at all -- and
        the operations block has to paint each case.
        """
        for name in self.harness.dispatcher.machine.states:
            self.harness.dispatcher.force_state(name)
            self._assert_painted(self.harness.frame())


class TestEventClassification(unittest.TestCase):
    def test_severity_and_source(self):
        cases = [
            ("VALIDATE: two starts", E.CRIT),
            ("panic: tank overpressure", E.CRIT),
            ("refusing to arm: validation problems outstanding", E.WARN),
            ("mismatch pv1 expected True", E.WARN),
            ("initial state PD_00_ALL_OFF", E.INFO),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                severity, _source = E.classify(message)
                self.assertEqual(severity, expected)

    def test_filters_hide_a_severity(self):
        log = E.EventLog()
        log.add("operation complete")
        log.add("panic: aborting")
        self.assertEqual(len(log.visible(10)), 2)
        log.toggle(E.CRIT)
        self.assertEqual(len(log.visible(10)), 1)


class TestFormatting(unittest.TestCase):
    def test_nan_never_reaches_the_screen_as_a_number(self):
        self.assertEqual(fmt(float("nan")), "--")
        self.assertEqual(fmt(float("inf")), "--")
        self.assertEqual(fmt(None), "--")
        self.assertEqual(fmt(1234.5, 1), "1,234.5")

    def test_normally_open_valves_invert(self):
        """De-energised is OPEN for PV 2 and the tank vents."""
        self.assertTrue(M.valve_is_open(False, "pv2"))
        self.assertFalse(M.valve_is_open(True, "pv2"))
        self.assertTrue(M.valve_is_open(False, "tank_vent"))
        self.assertTrue(M.valve_is_open(True, "sol_gn2_fill_1"))
        self.assertFalse(M.valve_is_open(False, "sol_gn2_fill_1"))


if __name__ == "__main__":
    unittest.main()
