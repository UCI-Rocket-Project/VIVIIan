from __future__ import annotations

from generic_connector import LatestServer
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
import numpy as np


RGBA = tuple[float, float, float, float]

COLOR_BLACK: RGBA = (0.0, 0.0, 0.0, 1.0)
COLOR_WHITE: RGBA = (1.0, 1.0, 1.0, 1.0)

APP_BACKGROUND_COLOR: RGBA = (0.1, 0.1, 0.1, 1.0)

BUTTON_BACKGROUND_COLOR: RGBA = (0.18, 0.18, 0.20, 1.0)
BUTTON_HOVER_COLOR: RGBA = (0.24, 0.24, 0.27, 1.0)
BUTTON_ACTIVE_COLOR: RGBA = (0.12, 0.34, 0.16, 1.0)
BUTTON_DISABLED_COLOR: RGBA = (0.11, 0.11, 0.12, 1.0)
BUTTON_BORDER_COLOR: RGBA = COLOR_BLACK
BUTTON_TEXT_COLOR: RGBA = COLOR_WHITE
BUTTON_DISABLED_TEXT_COLOR: RGBA = (0.45, 0.45, 0.48, 1.0)

BUTTON_STATUS_DEFAULT_COLOR: RGBA = (0.12, 0.55, 0.18, 1.0)
BUTTON_STATUS_OFF_COLOR: RGBA = (0.18, 0.18, 0.18, 1.0)
BUTTON_STATUS_ON_COLOR: RGBA = (0.0, 0.7, 0.15, 1.0)
BUTTON_STATUS_TEXT_COLOR: RGBA = COLOR_WHITE
BUTTON_STATUS_DISABLED_COLOR: RGBA = (0.08, 0.08, 0.09, 1.0)
BUTTON_STATUS_DISABLED_TEXT_COLOR: RGBA = (0.38, 0.38, 0.40, 1.0)


@dataclass
class Button:
    button_id: str
    text: str
    state: bool = False
    width: float = 180.0
    height: float = 42.0
    color: RGBA = BUTTON_BACKGROUND_COLOR
    hover_color: RGBA = BUTTON_HOVER_COLOR
    active_color: RGBA = BUTTON_ACTIVE_COLOR
    disabled_color: RGBA = BUTTON_DISABLED_COLOR
    text_color: RGBA = BUTTON_TEXT_COLOR
    disabled_text_color: RGBA = BUTTON_DISABLED_TEXT_COLOR
    status_text: str | None = None
    status_color: RGBA = BUTTON_STATUS_DEFAULT_COLOR
    status_text_color: RGBA = BUTTON_STATUS_TEXT_COLOR
    disabled_status_color: RGBA = BUTTON_STATUS_DISABLED_COLOR
    disabled_status_text_color: RGBA = BUTTON_STATUS_DISABLED_TEXT_COLOR
    status_width: float = 56.0
    toggle_on_click: bool = False
    enabled: Any = True
    on_click: Callable[[Button], None] | None = None

    def render(self, imgui) -> bool:
        enabled = self.is_enabled()
        pressed = imgui.invisible_button(f"##{self.button_id}", self.width, self.height)
        accepted = pressed and enabled
        if accepted:
            if self.toggle_on_click:
                self.state = not self.state
            if self.on_click is not None:
                self.on_click(self)

        x0, y0 = _xy(imgui.get_item_rect_min())
        x1, y1 = _xy(imgui.get_item_rect_max())
        status_x0 = max(x0, x1 - self.status_width)
        draw_list = imgui.get_window_draw_list()

        body_color = self.disabled_color if not enabled else self.color
        if enabled and imgui.is_item_active():
            body_color = self.active_color
        elif enabled and imgui.is_item_hovered():
            body_color = self.hover_color
        text_color = self.text_color if enabled else self.disabled_text_color
        status_color = self.status_color if enabled else self.disabled_status_color
        status_text_color = (
            self.status_text_color if enabled else self.disabled_status_text_color
        )

        draw_list.add_rect_filled(x0, y0, status_x0, y1, _rgba(imgui, body_color))
        draw_list.add_rect_filled(status_x0, y0, x1, y1, _rgba(imgui, status_color))
        draw_list.add_rect(x0, y0, x1, y1, _rgba(imgui, BUTTON_BORDER_COLOR))

        _draw_centered_text(
            imgui,
            draw_list,
            self.text,
            x0,
            y0,
            status_x0,
            y1,
            text_color,
        )
        _draw_centered_text(
            imgui,
            draw_list,
            self.status_text if self.status_text is not None else ("1" if self.state else "0"),
            status_x0,
            y0,
            x1,
            y1,
            status_text_color,
        )
        return accepted

    def is_enabled(self) -> bool:
        if callable(self.enabled):
            return bool(self.enabled())
        return bool(self.enabled)

    def set_text(self, text: str) -> None:
        self.text = text

    def set_status_text(self, text: str | None) -> None:
        self.status_text = text

    def set_state(self, state: bool) -> None:
        self.state = state

    def set_color(self, color: RGBA) -> None:
        self.color = color

    def set_colors(
        self,
        color: RGBA,
        hover_color: RGBA | None = None,
        active_color: RGBA | None = None,
    ) -> None:
        self.color = color
        if hover_color is not None:
            self.hover_color = hover_color
        if active_color is not None:
            self.active_color = active_color

    def set_status_color(self, color: RGBA) -> None:
        self.status_color = color

    def set_enabled(self, enabled: Any) -> None:
        self.enabled = enabled


def _xy(pos) -> tuple[float, float]:
    if isinstance(pos, tuple):
        return float(pos[0]), float(pos[1])
    return float(pos.x), float(pos.y)


def _rgba(imgui, color: RGBA) -> int:
    return imgui.get_color_u32_rgba(*color)


def _draw_centered_text(
    imgui,
    draw_list,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    color: RGBA,
) -> None:
    text_w, text_h = _xy(imgui.calc_text_size(text))
    draw_list.add_text(
        x0 + max(4.0, (x1 - x0 - text_w) * 0.5),
        y0 + max(2.0, (y1 - y0 - text_h) * 0.5),
        _rgba(imgui, color),
        text,
    )





def draw_table(imgui, server: LatestServer) -> None:
    imgui.text_unformatted(server.name)
    latest = server.latest
    if latest is None:
        imgui.text_disabled("waiting")
        return

    imgui.columns(2, f"{server.name}_table", border=True)
    for field in server.fields:
        imgui.text_unformatted(field)
        imgui.next_column()
        imgui.text_unformatted(str(latest.get(field, "")))
        imgui.next_column()
    imgui.columns(1)


class NidaqGraph:
    def __init__(self, server: LatestServer, max_points: int = 150) -> None:
        self.server = server
        self.max_points = max_points
        self.history: deque[list[float]] = deque(maxlen=max_points)
        self._last_seen: dict[str, float] | None = None

    def update(self) -> None:
        latest = self.server.latest
        if latest is None or latest is self._last_seen:
            return
        self._last_seen = latest
        self.history.append([float(latest[field]) for field in self.server.fields])

    def draw(self, imgui) -> None:
        self.update()
        imgui.text_unformatted("nidaq graph")
        if len(self.history) < 2:
            imgui.text_disabled("waiting")
            return

        data = np.asarray(list(self.history), dtype=np.float32)
        scale_min = float(data.min())
        scale_max = float(data.max())
        if scale_min == scale_max:
            scale_min -= 1.0
            scale_max += 1.0

        for i, field in enumerate(self.server.fields):
            values = np.ascontiguousarray(data[:, i], dtype=np.float32)
            imgui.plot_lines(
                field,
                values,
                scale_min=scale_min,
                scale_max=scale_max,
                graph_size=(0.0, 45.0),
            )
