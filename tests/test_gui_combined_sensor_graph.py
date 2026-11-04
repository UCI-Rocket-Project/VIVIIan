from pathlib import Path
import math
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "GUI2.1"))

from gse21connector import gse2v1_field_valid
from gui_elements import NidaqGraph, SensorGraph


class SensorGraphTests(unittest.TestCase):
    def make_graph(self, nidaq_latest=None, gse_latest=None):
        self.nidaq = SimpleNamespace(latest=nidaq_latest, latest_update_time=10.0)
        self.gse = SimpleNamespace(latest=gse_latest, latest_update_time=10.5)
        self.nidaq_history = NidaqGraph(self.nidaq, field_names={"Thrust": (2, 1)})
        self.gse_history = NidaqGraph(
            self.gse, field_names={"pressure0": (3, 4)}, field_validity=gse2v1_field_valid,
        )
        self.graph = SensorGraph({"NI-DAQ": self.nidaq_history, "GSE": self.gse_history})
        self.ui = MagicMock()
        self.ui.button.return_value = False
        self.ui.get_content_region_avail.return_value = (1200, 800)
        return self.graph

    def draw(self, now):
        with patch("gui_elements.time.monotonic", return_value=now):
            self.graph.draw(self.ui)

    @patch("gui_elements.implot")
    def test_independent_sampling_timing_scaling_and_source_labels(self, plot):
        self.make_graph({"Thrust": 10}, {"pressure0": 20, "externalAdcValidBitmask": 1})
        self.draw(11)
        self.gse.latest = {"pressure0": 30, "externalAdcValidBitmask": 1}
        self.gse.latest_update_time = 11.5
        self.draw(12)
        lines = {call.args[0]: call.args[1:] for call in plot.plot_line.call_args_list}
        self.assertEqual(set(lines), {"NI-DAQ · Thrust", "GSE · pressure0"})
        np.testing.assert_array_equal(lines["NI-DAQ · Thrust"][0], [-2])
        np.testing.assert_array_equal(lines["NI-DAQ · Thrust"][1], [21])
        np.testing.assert_array_equal(lines["GSE · pressure0"][0], [-1.5, -0.5])
        np.testing.assert_array_equal(lines["GSE · pressure0"][1], [64, 94])
        self.ui.text_unformatted.assert_any_call("NI-DAQ · Thrust: 21.00")
        self.ui.text_unformatted.assert_any_call("GSE · pressure0: 94.00")

    @patch("gui_elements.implot")
    def test_missing_source_does_not_block_graph(self, plot):
        self.make_graph({"Thrust": 10})
        self.draw(11)
        self.nidaq.latest = {"Thrust": 12}
        self.nidaq.latest_update_time = 12
        self.draw(12)
        self.assertEqual(plot.plot_line.call_args.args[0], "NI-DAQ · Thrust")
        self.ui.text_disabled.assert_any_call("GSE · pressure0: waiting")

    @patch("gui_elements.implot")
    def test_invalid_channel_gaps_and_recovers_without_affecting_nidaq(self, plot):
        self.make_graph({"Thrust": 10}, {"pressure0": 20, "externalAdcValidBitmask": 1})
        self.draw(11)
        self.gse.latest = {"pressure0": 99, "externalAdcValidBitmask": 0}
        self.gse.latest_update_time = 12
        self.draw(12)
        self.assertTrue(np.isnan(plot.plot_line.call_args.args[2][-1]))
        self.assertEqual(self.nidaq_history.history[-1], [21])
        self.assertEqual(self.ui.text_colored.call_args.args[1], "GSE · pressure0: INVALID")
        self.gse.latest = {"pressure0": 30, "externalAdcValidBitmask": 1}
        self.gse.latest_update_time = 13
        self.draw(13)
        np.testing.assert_array_equal(plot.plot_line.call_args.args[2], [64, np.nan, 94])
        self.assertTrue(all(math.isfinite(v) for v in plot.setup_axis_limits.call_args.args[1:3]))

    @patch("gui_elements.implot")
    def test_regression_controls_apply_to_both_sources(self, plot):
        self.make_graph({"Thrust": 10}, {"pressure0": 20, "externalAdcValidBitmask": 1})
        self.draw(11)
        self.gse.latest = dict(self.gse.latest)
        self.gse.latest_update_time = 12
        self.ui.button.side_effect = lambda label: label == "Start 3m avg"
        self.draw(12)
        for history in (self.nidaq_history, self.gse_history):
            self.assertEqual(history._regression_started_at, 12)
        self.ui.button.side_effect = lambda label: label == "Clear"
        self.draw(13)
        for history in (self.nidaq_history, self.gse_history):
            self.assertIsNone(history._regression_started_at)

    @patch("gui_elements.implot")
    def test_stopped_source_history_expires_independently(self, plot):
        self.make_graph({"Thrust": 10}, {"pressure0": 20, "externalAdcValidBitmask": 1})
        self.draw(11)
        self.gse.latest = dict(self.gse.latest)
        self.gse.latest_update_time = 12
        self.draw(12)
        self.nidaq.latest = {"Thrust": 12}
        self.nidaq.latest_update_time = 200
        self.draw(200)
        self.assertFalse(self.gse_history.history)
        self.assertEqual(list(self.nidaq_history.times), [200])


if __name__ == "__main__":
    unittest.main()
