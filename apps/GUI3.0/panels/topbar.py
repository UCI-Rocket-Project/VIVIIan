"""Top bar, page tabs and alarm banner.

The design's clock counts down to ignition. A pressure-decay run has no T-zero,
so the big number is time in the current state, which is the clock an operator
running this procedure is actually watching -- several states are held open by a
watchdog measured from exactly that.
"""
from __future__ import annotations

import math

import theme as T
from draw import Painter, Rect, clock


def _slot(p: Painter, text: str, sample: str, size: float,
          family: str = "mono") -> float:
    """Width to reserve for a live value.

    The wider of a worst-case *sample* and the value itself, so a number that
    outgrows what was reserved for it pushes its neighbours rather than
    overlapping them.
    """
    return max(p.measure(sample, size, family), p.measure(text, size, family))


TABS = (("ops", "Ops"), ("commands", "Commands"))


def draw(p: Painter, r: Rect, m, servers) -> None:
    gse_server, echo_server, nidaq_server = servers
    p.fill(r, T.PANEL)
    p.hline(r, r.y1, T.BORDER)

    x = r.x0 + p.px(18)
    p.square(x + p.px(5), r.cy, p.px(10), T.INK)
    x += p.px(26)
    x += p.text(x, r.cy, "UCIRPL", size=14, color=T.INK, track=0.18, middle=True)

    # -- tabs ---------------------------------------------------------------
    # Plain text items beside the wordmark, the way the reference console does
    # its nav: the active one is white, the rest grey, and nothing is boxed.
    x += p.px(22)
    for key, label in TABS:
        active = m.tab == key
        width = p.measure(label, 12, "display")
        hit = Rect(x - p.px(6), r.y0, x + width + p.px(6), r.y1)
        clicked, hovered, _ = p.hit(hit, "tab_" + key)
        color = T.INK if active else (T.INK2 if hovered else T.INK3)
        p.text(x, r.cy, label, size=12, color=color, middle=True)
        if active:
            p.line(x, r.y1 - p.px(2), x + width, r.y1 - p.px(2), T.INK, p.px(1.5))
        # The valve tiles are a tab away now, so a valve the board disagrees
        # with has to be visible from here.
        if key == "commands" and m.any_mismatch():
            p.dot(x + width + p.px(7), r.cy - p.px(6), p.px(3), T.CRIT)
        if clicked:
            m.tab = key
        x += width + p.px(24)

    x += p.px(2)
    p.fill(Rect(x, r.cy - p.px(10), x + 1, r.cy + p.px(10)), T.BORDER)
    x += p.px(16)

    x += p.text(x, r.cy, m.dispatcher.machine.name.upper(), size=11, color=T.INK3,
                family="mono", track=0.06, middle=True)

    x += p.px(16)
    fg, _ = m.phase_colors()
    # Reserve the widest ordinary mode name so the ARMED chip beside it holds
    # still as the dispatcher changes mode. An abort is allowed to grow the
    # chip past that: it happens once, and it should be impossible to miss.
    chip = p.chip(x, r.cy, m.phase_name(), fg=fg,
                  min_width=p.chip_width("AMBIGUOUS"))
    x = chip.x1 + p.px(12)

    armed_fg = T.ALERT if m.dispatcher.armed else T.INK3
    p.chip(x, r.cy, "ARMED" if m.dispatcher.armed else "DISARMED", fg=armed_fg)

    # -- right cluster ------------------------------------------------------
    # This side is laid out right to left, so a field sized from its own text
    # drags everything left of it -- the big clock included -- sideways on the
    # frame its value gains or loses a character. The feed ages alone do that
    # several times a second. Every live field therefore advances the cursor by
    # a slot measured from a worst-case sample instead, and is drawn
    # right-aligned inside it, so the digits move and the layout does not.
    right = r.x1 - p.px(18)

    pad = p.px(9)
    for name, value, color in reversed(
        m.feed_links(gse_server, echo_server, nidaq_server)
    ):
        width = (pad + p.px(10) + p.measure(name, 10, "mono") + p.px(6)
                 + _slot(p, value, "8888ms", 10) + pad)
        box = Rect(right - width, r.cy - p.px(12), right, r.cy + p.px(12))
        p.stroke(box, T.BORDER)
        p.square(box.x0 + pad + p.px(2.5), box.cy, p.px(5), color)
        p.text(box.x0 + pad + p.px(10), box.cy, name, size=10, color=T.INK2,
               family="mono", middle=True)
        # Right-aligned inside its own chip, so the units stay put too.
        p.text(box.x1 - pad, box.cy, value, size=10, color=T.INK2,
               family="mono", align="right", middle=True)
        right = box.x0 - p.px(6)

    right -= p.px(10)
    p.fill(Rect(right, r.cy - p.px(10), right + 1, r.cy + p.px(10)), T.BORDER)
    right -= p.px(16)

    in_state = m.ctx.in_state_for()
    big = clock(in_state)
    color = T.CRIT if m.aborted else T.INK
    p.text(right, r.cy, big, size=26, color=color, family="mono",
           align="right", middle=True)
    right -= _slot(p, big, "88:88:88", 26) + p.px(8)
    p.text(right, r.cy, "IN STATE", size=9, color=T.INK3, family="mono",
           align="right", middle=True)


def draw_alarm(p: Painter, r: Rect, m) -> None:
    """The banner. Returns nothing; the caller decides whether there is one."""
    alarm = m.alarm()
    if alarm is None:
        return

    p.fill(r, T.CRIT_BG if alarm.color == T.CRIT else T.ALERT_BG)
    p.hline(r, r.y1, alarm.color)

    # The design pulses the marker. Tie it to wall time so the rate does not
    # follow the frame rate.
    pulse = 0.35 + 0.65 * abs(math.sin(p.imgui.get_time() * math.pi))
    p.square(r.x0 + p.px(22), r.cy, p.px(8), T.fade(alarm.color, pulse))

    x = r.x0 + p.px(34)
    x += p.text(x, r.cy, alarm.title, size=12, color=alarm.color,
                track=0.16, middle=True)
    x += p.px(12)

    button_w = p.px(110)
    detail_rect = Rect(x, r.y0, r.x1 - button_w - p.px(28), r.y1)
    p.text_clipped(detail_rect, alarm.detail, size=11, color=T.INK2, family="mono")

    box = Rect(r.x1 - p.px(18) - button_w, r.cy - p.px(11),
               r.x1 - p.px(18), r.cy + p.px(11))
    clicked, hovered, _ = p.hit(box, "alarm_ack")
    if hovered:
        p.fill(box, T.fade(alarm.color, 0.12))
    p.stroke(box, alarm.color)
    p.text(box.cx, box.cy, "ACKNOWLEDGE", size=10, color=alarm.color,
           track=0.12, align="center", middle=True)
    if clicked:
        m.acknowledge()
