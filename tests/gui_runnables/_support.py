from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BufferedFrameReader:
    """Single-batch adapter that lets a widget pull at most one frame per UI tick."""

    max_rows: int
    expected_rows: int = 2
    dtype: np.dtype = np.dtype(np.float64)

    def __post_init__(self) -> None:
        self.shape = (self.expected_rows, self.max_rows)
        self._pending: np.ndarray | None = None

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def set_blocking(self, _blocking: bool) -> None:
        return None

    def prime(self, frame: np.ndarray) -> None:
        batch = np.asarray(frame, dtype=self.dtype)
        if batch.ndim != 2 or batch.shape[0] != self.expected_rows:
            raise ValueError(
                f"Expected buffered frame shape ({self.expected_rows}, rows), got {batch.shape}."
            )
        if batch.shape[1] < 1 or batch.shape[1] > self.max_rows:
            raise ValueError(
                f"Buffered frame rows must be between 1 and {self.max_rows}, got {batch.shape[1]}."
            )
        self._pending = batch.copy()

    def clear(self) -> None:
        self._pending = None

    def read(self) -> np.ndarray | None:
        frame = self._pending
        self._pending = None
        return None if frame is None else frame.copy()


def apply_operator_theme(imgui: Any) -> None:
    style = imgui.get_style()
    style.window_rounding = 10.0
    style.child_rounding = 8.0
    style.frame_rounding = 6.0
    style.grab_rounding = 6.0
    style.scrollbar_rounding = 8.0
    style.popup_rounding = 8.0
    style.tab_rounding = 6.0
    style.window_border_size = 1.0
    style.child_border_size = 1.0
    style.frame_border_size = 1.0
    style.window_padding = (16.0, 14.0)
    colors = style.colors
    colors[imgui.COLOR_WINDOW_BACKGROUND] = (0.025, 0.035, 0.055, 1.0)
    colors[imgui.COLOR_CHILD_BACKGROUND] = (0.040, 0.055, 0.080, 1.0)
    colors[imgui.COLOR_TITLE_BACKGROUND] = (0.045, 0.070, 0.110, 1.0)
    colors[imgui.COLOR_TITLE_BACKGROUND_ACTIVE] = (0.060, 0.090, 0.150, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND] = (0.080, 0.105, 0.150, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND_HOVERED] = (0.120, 0.165, 0.220, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND_ACTIVE] = (0.145, 0.195, 0.270, 1.0)
    colors[imgui.COLOR_BUTTON] = (0.110, 0.225, 0.330, 1.0)
    colors[imgui.COLOR_BUTTON_HOVERED] = (0.155, 0.310, 0.430, 1.0)
    colors[imgui.COLOR_BUTTON_ACTIVE] = (0.205, 0.390, 0.540, 1.0)
    colors[imgui.COLOR_BORDER] = (0.180, 0.260, 0.370, 1.0)
