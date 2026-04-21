from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from . import theme


@dataclass(frozen=True, slots=True)
class PanelFrame:
    draw_list: Any
    outer_bounds: tuple[float, float, float, float]
    inner_bounds: tuple[float, float, float, float]


def begin_panel(
    imgui: Any,
    *,
    width: float,
    height: float,
    label: str | None = None,
    label_right: str | None = None,
    dot_color: theme.RGBA | None = None,
    padding: float = 14.0,
    label_strip_height: float = 20.0,
    crop_marks: bool = True,
) -> PanelFrame:
    draw_pos = imgui.get_cursor_screen_pos()
    imgui.dummy(width, height)

    x0, y0 = xy(draw_pos)
    x1 = x0 + width
    y1 = y0 + height
    draw_list = imgui.get_window_draw_list()
    draw_list.add_rect_filled(x0, y0, x1, y1, rgba_u32(imgui, theme.PANEL_BG))
    draw_list.add_rect(x0, y0, x1, y1, rgba_u32(imgui, theme.PANEL_BORDER))

    inner_top = y0 + padding
    if label is not None or label_right is not None:
        strip_y = y0 + label_strip_height
        draw_list.add_line(x0, strip_y, x1, strip_y, rgba_u32(imgui, theme.PANEL_BORDER), 1.0)
        if dot_color is not None:
            draw_list.add_rect_filled(
                x0 + 10.0,
                y0 + 7.0,
                x0 + 16.0,
                y0 + 13.0,
                rgba_u32(imgui, dot_color),
            )
        if label:
            text_x = x0 + (24.0 if dot_color is not None else 10.0)
            text_y = y0 + 4.0
            draw_list.add_text(text_x, text_y, rgba_u32(imgui, theme.INK_2), label)
        if label_right:
            text_width, _ = estimate_text_size(imgui, label_right)
            draw_list.add_text(
                x1 - padding - text_width,
                y0 + 4.0,
                rgba_u32(imgui, theme.INK_3),
                label_right,
            )
        inner_top = strip_y + 10.0

    if crop_marks:
        draw_crop_marks(imgui, draw_list, x0, y0, x1, y1)

    return PanelFrame(
        draw_list=draw_list,
        outer_bounds=(x0, y0, x1, y1),
        inner_bounds=(x0 + padding, inner_top, x1 - padding, y1 - padding),
    )


def draw_crop_marks(
    imgui: Any,
    draw_list: Any,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    length: float = 8.0,
) -> None:
    del y0
    color = rgba_u32(imgui, theme.INK_3)
    draw_list.add_line(x0, y1, x0 + length, y1, color, 1.0)
    draw_list.add_line(x0, y1, x0, y1 - length, color, 1.0)
    draw_list.add_line(x1, y1, x1 - length, y1, color, 1.0)
    draw_list.add_line(x1, y1, x1, y1 - length, color, 1.0)


def draw_hazard_strip(
    imgui: Any,
    draw_list: Any,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    stripe_color: theme.RGBA,
    background_color: theme.RGBA = theme.VOID,
    step_px: float = 12.0,
    stripe_thickness: float = 8.0,
) -> None:
    draw_list.add_rect_filled(x0, y0, x1, y1, rgba_u32(imgui, background_color))
    height = max(1.0, y1 - y0)
    color = rgba_u32(imgui, stripe_color)
    push_clip = getattr(draw_list, "push_clip_rect", None)
    pop_clip = getattr(draw_list, "pop_clip_rect", None)
    if callable(push_clip) and callable(pop_clip):
        push_clip(x0, y0, x1, y1, True)
    pos = x0 - height
    while pos < x1:
        draw_list.add_line(pos, y0, pos + height, y1, color, stripe_thickness)
        pos += step_px * 2.0
    if callable(pop_clip):
        pop_clip()


def draw_dashed_line(
    imgui: Any,
    draw_list: Any,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    rgba: theme.RGBA,
    dash: float = 4.0,
    gap: float = 4.0,
    thickness: float = 1.0,
) -> None:
    dx = x1 - x0
    dy = y1 - y0
    length = sqrt((dx * dx) + (dy * dy))
    if length <= 0.0:
        return
    ux = dx / length
    uy = dy / length
    color = rgba_u32(imgui, rgba)
    position = 0.0
    while position < length:
        start_x = x0 + (ux * position)
        start_y = y0 + (uy * position)
        end_pos = min(position + dash, length)
        end_x = x0 + (ux * end_pos)
        end_y = y0 + (uy * end_pos)
        draw_list.add_line(start_x, start_y, end_x, end_y, color, thickness)
        position += dash + gap


def draw_status_chip(
    imgui: Any,
    draw_list: Any,
    *,
    x0: float,
    y0: float,
    text: str,
    color: theme.RGBA,
    background: theme.RGBA = theme.PANEL_BG_2,
) -> tuple[float, float]:
    text_width, text_height = estimate_text_size(imgui, text)
    width = text_width + 18.0
    height = max(18.0, text_height + 8.0)
    draw_list.add_rect_filled(x0, y0, x0 + width, y0 + height, rgba_u32(imgui, background))
    draw_list.add_rect(x0, y0, x0 + width, y0 + height, rgba_u32(imgui, color))
    draw_list.add_rect_filled(x0 + 6.0, y0 + 6.0, x0 + 12.0, y0 + 12.0, rgba_u32(imgui, color))
    draw_list.add_text(x0 + 16.0, y0 + 4.0, rgba_u32(imgui, color), text)
    return width, height


def available_width(imgui: Any, *, fallback: float) -> float:
    region = imgui.get_content_region_available()
    width = float(region[0] if isinstance(region, tuple) else region.x)
    if width <= 0.0:
        return fallback
    return width


def estimate_text_size(imgui: Any, text: str) -> tuple[float, float]:
    calc_text_size = getattr(imgui, "calc_text_size", None)
    if callable(calc_text_size):
        size = calc_text_size(text)
        return xy(size)
    return (max(6.0, len(text) * 6.0), 10.0)


def rgba_u32(imgui: Any, rgba: theme.RGBA) -> int:
    converter = getattr(imgui, "get_color_u32_rgba", None)
    if callable(converter):
        return converter(*rgba)
    r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
    return (a << 24) | (b << 16) | (g << 8) | r


def xy(value: Any) -> tuple[float, float]:
    if isinstance(value, tuple):
        return float(value[0]), float(value[1])
    return float(value.x), float(value.y)


__all__ = [
    "PanelFrame",
    "available_width",
    "begin_panel",
    "draw_crop_marks",
    "draw_dashed_line",
    "draw_hazard_strip",
    "draw_status_chip",
    "estimate_text_size",
    "rgba_u32",
    "xy",
]
