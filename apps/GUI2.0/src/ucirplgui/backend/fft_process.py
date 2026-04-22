from __future__ import annotations

from collections import deque

import numpy as np


class FftReconstructionProcess:
    """Low-pass reconstruction stage for frontend FFT overlays.

    The frontend wants an FFT-derived companion trace without the full-spectrum
    magnitude spikes overwhelming the source signal. This process keeps a short
    history per signal, projects it into the frequency domain, retains only the
    lowest bins, and reconstructs the latest smoothed sample back in the
    original signal units.
    """

    def __init__(
        self,
        *,
        window_size: int,
        retained_frequency_bins: int,
        min_samples: int,
    ) -> None:
        if window_size < 2:
            raise ValueError("window_size must be at least 2")
        if retained_frequency_bins < 0:
            raise ValueError("retained_frequency_bins must be non-negative")
        if min_samples < 2:
            raise ValueError("min_samples must be at least 2")
        self._window_size = int(window_size)
        self._retained_frequency_bins = int(retained_frequency_bins)
        self._min_samples = int(min_samples)
        self._histories: dict[str, deque[float]] = {}

    def reconstruct(self, signal_name: str, value: float) -> float:
        history = self._histories.setdefault(
            signal_name,
            deque(maxlen=self._window_size),
        )
        history.append(float(value))

        samples = np.asarray(history, dtype=np.float64)
        if len(samples) < self._min_samples:
            return float(np.mean(samples))

        spectrum = np.fft.rfft(samples)
        filtered = np.zeros_like(spectrum)
        retained_bins = min(len(spectrum), self._retained_frequency_bins + 1)
        filtered[:retained_bins] = spectrum[:retained_bins]
        reconstructed = np.fft.irfft(filtered, n=len(samples))
        latest_value = float(reconstructed[-1])
        if not np.isfinite(latest_value):
            return float(np.mean(samples))
        return latest_value

    def frame(
        self,
        timestamp_s: float,
        samples: tuple[tuple[str, float], ...],
    ) -> np.ndarray:
        return np.array(
            [
                [
                    timestamp_s,
                    *(
                        self.reconstruct(signal_name, value)
                        for signal_name, value in samples
                    ),
                ]
            ],
            dtype=np.float64,
        )
