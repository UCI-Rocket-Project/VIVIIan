# Tau-Ceti Cursor Theme

A reusable dark high-contrast Cursor/VS Code theme based on the Tau-Ceti operator-desk palette used in this repository.

## Palette Mapping

The theme colors are derived from:

- `src/viviian/gui_utils/theme.py`
- `VOID`, `PANEL_BG*`, `PANEL_BORDER`
- `INK`, `INK_2`, `INK_3`
- `ACID`, `ALERT`, `WARN`, `CRIT`

## Use In Cursor

1. Open Cursor command palette.
2. Run `Developer: Install Extension from Location...`.
3. Select this folder: `cursor-themes/tau-ceti-theme`.
4. Run `Preferences: Color Theme` and pick `Tau-Ceti`.

## Quick Tweaks

- Increase contrast further: make `editorLineNumber.foreground` brighter.
- Reduce glow: darken `editor.selectionBackground`.
- Soften accents: replace some `#9FE500` usages with `#B6BDB0`.
