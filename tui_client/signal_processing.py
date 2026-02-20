import multiprocessing as mp
import numpy as np


def downsample_for_plot(values: list[float], max_points: int) -> list[float]:
    if len(values) <= max_points:
        return values
    step = len(values) / float(max_points)
    out = []
    i = 0.0
    while int(i) < len(values) and len(out) < max_points:
        out.append(values[int(i)])
        i += step
    return out


def apply_notch_filters(values: list[float], sample_rate_hz: float, notch_hz: list[float], bin_half_width: int = 1) -> list[float]:
    if not values or sample_rate_hz <= 0 or not notch_hz:
        return values
    arr = np.asarray(values, dtype=np.float64)
    spec = np.fft.rfft(arr)
    freqs = np.fft.rfftfreq(arr.size, d=1.0 / sample_rate_hz)
    for f0 in notch_hz:
        idx = int(np.argmin(np.abs(freqs - f0)))
        lo = max(0, idx - bin_half_width)
        hi = min(spec.size, idx + bin_half_width + 1)
        spec[lo:hi] = 0
    return np.fft.irfft(spec, n=arr.size).astype(np.float32).tolist()


def fft_worker(values: list[float], sample_rate_hz: float, top_n: int, out_q: mp.Queue) -> None:
    if not values or sample_rate_hz <= 0:
        out_q.put({"ok": False, "error": "invalid input"})
        return
    arr = np.asarray(values, dtype=np.float64)
    arr = arr - np.mean(arr)
    spec = np.fft.rfft(arr)
    freqs = np.fft.rfftfreq(arr.size, d=1.0 / sample_rate_hz)
    amps = np.abs(spec)
    if amps.size > 0:
        amps[0] = 0.0
    k = min(top_n, amps.size)
    if k == 0:
        out_q.put({"ok": False, "error": "no fft bins"})
        return
    idx = np.argpartition(amps, -k)[-k:]
    idx = idx[np.argsort(amps[idx])[::-1]]
    peaks = [(float(freqs[i]), float(amps[i])) for i in idx]
    out_q.put({"ok": True, "peaks": peaks, "n": int(arr.size), "sr": float(sample_rate_hz)})
