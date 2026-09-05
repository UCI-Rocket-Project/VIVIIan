"""Font loading, with fallbacks.

The design specifies Archivo (labels) and JetBrains Mono (numbers). Neither
ships with the repo, so each role resolves to the closest thing the machine
actually has, ending at the two faces bundled inside imgui_bundle so this works
on a bare checkout.

Monospace is not cosmetic here: a proportional face makes a pressure readout
jitter sideways as the digits change, which is exactly the thing an operator
watches for.

ImGui 1.92 sizes fonts dynamically, so one face covers every size the design
asks for via ``imgui.push_font(font, size)``.
"""
from __future__ import annotations

import os
from pathlib import Path

_WIN_FONTS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"


def _bundled() -> Path:
    import imgui_bundle

    return Path(imgui_bundle.__file__).parent / "assets" / "fonts"


def _first_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.is_file():
            return path
    return None


def _display_candidates() -> list[Path]:
    return [
        _WIN_FONTS / "seguisb.ttf",     # Segoe UI Semibold
        _WIN_FONTS / "segoeuib.ttf",    # Segoe UI Bold
        Path("/System/Library/Fonts/SFNSDisplay.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        _bundled() / "Roboto" / "Roboto-Bold.ttf",
        _bundled() / "DroidSans.ttf",
    ]


def _mono_candidates() -> list[Path]:
    return [
        _WIN_FONTS / "JetBrainsMono-Regular.ttf",
        _WIN_FONTS / "CascadiaMono.ttf",
        _WIN_FONTS / "consola.ttf",
        Path("/System/Library/Fonts/Menlo.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        _bundled() / "Inconsolata-Medium.ttf",
    ]


class Fonts:
    """The two faces the ops view draws with."""

    def __init__(self, display, mono, base_size: float) -> None:
        self.display = display
        self.mono = mono
        self.base_size = base_size


def load(imgui, base_size: float = 16.0) -> Fonts:
    """Load both faces into the atlas. Falls back to the default font on miss."""
    atlas = imgui.get_io().fonts

    display_path = _first_existing(_display_candidates())
    mono_path = _first_existing(_mono_candidates())

    display = (
        atlas.add_font_from_file_ttf(str(display_path), base_size)
        if display_path is not None
        else None
    )
    mono = (
        atlas.add_font_from_file_ttf(str(mono_path), base_size)
        if mono_path is not None
        else None
    )

    if display is None or mono is None:
        print("[gui3] falling back to the built-in font for one or both roles")

    return Fonts(display=display, mono=mono, base_size=base_size)
