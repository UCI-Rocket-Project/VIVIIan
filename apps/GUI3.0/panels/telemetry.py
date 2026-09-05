"""Right column: tank pressures, the decay result, and the remaining readouts.

The design gives this column a headline metric in oversized type -- thrust,
because it is a hotfire console. The equivalent here is the decay rate: it is
the number this procedure exists to produce, and the number the pass/fail
decision is taken on. Thrust is still a live channel, so it keeps a row further
down rather than the headline slot.
"""
from __future__ import annotations

import math

import theme as T
import model as M
from draw import Painter, Rect, fmt

from procedures.pressure_decay import DECAY_LIMIT_PSI_PER_MIN

GAUGE_BLOCK_H = 158.0
DECAY_BLOCK_H = 176.0
ROW_H = 29.0

READOUT_CHANNELS = (
    M.VENT, M.LOX_ING, M.LNG_ING, M.LOX_POT, M.LNG_POT, M.PT10, M.THRUST,
)
UNITS = {M.THRUST: "lbf"}


def draw(p: Painter, r: Rect, m) -> None:
    gauges, rest = r.cut_top(p.px(T.HEADER_H) + p.px(GAUGE_BLOCK_H))
    decay, readouts = rest.cut_top(p.px(DECAY_BLOCK_H))

    _tanks(p, gauges, m)
    _decay(p, decay, m)
    _readouts(p, readouts, m)


# --------------------------------------------------------------------------


def _tanks(p: Painter, r: Rect, m) -> None:
    body = p.panel_header(r, "TANK PRESSURES")
    p.hline(r, r.y1, T.BORDER)

    channels = (M.COPV, M.LOX_TANK, M.LNG_TANK)
    inner = body.pad(left=p.px(14), top=p.px(13), right=p.px(14), bottom=p.px(13))
    slot_h = inner.h / len(channels)

    for index, channel in enumerate(channels):
        slot = Rect(inner.x0, inner.y0 + slot_h * index,
                    inner.x1, inner.y0 + slot_h * (index + 1))
        _gauge(p, slot, m, channel)


def _gauge(p: Painter, r: Rect, m, channel: str) -> None:
    value = m.value(channel)
    color = m.severity_color(channel)
    full = M.GAUGE_FULL_SCALE[channel]
    redline = M.REDLINE[channel]

    clicked, hovered, _ = p.hit(r, "gauge_" + channel)

    label_y = r.y0 + p.px(8)
    p.text(r.x0, label_y, M.DISPLAY_NAME[channel], size=11, color=T.INK2,
           track=0.1, middle=True)
    p.text(r.x1, label_y, "PSI", size=10, color=T.INK3, family="mono",
           align="right", middle=True)
    p.text(r.x1 - p.px(26), label_y, fmt(value, 0), size=22, color=color,
           family="mono", align="right", middle=True)

    bar = Rect(r.x0, label_y + p.px(12), r.x1, label_y + p.px(23))
    fraction = (value / full) if math.isfinite(value) else 0.0
    p.bar(bar, fraction, color, redline=redline / full)
    if m.selected == channel:
        p.stroke(Rect(bar.x0 - p.px(3), bar.y0 - p.px(3),
                      bar.x1 + p.px(3), bar.y1 + p.px(3)), T.fade(color, 0.5))

    foot_y = bar.y1 + p.px(9)
    p.text(r.x1, foot_y, fmt(full, 0), size=9, color=T.INK3, family="mono",
           align="right", middle=True)

    if hovered:
        p.tooltip("%s\nabort limit %s psig\nclick to trend"
                  % (M.DISPLAY_NAME[channel], fmt(redline, 0)))
    if clicked:
        m.selected = channel


# --------------------------------------------------------------------------


def _decay(p: Painter, r: Rect, m) -> None:
    p.hline(r, r.y1, T.BORDER)
    inner = r.pad(left=p.px(14), top=p.px(14), right=p.px(14), bottom=p.px(14))

    p.caption(inner.x0, inner.y0 + p.px(5), "PRESSURE DECAY - WORST SECTION")

    rows = m.decay_rows()
    finite = [row for row in rows if math.isfinite(row["slope"])]
    worst = max((row["slope"] for row in finite), default=float("nan"))
    ready = m.ctx.slope_ready()

    verdict, verdict_color = m.decay_verdict()
    if verdict:
        headline_color = verdict_color
    elif math.isfinite(worst) and worst > DECAY_LIMIT_PSI_PER_MIN:
        headline_color = T.CRIT
    elif ready:
        headline_color = T.ACID
    else:
        headline_color = T.INK3

    value_y = inner.y0 + p.px(34)
    width = p.text(inner.x0, value_y, fmt(worst, 2), size=40, color=headline_color,
                   family="mono", middle=True)
    p.text(inner.x0 + width + p.px(9), value_y + p.px(6), "psi/min", size=11,
           color=T.INK3, family="mono", middle=True)

    right_text = verdict if verdict else ("MEASURING" if ready else "WINDOW FILLING")
    p.text(inner.x1, value_y - p.px(4), right_text, size=11,
           color=verdict_color if verdict else T.INK2, family="mono",
           align="right", middle=True)
    p.text(inner.x1, value_y + p.px(11), "limit %s" % fmt(DECAY_LIMIT_PSI_PER_MIN, 1),
           size=10, color=T.INK3, family="mono", align="right", middle=True)

    # Window progress: until it is full the slope is not a decay measurement.
    span, window = m.decay_progress()
    track = Rect(inner.x0, inner.y0 + p.px(56), inner.x1, inner.y0 + p.px(60))
    p.bar(track, span / window if window else 0.0,
          T.ACID if ready else T.INK4, trough=T.SURFACE_ALT)
    if not ready:
        p.text(inner.x0, inner.y0 + p.px(70),
               "window %s of %s s" % (fmt(span, 0), fmt(window, 0)),
               size=9, color=T.INK3, family="mono", middle=True)

    # Per-section slopes.
    row_h = p.px(19)
    y = inner.y1 - row_h * len(rows)
    for row in rows:
        rect = Rect(inner.x0, y, inner.x1, y + row_h)
        y += row_h
        p.text(rect.x0, rect.cy, row["name"], size=10, color=T.INK3,
               family="mono", middle=True)
        p.text(rect.x0 + p.px(96), rect.cy, fmt(row["psi"], 0), size=11,
               color=T.INK2, family="mono", align="right", middle=True)
        p.text(rect.x0 + p.px(168), rect.cy, fmt(row["slope"], 2), size=11,
               color=row["color"], family="mono", align="right", middle=True)
        if row["recorded"] is not None:
            p.text(rect.x1, rect.cy, "rec " + fmt(row["recorded"], 2), size=10,
                   color=T.INK2, family="mono", align="right", middle=True)


# --------------------------------------------------------------------------


def _readouts(p: Painter, r: Rect, m) -> None:
    body = p.panel_header(r, "FEED & INGRESS")

    p.push_clip(body)
    row_h = p.px(ROW_H)
    y = body.y0 + p.px(3)
    for channel in READOUT_CHANNELS:
        rect = Rect(body.x0, y, body.x1, y + row_h)
        y += row_h
        if rect.y0 > body.y1:
            break

        clicked, hovered, _ = p.hit(rect, "readout_" + channel)
        if m.selected == channel:
            p.fill(rect, T.SURFACE)
        elif hovered:
            p.fill(rect, T.fade(T.INK, 0.04))
        p.hline(rect, rect.y1, T.ROW_BORDER)

        p.square(rect.x0 + p.px(14), rect.cy, p.px(6), M.SERIES_COLOR[channel])
        p.text(rect.x0 + p.px(24), rect.cy, M.DISPLAY_NAME[channel], size=11,
               color=T.INK3, family="mono", middle=True)
        unit = UNITS.get(channel, "PSI")
        p.text(rect.x1 - p.px(52), rect.cy, fmt(m.value(channel), 1), size=14,
               color=T.INK, family="mono", align="right", middle=True)
        p.text(rect.x1 - p.px(14), rect.cy, unit, size=10, color=T.INK3,
               family="mono", align="right", middle=True)
        p.tooltip("click to trend")
        if clicked:
            m.selected = channel
    p.pop_clip()
