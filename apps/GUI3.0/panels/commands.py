"""Valve and ECU commands, and the arm / resume / abort controls.

Two rules from ``docs/state-machine.md`` shape this panel:

    1. Never take manual control away.
    2. Make sure manual control is never dangerous.

So every valve stays clickable in every mode, armed or not, and the friction is
in the gesture instead: ARM and ABORT are press-and-hold, as the design draws
them. A tile that disagrees with the board says so on its own face rather than
only in the alarm banner, because the operator's hand is already here.

The two grids are a page of their own on the Commands tab. The arm / resume /
abort row stays on the Ops tab: those are how the procedure is run and stopped,
and ABORT in particular is never a tab away.
"""
from __future__ import annotations

import time

import theme as T
import model as M
from draw import Painter, Rect

from gui_gse2v1 import GSE2V1_COMMAND_BUTTONS
from state_machine import DispatcherMode

TILE_H = 46.0
GAP = 7.0
PAGE_TILE_H = 62.0
PAGE_W = 1360.0
COLUMNS = 5
CONTROL_W = 300.0
ABORT_W = 190.0


def draw_page(p: Painter, r: Rect, m) -> None:
    """The Commands tab: every raw command, at a size you can hit.

    Each block is sized to its own rows and stacked from the top, rather than
    given a share of the page: a grid stretched to fill the height would put the
    igniters somewhere different every time the valve list changed.
    """
    inner = r.pad(left=p.px(28), top=p.px(22), right=p.px(28), bottom=p.px(22))
    inner = Rect(inner.x0, inner.y0, min(inner.x1, inner.x0 + p.px(PAGE_W)),
                 inner.y1)

    gse, rest = inner.cut_top(_block_height(p, len(M.GSE_VALVES), COLUMNS))
    _grid_block(p, gse, m, "GSE COMMANDS", M.GSE_VALVES, columns=COLUMNS,
                tile_h=PAGE_TILE_H)

    ecu, _ = rest.pad(top=p.px(30)).cut_top(
        _block_height(p, len(M.ECU_COMMANDS), COLUMNS))
    _grid_block(p, ecu, m, "ECU COMMANDS", M.ECU_COMMANDS, columns=COLUMNS,
                tile_h=PAGE_TILE_H)


def _block_height(p: Painter, count: int, columns: int) -> float:
    rows = max(1, (count + columns - 1) // columns)
    return p.px(22) + p.px(PAGE_TILE_H) * rows + p.px(GAP) * (rows - 1)


def draw_controls(p: Painter, r: Rect, m) -> None:
    """The Ops tab's bottom strip: arm, resume, abort."""
    p.fill(r, T.PANEL)
    p.hline(r, r.y0, T.BORDER)

    inner = r.pad(left=p.px(14), top=p.px(11), right=p.px(14), bottom=p.px(11))
    abort_rect, rest = inner.cut_right(p.px(ABORT_W))
    resume_rect, rest = Rect(rest.x0, rest.y0, rest.x1 - p.px(9), rest.y1)         .cut_right(p.px(CONTROL_W))
    arm_rect, _ = Rect(rest.x0, rest.y0, rest.x1 - p.px(9), rest.y1)         .cut_right(p.px(CONTROL_W))

    _arm(p, arm_rect, m)
    _resume(p, resume_rect, m)
    _abort(p, abort_rect, m)


# --------------------------------------------------------------------------


def _grid_block(p: Painter, r: Rect, m, title: str, button_ids, *, columns: int,
                tile_h: float = TILE_H) -> None:
    p.text(r.x0, r.y0 + p.px(5), title, size=10, color=T.INK3,
           family="mono", track=T.TRACK_WIDE, middle=True)

    grid = Rect(r.x0, r.y0 + p.px(22), r.x1, r.y1)
    rows = (len(button_ids) + columns - 1) // columns
    gap = p.px(GAP)
    tile_w = (grid.w - gap * (columns - 1)) / columns
    tile_h = min(p.px(tile_h), (grid.h - gap * (rows - 1)) / max(rows, 1))

    for index, button_id in enumerate(button_ids):
        row, column = divmod(index, columns)
        rect = Rect(
            grid.x0 + (tile_w + gap) * column,
            grid.y0 + (tile_h + gap) * row,
            grid.x0 + (tile_w + gap) * column + tile_w,
            grid.y0 + (tile_h + gap) * row + tile_h,
        )
        _tile(p, rect, m, button_id)


def _tile(p: Painter, r: Rect, m, button_id: str) -> None:
    config = GSE2V1_COMMAND_BUTTONS.get(button_id, {})
    button = config.get("button")
    if button is None:
        return

    _expire_momentary(button)

    energised = bool(button.state)
    is_open = M.valve_is_open(energised, button_id)
    agrees = m.valve_agrees(button_id)
    enabled = bool(button.is_enabled())

    if agrees is False:
        border, led, fg = T.CRIT, T.CRIT, T.CRIT
        background = T.CRIT_BG
        feedback, feedback_color = "board disagrees", T.CRIT
    elif agrees is None:
        border, led = T.WARN, T.WARN
        fg = T.INK if enabled else T.INK4
        background = T.BG
        feedback, feedback_color = "no echo", T.WARN
    elif is_open:
        border, led, fg = T.ACID, T.ACID, T.ACID
        background = T.ACID_BG
        feedback, feedback_color = "fb open", T.INK3
    else:
        border, led = T.BORDER, T.BORDER
        fg = T.INK if enabled else T.INK4
        background = T.BG
        feedback, feedback_color = "fb closed", T.INK3

    clicked, hovered, _ = p.hit(r, "cmd_" + button_id)
    if hovered and enabled:
        background = T.mix(background, T.INK, 0.08)

    p.fill(r, background)
    p.stroke(r, border)
    p.fill(Rect(r.x0, r.y0, r.x0 + p.px(4), r.y1), led)

    state_text = "OPEN" if is_open else "CLOSED"
    if button.momentary_seconds is not None:
        state_text = "PULSE" if energised else "READY"
    state_w = p.measure(state_text, 10, "mono")

    label_rect = Rect(r.x0 + p.px(12), r.y0 + p.px(6),
                      r.x1 - state_w - p.px(18), r.y0 + r.h * 0.5)
    p.text_clipped(label_rect, m.valve_label(button_id).upper(), size=11,
                   color=fg, track=T.TRACK_TIGHT)
    p.text(r.x0 + p.px(12), r.y1 - p.px(13), feedback, size=9,
           color=feedback_color, family="mono", middle=True)
    p.text(r.x1 - p.px(9), r.cy, state_text, size=10,
           color=fg if is_open or agrees is False else T.INK3,
           family="mono", align="right", middle=True)

    p.tooltip(_tile_tooltip(m, button_id, enabled))

    if clicked and enabled:
        _actuate(button)


def _tile_tooltip(m, button_id: str, enabled: bool) -> str:
    lines = [m.valve_label(button_id)]
    if button_id in M.NORMALLY_OPEN:
        lines.append("Normally open: de-energised is OPEN.")
    actual = m.actual(button_id)
    lines.append("commanded %s  /  board %s"
                 % (m.commanded(button_id),
                    "no report" if actual is None else actual))
    if not enabled:
        lines.append("Disabled: the command link is not connected.")
    elif m.dispatcher.armed:
        lines.append("Armed: a manual move here interrupts the running operation.")
    return "\n".join(lines)


def _expire_momentary(button) -> None:
    """Release a momentary command once its pulse is up.

    ``Button.render`` does this in GUI2.1, and GUI3.0 never calls it, so the
    pulse would otherwise latch on forever.
    """
    if button.momentary_until is not None and button.momentary_until < time.monotonic():
        was_on = button.state
        button.momentary_until = None
        button.state = False
        if was_on and button.on_click is not None:
            button.on_click(button)


def _actuate(button) -> None:
    """Drive a button exactly the way GUI2.1's own render path does."""
    if button.momentary_seconds is not None:
        button.state = True
        button.momentary_until = time.monotonic() + button.momentary_seconds
    elif button.toggle_on_click:
        button.state = not button.state
    if button.on_click is not None:
        button.on_click(button)


# --------------------------------------------------------------------------


def _arm(p: Painter, r: Rect, m) -> None:
    dispatcher = m.dispatcher
    armed = dispatcher.armed
    blocked = bool(dispatcher.problems)

    clicked, hovered, held = p.hit(r, "arm")
    if armed:
        fraction, fired = 1.0, False
        if clicked:
            dispatcher.disarm()
    elif blocked:
        fraction, fired = 0.0, False
        if clicked:
            m.events.add("refusing to arm: validation problems outstanding",
                         source="ARM", severity="warn")
    else:
        fraction, fired = p.hold.progress("arm", held, M.HOLD_ARM_SECONDS)
        if fired:
            dispatcher.arm()

    if blocked and not armed:
        background, border, fg, dot = T.BG, T.BORDER, T.INK4, T.INK4
        state, hint = "BLOCKED", "validation problems outstanding"
    elif armed:
        background, border, fg, dot = T.ALERT_BG, T.ALERT, T.ALERT, T.ALERT
        state, hint = "ARMED", "auto transitions live"
    else:
        background = T.mix(T.BG, T.INK, 0.07) if hovered else T.BG
        border, fg, dot = T.BORDER, T.INK, T.INK4
        state, hint = "SAFE", "press and hold %.1f s" % M.HOLD_ARM_SECONDS

    p.fill(r, background)
    p.stroke(r, border)
    p.square(r.x0 + p.px(13) + p.px(4.5), r.cy, p.px(9), dot)
    p.text(r.x0 + p.px(33), r.cy - p.px(7), "ARM", size=13, color=fg,
           track=0.12, middle=True)
    state_w = p.measure(state, 11, "mono") + p.px(20)
    p.text_clipped(Rect(r.x0 + p.px(33), r.cy + p.px(2),
                        r.x1 - state_w, r.cy + p.px(14)),
                   hint, size=9, color=T.INK3, family="mono")
    p.text(r.x1 - p.px(13), r.cy, state, size=11, color=fg, family="mono",
           align="right", middle=True)
    if fraction > 0 and not armed:
        p.fill(Rect(r.x0, r.y1 - p.px(3), r.x0 + r.w * fraction, r.y1), fg)


def _resume(p: Painter, r: Rect, m) -> None:
    """Continue a suspended operation.

    This is the slot the design gives the igniter. A decay procedure has no
    ignition; what it does have is an operation suspended by a manual command,
    which is the one thing in this panel that must be picked up again by hand.
    """
    dispatcher = m.dispatcher
    available = dispatcher.mode is DispatcherMode.SUSPENDED

    clicked, hovered, _ = p.hit(r, "resume")
    if available:
        background = T.mix(T.ALERT_BG, T.ALERT, 0.12) if hovered else T.ALERT_BG
        border = fg = dot = T.ALERT
        state, hint = "READY", "operation suspended by a manual command"
    else:
        background, border = T.BG, T.BORDER
        fg = dot = T.INK4
        state = "IDLE"
        hint = "nothing suspended"

    p.fill(r, background)
    p.stroke(r, border)
    p.square(r.x0 + p.px(13) + p.px(4.5), r.cy, p.px(9), dot)
    p.text(r.x0 + p.px(33), r.cy - p.px(7), "RESUME", size=13, color=fg,
           track=0.12, middle=True)
    state_w = p.measure(state, 11, "mono") + p.px(20)
    p.text_clipped(Rect(r.x0 + p.px(33), r.cy + p.px(2),
                        r.x1 - state_w, r.cy + p.px(14)),
                   hint, size=9, color=T.INK3, family="mono")
    p.text(r.x1 - p.px(13), r.cy, state, size=11, color=fg, family="mono",
           align="right", middle=True)

    if clicked and available:
        dispatcher.resume()


def _abort(p: Painter, r: Rect, m) -> None:
    clicked, hovered, held = p.hit(r, "abort")
    fraction, fired = p.hold.progress("abort", held, M.HOLD_ABORT_SECONDS)
    if fired:
        m.dispatcher.panic()

    p.fill(r, T.mix(T.CRIT_BG, T.CRIT, 0.12) if hovered else T.CRIT_BG)
    p.stroke(r, T.CRIT, p.px(2))

    if fraction > 0:
        p.fill(Rect(r.x0, r.y0, r.x0 + r.w * fraction, r.y1), T.fade(T.CRIT, 0.22))

    p.text(r.cx, r.cy - p.px(9), "ABORT", size=28, color=T.CRIT, track=0.1,
           align="center", middle=True)
    active = m.aborted
    hint = ("ABORT ACTIVE - vents open" if active
            else "press and hold %.1f s" % M.HOLD_ABORT_SECONDS)
    p.text(r.cx, r.cy + p.px(14), hint, size=9, color=T.INK2, family="mono",
           align="center", middle=True)
    p.tooltip("Applies this state's safe-out: abort configuration, alarm on.")

    # Hazard stripe along the bottom edge.
    stripe_h = p.px(6)
    step = p.px(18)
    x = r.x0
    p.push_clip(Rect(r.x0, r.y1 - stripe_h, r.x1, r.y1))
    while x < r.x1 + step:
        p.poly_filled(
            [
                (x, r.y1),
                (x + stripe_h, r.y1 - stripe_h),
                (x + stripe_h + p.px(8), r.y1 - stripe_h),
                (x + p.px(8), r.y1),
            ],
            T.CRIT,
        )
        x += step
    p.pop_clip()
