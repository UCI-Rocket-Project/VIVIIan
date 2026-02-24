import threading
from collections import deque


class SharedRingBuffer:
    """Thread-safe store for ready-to-plot per-signal numeric series."""

    def __init__(self, max_batches: int = 512, max_points_per_signal: int = 30000):
        self._lock = threading.Lock()
        self._batches = deque(maxlen=max_batches)  # each entry: (columns, rows)
        self._series = {}  # signal_name -> deque[float]
        self._max_points_per_signal = max_points_per_signal

    def push(self, columns: list[str], rows: list[tuple]) -> None:
        with self._lock:
            self._batches.append((columns, rows))

    def snapshot(self):
        with self._lock:
            return list(self._batches)

    def push_series_arrays(self, series_arrays: dict[str, list[float]]) -> None:
        """Append numeric arrays into per-signal deques."""
        if not series_arrays:
            return
        with self._lock:
            for col, values in series_arrays.items():
                if col not in self._series:
                    self._series[col] = deque(maxlen=self._max_points_per_signal)
                dq = self._series[col]
                dq.extend(values)

    def snapshot_series(self, max_points: int | None = None) -> dict[str, list[float]]:
        with self._lock:
            if max_points is None:
                return {k: list(dq) for k, dq in self._series.items()}
            return {k: list(dq)[-max_points:] for k, dq in self._series.items()}
