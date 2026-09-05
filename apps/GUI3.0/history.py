"""Trend buffers for the strip charts and sparklines.

``LatestServer`` only keeps the newest frame, so anything that draws a line over
time has to accumulate it here. Samples are taken once per frame from the
scaled context, and only when the server's generation has actually advanced --
otherwise a fast render loop packs the buffer with duplicates of one reading and
the window covers a fraction of the time it claims to.

A stale feed records NaN rather than the last good value, so a dropout shows as
a gap in the trace instead of a flat line that looks like a healthy hold.
"""
from __future__ import annotations

import math
import time
from collections import deque


class Channel:
    """One named signal over time."""

    def __init__(self, name: str, capacity: int) -> None:
        self.name = name
        self.values: deque[float] = deque(maxlen=capacity)
        self.stamps: deque[float] = deque(maxlen=capacity)

    def push(self, value: float, now: float) -> None:
        self.values.append(float(value))
        self.stamps.append(now)

    def latest(self) -> float:
        return self.values[-1] if self.values else float("nan")

    def span_seconds(self) -> float:
        if len(self.stamps) < 2:
            return 0.0
        return self.stamps[-1] - self.stamps[0]

    def series(self) -> list[float]:
        return list(self.values)

    def rate_per_second(self, lookback: int = 10) -> float:
        """Change per second across the last *lookback* samples."""
        if len(self.values) <= lookback:
            return float("nan")
        newest = self.values[-1]
        oldest = self.values[-1 - lookback]
        dt = self.stamps[-1] - self.stamps[-1 - lookback]
        if dt <= 0 or not math.isfinite(newest) or not math.isfinite(oldest):
            return float("nan")
        return (newest - oldest) / dt

    def bounds(self) -> tuple[float, float]:
        finite = [v for v in self.values if math.isfinite(v)]
        if not finite:
            return 0.0, 1.0
        return min(finite), max(finite)


class History:
    """Every channel the ops view trends."""

    def __init__(self, names: tuple[str, ...], capacity: int = 900) -> None:
        self.channels = {name: Channel(name, capacity) for name in names}
        self._last_generation = -1

    def __getitem__(self, name: str) -> Channel:
        return self.channels[name]

    def sample(self, ctx, generation: int) -> None:
        """Record one frame of scaled pressures, if the feed has moved on."""
        if generation == self._last_generation:
            return
        self._last_generation = generation
        now = time.monotonic()
        for name, channel in self.channels.items():
            try:
                channel.push(ctx.psi(name), now)
            except Exception:
                channel.push(float("nan"), now)

    def bounds(self, names: tuple[str, ...]) -> tuple[float, float]:
        """A shared y range across *names*, padded so traces are not flush."""
        low = math.inf
        high = -math.inf
        for name in names:
            channel = self.channels.get(name)
            if channel is None:
                continue
            lo, hi = channel.bounds()
            low = min(low, lo)
            high = max(high, hi)
        if not math.isfinite(low) or not math.isfinite(high):
            return 0.0, 1.0
        pad = max((high - low) * 0.1, 1.0)
        return low - pad, high + pad
