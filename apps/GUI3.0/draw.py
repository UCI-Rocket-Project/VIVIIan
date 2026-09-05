"""Drawing primitives for the ops view.

The design is a fixed dashboard, so GUI3.0 lays itself out in absolute screen
rectangles and paints straight onto one window draw list, rather than stacking
ImGui widgets. That keeps the result faithful to the source design at any window
size, and keeps hit-testing explicit: every interactive element calls
``Painter.hit`` for its own rectangle.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import theme as T


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) * 0.5

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) * 0.5

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 - dx, self.y1 - dy)

    def pad(self, left=0.0, top=0.0, right=0.0, bottom=0.0) -> "Rect":
        return Rect(self.x0 + left, self.y0 + top, self.x1 - right, self.y1 - bottom)

    def top(self, height: float) -> "Rect":
        return Rect(self.x0, self.y0, self.x1, min(self.y1, self.y0 + height))

    def bottom(self, height: float) -> "Rect":
        return Rect(self.x0, max(self.y0, self.y1 - height), self.x1, self.y1)

    def cut_top(self, height: float) -> tuple["Rect", "Rect"]:
        """(the strip, whatever is left below it)."""
        y = min(self.y1, self.y0 + height)
        return Rect(self.x0, self.y0, self.x1, y), Rect(self.x0, y, self.x1, self.y1)

    def cut_bottom(self, height: float) -> tuple["Rect", "Rect"]:
        y = max(self.y0, self.y1 - height)
        return Rect(self.x0, y, self.x1, self.y1), Rect(self.x0, self.y0, self.x1, y)

    def cut_left(self, width: float) -> tuple["Rect", "Rect"]:
        x = min(self.x1, self.x0 + width)
        return Rect(self.x0, self.y0, x, self.y1), Rect(x, self.y0, self.x1, self.y1)

    def cut_right(self, width: float) -> tuple["Rect", "Rect"]:
        x = max(self.x0, self.x1 - width)
        return Rect(x, self.y0, self.x1, self.y1), Rect(self.x0, self.y0, x, self.y1)

    def is_empty(self) -> bool:
        return self.w <= 1.0 or self.h <= 1.0


class Hold:
    """Press-and-hold state, keyed by control.

    The design gates ARM and ABORT behind a hold rather than a click. The bar
    fills only while the pointer stays down, so a mis-click cannot arm or abort;
    releasing early resets it.
    """

    def __init__(self) -> None:
        self._started_at: dict[str, float] = {}
        self._fired: set[str] = set()

    def progress(self, key: str, held: bool, seconds: float) -> tuple[float, bool]:
        """(0..1 fill, fired-on-this-frame)."""
        now = time.monotonic()
        if not held:
            self._started_at.pop(key, None)
            self._fired.discard(key)
            return 0.0, False
        start = self._started_at.setdefault(key, now)
        fraction = min(1.0, (now - start) / max(seconds, 1e-6))
        if fraction >= 1.0 and key not in self._fired:
            self._fired.add(key)
            return 1.0, True
        return fraction, False


class Painter:
    """Everything a panel needs in order to draw itself."""

    def __init__(self, imgui, fonts, scale: float, hold: Hold | None = None) -> None:
        self.imgui = imgui
        self.fonts = fonts
        self.s = scale
        # The draw list belongs to the window that is open right now, so a
        # Painter is built per frame. Hold state has to outlive it, or a
        # press-and-hold would restart from zero on every frame.
        self.dl = imgui.get_window_draw_list()
        self.hold = hold if hold is not None else Hold()

    # -- units --------------------------------------------------------------

    def px(self, design_px: float) -> float:
        """A design-space length in real pixels."""
        return design_px * self.s

    def v2(self, x: float, y: float):
        return self.imgui.ImVec2(x, y)

    # -- shapes -------------------------------------------------------------

    def fill(self, r: Rect, color: int, rounding: float = 0.0) -> None:
        self.dl.add_rect_filled(self.v2(r.x0, r.y0), self.v2(r.x1, r.y1), color, rounding)

    def stroke(self, r: Rect, color: int, thickness: float = 1.0, rounding: float = 0.0) -> None:
        # add_rect is (p_min, p_max, col, rounding, thickness, flags).
        self.dl.add_rect(
            self.v2(r.x0, r.y0), self.v2(r.x1, r.y1), color, rounding, thickness, 0
        )

    def line(self, x0: float, y0: float, x1: float, y1: float, color: int,
             thickness: float = 1.0) -> None:
        self.dl.add_line(self.v2(x0, y0), self.v2(x1, y1), color, thickness)

    def hline(self, r: Rect, y: float, color: int, thickness: float = 1.0) -> None:
        self.line(r.x0, y, r.x1, y, color, thickness)

    def vline(self, r: Rect, x: float, color: int, thickness: float = 1.0) -> None:
        self.line(x, r.y0, x, r.y1, color, thickness)

    def dot(self, x: float, y: float, radius: float, color: int) -> None:
        self.dl.add_circle_filled(self.v2(x, y), radius, color)

    def ring(self, x: float, y: float, radius: float, color: int,
             thickness: float = 1.0) -> None:
        """An outlined circle -- the reference console's valve glyph."""
        self.dl.add_circle(self.v2(x, y), radius, color, 0, thickness)

    def square(self, x: float, y: float, size: float, color: int) -> None:
        """The small solid status square the design puts before every label."""
        half = size * 0.5
        self.fill(Rect(x - half, y - half, x + half, y + half), color)

    def polyline(self, points, color: int, thickness: float) -> None:
        if len(points) < 2:
            return
        # add_polyline is (points, col, thickness, flags) in this binding, not
        # the C++ (points, count, col, flags, thickness).
        self.dl.add_polyline(
            [self.v2(px, py) for px, py in points], color, max(0.5, thickness), 0
        )

    def poly_filled(self, points, color: int) -> None:
        if len(points) < 3:
            return
        self.dl.add_convex_poly_filled([self.v2(px, py) for px, py in points], color)

    # -- clipping -----------------------------------------------------------

    def push_clip(self, r: Rect) -> None:
        self.dl.push_clip_rect(self.v2(r.x0, r.y0), self.v2(r.x1, r.y1), True)

    def pop_clip(self) -> None:
        self.dl.pop_clip_rect()

    # -- text ---------------------------------------------------------------

    def _font(self, family: str):
        return self.fonts.mono if family == "mono" else self.fonts.display

    def measure(self, text: str, size: float, family: str = "display",
                track: float = 0.0) -> float:
        """Width in pixels of *text* at *size*, including letter tracking."""
        if not text:
            return 0.0
        real = self.px(size)
        self.imgui.push_font(self._font(family), real)
        width = float(self.imgui.calc_text_size(text).x)
        self.imgui.pop_font()
        if track:
            width += track * real * max(0, len(text) - 1)
        return width

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 11.0,
        color: int = T.INK,
        family: str = "display",
        track: float = 0.0,
        align: str = "left",
        middle: bool = False,
    ) -> float:
        """Draw *text* and return its width.

        ``track`` is letter-spacing in em. The design uses it on every small-caps
        label and ImGui has no concept of it, so tracked runs are drawn one glyph
        at a time.
        """
        if not text:
            return 0.0
        real = self.px(size)
        self.imgui.push_font(self._font(family), real)
        try:
            width = float(self.imgui.calc_text_size(text).x)
            spacing = track * real
            if track:
                width += spacing * max(0, len(text) - 1)

            draw_x = x
            if align == "center":
                draw_x = x - width * 0.5
            elif align == "right":
                draw_x = x - width
            draw_y = y - real * 0.5 if middle else y

            if not track:
                self.dl.add_text(self.v2(draw_x, draw_y), color, text)
            else:
                pen = draw_x
                for ch in text:
                    self.dl.add_text(self.v2(pen, draw_y), color, ch)
                    pen += float(self.imgui.calc_text_size(ch).x) + spacing
            return width
        finally:
            self.imgui.pop_font()

    def text_clipped(
        self,
        r: Rect,
        text: str,
        *,
        size: float = 11.0,
        color: int = T.INK,
        family: str = "display",
        track: float = 0.0,
        middle: bool = True,
    ) -> None:
        """Left-aligned text, truncated with an ellipsis to fit *r*."""
        if not text or r.w <= 0:
            return
        shown = text
        if self.measure(shown, size, family, track) > r.w:
            while shown and self.measure(shown + "...", size, family, track) > r.w:
                shown = shown[:-1]
            shown = shown + "..."
        y = r.cy if middle else r.y0
        self.text(r.x0, y, shown, size=size, color=color, family=family,
                  track=track, middle=middle)

    def text_wrapped(
        self,
        r: Rect,
        text: str,
        *,
        size: float = 13.0,
        color: int = T.INK,
        family: str = "display",
        line_height: float = 1.3,
        max_lines: int = 3,
    ) -> float:
        """Word-wrapped text from the top of *r*. Returns the height used."""
        if not text:
            return 0.0
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if self.measure(candidate, size, family) <= r.w or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) == max_lines:
            # Mark the truncation rather than ending mid-thought.
            last = lines[-1]
            if self.measure(last, size, family) > r.w or len(" ".join(lines)) < len(text):
                while last and self.measure(last + "...", size, family) > r.w:
                    last = last[: last.rfind(" ")] if " " in last else last[:-1]
                lines[-1] = last + "..."

        step = self.px(size) * line_height
        for index, line in enumerate(lines):
            self.text(r.x0, r.y0 + step * index, line, size=size, color=color,
                      family=family)
        return step * len(lines)

    # -- interaction --------------------------------------------------------

    def scroll_area(
        self,
        r: Rect,
        content_height: float,
        key: str,
        state: dict,
        *,
        keep_visible: tuple[float, float] | None = None,
    ) -> float:
        """Vertical scroll offset for a list drawn into *r*.

        A child window would bring its own draw list and its own coordinate
        space; the whole view is painted in absolute screen coordinates, so
        scrolling is done here instead. Returns the offset the caller subtracts
        from its content origin.

        ``keep_visible`` is (offset, height) of a row that must stay on screen --
        the active state, which is otherwise easy to lose in a long procedure.
        """
        maximum = max(0.0, content_height - r.h)
        offset = min(state.get(key, 0.0), maximum)

        if keep_visible is not None:
            top, height = keep_visible
            if top < offset:
                offset = top
            elif top + height > offset + r.h:
                offset = top + height - r.h
            offset = max(0.0, min(offset, maximum))

        if maximum > 0:
            imgui = self.imgui
            mouse = imgui.get_io().mouse_pos
            inside = r.x0 <= mouse.x <= r.x1 and r.y0 <= mouse.y <= r.y1
            if inside:
                wheel = float(imgui.get_io().mouse_wheel)
                if wheel:
                    offset = max(0.0, min(maximum, offset - wheel * self.px(48)))

            # A thin track, so a long list reads as scrollable at a glance.
            track = Rect(r.x1 - self.px(3), r.y0, r.x1, r.y1)
            self.fill(track, T.SURFACE)
            visible = max(0.05, r.h / content_height)
            thumb_h = r.h * visible
            thumb_y = r.y0 + (r.h - thumb_h) * (offset / maximum)
            self.fill(Rect(track.x0, thumb_y, track.x1, thumb_y + thumb_h), T.INK4)

        state[key] = offset
        return offset

    def hit(self, r: Rect, key: str) -> tuple[bool, bool, bool]:
        """(clicked, hovered, held) for an invisible button covering *r*."""
        if r.is_empty():
            return False, False, False
        imgui = self.imgui
        imgui.set_cursor_screen_pos(self.v2(r.x0, r.y0))
        clicked = imgui.invisible_button("##" + key, self.v2(r.w, r.h))
        return clicked, imgui.is_item_hovered(), imgui.is_item_active()

    def tooltip(self, text: str) -> None:
        if text and self.imgui.is_item_hovered():
            self.imgui.set_tooltip(text)

    # -- composites ---------------------------------------------------------

    def panel_header(self, r: Rect, label: str, *, right: str = "") -> Rect:
        """The 28px section header. Returns the body rect below it.

        A grey mono caption over a rule, with no accent square: the reference
        console marks its blocks by the label alone, and a coloured square in
        front of every heading spends the eye's attention on furniture.
        """
        head, body = r.cut_top(self.px(T.HEADER_H))
        self.text(head.x0 + self.px(12), head.cy, label, size=10, color=T.INK3,
                  family="mono", track=T.TRACK_WIDE, middle=True)
        if right:
            self.text(head.x1 - self.px(12), head.cy, right, size=10, color=T.INK3,
                      family="mono", align="right", middle=True)
        self.hline(head, head.y1, T.BORDER)
        return body

    def caption(self, x: float, y: float, label: str, color: int = T.INK3) -> None:
        """A bare tracked caption, for blocks that carry no header rule."""
        self.text(x, y, label, size=10, color=color, family="mono",
                  track=T.TRACK_WIDE, middle=True)

    def chip_width(self, label: str, *, dot: bool = True, size: float = 11.0) -> float:
        """The width ``chip`` would give *label*, for reserving a fixed slot."""
        pad = self.px(10)
        dot_w = self.px(15) if dot else 0.0
        return pad * 2 + dot_w + self.measure(label, size, "display", T.TRACK_WIDE)

    def chip(
        self,
        x: float,
        y: float,
        label: str,
        *,
        fg: int,
        bg: int = 0,
        dot: bool = True,
        height: float = 26.0,
        size: float = 11.0,
        min_width: float = 0.0,
    ) -> Rect:
        """Bordered pill with an optional leading square. Returns its rect.

        ``min_width`` reserves room for the longest label the chip will ever
        show, so a chip whose text changes does not shove its neighbours along
        with it.
        """
        pad = self.px(10)
        h = self.px(height)
        width = max(self.chip_width(label, dot=dot, size=size), min_width)
        r = Rect(x, y - h * 0.5, x + width, y + h * 0.5)
        if bg:
            self.fill(r, bg)
        self.stroke(r, fg)
        pen = r.x0 + pad
        if dot:
            self.square(pen + self.px(3.5), r.cy, self.px(7), fg)
            pen += self.px(15)
        self.text(pen, r.cy, label, size=size, color=fg, track=T.TRACK_WIDE, middle=True)
        return r

    def bar(
        self,
        r: Rect,
        fraction: float,
        color: int,
        *,
        redline: float | None = None,
        trough: int = T.SURFACE_ALT,
    ) -> None:
        """Horizontal fill gauge with an optional redline tick."""
        self.fill(r, trough)
        self.stroke(r, T.BORDER)
        fraction = max(0.0, min(1.0, fraction)) if math.isfinite(fraction) else 0.0
        if fraction > 0:
            self.fill(Rect(r.x0 + 1, r.y0 + 1,
                           r.x0 + 1 + (r.w - 2) * fraction, r.y1 - 1), color)
        if redline is not None and 0.0 <= redline <= 1.0:
            x = r.x0 + r.w * redline
            self.fill(Rect(x - self.px(1), r.y0 - self.px(2),
                           x + self.px(1), r.y1 + self.px(2)), T.CRIT)

    def spark(
        self,
        r: Rect,
        values,
        color: int,
        *,
        lo: float | None = None,
        hi: float | None = None,
        thickness: float = 1.6,
    ) -> None:
        """A trace across *r*.

        A gap in the data breaks the line instead of drawing a stroke through
        it: a dropout is not a reading, and joining across one would invent a
        trend that never happened.
        """
        finite = [v for v in values if v is not None and math.isfinite(v)]
        if len(finite) < 2:
            return
        low = min(finite) if lo is None else lo
        high = max(finite) if hi is None else hi
        span = max(high - low, 1e-6)
        n = len(values)
        run = []
        for i, value in enumerate(values):
            if value is None or not math.isfinite(value):
                if len(run) >= 2:
                    self.polyline(run, color, self.px(thickness))
                run = []
                continue
            x = r.x0 + (i / max(n - 1, 1)) * r.w
            y = r.y1 - max(0.0, min(1.0, (value - low) / span)) * r.h
            run.append((x, y))
        if len(run) >= 2:
            self.polyline(run, color, self.px(thickness))


def fmt(value, digits: int = 0) -> str:
    """Thousands-separated, or a dash when there is no reading."""
    if value is None or not math.isfinite(value):
        return "--"
    return format(value, ",." + str(digits) + "f")


def clock(seconds: float) -> str:
    """HH:MM:SS from a duration."""
    if not math.isfinite(seconds) or seconds < 0:
        seconds = 0.0
    total = int(seconds)
    return "%02d:%02d:%02d" % (total // 3600, (total // 60) % 60, total % 60)
