from __future__ import annotations

import unittest

import numpy as np

from tests.gui_runnables.gauge_lab import (
    GaugeLabApp,
    _DEFAULT_SAMPLE_RATE_HZ,
)


class GaugeLabAppTests(unittest.TestCase):
    def make_app(self, **overrides) -> GaugeLabApp:
        params = {}
        params.update(overrides)
        return GaugeLabApp(**params)

    def test_initial_state_is_running_with_empty_histories(self) -> None:
        app = self.make_app()

        self.assertTrue(app.running)
        self.assertIsNone(app.latest_value)
        self.assertEqual(app.signal.sample_index, 0)
        self.assertEqual(app.analog_gauge.width, 360.0)
        self.assertEqual(app.analog_gauge.height, 190.0)
        self.assertEqual(app.led_gauge.width, 360.0)
        self.assertEqual(app.led_gauge.height, 72.0)
        self.assertFalse(app.analog_gauge.has_value)
        self.assertFalse(app.led_gauge.has_value)
        self.assertEqual(app.graph.series_snapshot("signal").shape, (2, 0))

    def test_advance_primes_gauges_and_graph_from_same_source(self) -> None:
        app = self.make_app()

        had_update = app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)

        self.assertTrue(had_update)
        signal_snapshot = app.graph.series_snapshot("signal")
        low_snapshot = app.graph.series_snapshot("low_guide")
        high_snapshot = app.graph.series_snapshot("high_guide")
        self.assertEqual(signal_snapshot.shape, (2, 4))
        np.testing.assert_allclose(signal_snapshot[0], np.array([0.0, 1.0, 2.0, 3.0]) / _DEFAULT_SAMPLE_RATE_HZ)
        np.testing.assert_allclose(low_snapshot[1], np.zeros(4))
        np.testing.assert_allclose(high_snapshot[1], np.full(4, 100.0))
        self.assertEqual(app.latest_value, float(signal_snapshot[1, -1]))
        self.assertEqual(app.analog_gauge.target_value, float(signal_snapshot[1, -1]))
        self.assertEqual(app.led_gauge.target_value, float(signal_snapshot[1, -1]))

    def test_paused_app_does_not_advance_signal_or_widgets(self) -> None:
        app = self.make_app()
        app.running = False

        had_update = app.advance(0.25)

        self.assertFalse(had_update)
        self.assertEqual(app.signal.sample_index, 0)
        self.assertIsNone(app.latest_value)
        self.assertEqual(app.graph.series_snapshot("signal").shape, (2, 0))

    def test_reset_clears_histories_and_generator_phase(self) -> None:
        app = self.make_app()
        app.advance(8.0 / _DEFAULT_SAMPLE_RATE_HZ)

        self.assertGreater(app.signal.sample_index, 0)
        self.assertTrue(app.analog_gauge.has_value)
        self.assertEqual(app.graph.series_snapshot("signal").shape[1], 8)

        app.reset()

        self.assertEqual(app.signal.sample_index, 0)
        self.assertIsNone(app.latest_value)
        self.assertFalse(app.analog_gauge.has_value)
        self.assertFalse(app.led_gauge.has_value)
        self.assertEqual(app.graph.series_snapshot("signal").shape, (2, 0))

    def test_signal_can_overshoot_configured_range_while_gauges_clamp(self) -> None:
        app = self.make_app(
            sample_rate_hz=10.0,
            cycle_seconds=4.0,
            max_rows_per_tick=20,
            overshoot_ratio=0.20,
        )

        app.advance(1.2)

        signal_snapshot = app.graph.series_snapshot("signal")
        self.assertTrue(np.max(signal_snapshot[1]) > app.high_value)
        self.assertGreaterEqual(app.analog_gauge.target_fraction(), 0.0)
        self.assertLessEqual(app.analog_gauge.target_fraction(), 1.0)
        self.assertGreaterEqual(app.led_gauge.target_fraction(), 0.0)
        self.assertLessEqual(app.led_gauge.target_fraction(), 1.0)


if __name__ == "__main__":
    unittest.main()
