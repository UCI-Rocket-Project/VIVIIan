"""Bottom strip charts.

Three panels, as the design has them. The first two are fixed groups; the third
follows whatever the operator last clicked -- on the P&ID, on a tank gauge, or in
a readout row -- which is what makes "click a tag to trend it" mean anything.

The selected series is drawn heavier and fully opaque in every panel it appears
in, so picking a channel highlights it everywhere at once.
"""
from __future__ import annotations

import math

import theme as T
import model as M
from draw import Painter, Rect, fmt

GROUPS = (
    ("UPPER FEED", (M.COPV, M.LOX_TANK, M.LNG_TANK, M.VENT)),
    ("INGRESS & POTS", (M.LOX_ING, M.LNG_ING, M.LOX_POT, M.LNG_POT)),
)


def draw(p: Painter, r: Rect, m) -> None:
    p.hline(r, r.y0, T.BORDER)
    panels = list(GROUPS) + [("SELECTED - " + M.DISPLAY_NAME.get(m.selected, m.selected),
                              (m.selected,))]

    width = r.w / len(panels)
    for index, (title, channels) in enumerate(panels):
        rect = Rect(r.x0 + width * index, r.y0 + 1,
                    r.x0 + width * (index + 1), r.y1)
        _strip(p, rect, m, title, channels, index)
        p.vline(rect, rect.x1, T.BORDER)


def _strip(p: Painter, r: Rect, m, title: str, channels, index: int) -> None:
    head, body = r.cut_top(p.px(26))
    p.hline(head, head.y1, T.BORDER)

    p.text(head.x0 + p.px(12), head.cy, title, size=10, color=T.INK3,
           family="mono", track=T.TRACK_WIDE, middle=True)

    span = max((m.history[c].span_seconds() for c in channels if c in m.history.channels),
               default=0.0)
    p.text(head.x1 - p.px(12), head.cy, "%s s" % fmt(span, 0), size=9,
           color=T.INK3, family="mono", align="right", middle=True)

    # Series keys, right to left so they end flush against the window label.
    x = head.x1 - p.px(12) - p.measure("%s s" % fmt(span, 0), 9, "mono") - p.px(14)
    for channel in reversed(channels):
        label = M.DISPLAY_NAME.get(channel, channel)
        width = p.measure(label, 9, "mono")
        selected = m.selected == channel
        alpha = 1.0 if selected else 0.45
        rect = Rect(x - width - p.px(11), head.y0, x, head.y1)
        clicked, _, _ = p.hit(rect, "key_%d_%s" % (index, channel))
        p.text(x, head.cy, label, size=9, color=T.fade(T.INK2, alpha),
               family="mono", align="right", middle=True)
        p.square(x - width - p.px(7), head.cy, p.px(7),
                 T.fade(M.SERIES_COLOR.get(channel, T.INK2), alpha))
        if clicked:
            m.selected = channel
        x = rect.x0 - p.px(10)

    p.fill(body, T.CHART_BG)
    p.push_clip(body)
    _grid(p, body)

    low, high = m.history.bounds(tuple(c for c in channels if c in m.history.channels))
    plot = body.inset(p.px(2))
    for channel in channels:
        if channel not in m.history.channels:
            continue
        selected = m.selected == channel
        p.spark(
            plot,
            m.history[channel].series(),
            T.fade(M.SERIES_COLOR.get(channel, T.INK2), 1.0 if selected else 0.5),
            lo=low,
            hi=high,
            thickness=2.2 if selected else 1.4,
        )

    p.text(body.x0 + p.px(8), body.y0 + p.px(9), fmt(high, 0), size=9,
           color=T.INK3, family="mono", middle=True)
    p.text(body.x0 + p.px(8), body.y1 - p.px(9), fmt(low, 0), size=9,
           color=T.INK3, family="mono", middle=True)
    p.pop_clip()


def _grid(p: Painter, r: Rect) -> None:
    for fraction in (0.25, 0.5, 0.75):
        x = r.x0 + r.w * fraction
        p.line(x, r.y0, x, r.y1, T.CHART_GRID, 1.0)
    for fraction in (1 / 3, 2 / 3):
        y = r.y0 + r.h * fraction
        p.line(r.x0, y, r.x1, y, T.CHART_GRID, 1.0)
