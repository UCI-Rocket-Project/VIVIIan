from __future__ import annotations

import unittest

import numpy as np

from ucirplgui.backend.fft_process import FftReconstructionProcess


class FftReconstructionProcessTests(unittest.TestCase):
    def test_short_history_returns_running_mean(self) -> None:
        process = FftReconstructionProcess(
            window_size=16,
            retained_frequency_bins=2,
            min_samples=8,
        )

        outputs = [
            process.reconstruct("signal", value)
            for value in (10.0, 20.0, 30.0)
        ]

        np.testing.assert_allclose(outputs, np.array([10.0, 15.0, 20.0]))

    def test_reconstruction_suppresses_high_frequency_component(self) -> None:
        process = FftReconstructionProcess(
            window_size=32,
            retained_frequency_bins=2,
            min_samples=8,
        )

        samples = []
        baseline = []
        for index in range(64):
            low_component = 100.0 + (6.0 * np.sin((2.0 * np.pi * index) / 32.0))
            high_component = 12.0 if index % 2 == 0 else -12.0
            baseline.append(low_component)
            samples.append(low_component + high_component)

        outputs = [
            process.reconstruct("signal", value)
            for value in samples
        ]

        latest_output = outputs[-1]
        latest_sample = samples[-1]
        latest_baseline = baseline[-1]

        self.assertLess(
            abs(latest_output - latest_baseline),
            abs(latest_sample - latest_baseline),
        )

    def test_frame_builds_timestamped_row(self) -> None:
        process = FftReconstructionProcess(
            window_size=16,
            retained_frequency_bins=1,
            min_samples=4,
        )

        frame = process.frame(
            123.0,
            (
                ("a", 5.0),
                ("b", 10.0),
            ),
        )

        self.assertEqual(frame.shape, (1, 3))
        np.testing.assert_allclose(frame[0], np.array([123.0, 5.0, 10.0]))


if __name__ == "__main__":
    unittest.main()
