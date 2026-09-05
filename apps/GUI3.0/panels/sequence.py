"""Left column: the sequence, the active step, its operations, and the log.

This is the part of the design that only works because the machine underneath is
procedure-aware. The rows are the real states and the criteria under the active
one are the guards the dispatcher is evaluating this cycle, so the operator can
see why the machine is about to move before it moves. The operations that state
offers sit directly under the guards it is watching, because the two are read
together: what the machine is waiting for, and what you can do about it.
"""
from __future__ import annotations

import math

import theme as T
import events as E
from draw import Painter, Rect, fmt
from model import pretty_state

from state_machine import DispatcherMode

ROW_H = 26.0
STEP_BLOCK_H = 132.0
WATCH_ROW_H = 19.0
OPS_ROW_H = 40.0
OPS_GAP = 7.0
BLANK_LINE = "\n\n"
LOG_ROW_H = 17.0


def draw(p: Painter, r: Rect, m) -> None:
    p.vline(r, r.x1, T.BORDER)

    body = Rect(r.x0, r.y0, r.x1 - 1, r.y1)
    # Evaluated once and passed down: the guards run real predicates over the
    # context, and the block has to be sized to however many this state has.
    watched = m.watched()
    step_h = p.px(STEP_BLOCK_H) + p.px(WATCH_ROW_H) * max(0, len(watched) - 1)

    # A tie outranks the state's own operations: the dispatcher has stopped
    # rather than pick one, so the only useful control is the choice.
    ambiguous = m.mode is DispatcherMode.AMBIGUOUS
    operations = list(m.dispatcher.tie_candidates) if ambiguous else list(m.manual_operations())
    ops_h = (p.px(T.HEADER_H) + p.px(12)
             + p.px(OPS_ROW_H) * max(1, len(operations))
             + p.px(OPS_GAP) * max(0, len(operations) - 1))

    remaining = body.h - step_h - ops_h
    sequence_h = max(p.px(120), remaining * 0.46)

    sequence_rect, rest = body.cut_top(sequence_h)
    step_rect, rest = rest.cut_top(step_h)
    ops_rect, log_rect = rest.cut_top(ops_h)

    _sequence(p, sequence_rect, m)
    _active_step(p, step_rect, m, watched)
    _operations(p, ops_rect, m, operations, ambiguous)
    _event_log(p, log_rect, m)


# --------------------------------------------------------------------------
# Sequence


def _sequence(p: Painter, r: Rect, m) -> None:
    body = p.panel_header(r, "SEQUENCE")
    p.hline(r, r.y1, T.BORDER)
    body = Rect(body.x0, body.y0, body.x1, r.y1 - 1)

    rows = m.sequence_rows()
    row_h = p.px(ROW_H)
    content_h = row_h * len(rows) + p.px(10)

    active_index = next((i for i, row in enumerate(rows) if row["active"]), 0)
    offset = p.scroll_area(
        body, content_h, "sequence", m.scroll,
        keep_visible=(active_index * row_h, row_h),
    )

    p.push_clip(body)
    y = body.y0 + p.px(5) - offset
    for row in rows:
        rect = Rect(body.x0, y, body.x1, y + row_h)
        y += row_h
        if rect.y1 < body.y0 or rect.y0 > body.y1:
            continue

        clicked, hovered, _ = p.hit(rect, "seq_" + row["name"])
        if row["active"]:
            p.fill(rect, T.SURFACE)
        elif hovered:
            p.fill(rect, T.fade(T.INK, 0.04))

        p.fill(Rect(rect.x0 + p.px(12), rect.cy - p.px(7.5),
                    rect.x0 + p.px(15), rect.cy + p.px(7.5)), row["bar"])
        p.text(rect.x0 + p.px(24), rect.cy, "%02d" % (row["index"] + 1),
               size=10, color=T.INK3, family="mono", middle=True)

        status_w = p.measure(row["status"], 9, "mono", 0.1)
        label_rect = Rect(rect.x0 + p.px(46), rect.y0,
                          rect.x1 - status_w - p.px(18), rect.y1)
        p.text_clipped(label_rect, row["label"], size=11, color=row["fg"],
                       family="mono", track=T.TRACK_TIGHT)
        p.text(rect.x1 - p.px(12), rect.cy, row["status"], size=9,
               color=row["status_fg"], family="mono", track=0.1, align="right",
               middle=True)

        if hovered:
            description = row["state"].description or ""
            p.tooltip(
                (row["name"] + "\n\n" + description).strip()
                + "\n\nClick to force this state (67 captcha)."
            )
        if clicked and not row["active"]:
            _force_state(m, row["name"])
    p.pop_clip()


def _force_state(m, name: str) -> None:
    """Jumping states by hand is the most dangerous thing in the panel.

    ``docs/state-machine.md``: never take manual control away, but make it
    difficult. The captcha is the friction.
    """
    m.captcha.require(
        "Force the machine into %s?\n\n"
        "This abandons the current state without running the operation that "
        "normally leads out of it." % name,
        lambda: m.dispatcher.force_state(name),
    )


# --------------------------------------------------------------------------
# Active step


def _active_step(p: Painter, r: Rect, m, watched: list[dict]) -> None:
    p.hline(r, r.y1, T.BORDER)
    inner = r.pad(left=p.px(12), top=p.px(13), right=p.px(12), bottom=p.px(13))

    state = m.dispatcher.current
    p.caption(inner.x0, inner.y0 + p.px(5), "ACTIVE STATE - " + pretty_state(state.name))

    title_rect = Rect(inner.x0, inner.y0 + p.px(18), inner.x1, inner.y0 + p.px(60))
    title_h = p.text_wrapped(title_rect, state.description or pretty_state(state.name),
                             size=13, color=T.INK, line_height=1.3, max_lines=3)

    # -- what the machine is waiting for -----------------------------------
    # Every guard the dispatcher is evaluating this cycle, the way GUI2.1 listed
    # them: a state is commonly watched by several -- its own automatic exits
    # plus the global abort -- and which of them are met, and where each one
    # leads, is the whole reason an operator looks at this block. Showing only
    # the first hid the abort guard behind a satisfied one.
    y = max(inner.y0 + p.px(66), title_rect.y0 + title_h + p.px(14))
    if watched:
        for index, row in enumerate(watched):
            _watch_row(p, Rect(inner.x0, y - p.px(9.5), inner.x1, y + p.px(9.5)),
                       row, index)
            y += p.px(WATCH_ROW_H)
    else:
        _watch_row(p, Rect(inner.x0, y - p.px(9.5), inner.x1, y + p.px(9.5)), None, 0)
        y += p.px(WATCH_ROW_H)

    # Remaining watchdog, when the state has one. A guard reading NaN never
    # fires, so this is the number that says how long the wait can last.
    remaining = m.watchdog_remaining()
    if math.isfinite(remaining):
        p.text(inner.x0, y + p.px(2),
               "watchdog %s" % fmt(max(0.0, remaining), 0) + " s",
               size=10, color=T.INK3, family="mono", middle=True)


def _watch_row(p: Painter, r: Rect, row: dict | None, index: int) -> None:
    """One guard: whether it is met, what it is waiting for, where it leads.

    *row* is None for a state with no automatic exit at all, which is a fact
    worth stating rather than an empty space.
    """
    if row is None:
        met, text, dest, description = False, "no automatic exit from this state", "", ""
        color, tag = T.INK3, "MANUAL"
    else:
        met, text = row["met"], row["text"]
        dest, description = row["dest"], row["description"]
        color = T.ACID if met else T.WARN
        tag = "MET" if met else "WAIT"

    p.hit(r, "watch_%d" % index)
    if dest or description:
        parts = [text]
        if dest:
            parts.append("-> " + pretty_state(dest))
        if description:
            parts.append(description)
        p.tooltip("\n\n".join(parts))

    p.square(r.x0 + p.px(3), r.cy, p.px(5), color)

    tag_w = p.measure(tag, 9, "display", 0.1)
    p.text(r.x1, r.cy, tag, size=9, color=color, track=0.1, align="right",
           middle=True)

    right = r.x1 - tag_w - p.px(8)
    if dest:
        # The step number alone -- "-> 08" -- since the full name rarely fits
        # beside the guard it belongs to. The tooltip carries the rest.
        short = "-> " + pretty_state(dest).split(" ")[0]
        p.text(right, r.cy, short, size=9, color=T.INK3, family="mono",
               align="right", middle=True)
        right -= p.measure(short, 9, "mono") + p.px(8)

    p.text_clipped(Rect(r.x0 + p.px(12), r.y0, right, r.y1), text,
                   size=10, color=T.INK if met else T.INK2, family="mono")



# --------------------------------------------------------------------------
# Operations


def _operations(p: Painter, r: Rect, m, operations, ambiguous: bool) -> None:
    """What the operator can do from here, under what the machine is watching."""
    body = p.panel_header(r, "TIE - CHOOSE ONE" if ambiguous else "OPERATIONS")
    p.hline(r, r.y1, T.BORDER)
    body = body.pad(left=p.px(12), top=p.px(6), right=p.px(12), bottom=p.px(6))

    if not operations:
        p.text(body.x0, body.y0 + p.px(14),
               "none - this state leaves on a guard", size=10, color=T.INK4,
               family="mono", middle=True)
        return

    enabled = True if ambiguous else m.can_run_manual()
    row_h = p.px(OPS_ROW_H)
    for index, op in enumerate(operations):
        top = body.y0 + (row_h + p.px(OPS_GAP)) * index
        _operation(p, Rect(body.x0, top, body.x1, top + row_h), m, op, enabled,
                   index, tie=ambiguous)


def _operation(p: Painter, r: Rect, m, op, enabled: bool, index: int,
               *, tie: bool) -> None:
    clicked, hovered, _ = p.hit(r, "op_%d_%s" % (index, op.name))
    accent = T.CRIT if tie else T.ACID
    if not enabled:
        border, fg = T.BORDER, T.INK4
    else:
        border, fg = (accent, accent) if hovered else (T.BORDER, T.INK)
        if hovered:
            p.fill(r, T.fade(accent, 0.07))
    p.stroke(r, border)

    inner = r.pad(left=p.px(10), right=p.px(10))
    dest = "-> " + pretty_state(op.dest_name()).split(" ")[0]
    dest_w = p.measure(dest, 9, "mono")
    p.text(inner.x1, r.cy - p.px(7), dest, size=9,
           color=T.INK3 if enabled else T.INK4, family="mono",
           align="right", middle=True)
    p.text_clipped(Rect(inner.x0, r.cy - p.px(14), inner.x1 - dest_w - p.px(8),
                        r.cy),
                   op.name.upper(), size=11, color=fg, track=T.TRACK_TIGHT)
    if op.description:
        p.text_clipped(Rect(inner.x0, r.cy, inner.x1, r.cy + p.px(13)),
                       op.description, size=9,
                       color=T.INK3 if enabled else T.INK4, family="mono")

    parts = [op.name]
    if op.description:
        parts.append(op.description)
    parts.append("-> " + pretty_state(op.dest_name()))
    if op.requires_captcha:
        parts.append("Asks for confirmation first.")
    if not enabled:
        parts.append(_unavailable(m))
    p.tooltip(BLANK_LINE.join(parts))

    if not (clicked and enabled):
        return
    if tie:
        m.dispatcher.choose(op)
    elif op.requires_captcha:
        m.captcha.require(
            op.name + BLANK_LINE + op.description,
            lambda captured=op: m.dispatcher.request(captured),
        )
    else:
        m.dispatcher.request(op)


def _unavailable(m) -> str:
    if m.mode is DispatcherMode.RUNNING:
        return "An operation is running; manual requests are refused until it finishes."
    if m.mode is DispatcherMode.SUSPENDED:
        return "Suspended: resume or abort the held operation first."
    return "Not available in this mode."


# --------------------------------------------------------------------------
# Event log


def _event_log(p: Painter, r: Rect, m) -> None:
    head, body = r.cut_top(p.px(T.HEADER_H))
    p.text(head.x0 + p.px(12), head.cy, "EVENT LOG", size=10, color=T.INK3,
           family="mono", track=T.TRACK_WIDE, middle=True)
    p.hline(head, head.y1, T.BORDER)

    x = head.x1 - p.px(12)
    for severity, label in reversed(
        [(E.INFO, "INFO"), (E.OK, "OK"), (E.WARN, "WARN"), (E.CRIT, "CRIT")]
    ):
        width = p.measure(label, 9, "mono")
        rect = Rect(x - width, head.y0, x, head.y1)
        clicked, _, _ = p.hit(rect, "filter_" + severity)
        on = m.events.filters.get(severity, True)
        p.text(x, head.cy, label, size=9,
               color=T.fade(E.SEVERITY_COLOR[severity], 1.0 if on else 0.35),
               family="mono", align="right", middle=True)
        p.tooltip("Show or hide %s events" % label.lower())
        if clicked:
            m.events.toggle(severity)
        x = rect.x0 - p.px(9)

    row_h = p.px(LOG_ROW_H)
    capacity = max(1, int((body.h - p.px(6)) / row_h))
    visible = m.events.visible(capacity)

    p.push_clip(body)
    y = body.y0 + p.px(4)
    for event in visible:
        rect = Rect(body.x0, y, body.x1, y + row_h)
        y += row_h
        if event.row_background:
            p.fill(rect, event.row_background)
        p.text(rect.x0 + p.px(12), rect.cy, event.stamp, size=10, color=T.INK3,
               family="mono", middle=True)
        p.text(rect.x0 + p.px(66), rect.cy, event.source, size=9,
               color=event.color, track=0.08, middle=True)
        p.text_clipped(Rect(rect.x0 + p.px(112), rect.y0, rect.x1 - p.px(10), rect.y1),
                       event.message, size=10, color=T.INK2, family="mono")
    p.pop_clip()
