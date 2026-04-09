from __future__ import annotations

import unittest

import numpy as np

from tests.gui_runnables.signal_graph_lab import (
    SignalGraphLabApp,
    _DEFAULT_SAMPLE_RATE_HZ,
)


class SignalGraphLabAppTests(unittest.TestCase):
    def make_app(self, **overrides) -> SignalGraphLabApp:
        params = {"bank_seed": 314159}
        params.update(overrides)
        return SignalGraphLabApp(**params)

    def test_initial_state_has_no_bank_and_disabled_signal_buttons(self) -> None:
        app = self.make_app()

        self.assertFalse(app.generated_bank)
        self.assertIsNone(app.bank_seed)
        self.assertTrue(app.generate_button.enabled_by_default)
        self.assertEqual(len(app.generators), 0)
        self.assertTrue(all(not button.enabled_by_default for button in app.signal_buttons))
        self.assertTrue(all(not reader.has_pending for reader in app.readers.values()))

    def test_generate_signal_bank_is_one_shot(self) -> None:
        app = self.make_app()

        generated = app.generate_signal_bank(seed=42)
        second_attempt = app.generate_signal_bank(seed=999)

        self.assertTrue(generated)
        self.assertFalse(second_attempt)
        self.assertTrue(app.generated_bank)
        self.assertEqual(app.bank_seed, 42)
        self.assertFalse(app.generate_button.enabled_by_default)
        self.assertEqual(len(app.generators), 8)
        self.assertTrue(all(button.enabled_by_default for button in app.signal_buttons))
        self.assertTrue(all(button.state is False for button in app.signal_buttons))

    def test_active_signal_appends_batches_into_graph(self) -> None:
        app = self.make_app()
        app.generate_signal_bank(seed=42)
        app.set_signal_enabled(1, True)

        had_update = app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)

        self.assertTrue(had_update)
        signal_1 = app.graph.series_snapshot("signal_1")
        signal_2 = app.graph.series_snapshot("signal_2")
        self.assertEqual(signal_1.shape, (2, 4))
        self.assertEqual(signal_2.shape, (2, 0))
        np.testing.assert_allclose(signal_1[0], np.array([0.0, 1.0, 2.0, 3.0]) / _DEFAULT_SAMPLE_RATE_HZ)
        self.assertTrue(all(generator.sample_index == 4 for generator in app.generators))

    def test_disabled_signal_freezes_history_but_generator_clock_advances(self) -> None:
        app = self.make_app()
        app.generate_signal_bank(seed=42)
        app.set_signal_enabled(1, True)
        app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)
        before_freeze = app.graph.series_snapshot("signal_1").copy()

        app.set_signal_enabled(1, False)
        had_update = app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)
        after_freeze = app.graph.series_snapshot("signal_1")

        self.assertFalse(had_update)
        np.testing.assert_allclose(after_freeze, before_freeze)
        self.assertTrue(all(generator.sample_index == 8 for generator in app.generators))

    def test_disabled_signal_ages_out_when_other_signal_advances_graph_time(self) -> None:
        app = self.make_app(
            sample_rate_hz=10.0,
            samples_per_cycle=256,
            graph_window_seconds=1.0,
            max_rows_per_tick=10,
        )
        app.generate_signal_bank(seed=42)
        app.set_signal_enabled(1, True)
        app.advance(0.5)

        app.set_signal_enabled(1, False)
        app.set_signal_enabled(2, True)
        for _ in range(4):
            app.advance(0.5)

        self.assertEqual(app.graph.series_snapshot("signal_1").shape, (2, 0))
        self.assertGreater(app.graph.series_snapshot("signal_2").shape[1], 0)

    def test_reenabled_signal_resumes_at_current_time_not_old_time(self) -> None:
        app = self.make_app()
        app.generate_signal_bank(seed=42)
        app.set_signal_enabled(1, True)
        app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)
        app.set_signal_enabled(1, False)
        app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)

        app.set_signal_enabled(1, True)
        app.advance(2.0 / _DEFAULT_SAMPLE_RATE_HZ)
        snapshot = app.graph.series_snapshot("signal_1")

        np.testing.assert_allclose(
            snapshot[0],
            np.array([0.0, 1.0, 2.0, 3.0, 8.0, 9.0]) / _DEFAULT_SAMPLE_RATE_HZ,
        )
        self.assertEqual(app.generators[0].sample_index, 10)

    def test_small_cycle_bank_generation_caps_fft_bin_range(self) -> None:
        app = self.make_app(samples_per_cycle=32)

        generated = app.generate_signal_bank(seed=42)

        self.assertTrue(generated)
        self.assertEqual(len(app.generators), 8)


if __name__ == "__main__":
    unittest.main()
