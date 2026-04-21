from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence

from . import theme
from .chrome import available_width, begin_panel, draw_status_chip, estimate_text_size, rgba_u32

Severity = Literal["info", "ok", "warn", "crit"]
ToolbarVariant = Literal["neutral", "primary", "warn", "crit"]


class ConsoleComponent:
    component_id: str

    def required_streams(self) -> tuple[str, ...]:
        return ()

    def bind(self, _readers: Mapping[str, Any]) -> None:
        return None

    def consume(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class KeyValueRow:
    key: str
    value: str
    severity: Severity = "info"


@dataclass
class MicroButton(ConsoleComponent):
    component_id: str
    label: str
    icon: str = ""
    active: bool = False
    disabled: bool = False
    toggle_on_press: bool = True
    theme_name: theme.GuiThemeName = "tau_ceti"
    on_press: Callable[["MicroButton"], None] | None = None

    SIZE: ClassVar[float] = 32.0

    def render(self) -> bool:
        imgui = _require_imgui()
        base = theme.ACID if self.active else theme.PANEL_BG_2
        fg = theme.VOID if self.active else theme.INK_2
        hovered = theme.ACID if self.active else theme.PANEL_BG_3
        active = theme.ACID if self.active else theme.BUTTON_OFF_ACTIVE
        if self.disabled:
            base = theme.BUTTON_DISABLED_BG
            hovered = base
            active = base
            fg = theme.BUTTON_DISABLED_FG
        color_count, var_count = _push_button_colors(
            imgui,
            base=base,
            hovered=hovered,
            active=active,
            text=fg,
            border=(base if self.active else theme.PANEL_BORDER),
        )
        pressed = imgui.button(self._display_text(), width=self.SIZE, height=self.SIZE)
        _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
        if pressed and not self.disabled and _ctrl_held(imgui):
            if self.toggle_on_press:
                self.active = not self.active
            if self.on_press is not None:
                self.on_press(self)
            return True
        return False

    def _display_text(self) -> str:
        text = f"{self.icon} {self.label}".strip()
        return text or "·"


@dataclass
class ReadoutCard(ConsoleComponent):
    component_id: str
    title: str
    value: str
    subtitle: str = ""
    footer_left: str = ""
    footer_right: str = ""
    severity: Severity = "info"
    width: float = 220.0
    height: float = 104.0
    theme_name: theme.GuiThemeName = "tau_ceti"

    def render(self) -> None:
        imgui = _require_imgui()
        color = _severity_color(self.severity)
        panel = begin_panel(
            imgui,
            width=min(self.width, available_width(imgui, fallback=self.width)),
            height=self.height,
            crop_marks=True,
        )
        draw_list = panel.draw_list
        left, top, right, bottom = panel.inner_bounds
        draw_list.add_text(left, top, rgba_u32(imgui, theme.INK_3), self.title.upper())
        value_width, value_height = estimate_text_size(imgui, self.value)
        draw_list.add_text(left, top + 22.0, rgba_u32(imgui, color), self.value)
        if self.subtitle:
            draw_list.add_text(left, top + 22.0 + value_height + 6.0, rgba_u32(imgui, theme.INK_2), self.subtitle)
        footer_y = bottom - 16.0
        if self.footer_left:
            draw_list.add_text(left, footer_y, rgba_u32(imgui, theme.INK_3), self.footer_left)
        if self.footer_right:
            right_width, _ = estimate_text_size(imgui, self.footer_right)
            draw_list.add_text(right - right_width, footer_y, rgba_u32(imgui, color), self.footer_right)


@dataclass
class KeyValuePanel(ConsoleComponent):
    component_id: str
    title: str
    rows: Sequence[KeyValueRow]
    width: float = 260.0
    row_height: float = 18.0

    def render(self) -> None:
        imgui = _require_imgui()
        panel_height = 34.0 + (len(self.rows) * self.row_height) + 18.0
        panel = begin_panel(
            imgui,
            width=min(self.width, available_width(imgui, fallback=self.width)),
            height=panel_height,
            label=self.title.upper(),
            label_right=f"{len(self.rows)}/{len(self.rows)}",
            dot_color=theme.ACID,
        )
        draw_list = panel.draw_list
        left, top, right, _bottom = panel.inner_bounds
        for index, row in enumerate(self.rows):
            y = top + (index * self.row_height)
            draw_list.add_text(left, y, rgba_u32(imgui, theme.INK_3), row.key)
            value_width, _ = estimate_text_size(imgui, row.value)
            draw_list.add_text(
                right - value_width,
                y,
                rgba_u32(imgui, _severity_color(row.severity)),
                row.value,
            )


@dataclass
class ToolbarButton:
    label: str
    icon: str = ""
    shortcut: str = ""
    variant: ToolbarVariant = "neutral"
    active: bool = False
    disabled: bool = False
    on_press: Callable[["ToolbarButton"], None] | None = None

    def render(self, imgui: Any) -> bool:
        base, hovered, active, text, border = _toolbar_palette(self.variant, self.active, self.disabled)
        color_count, var_count = _push_button_colors(
            imgui,
            base=base,
            hovered=hovered,
            active=active,
            text=text,
            border=border,
        )
        pressed = imgui.button(self._display_text(), width=0.0, height=32.0)
        _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
        if pressed and not self.disabled and _ctrl_held(imgui):
            if self.on_press is not None:
                self.on_press(self)
            return True
        return False

    def _display_text(self) -> str:
        text = f"{self.icon} {self.label}".strip()
        if self.shortcut:
            return f"{text} [{self.shortcut}]"
        return text


@dataclass
class ToolbarSearch:
    query: str = ""
    placeholder: str = "find stream"

    def render(self, imgui: Any) -> None:
        display_value = self.query or self.placeholder
        if hasattr(imgui, "input_text"):
            color_count, var_count = _push_button_colors(
                imgui,
                base=theme.PANEL_BG,
                hovered=theme.PANEL_BG_2,
                active=theme.PANEL_BG_2,
                text=theme.INK_2,
                border=theme.PANEL_BORDER,
            )
            changed, new_value = imgui.input_text("##toolbar_search", display_value)
            _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
            if changed:
                self.query = new_value
        else:
            imgui.text_unformatted(f"// {display_value}")


@dataclass
class ToolbarMeter:
    label: str
    value: float
    right_label: str = ""
    width: float = 160.0

    def render(self, imgui: Any) -> None:
        panel = begin_panel(imgui, width=self.width, height=54.0, crop_marks=False)
        draw_list = panel.draw_list
        left, top, right, bottom = panel.inner_bounds
        draw_list.add_text(left, top, rgba_u32(imgui, theme.INK_3), self.label.upper())
        bar_top = top + 16.0
        bar_bottom = bar_top + 10.0
        draw_list.add_rect_filled(left, bar_top, right, bar_bottom, rgba_u32(imgui, theme.PANEL_BG_2))
        fill = left + ((right - left) * max(0.0, min(1.0, self.value)))
        draw_list.add_rect_filled(left, bar_top, fill, bar_bottom, rgba_u32(imgui, theme.ACID))
        if self.right_label:
            right_width, _ = estimate_text_size(imgui, self.right_label)
            draw_list.add_text(right - right_width, bottom - 14.0, rgba_u32(imgui, theme.INK_2), self.right_label)


@dataclass
class OperatorToolbar(ConsoleComponent):
    component_id: str
    file_buttons: list[ToolbarButton] = field(default_factory=list)
    ops_buttons: list[ToolbarButton] = field(default_factory=list)
    search: ToolbarSearch | None = None
    meter: ToolbarMeter | None = None

    def render(self) -> None:
        imgui = _require_imgui()
        for group_index, buttons in enumerate((self.file_buttons, self.ops_buttons)):
            for index, button in enumerate(buttons):
                button.render(imgui)
                if index < len(buttons) - 1:
                    imgui.same_line()
            if group_index == 0 and self.search is not None:
                imgui.same_line()
                self.search.render(imgui)
            if group_index == 0:
                imgui.spacing()
        if self.meter is not None:
            self.meter.render(imgui)


@dataclass
class Subbar(ConsoleComponent):
    component_id: str
    tabs: list[str]
    active_tab: int = 0
    breadcrumbs: list[str] = field(default_factory=list)
    status_text: str = "GO-FLIGHT"
    status_severity: Severity = "ok"

    def render(self) -> None:
        imgui = _require_imgui()
        for index, tab in enumerate(self.tabs):
            color_count, var_count = _push_button_colors(
                imgui,
                base=(theme.PANEL_BG_2 if index == self.active_tab else theme.PANEL_BG),
                hovered=theme.PANEL_BG_2,
                active=theme.PANEL_BG_3,
                text=(theme.ACID if index == self.active_tab else theme.INK_2),
                border=(theme.ACID if index == self.active_tab else theme.PANEL_BORDER),
            )
            if imgui.button(tab, width=0.0, height=28.0) and _ctrl_held(imgui):
                self.active_tab = index
            _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
            if index < len(self.tabs) - 1:
                imgui.same_line()
        if self.breadcrumbs:
            imgui.text_unformatted(" / ".join(self.breadcrumbs))
        draw_list = imgui.get_window_draw_list()
        x0, y0 = imgui.get_cursor_screen_pos()
        draw_status_chip(
            imgui,
            draw_list,
            x0=x0,
            y0=y0,
            text=self.status_text,
            color=_severity_color(self.status_severity),
        )
        imgui.dummy(140.0, 24.0)


@dataclass(frozen=True, slots=True)
class EventRecord:
    timestamp: str
    severity: Severity
    source: str
    message: str
    event_id: str = ""
    is_new: bool = False


@dataclass
class EventLogPanel(ConsoleComponent):
    component_id: str
    records: list[EventRecord]
    title: str = "EVENT LOG"
    active_filters: set[Severity] = field(default_factory=lambda: {"info", "ok", "warn", "crit"})

    def render(self) -> None:
        imgui = _require_imgui()
        imgui.text_unformatted(self.title)
        for index, severity in enumerate(("info", "ok", "warn", "crit")):
            count = sum(1 for record in self.records if record.severity == severity)
            active = severity in self.active_filters
            color_count, var_count = _push_button_colors(
                imgui,
                base=(theme.PANEL_BG_2 if active else theme.PANEL_BG),
                hovered=theme.PANEL_BG_2,
                active=theme.PANEL_BG_3,
                text=_severity_color(severity),
                border=_severity_color(severity),
            )
            if imgui.button(f"{severity.upper()} {count}", width=0.0, height=24.0) and _ctrl_held(imgui):
                if active:
                    self.active_filters.discard(severity)
                else:
                    self.active_filters.add(severity)
            _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
            if index < 3:
                imgui.same_line()
        for record in self._visible_records():
            imgui.text_colored(record.timestamp, *theme.INK_3)
            imgui.same_line()
            imgui.text_colored(record.source, *_severity_color(record.severity))
            imgui.same_line()
            suffix = f" [{record.event_id}]" if record.event_id else ""
            imgui.text_unformatted(f"{record.message}{suffix}")

    def _visible_records(self) -> list[EventRecord]:
        return [record for record in self.records if record.severity in self.active_filters]


@dataclass(frozen=True, slots=True)
class ProcedureStep:
    title: str
    subtitle: str
    status: Literal["pending", "active", "done"] = "pending"
    detail: str = ""


@dataclass
class ProcedureCarousel(ConsoleComponent):
    component_id: str
    steps: list[ProcedureStep]
    active_index: int = 0

    def render(self) -> None:
        imgui = _require_imgui()
        if not self.steps:
            imgui.text_unformatted("No procedure steps.")
            return
        start = max(0, self.active_index - 1)
        end = min(len(self.steps), start + 3)
        for index in range(start, end):
            step = self.steps[index]
            color = theme.ACID if step.status == "done" else theme.ALERT if step.status == "active" else theme.INK_2
            color_count, var_count = _push_button_colors(
                imgui,
                base=theme.PANEL_BG_2,
                hovered=theme.PANEL_BG_3,
                active=theme.PANEL_BG_3,
                text=color,
                border=color,
            )
            if imgui.button(f"{index + 1:02d} {step.title}", width=0.0, height=34.0) and _ctrl_held(imgui):
                self.active_index = index
            _pop_button_colors(imgui, color_count=color_count, var_count=var_count)
            imgui.text_disabled(step.subtitle)
        if self.active_index > 0:
            if imgui.button("Prev", width=70.0, height=28.0) and _ctrl_held(imgui):
                self.active_index -= 1
            imgui.same_line()
        if self.active_index < len(self.steps) - 1:
            if imgui.button("Next", width=70.0, height=28.0) and _ctrl_held(imgui):
                self.active_index += 1


@dataclass(frozen=True, slots=True)
class TelemetryCard:
    label: str
    value: str
    unit: str = ""
    detail: str = ""
    severity: Severity = "info"


@dataclass
class TelemetryFilmstrip(ConsoleComponent):
    component_id: str
    cards: list[TelemetryCard]
    cards_per_view: int = 4
    auto_scroll: bool = False
    scroll_period_s: float = 1.5

    def __post_init__(self) -> None:
        self._offset = 0
        self._last_scroll_s = time.monotonic()

    def consume(self) -> bool:
        if not self.auto_scroll or len(self.cards) <= self.cards_per_view:
            return False
        now = time.monotonic()
        if now - self._last_scroll_s < self.scroll_period_s:
            return False
        self._last_scroll_s = now
        self._offset = (self._offset + 1) % len(self.cards)
        return True

    def render(self) -> None:
        imgui = _require_imgui()
        for index, card in enumerate(self._visible_cards()):
            widget = ReadoutCard(
                component_id=f"{self.component_id}_{index}",
                title=card.label,
                value=f"{card.value}{card.unit}",
                subtitle=card.detail,
                severity=card.severity,
                width=170.0,
                height=84.0,
            )
            widget.render()
            if index < len(self._visible_cards()) - 1:
                imgui.same_line()

    def _visible_cards(self) -> list[TelemetryCard]:
        if len(self.cards) <= self.cards_per_view:
            return list(self.cards)
        items = []
        for index in range(self.cards_per_view):
            items.append(self.cards[(self._offset + index) % len(self.cards)])
        return items


@dataclass
class TelemetryTicker(ConsoleComponent):
    component_id: str
    items: list[str]
    visible_items: int = 5
    auto_scroll: bool = False
    scroll_period_s: float = 1.0

    def __post_init__(self) -> None:
        self._offset = 0
        self._last_scroll_s = time.monotonic()

    def consume(self) -> bool:
        if not self.auto_scroll or len(self.items) <= self.visible_items:
            return False
        now = time.monotonic()
        if now - self._last_scroll_s < self.scroll_period_s:
            return False
        self._last_scroll_s = now
        self._offset = (self._offset + 1) % len(self.items)
        return True

    def render(self) -> None:
        imgui = _require_imgui()
        visible = self._visible_items()
        imgui.text_unformatted(" · ".join(visible))

    def _visible_items(self) -> list[str]:
        if len(self.items) <= self.visible_items:
            return list(self.items)
        return [
            self.items[(self._offset + index) % len(self.items)]
            for index in range(self.visible_items)
        ]


def _severity_color(severity: Severity) -> theme.RGBA:
    if severity == "ok":
        return theme.ACID
    if severity == "warn":
        return theme.ALERT
    if severity == "crit":
        return theme.CRIT
    return theme.INK_2


def _toolbar_palette(
    variant: ToolbarVariant,
    active: bool,
    disabled: bool,
) -> tuple[theme.RGBA, theme.RGBA, theme.RGBA, theme.RGBA, theme.RGBA]:
    if disabled:
        return (
            theme.BUTTON_DISABLED_BG,
            theme.BUTTON_DISABLED_BG,
            theme.BUTTON_DISABLED_BG,
            theme.BUTTON_DISABLED_FG,
            theme.PANEL_BORDER,
        )
    if variant == "crit":
        return (
            theme.BUTTON_ON_CRIT_BASE if active else theme.PANEL_BG_2,
            theme.BUTTON_ON_CRIT_HOVER,
            theme.BUTTON_ON_CRIT_ACTIVE,
            (theme.INK if active else theme.CRIT),
            theme.CRIT,
        )
    if variant == "warn":
        return (
            theme.BUTTON_ON_ALERT_BASE if active else theme.PANEL_BG_2,
            theme.BUTTON_ON_ALERT_HOVER,
            theme.BUTTON_ON_ALERT_ACTIVE,
            (theme.INK if active else theme.ALERT),
            theme.ALERT,
        )
    if variant == "primary":
        return (
            theme.BUTTON_ON_PRIMARY_BASE if active else theme.PANEL_BG_2,
            theme.BUTTON_ON_PRIMARY_HOVER,
            theme.BUTTON_ON_PRIMARY_ACTIVE,
            (theme.VOID if active else theme.ACID),
            theme.ACID,
        )
    return (
        theme.PANEL_BG_2,
        theme.PANEL_BG_3,
        theme.BUTTON_OFF_ACTIVE,
        theme.INK_2,
        theme.PANEL_BORDER,
    )


def _push_button_colors(
    imgui: Any,
    *,
    base: theme.RGBA,
    hovered: theme.RGBA,
    active: theme.RGBA,
    text: theme.RGBA,
    border: theme.RGBA,
) -> tuple[int, int]:
    color_count = 0
    var_count = 0
    imgui.push_style_color(imgui.COLOR_BUTTON, *base)
    color_count += 1
    imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *hovered)
    color_count += 1
    imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *active)
    color_count += 1
    if hasattr(imgui, "COLOR_TEXT"):
        imgui.push_style_color(imgui.COLOR_TEXT, *text)
        color_count += 1
    if hasattr(imgui, "COLOR_BORDER"):
        imgui.push_style_color(imgui.COLOR_BORDER, *border)
        color_count += 1
    if hasattr(imgui, "STYLE_FRAME_BORDER_SIZE"):
        imgui.push_style_var(imgui.STYLE_FRAME_BORDER_SIZE, 1.0)
        var_count += 1
    if hasattr(imgui, "STYLE_FRAME_ROUNDING"):
        imgui.push_style_var(imgui.STYLE_FRAME_ROUNDING, 0.0)
        var_count += 1
    return color_count, var_count


def _pop_button_colors(imgui: Any, *, color_count: int, var_count: int) -> None:
    if var_count > 0:
        imgui.pop_style_var(var_count)
    if color_count > 0:
        imgui.pop_style_color(color_count)


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for operator widget rendering. Install a Dear ImGui binding."
        ) from exc
    return imgui


def _ctrl_held(imgui: Any) -> bool:
    """Return True when the Ctrl modifier is currently held."""
    io = imgui.get_io()
    return bool(getattr(io, 'key_ctrl', False))


__all__ = [
    "ConsoleComponent",
    "EventLogPanel",
    "EventRecord",
    "KeyValuePanel",
    "KeyValueRow",
    "MicroButton",
    "OperatorToolbar",
    "ProcedureCarousel",
    "ProcedureStep",
    "ReadoutCard",
    "Subbar",
    "TelemetryCard",
    "TelemetryFilmstrip",
    "TelemetryTicker",
    "ToolbarButton",
    "ToolbarMeter",
    "ToolbarSearch",
]
