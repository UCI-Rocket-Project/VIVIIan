"""Centre column: the feed-system schematic.

The design's P&ID is a hotfire tree. This one is drawn from the plumbing the
pressure-decay procedure actually commands: the GN2 supply, the four fill
solenoids, COPV, PV 1 and PV 2 into the LOX and LNG tanks, the vent header, and
MVAS into the engine.

Every glyph is bound to a real valve or a real PT. A valve is filled when it is
open -- which is not the same as energised, since PV 2 and the tank vents are
normally open -- and outlined in red when the board disagrees with what we asked
for, because a valve that is not where the procedure thinks it is matters more
than anything downstream of it.

The schematic is authored in a fixed 940x590 space and fitted to whatever
rectangle the layout gives it, the same way the source design uses an SVG
viewBox.
"""
from __future__ import annotations

import math

import theme as T
import model as M
from draw import Painter, Rect, fmt

VIEW_W = 940.0
VIEW_H = 590.0

# Fluid coding, as the reference console has it: green for the pressurant tree,
# blue for fuel, red for oxidiser, grey for anything venting to atmosphere.
GN2 = T.ACID
LOX = T.OX
LNG = T.BLUE
VENTLINE = T.INK4

# --- Schematic geometry, in view space -------------------------------------
FILL_BRANCH_Y = (200.0, 260.0, 320.0, 380.0)
FILL_VALVES = ("sol_gn2_fill_1", "sol_gn2_fill_2", "sol_gn2_fill_3", "sol_gn2_fill_4")
MANIFOLD_IN_X = 140.0
MANIFOLD_OUT_X = 290.0
MAIN_Y = 290.0
VENT_HEADER_Y = 95.0
VENT_RISER_X = 925.0


class _View:
    """View space -> screen."""

    def __init__(self, r: Rect) -> None:
        self.scale = min(r.w / VIEW_W, r.h / VIEW_H)
        self.ox = r.x0 + (r.w - VIEW_W * self.scale) * 0.5
        self.oy = r.y0 + (r.h - VIEW_H * self.scale) * 0.5

    def x(self, vx: float) -> float:
        return self.ox + vx * self.scale

    def y(self, vy: float) -> float:
        return self.oy + vy * self.scale

    def s(self, length: float) -> float:
        return length * self.scale

    def rect(self, x0: float, y0: float, x1: float, y1: float) -> Rect:
        return Rect(self.x(x0), self.y(y0), self.x(x1), self.y(y1))


def draw(p: Painter, r: Rect, m) -> None:
    p.vline(r, r.x1, T.BORDER)
    outer = Rect(r.x0, r.y0, r.x1 - 1, r.y1)

    body = p.panel_header(outer, "FEED SYSTEM - P&ID")

    p.push_clip(body)
    view = _View(body.inset(p.px(6)))
    _pipes(p, view, m)
    _vessels(p, view, m)
    _valves(p, view, m)
    _engine(p, view, m)
    _labels(p, view, m)

    p.pop_clip()


def _run(p: Painter, view: _View, points, color: int, width: float = 2.5) -> None:
    """A pipe run through a list of view-space points."""
    screen = [(view.x(vx), view.y(vy)) for vx, vy in points]
    p.polyline(screen, color, max(1.0, view.s(width)))


def _pipes(p: Painter, view: _View, m) -> None:
    # GN2 supply into the fill manifold.
    _run(p, view, [(56, MAIN_Y), (MANIFOLD_IN_X, MAIN_Y)], GN2)
    _run(p, view, [(MANIFOLD_IN_X, FILL_BRANCH_Y[0]),
                   (MANIFOLD_IN_X, FILL_BRANCH_Y[-1])], GN2)
    _run(p, view, [(MANIFOLD_OUT_X, FILL_BRANCH_Y[0]),
                   (MANIFOLD_OUT_X, FILL_BRANCH_Y[-1])], GN2)
    for y in FILL_BRANCH_Y:
        _run(p, view, [(MANIFOLD_IN_X, y), (MANIFOLD_OUT_X, y)], GN2)

    # Manifold outlet to COPV, and on to the tank split.
    _run(p, view, [(MANIFOLD_OUT_X, MAIN_Y), (400, MAIN_Y)], GN2)
    _run(p, view, [(530, MAIN_Y), (590, MAIN_Y)], GN2)
    _run(p, view, [(590, 205), (590, 375)], GN2)
    _run(p, view, [(590, 205), (660, 205)], LOX)
    _run(p, view, [(590, 375), (660, 375)], LNG)

    # Tanks to the injector, through MVAS.
    _run(p, view, [(790, 205), (800, 205), (800, 272)], LOX)
    _run(p, view, [(790, 375), (800, 375), (800, 308)], LNG)

    # Vent header, and everything that ties into it.
    _run(p, view, [(350, VENT_HEADER_Y), (VENT_RISER_X, VENT_HEADER_Y)], VENTLINE, 2.0)
    _run(p, view, [(350, MAIN_Y), (350, VENT_HEADER_Y)], VENTLINE, 2.0)
    _run(p, view, [(465, 250), (465, VENT_HEADER_Y)], VENTLINE, 2.0)
    _run(p, view, [(725, 165), (725, VENT_HEADER_Y)], VENTLINE, 2.0)
    _run(p, view, [(725, 415), (725, 520), (VENT_RISER_X, 520),
                   (VENT_RISER_X, VENT_HEADER_Y)], VENTLINE, 2.0)

    # Vent stack.
    p.polyline(
        [
            (view.x(VENT_RISER_X - 7), view.y(70)),
            (view.x(VENT_RISER_X), view.y(56)),
            (view.x(VENT_RISER_X + 7), view.y(70)),
        ],
        VENTLINE,
        max(1.0, view.s(2.0)),
    )
    _run(p, view, [(VENT_RISER_X, VENT_HEADER_Y), (VENT_RISER_X, 60)], VENTLINE, 2.0)


def _vessels(p: Painter, view: _View, m) -> None:
    radius = view.s(19)
    # GN2 supply bottle.
    p.fill(view.rect(20, 255, 56, 325), T.PANEL, radius)
    p.stroke(view.rect(20, 255, 56, 325), GN2, max(1.0, view.s(2)), radius)

    # COPV.
    copv = view.rect(400, 250, 530, 330)
    p.fill(copv, T.PANEL, view.s(40))
    p.stroke(copv, _vessel_stroke(m, M.COPV, GN2), max(1.0, view.s(2)), view.s(40))

    lox = view.rect(660, 165, 790, 245)
    p.fill(lox, T.PANEL, view.s(40))
    p.stroke(lox, _vessel_stroke(m, M.LOX_TANK, LOX), max(1.0, view.s(2)), view.s(40))

    lng = view.rect(660, 335, 790, 415)
    p.fill(lng, T.PANEL, view.s(40))
    p.stroke(lng, _vessel_stroke(m, M.LNG_TANK, LNG), max(1.0, view.s(2)), view.s(40))


def _vessel_stroke(m, channel: str, base: int) -> int:
    """A vessel outlines red once it is over its abort limit."""
    color = m.severity_color(channel)
    return T.CRIT if color == T.CRIT else base


def _ring_valve(p: Painter, view: _View, vx: float, vy: float, color: int) -> None:
    """The reference's valve: a heavy ring, black inside, with a bar across it."""
    x, y = view.x(vx), view.y(vy)
    radius = view.s(12)
    p.dot(x, y, radius, T.BG)          # punch the pipe out from under the glyph
    p.ring(x, y, radius, color, max(1.5, view.s(2.5)))
    bar = radius * 0.62
    p.line(x - bar, y, x + bar, y, T.INK2, max(1.0, view.s(2)))


def _valve_color(m, button_id: str) -> int:
    """The ring colour for one valve: its state, in one colour."""
    agrees = m.valve_agrees(button_id)
    if agrees is False:
        # The board says something other than what we commanded. Nothing else
        # on this panel outranks that.
        return T.CRIT
    if agrees is None:
        return T.WARN  # commanded, but the board is not reporting this valve
    return T.ACID if m.valve_open(button_id) else T.INK4


# (button_id, view x, view y, vertical, fluid color)
_VALVE_GLYPHS = (
    ("sol_gn2_fill_1", 215.0, FILL_BRANCH_Y[0], False, GN2),
    ("sol_gn2_fill_2", 215.0, FILL_BRANCH_Y[1], False, GN2),
    ("sol_gn2_fill_3", 215.0, FILL_BRANCH_Y[2], False, GN2),
    ("sol_gn2_fill_4", 215.0, FILL_BRANCH_Y[3], False, GN2),
    ("sol_gn2_vent", 350.0, 180.0, True, VENTLINE),
    ("copv_vent", 465.0, 175.0, True, VENTLINE),
    ("pv1", 625.0, 205.0, False, LOX),
    ("pv2", 625.0, 375.0, False, LNG),
    ("tank_vent", 725.0, 130.0, True, VENTLINE),
    ("tank_vent", 725.0, 468.0, True, VENTLINE),
)


def _valves(p: Painter, view: _View, m) -> None:
    for button_id, vx, vy, _vertical, _fluid in _VALVE_GLYPHS:
        _ring_valve(p, view, vx, vy, _valve_color(m, button_id))

    # MVAS on both main feeds. The actuators hold position, so the glyph shows
    # the last commanded direction rather than a live solenoid state.
    mvas = T.ACID if m.commanded("mvas_open") else T.INK4
    _ring_valve(p, view, 800.0, 240.0, mvas)
    _ring_valve(p, view, 800.0, 340.0, mvas)


def _engine(p: Painter, view: _View, m) -> None:
    width = max(1.0, view.s(2))
    injector = view.rect(800, 265, 828, 315)
    p.fill(injector, T.SURFACE)
    p.stroke(injector, T.INK2, width)

    nozzle = [
        (view.x(828), view.y(265)),
        (view.x(866), view.y(240)),
        (view.x(866), view.y(340)),
        (view.x(828), view.y(315)),
    ]
    p.poly_filled(nozzle, T.PANEL)
    p.polyline(nozzle + [nozzle[0]], T.INK2, width)

    # Load cell, hung off the nozzle the way the design does. It sits well below
    # the nozzle so the ingress labels either side of the engine stay clear.
    p.polyline(
        [(view.x(847), view.y(340)), (view.x(847), view.y(457)),
         (view.x(855), view.y(457))],
        T.INK3, max(1.0, view.s(1.5)),
    )
    cell = view.rect(855, 440, 935, 474)
    p.fill(cell, T.PANEL)
    p.stroke(cell, T.INK4, max(1.0, view.s(1.5)))


# --------------------------------------------------------------------------
# Labels


def _labels(p: Painter, view: _View, m) -> None:
    # Valve states.
    for button_id, vx, vy, vertical, _fluid in _VALVE_GLYPHS:
        offset = -26.0 if vertical else -24.0
        _valve_label(p, view, m, button_id, vx, vy + offset)

    # MVAS sits in the gap between the two tanks, clear of the injector and of
    # the ingress readings either side of the engine.
    _valve_label(p, view, m, "mvas_open", 752.0, 292.0, label="MVAS")

    # Static plant labels.
    _tag(p, view, 38.0, 345.0, "GN2 SUPPLY", "BOTTLE", T.INK3)

    # Live pressures. Each is clickable and drives the trend selection.
    _channel(p, view, m, 465.0, 290.0, M.COPV, "COPV")
    _channel(p, view, m, 725.0, 205.0, M.LOX_TANK, "LOX TANK")
    _channel(p, view, m, 725.0, 375.0, M.LNG_TANK, "LNG TANK")
    _channel(p, view, m, 600.0, 68.0, M.VENT, "VENT PT")
    _channel(p, view, m, 866.0, 196.0, M.LOX_ING, "LOX ING")
    _channel(p, view, m, 866.0, 392.0, M.LNG_ING, "LNG ING")
    _channel(p, view, m, 895.0, 457.0, M.THRUST, "LOAD CELL", unit="")


def _tag(p: Painter, view: _View, vx: float, vy: float, tag: str, value: str,
         color: int, *, boxed: bool = False) -> Rect:
    x, y = view.x(vx), view.y(vy)
    tag_w = p.measure(tag, 9, "mono", 0.12)
    val_w = p.measure(value, 14, "mono")
    half = max(tag_w, val_w) * 0.5 + p.px(10)
    box = Rect(x - half, y - p.px(17), x + half, y + p.px(17))
    if boxed:
        p.fill(box, T.BG)
        p.stroke(box, T.BORDER)
    p.text(x, box.y0 + p.px(9), tag, size=9, color=T.INK3, family="mono",
           track=0.12, align="center", middle=True)
    p.text(x, box.y1 - p.px(11), value, size=14, color=color, family="mono",
           align="center", middle=True)
    return box


def _valve_label(p: Painter, view: _View, m, button_id: str, vx: float, vy: float,
                 label: str = "") -> None:
    is_open = m.valve_open(button_id)
    agrees = m.valve_agrees(button_id)
    if agrees is False:
        color = T.CRIT
        text = "DISAGREE"
    elif agrees is None:
        color = T.WARN
        text = "NO ECHO"
    else:
        color = T.ACID if is_open else T.INK3
        text = "OPEN" if is_open else "CLOSED"
    _tag(p, view, vx, vy, label or _short_name(m, button_id), text, color)


def _short_name(m, button_id: str) -> str:
    name = m.valve_label(button_id).upper()
    return {"LOX VV & LNG VV": "TANK VV"}.get(name, name)


def _channel(p: Painter, view: _View, m, vx: float, vy: float, channel: str,
             tag: str, unit: str = "") -> None:
    value = m.value(channel)
    color = m.severity_color(channel)
    box = _tag(p, view, vx, vy, tag, fmt(value, 0) + unit, color, boxed=True)

    selected = m.selected == channel
    if selected:
        p.fill(box, T.BG)
        p.stroke(box, color)
        # Redraw over the panel fill.
        p.text(box.cx, box.y0 + p.px(9), tag, size=9, color=T.INK3,
               family="mono", track=0.12, align="center", middle=True)
        p.text(box.cx, box.y1 - p.px(11), fmt(value, 0) + unit, size=14,
               color=color, family="mono", align="center", middle=True)

    clicked, _, _ = p.hit(box, "pid_" + channel)
    p.tooltip("%s  -  click to trend" % M.DISPLAY_NAME.get(channel, channel))
    if clicked:
        m.selected = channel
