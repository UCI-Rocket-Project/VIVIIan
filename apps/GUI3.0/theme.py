"""Design tokens for the UCIRPL Ops View.

Values come from the Claude Design source (``UCIRPL Ops View.dc.html``). Colors
are stored pre-packed in ImGui's ABGR u32 form so no ImGui context is needed to
build the palette at import time.

The design is drawn for a 1920x1080 ops display. ``scale_for`` derives a uniform
factor from the real window so the layout keeps its proportions on a smaller
screen rather than clipping.
"""
from __future__ import annotations

DESIGN_W = 1920.0
DESIGN_H = 1080.0


def col(hex_rgb: str, alpha: float = 1.0) -> int:
    """'#9FE500' -> ImGui u32 (ABGR)."""
    text = hex_rgb.lstrip("#")
    r = int(text[0:2], 16)
    g = int(text[2:4], 16)
    b = int(text[4:6], 16)
    a = max(0, min(255, round(alpha * 255)))
    return (a << 24) | (b << 16) | (g << 8) | r


def fade(color: int, alpha: float) -> int:
    """The same color at a new alpha."""
    return (max(0, min(255, round(alpha * 255))) << 24) | (color & 0x00FFFFFF)


def mix(a: int, b: int, t: float) -> int:
    """Linear blend, for hover states."""
    t = max(0.0, min(1.0, t))
    out = 0
    for shift in (0, 8, 16, 24):
        ca = (a >> shift) & 0xFF
        cb = (b >> shift) & 0xFF
        out |= round(ca + (cb - ca) * t) << shift
    return out


# --- Ground ----------------------------------------------------------------
# The reference console is drawn on true black with no panel fills at all:
# structure comes from 1px rules, not from boxes of slightly different grey.
BG = col("#000000")           # page
PANEL = col("#000000")        # top bar, command bar
CHART_BG = col("#000000")     # plot interiors
SURFACE = col("#0E0F10")      # the few cells that are actually filled
SURFACE_ALT = col("#141414")  # gauge troughs
BORDER = col("#2A2C2B")
ROW_BORDER = col("#161717")
CHART_GRID = col("#141514")   # strip-chart rules

# --- Ink -------------------------------------------------------------------
# Neutral greys. The old ink carried a green cast that fought the signal colors.
INK = col("#F2F2F0")
INK2 = col("#A8AAA6")
INK3 = col("#6C6E6B")
INK4 = col("#3A3C39")

# --- Signal ----------------------------------------------------------------
# Colour means state, nothing else: green open/nominal, red redline, blue and
# red for the fluids. Everything else on the page is grey.
ACID = col("#2ED15E")   # nominal / open / pass
ALERT = col("#E0A63C")  # active / caution
WARN = col("#D2A03A")   # waiting
CRIT = col("#E5484D")   # abort / redline
BLUE = col("#2E7DF7")   # fuel side
OX = col("#C4303A")     # oxidiser side -- deeper than CRIT, which means abort
LIME = col("#9FE500")
PURPLE = col("#B340FF")
PINK = col("#FF33B3")

# Tints that pair with the signal colors. Nearly black: on this ground a filled
# chip reads as a box, and the reference has none.
ACID_BG = col("#06170D")
ALERT_BG = col("#1A1204")
CRIT_BG = col("#1C0709")
CRIT_ROW = col("#140406")
WARN_ROW = col("#131004")

# --- Fixed block heights (design pixels) -----------------------------------
TOPBAR_H = 52.0
ALARM_H = 34.0
STRIPS_H = 176.0
CONTROLS_H = 84.0
LEFT_W = 340.0
RIGHT_W = 400.0
HEADER_H = 28.0

# --- Type ------------------------------------------------------------------
# The design uses Archivo for labels and JetBrains Mono for anything numeric.
# We substitute whatever the machine actually has; see fonts.py.
TRACK_WIDE = 0.14   # letter-spacing on small caps labels, in em
TRACK_TIGHT = 0.05


def scale_for(width: float, height: float) -> float:
    """Uniform layout scale, clamped so text stays legible on small windows."""
    raw = min(width / DESIGN_W, height / DESIGN_H)
    return max(0.62, min(1.75, raw))
