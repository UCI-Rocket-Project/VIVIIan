"""Tests for the GSE2V1 command layer, specifically the echo round trip.

No Flight and no GUI: a fake echo server stands in for the board, so the
question "does a command survive until the board acknowledges it" can be asked
directly.

    python -m unittest tests.test_gse2v1_commands -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "GUI2.1"))

import gui_gse2v1  # noqa: E402
from gse21connector import (  # noqa: E402
    GSE2V1_COMMAND_FIELD_NAMES,
    GSE2V1_ECHO_FIELD_NAMES,
)
from gui_gse2v1 import (  # noqa: E402
    GSE2V1_COMMAND_BUTTONS,
    GseCommandClient,
    make_gse2v1_command_buttons,
    sync_gse2v1_command_buttons_from_echo,
)
from procedures import table_states  # noqa: E402


class FakeEcho:
    """Echo server reporting whatever command the board last acknowledged."""

    def __init__(self):
        self.latest = {name: 0.0 for name in GSE2V1_ECHO_FIELD_NAMES}
        self.latest["connected"] = 1.0
        self.latest_generation = 1

    def is_fresh(self, _timeout):
        return True

    def tick(self):
        """A new echo frame that does not acknowledge anything new."""
        self.latest_generation += 1

    def acknowledge(self, row):
        self.latest = {name: float(v) for name, v in zip(GSE2V1_COMMAND_FIELD_NAMES, row)}
        self.latest["connected"] = 1.0
        self.latest_generation += 1


# The two valves the abort table opens; everything else in ABORT is closed.
ABORT_VENTS = ("sol_gn2_vent", "copv_vent")


class EchoRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.client = GseCommandClient()
        self.echo = FakeEcho()
        make_gse2v1_command_buttons(self.client, self.echo)
        # Start from an acknowledged ALL OFF.
        gui_gse2v1._apply_table_state(self.client, dict(table_states.ALL_OFF))
        self.echo.acknowledge(self.client.row)

    def _vent_states(self):
        return {v: GSE2V1_COMMAND_BUTTONS[v]["button"].state for v in ABORT_VENTS}

    def _send_abort(self):
        gui_gse2v1._apply_table_state(self.client, dict(table_states.ABORT))
        self.client._mark_sent()

    def test_abort_survives_an_echo_that_has_not_caught_up(self):
        # The bug this guards: the 0.5s grace expires before the board round
        # trip finishes, the echo still reads "off", and the sync reverts the
        # abort's vent-opens. The abort then silently does not open the vents.
        self._send_abort()
        self.assertEqual(self._vent_states(), {v: True for v in ABORT_VENTS})

        self.client._ignore_echo_until = 0.0          # grace expired
        self.echo.tick()                              # board still behind
        sync_gse2v1_command_buttons_from_echo(self.client, self.echo)

        self.assertEqual(
            self._vent_states(), {v: True for v in ABORT_VENTS},
            "an unacknowledged command must not be reverted by a stale echo",
        )

    def test_the_command_row_still_carries_the_vents(self):
        self._send_abort()
        self.client._ignore_echo_until = 0.0
        self.echo.tick()
        sync_gse2v1_command_buttons_from_echo(self.client, self.echo)

        row = dict(zip(GSE2V1_COMMAND_FIELD_NAMES, self.client.row))
        self.assertEqual(row["solenoidState0"], 1.0, "GN2 vent")
        self.assertEqual(row["solenoidState9"], 1.0, "COPV vent")

    def test_echo_resumes_once_the_board_acknowledges(self):
        self._send_abort()
        self.client._ignore_echo_until = 0.0
        self.echo.acknowledge(self.client.row)
        sync_gse2v1_command_buttons_from_echo(self.client, self.echo)

        self.assertEqual(self._vent_states(), {v: True for v in ABORT_VENTS})

        # With nothing pending, the board is authoritative again: if it now
        # reports the vents shut, the buttons must follow.
        self.echo.latest["solenoidState0"] = 0.0
        self.echo.latest["solenoidState9"] = 0.0
        self.echo.latest_generation += 1
        sync_gse2v1_command_buttons_from_echo(self.client, self.echo)

        self.assertEqual(self._vent_states(), {v: False for v in ABORT_VENTS})

    def test_a_command_the_board_refuses_stops_being_pinned(self):
        # If the board never acknowledges, the button must not stay stuck on a
        # command that did not take: _pending_until bounds the wait.
        self._send_abort()
        self.client._ignore_echo_until = 0.0
        self.client._pending_until = 0.0              # the wait has elapsed
        self.echo.tick()
        sync_gse2v1_command_buttons_from_echo(self.client, self.echo)

        self.assertEqual(
            self._vent_states(), {v: False for v in ABORT_VENTS},
            "an unacknowledged command must eventually surface as not taken",
        )


if __name__ == "__main__":
    unittest.main()
