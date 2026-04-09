from __future__ import annotations

import tempfile
import unittest

import numpy as np

from simulation_utils.simulators import (
    RotationMatrixSignalGenerator,
    SpectralSignalConfig,
    SpectralTerm,
    random_orientation_matrix_generator,
    random_sparse_spectrum_config,
    random_sparse_spectrum_generator,
)


class SpectralSignalConfigTests(unittest.TestCase):
    def test_round_trip_preserves_signal_configuration(self) -> None:
        config = SpectralSignalConfig(
            signal_id="copv_pressure",
            sample_rate_hz=128.0,
            samples_per_cycle=16,
            terms=(
                SpectralTerm(bin_index=1, real=2.5, imag=-1.25),
                SpectralTerm(bin_index=3, real=-0.75, imag=0.5),
            ),
            offset=10.0,
            scale=2.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = config.export(f"{tmpdir}/signal.toml")
            rebuilt = SpectralSignalConfig.reconstruct(path)

        self.assertEqual(rebuilt, config)

    def test_rejects_out_of_range_bin(self) -> None:
        with self.assertRaises(ValueError):
            SpectralSignalConfig(
                signal_id="bad_bin",
                sample_rate_hz=64.0,
                samples_per_cycle=8,
                terms=(SpectralTerm(bin_index=5, real=1.0, imag=0.0),),
            )

    def test_rejects_negative_bin(self) -> None:
        with self.assertRaises(ValueError):
            SpectralTerm(bin_index=-1, real=1.0, imag=0.0)

    def test_rejects_imaginary_dc_term(self) -> None:
        with self.assertRaises(ValueError):
            SpectralSignalConfig(
                signal_id="bad_dc",
                sample_rate_hz=64.0,
                samples_per_cycle=8,
                terms=(SpectralTerm(bin_index=0, real=1.0, imag=0.25),),
            )

    def test_rejects_imaginary_nyquist_term(self) -> None:
        with self.assertRaises(ValueError):
            SpectralSignalConfig(
                signal_id="bad_nyquist",
                sample_rate_hz=64.0,
                samples_per_cycle=8,
                terms=(SpectralTerm(bin_index=4, real=1.0, imag=0.25),),
            )

    def test_offset_and_scale_apply_after_inverse_transform(self) -> None:
        config = SpectralSignalConfig(
            signal_id="scaled",
            sample_rate_hz=16.0,
            samples_per_cycle=8,
            terms=(SpectralTerm(bin_index=1, real=3.0, imag=-2.0),),
            offset=5.0,
            scale=1.5,
        )

        dense = np.zeros(config.rfft_length(), dtype=np.complex128)
        dense[1] = complex(3.0, -2.0)
        expected = 5.0 + (1.5 * np.fft.irfft(dense, n=8))

        np.testing.assert_allclose(config.cycle_values(), expected)


class SpectralSignalGeneratorTests(unittest.TestCase):
    def make_config(self) -> SpectralSignalConfig:
        return SpectralSignalConfig(
            signal_id="demo",
            sample_rate_hz=4.0,
            samples_per_cycle=4,
            terms=(SpectralTerm(bin_index=1, real=0.0, imag=-2.0),),
        )

    def test_cycle_reconstruction_matches_numpy_irfft(self) -> None:
        config = self.make_config()
        dense = np.zeros(config.rfft_length(), dtype=np.complex128)
        dense[1] = complex(0.0, -2.0)
        expected = np.fft.irfft(dense, n=config.samples_per_cycle)

        np.testing.assert_allclose(config.build_generator().cycle_values(), expected)

    def test_next_batch_returns_timestamp_value_rows_and_wraps_exactly(self) -> None:
        generator = self.make_config().build_generator()

        batch = generator.next_batch(6)

        self.assertEqual(batch.shape, (2, 6))
        np.testing.assert_allclose(batch[0], np.array([0.0, 0.25, 0.5, 0.75, 1.0, 1.25]))
        np.testing.assert_allclose(batch[1], np.array([0.0, 1.0, 0.0, -1.0, 0.0, 1.0]))

    def test_reset_restores_requested_sample_index(self) -> None:
        generator = self.make_config().build_generator()
        generator.next_batch(5)

        generator.reset(sample_index=2)
        batch = generator.next_batch(3)

        np.testing.assert_allclose(batch[0], np.array([0.5, 0.75, 1.0]))
        np.testing.assert_allclose(batch[1], np.array([0.0, -1.0, 0.0]))


class RandomSpectralSignalTests(unittest.TestCase):
    def test_seeded_sparse_helper_is_deterministic(self) -> None:
        kwargs = {
            "signal_id": "seeded",
            "sample_rate_hz": 128.0,
            "samples_per_cycle": 32,
            "seed": 7,
            "nonzero_terms": 4,
            "coefficient_scale": 2.0,
        }
        config_a = random_sparse_spectrum_config(**kwargs)
        config_b = random_sparse_spectrum_config(**kwargs)
        config_c = random_sparse_spectrum_config(**{**kwargs, "seed": 11})

        self.assertEqual(config_a, config_b)
        self.assertNotEqual(config_a, config_c)
        np.testing.assert_allclose(config_a.cycle_values(), config_b.cycle_values())

    def test_noise_floor_still_produces_exact_repeating_cycle(self) -> None:
        generator = random_sparse_spectrum_generator(
            signal_id="noisy",
            sample_rate_hz=64.0,
            samples_per_cycle=16,
            seed=19,
            nonzero_terms=3,
            coefficient_scale=1.0,
            noise_floor_std=0.15,
        )

        batch = generator.next_batch(32)

        np.testing.assert_allclose(batch[1, :16], batch[1, 16:])
        np.testing.assert_allclose(batch[0, :16] + (16 / 64.0), batch[0, 16:])

    def test_noise_floor_respects_requested_bin_constraints(self) -> None:
        config = random_sparse_spectrum_config(
            signal_id="band_limited",
            sample_rate_hz=64.0,
            samples_per_cycle=16,
            seed=19,
            nonzero_terms=3,
            min_bin=1,
            max_bin=4,
            coefficient_scale=1.0,
            noise_floor_std=0.15,
            allow_dc=False,
            allow_nyquist=False,
        )

        self.assertTrue(all(1 <= term.bin_index <= 4 for term in config.terms))
        self.assertTrue(all(term.bin_index != 0 for term in config.terms))


class RotationMatrixSignalGeneratorTests(unittest.TestCase):
    def test_random_orientation_generator_returns_timestamped_rotation_batches(self) -> None:
        generator = random_orientation_matrix_generator(
            sample_rate_hz=8.0,
            samples_per_cycle=16,
            seed=5,
            angle_scale_radians=0.1,
        )

        batch = generator.next_batch(3)

        self.assertIsInstance(generator, RotationMatrixSignalGenerator)
        self.assertEqual(batch.shape, (10, 3))
        np.testing.assert_allclose(batch[0], np.array([0.0, 0.125, 0.25]))
        for column in range(batch.shape[1]):
            matrix = batch[1:10, column].reshape(3, 3)
            np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1.0e-6)
            np.testing.assert_allclose(np.linalg.det(matrix), 1.0, atol=1.0e-6)


if __name__ == "__main__":
    unittest.main()
