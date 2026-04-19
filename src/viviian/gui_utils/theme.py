from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

GuiThemeName = Literal["legacy", "tau_ceti"]
RGBA = tuple[float, float, float, float]

_ASSET_ROOT = Path(__file__).resolve().parents[2] / "gui_assets" / "fonts"
_FONT_CACHE: dict[int, "ThemeFonts"] = {}


@dataclass(frozen=True, slots=True)
class ThemeFonts:
    ui_regular: Any | None = None
    ui_emphasis: Any | None = None
    mono_regular: Any | None = None
    mono_emphasis: Any | None = None


ACID: Final[RGBA] = (0.624, 0.898, 0.000, 1.0)
ALERT: Final[RGBA] = (1.000, 0.357, 0.122, 1.0)
WARN: Final[RGBA] = (1.000, 0.690, 0.125, 1.0)
CRIT: Final[RGBA] = (1.000, 0.176, 0.239, 1.0)

INK: Final[RGBA] = (0.910, 0.933, 0.886, 1.0)
INK_2: Final[RGBA] = (0.714, 0.741, 0.690, 1.0)
INK_3: Final[RGBA] = (0.431, 0.463, 0.420, 1.0)
INK_4: Final[RGBA] = (0.227, 0.247, 0.216, 1.0)

VOID: Final[RGBA] = (0.027, 0.035, 0.039, 1.0)
PANEL_BG: Final[RGBA] = (0.039, 0.051, 0.055, 1.0)
PANEL_BG_2: Final[RGBA] = (0.071, 0.086, 0.094, 1.0)
PANEL_BG_3: Final[RGBA] = (0.090, 0.110, 0.125, 1.0)
PANEL_BORDER: Final[RGBA] = (0.149, 0.165, 0.161, 1.0)
LINE: Final[RGBA] = (0.165, 0.184, 0.180, 1.0)
INACTIVE_SEG: Final[RGBA] = (0.071, 0.082, 0.082, 1.0)

BUTTON_OFF_BASE: Final[RGBA] = PANEL_BG_2
BUTTON_OFF_HOVER: Final[RGBA] = PANEL_BG_3
BUTTON_OFF_ACTIVE: Final[RGBA] = (0.122, 0.145, 0.161, 1.0)

BUTTON_ON_PRIMARY_BASE: Final[RGBA] = (0.082, 0.165, 0.000, 1.0)
BUTTON_ON_PRIMARY_HOVER: Final[RGBA] = (0.102, 0.204, 0.000, 1.0)
BUTTON_ON_PRIMARY_ACTIVE: Final[RGBA] = (0.125, 0.251, 0.000, 1.0)

BUTTON_ON_ALERT_BASE: Final[RGBA] = (0.165, 0.055, 0.000, 1.0)
BUTTON_ON_ALERT_HOVER: Final[RGBA] = (0.200, 0.075, 0.000, 1.0)
BUTTON_ON_ALERT_ACTIVE: Final[RGBA] = (0.245, 0.090, 0.000, 1.0)

BUTTON_ON_CRIT_BASE: Final[RGBA] = (0.165, 0.000, 0.031, 1.0)
BUTTON_ON_CRIT_HOVER: Final[RGBA] = (0.212, 0.000, 0.043, 1.0)
BUTTON_ON_CRIT_ACTIVE: Final[RGBA] = (0.275, 0.000, 0.056, 1.0)

BUTTON_DISABLED_BG: Final[RGBA] = (0.047, 0.055, 0.063, 1.0)
BUTTON_DISABLED_FG: Final[RGBA] = INK_4
BUTTON_META_FG: Final[RGBA] = INK_3
BUTTON_STATE_FIELD_FG: Final[RGBA] = (0.039, 0.043, 0.043, 1.0)

GAUGE_LOW: Final[RGBA] = ACID
GAUGE_HIGH: Final[RGBA] = CRIT
GAUGE_FRAME_BG: Final[RGBA] = (0.023, 0.031, 0.039, 1.0)
GAUGE_FRAME_BORDER: Final[RGBA] = PANEL_BORDER
GAUGE_INACTIVE_SEGMENT: Final[RGBA] = INACTIVE_SEG
GAUGE_TEXT_ACCENT: Final[RGBA] = INK
GAUGE_TEXT_DIM: Final[RGBA] = INK_3

GRAPH_BG: Final[RGBA] = GAUGE_FRAME_BG
GRAPH_BORDER: Final[RGBA] = PANEL_BORDER
GRAPH_GRIDLINE: Final[RGBA] = (0.100, 0.110, 0.106, 1.0)
GRAPH_ZERO_LINE: Final[RGBA] = LINE
GRAPH_TEXT: Final[RGBA] = INK_3
GRAPH_SERIES_PALETTE: Final[tuple[RGBA, ...]] = (
    ACID,
    ALERT,
    INK,
    WARN,
    (0.000, 0.898, 0.761, 1.0),
    (0.847, 1.000, 0.000, 1.0),
)

BUTTON_HEIGHT: Final[float] = 52.0
BUTTON_LED_TAB_PX: Final[float] = 10.0
BUTTON_HAZARD_PX: Final[float] = 3.0

GAUGE_PADDING_PX: Final[float] = 14.0
GAUGE_ARC_THICKNESS: Final[float] = 12.0
GAUGE_NEEDLE_PX: Final[float] = 2.2
LED_ORANGE_BLEND: Final[float] = 0.58
LED_ORANGE_RED_BLEND: Final[float] = 0.78

GRAPH_LINE_THICK: Final[float] = 2.0
GRAPH_OVERLAY_THICK: Final[float] = 1.4


def apply_imgui_theme(imgui: Any, *, theme_name: GuiThemeName = "legacy") -> ThemeFonts:
    if theme_name == "tau_ceti":
        return _apply_tau_ceti(imgui)
    return ThemeFonts()


def get_theme_fonts(imgui: Any) -> ThemeFonts:
    io = getattr(imgui, "get_io", lambda: None)()
    if io is None:
        return ThemeFonts()
    return _FONT_CACHE.get(id(io), ThemeFonts())


def font_asset_root() -> Path:
    return _ASSET_ROOT


def expected_font_paths() -> dict[str, Path]:
    return {
        "ui_regular": _ASSET_ROOT / "Archivo-SemiBold.ttf",
        "ui_emphasis": _ASSET_ROOT / "Archivo-ExtraBold.ttf",
        "mono_regular": _ASSET_ROOT / "JetBrainsMono-Regular.ttf",
        "mono_emphasis": _ASSET_ROOT / "JetBrainsMono-SemiBold.ttf",
    }


def _apply_tau_ceti(imgui: Any) -> ThemeFonts:
    style = getattr(imgui, "get_style", lambda: None)()
    if style is not None:
        style.window_rounding = 0.0
        style.child_rounding = 0.0
        style.frame_rounding = 0.0
        style.grab_rounding = 0.0
        style.scrollbar_rounding = 0.0
        style.popup_rounding = 0.0
        style.tab_rounding = 0.0
        style.window_border_size = 1.0
        style.child_border_size = 1.0
        style.frame_border_size = 1.0
        style.window_padding = (14.0, 14.0)
        colors = getattr(style, "colors", None)
        if colors is not None:
            _set_color(colors, getattr(imgui, "COLOR_WINDOW_BACKGROUND", None), VOID)
            _set_color(colors, getattr(imgui, "COLOR_CHILD_BACKGROUND", None), PANEL_BG)
            _set_color(colors, getattr(imgui, "COLOR_TITLE_BACKGROUND", None), PANEL_BG)
            _set_color(colors, getattr(imgui, "COLOR_TITLE_BACKGROUND_ACTIVE", None), PANEL_BG_2)
            _set_color(colors, getattr(imgui, "COLOR_FRAME_BACKGROUND", None), PANEL_BG_2)
            _set_color(colors, getattr(imgui, "COLOR_FRAME_BACKGROUND_HOVERED", None), PANEL_BG_3)
            _set_color(colors, getattr(imgui, "COLOR_FRAME_BACKGROUND_ACTIVE", None), PANEL_BG_3)
            _set_color(colors, getattr(imgui, "COLOR_BUTTON", None), BUTTON_OFF_BASE)
            _set_color(colors, getattr(imgui, "COLOR_BUTTON_HOVERED", None), BUTTON_OFF_HOVER)
            _set_color(colors, getattr(imgui, "COLOR_BUTTON_ACTIVE", None), BUTTON_OFF_ACTIVE)
            _set_color(colors, getattr(imgui, "COLOR_BORDER", None), PANEL_BORDER)
            _set_color(colors, getattr(imgui, "COLOR_TEXT", None), INK)
            _set_color(colors, getattr(imgui, "COLOR_TEXT_DISABLED", None), INK_3)
    return _load_tau_ceti_fonts(imgui)


def _load_tau_ceti_fonts(imgui: Any) -> ThemeFonts:
    io = getattr(imgui, "get_io", lambda: None)()
    fonts = getattr(io, "fonts", None)
    if io is None or fonts is None:
        return ThemeFonts()
    cache_key = id(io)
    cached = _FONT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    add_default = getattr(fonts, "add_font_default", None)
    add_file = getattr(fonts, "add_font_from_file_ttf", None)
    if not callable(add_default):
        loaded = ThemeFonts()
        _FONT_CACHE[cache_key] = loaded
        return loaded

    paths = expected_font_paths()
    ui_regular = _maybe_add_font(add_file, paths["ui_regular"], 18.0)
    ui_emphasis = _maybe_add_font(add_file, paths["ui_emphasis"], 20.0)
    mono_regular = _maybe_add_font(add_file, paths["mono_regular"], 14.0)
    mono_emphasis = _maybe_add_font(add_file, paths["mono_emphasis"], 15.0)
    default_font = add_default()
    loaded = ThemeFonts(
        ui_regular=ui_regular or default_font,
        ui_emphasis=ui_emphasis or ui_regular or default_font,
        mono_regular=mono_regular or default_font,
        mono_emphasis=mono_emphasis or mono_regular or default_font,
    )
    if hasattr(io, "font_default"):
        try:
            io.font_default = loaded.ui_regular
        except Exception:
            pass
    _FONT_CACHE[cache_key] = loaded
    return loaded


def _maybe_add_font(add_file: Any, path: Path, size_px: float) -> Any | None:
    if not callable(add_file) or not path.exists():
        return None
    try:
        return add_file(str(path), float(size_px))
    except Exception:
        return None


def _set_color(colors: Any, index: int | None, value: RGBA) -> None:
    if index is None:
        return
    try:
        colors[index] = value
    except Exception:
        return


__all__ = [
    "ACID",
    "ALERT",
    "BUTTON_HEIGHT",
    "BUTTON_HAZARD_PX",
    "BUTTON_LED_TAB_PX",
    "BUTTON_OFF_ACTIVE",
    "BUTTON_OFF_BASE",
    "BUTTON_OFF_HOVER",
    "BUTTON_ON_ALERT_ACTIVE",
    "BUTTON_ON_ALERT_BASE",
    "BUTTON_ON_ALERT_HOVER",
    "BUTTON_ON_CRIT_ACTIVE",
    "BUTTON_ON_CRIT_BASE",
    "BUTTON_ON_CRIT_HOVER",
    "BUTTON_ON_PRIMARY_ACTIVE",
    "BUTTON_ON_PRIMARY_BASE",
    "BUTTON_ON_PRIMARY_HOVER",
    "BUTTON_DISABLED_BG",
    "BUTTON_DISABLED_FG",
    "BUTTON_META_FG",
    "BUTTON_STATE_FIELD_FG",
    "CRIT",
    "GAUGE_ARC_THICKNESS",
    "GAUGE_FRAME_BG",
    "GAUGE_FRAME_BORDER",
    "GAUGE_HIGH",
    "GAUGE_INACTIVE_SEGMENT",
    "GAUGE_LOW",
    "GAUGE_NEEDLE_PX",
    "GAUGE_PADDING_PX",
    "GAUGE_TEXT_ACCENT",
    "GAUGE_TEXT_DIM",
    "GRAPH_BG",
    "GRAPH_BORDER",
    "GRAPH_GRIDLINE",
    "GRAPH_LINE_THICK",
    "GRAPH_OVERLAY_THICK",
    "GRAPH_SERIES_PALETTE",
    "GRAPH_TEXT",
    "GRAPH_ZERO_LINE",
    "GuiThemeName",
    "INK",
    "INK_2",
    "INK_3",
    "INK_4",
    "INACTIVE_SEG",
    "LED_ORANGE_BLEND",
    "LED_ORANGE_RED_BLEND",
    "LINE",
    "PANEL_BG",
    "PANEL_BG_2",
    "PANEL_BG_3",
    "PANEL_BORDER",
    "RGBA",
    "ThemeFonts",
    "VOID",
    "WARN",
    "apply_imgui_theme",
    "expected_font_paths",
    "font_asset_root",
    "get_theme_fonts",
]
