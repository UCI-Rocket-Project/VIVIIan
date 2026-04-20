from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal, Mapping

from . import theme
from .chrome import available_width, draw_hazard_strip, estimate_text_size, rgba_u32, xy
from .configure import (
    ScalarState,
    parse_optional_color_rgba,
    parse_scalar_state,
    parse_string_tuple,
    read_toml_document,
    require_keys,
    require_kind,
    toml_bool,
    toml_float_array,
    toml_header,
    toml_scalar,
    toml_string,
    toml_string_array,
    write_toml_document,
)

ButtonVariant = Literal["neutral", "primary", "alert", "crit"]


@dataclass(frozen=True, slots=True)
class ButtonStateUpdate:
    button_id: str
    state_id: str
    state: ScalarState


@dataclass
class StateButton:
    KIND: ClassVar[str] = "state_button"

    button_id: str
    label: str
    state_id: str
    state: ScalarState
    gate_id: str | None = None
    interlock_ids: tuple[str, ...] = ()
    enabled_by_default: bool = True
    color_rgba: tuple[float, float, float, float] | None = None
    theme_name: theme.GuiThemeName = "legacy"
    variant: ButtonVariant | None = None

    def __post_init__(self) -> None:
        if not self.button_id:
            raise ValueError("button_id must be non-empty.")
        if not self.label:
            raise ValueError("label must be non-empty.")
        if not self.state_id:
            raise ValueError("state_id must be non-empty.")
        self.state = parse_scalar_state(self.state)
        self.interlock_ids = parse_string_tuple(self.interlock_ids, "interlock_ids")
        if self.gate_id is not None:
            self.gate_id = str(self.gate_id)
        self.color_rgba = parse_optional_color_rgba(self.color_rgba)
        if self.theme_name not in ("legacy", "tau_ceti"):
            raise ValueError("theme_name must be 'legacy' or 'tau_ceti'.")
        if self.variant not in (None, "neutral", "primary", "alert", "crit"):
            raise ValueError("variant must be one of: neutral, primary, alert, crit.")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"button_id={self.button_id!r}, "
            f"state_id={self.state_id!r}, "
            f"state={self.state!r}, "
            f"gate_id={self.gate_id!r}, "
            f"interlock_ids={self.interlock_ids!r}, "
            f"theme_name={self.theme_name!r}, "
            f"variant={self.variant!r})"
        )

    def is_enabled(
        self,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> bool:
        enabled = bool(self.enabled_by_default)
        if self.gate_id is not None:
            enabled = enabled and bool((gate_states or {}).get(self.gate_id, False))
        if self.interlock_ids:
            states = interlock_states or {}
            enabled = enabled and all(bool(states.get(item, False)) for item in self.interlock_ids)
        return enabled

    def build_widget(
        self,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> Callable[[], ButtonStateUpdate | None]:
        return lambda: self.render(
            gate_states=gate_states,
            interlock_states=interlock_states,
        )

    def render(
        self,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> ButtonStateUpdate | None:
        imgui = _require_imgui()
        if self.theme_name == "tau_ceti":
            return self._render_tau_ceti(
                imgui,
                gate_states=gate_states,
                interlock_states=interlock_states,
            )
        return self._render_legacy(
            imgui,
            gate_states=gate_states,
            interlock_states=interlock_states,
        )

    def _render_legacy(
        self,
        imgui: Any,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> ButtonStateUpdate | None:
        enabled = self.is_enabled(
            gate_states=gate_states,
            interlock_states=interlock_states,
        )

        button_color, hover_color, active_color = self._button_colors(enabled=enabled)
        imgui.push_style_color(imgui.COLOR_BUTTON, *button_color)
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *hover_color)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *active_color)
        pressed = imgui.button(
            self._button_label(),
            width=0.0,
            height=34.0,
        )
        imgui.pop_style_color(3)

        self._render_status_text(imgui, enabled=enabled)
        if not enabled or not pressed or not _ctrl_held(imgui):
            return None
        return self._emit_update()

    def _render_tau_ceti(
        self,
        imgui: Any,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> ButtonStateUpdate | None:
        enabled = self.is_enabled(
            gate_states=gate_states,
            interlock_states=interlock_states,
        )

        base, hover, active = self._button_colors(enabled=enabled)
        border = self._border_color(enabled=enabled)
        widget_width = available_width(imgui, fallback=220.0)
        widget_height = theme.BUTTON_HEIGHT
        color_count = 0
        var_count = 0

        imgui.push_style_color(imgui.COLOR_BUTTON, *base)
        color_count += 1
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *hover)
        color_count += 1
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *active)
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
        if hasattr(imgui, "STYLE_FRAME_PADDING"):
            imgui.push_style_var(imgui.STYLE_FRAME_PADDING, (0.0, 0.0))
            var_count += 1

        pressed = imgui.button(f"##{self.button_id}", width=widget_width, height=widget_height)
        x0, y0 = xy(imgui.get_item_rect_min())
        x1, y1 = xy(imgui.get_item_rect_max())
        draw_list = imgui.get_window_draw_list()

        led_color = self._led_color(enabled=enabled)
        state_text = self._state_text()
        state_text_width, state_text_height = estimate_text_size(imgui, state_text)
        state_field_width = max(56.0, state_text_width + 28.0)
        led_x1 = x0 + theme.BUTTON_LED_TAB_PX
        state_x0 = max(led_x1 + 72.0, x1 - state_field_width)

        draw_list.add_rect_filled(x0, y0, led_x1, y1, rgba_u32(imgui, led_color))
        if state_x0 < x1:
            draw_list.add_line(state_x0, y0, state_x0, y1, rgba_u32(imgui, border), 1.0)

        state_bg, state_fg = self._state_field_colors(enabled=enabled)
        if state_x0 < x1:
            draw_list.add_rect_filled(state_x0, y0, x1, y1, rgba_u32(imgui, state_bg))

        label_x = led_x1 + 14.0
        label_y = y0 + 9.0
        meta_y = y0 + 28.0
        draw_list.add_text(label_x, label_y, rgba_u32(imgui, self._label_color(enabled=enabled)), self.label.upper())
        draw_list.add_text(label_x, meta_y, rgba_u32(imgui, theme.BUTTON_META_FG), self._meta_text(enabled=enabled))

        state_x = state_x0 + max(8.0, (state_field_width - state_text_width) * 0.5)
        state_y = y0 + max(8.0, (widget_height - state_text_height) * 0.5)
        draw_list.add_text(state_x, state_y, rgba_u32(imgui, state_fg), state_text)

        draw_hazard_strip(
            imgui,
            draw_list,
            x0=x0,
            y0=max(y0, y1 - theme.BUTTON_HAZARD_PX),
            x1=x1,
            y1=y1,
            stripe_color=led_color,
        )

        if var_count > 0:
            imgui.pop_style_var(var_count)
        if color_count > 0:
            imgui.pop_style_color(color_count)

        if not enabled or not pressed or not _ctrl_held(imgui):
            return None
        return self._emit_update()

    def export(self, path: str | Path) -> Path:
        lines = toml_header(self.KIND)
        lines.extend(
            [
                f"button_id = {toml_string(self.button_id)}",
                f"label = {toml_string(self.label)}",
                f"state_id = {toml_string(self.state_id)}",
                f"state = {toml_scalar(self.state)}",
                f"enabled_by_default = {toml_bool(self.enabled_by_default)}",
            ]
        )
        if self.gate_id is not None:
            lines.append(f"gate_id = {toml_string(self.gate_id)}")
        if self.interlock_ids:
            lines.append(f"interlock_ids = {toml_string_array(self.interlock_ids)}")
        if self.color_rgba is not None:
            lines.append(f"color_rgba = {toml_float_array(self.color_rgba)}")
        if self.theme_name != "legacy":
            lines.append(f"theme_name = {toml_string(self.theme_name)}")
        if self.variant is not None:
            lines.append(f"variant = {toml_string(self.variant)}")
        lines.append("")
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "StateButton":
        data = read_toml_document(path)
        kind = require_kind(
            data, cls.KIND, ToggleButton.KIND, MomentaryButton.KIND, SetpointButton.KIND
        )
        target_cls: type[StateButton]
        if kind == cls.KIND:
            target_cls = StateButton
        elif kind == ToggleButton.KIND:
            target_cls = ToggleButton
        elif kind == MomentaryButton.KIND:
            target_cls = MomentaryButton
        else:
            target_cls = SetpointButton

        button = target_cls.from_dict(data)
        if cls is not StateButton and not isinstance(button, cls):
            raise ValueError(f"{path!s} contains kind {kind!r}, not {cls.KIND!r}.")
        return button

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StateButton":
        require_keys(data, cls.KIND, "button_id", "label", "state_id", "state")
        return cls(
            button_id=str(data["button_id"]),
            label=str(data["label"]),
            state_id=str(data["state_id"]),
            state=parse_scalar_state(data["state"]),
            gate_id=(str(data["gate_id"]) if data.get("gate_id") is not None else None),
            interlock_ids=parse_string_tuple(data.get("interlock_ids"), "interlock_ids"),
            enabled_by_default=bool(data.get("enabled_by_default", True)),
            color_rgba=parse_optional_color_rgba(data.get("color_rgba")),
            theme_name=str(data.get("theme_name", "legacy")),
            variant=(str(data["variant"]) if data.get("variant") is not None else None),
        )

    def _button_label(self) -> str:
        return self.label

    def _emit_update(self) -> ButtonStateUpdate:
        return ButtonStateUpdate(
            button_id=self.button_id,
            state_id=self.state_id,
            state=self.state,
        )

    def _button_colors(
        self,
        *,
        enabled: bool,
    ) -> tuple[
        tuple[float, float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float, float],
    ]:
        if self.theme_name == "tau_ceti":
            if not enabled:
                return (
                    theme.BUTTON_DISABLED_BG,
                    theme.BUTTON_DISABLED_BG,
                    theme.BUTTON_DISABLED_BG,
                )
            if self._is_on():
                resolved = self._resolved_variant()
                if resolved == "crit":
                    return (
                        theme.BUTTON_ON_CRIT_BASE,
                        theme.BUTTON_ON_CRIT_HOVER,
                        theme.BUTTON_ON_CRIT_ACTIVE,
                    )
                if resolved == "alert":
                    return (
                        theme.BUTTON_ON_ALERT_BASE,
                        theme.BUTTON_ON_ALERT_HOVER,
                        theme.BUTTON_ON_ALERT_ACTIVE,
                    )
                return (
                    theme.BUTTON_ON_PRIMARY_BASE,
                    theme.BUTTON_ON_PRIMARY_HOVER,
                    theme.BUTTON_ON_PRIMARY_ACTIVE,
                )
            return (
                theme.BUTTON_OFF_BASE,
                theme.BUTTON_OFF_HOVER,
                theme.BUTTON_OFF_ACTIVE,
            )

        if not enabled:
            return (
                (0.080, 0.095, 0.125, 1.0),
                (0.080, 0.095, 0.125, 1.0),
                (0.080, 0.095, 0.125, 1.0),
            )

        base = self.color_rgba or (0.110, 0.225, 0.330, 1.0)
        hovered = tuple(min(1.0, channel + 0.10) for channel in base[:3]) + (1.0,)
        active = tuple(min(1.0, channel + 0.18) for channel in base[:3]) + (1.0,)
        return base, hovered, active

    def _render_status_text(self, imgui: Any, *, enabled: bool) -> None:
        if enabled:
            imgui.text_disabled(f"state_id: {self.state_id}")
            return
        imgui.text_colored("DISABLED", 0.880, 0.420, 0.420, 1.0)
        imgui.same_line()
        imgui.text_disabled(f"state_id: {self.state_id}")

    def _state_text(self) -> str:
        if isinstance(self, ToggleButton):
            return "ON" if bool(self.state) else "OFF"
        if isinstance(self.state, str):
            return self.state.upper()
        if isinstance(self, MomentaryButton):
            return "ARM"
        if isinstance(self.state, bool):
            return "ON" if self.state else "OFF"
        return str(self.state).upper()

    def _meta_text(self, *, enabled: bool) -> str:
        if enabled:
            return f"{self.KIND} · state_id: {self.state_id}"
        reasons: list[str] = []
        if self.gate_id is not None:
            reasons.append(f"gate: {self.gate_id}")
        if self.interlock_ids:
            if len(self.interlock_ids) == 1:
                reasons.append(f"interlock: {self.interlock_ids[0]}")
            else:
                reasons.append(f"interlock×{len(self.interlock_ids)}")
        if not reasons:
            reasons.append("interlock")
        return " · ".join(reasons)

    def _resolved_variant(self) -> ButtonVariant:
        if self.variant is not None:
            return self.variant
        if self.color_rgba is None:
            return "primary"
        red, green, _blue, _alpha = self.color_rgba
        if red > 0.85 and green < 0.22:
            return "crit"
        if red > 0.85 and green < 0.35:
            return "alert"
        return "primary"

    def _led_color(self, *, enabled: bool) -> theme.RGBA:
        if not enabled:
            return theme.INK_4
        resolved = self._resolved_variant()
        if resolved == "neutral":
            return theme.INK_2
        if resolved == "alert":
            return theme.ALERT
        if resolved == "crit":
            return theme.CRIT
        return self.color_rgba or theme.ACID

    def _border_color(self, *, enabled: bool) -> theme.RGBA:
        if not enabled:
            return theme.PANEL_BORDER
        if self._is_on():
            return self._led_color(enabled=enabled)
        return theme.PANEL_BORDER

    def _label_color(self, *, enabled: bool) -> theme.RGBA:
        if not enabled:
            return theme.BUTTON_DISABLED_FG
        if self._is_on():
            return self._led_color(enabled=enabled)
        return theme.INK

    def _state_field_colors(self, *, enabled: bool) -> tuple[theme.RGBA, theme.RGBA]:
        if not enabled:
            return theme.BUTTON_DISABLED_BG, theme.BUTTON_DISABLED_FG
        if self._is_on():
            if self._resolved_variant() == "crit":
                return theme.CRIT, theme.INK
            return self._led_color(enabled=enabled), theme.BUTTON_STATE_FIELD_FG
        return theme.PANEL_BG, theme.INK_2

    def _is_on(self) -> bool:
        if isinstance(self, ToggleButton):
            return bool(self.state)
        return False


class ToggleButton(StateButton):
    KIND = "toggle_button"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.state, bool):
            raise TypeError("ToggleButton.state must be a bool.")

    def _button_label(self) -> str:
        status = "ON" if bool(self.state) else "OFF"
        return f"{self.label} [{status}]"

    def _emit_update(self) -> ButtonStateUpdate:
        new_state = not bool(self.state)
        update = ButtonStateUpdate(
            button_id=self.button_id,
            state_id=self.state_id,
            state=new_state,
        )
        self.state = new_state
        return update


_MOMENTARY_HOLD_SECONDS = 0.35


class MomentaryButton(StateButton):
    KIND = "momentary_button"

    def __post_init__(self) -> None:
        super().__post_init__()
        self._press_timestamp: float | None = None

    def _button_label(self) -> str:
        return self.label

    def _emit_update(self) -> ButtonStateUpdate:
        self._press_timestamp = time.monotonic()
        return ButtonStateUpdate(
            button_id=self.button_id,
            state_id=self.state_id,
            state=self.state,
        )

    def _is_on(self) -> bool:
        if self._press_timestamp is None:
            return False
        return (time.monotonic() - self._press_timestamp) < _MOMENTARY_HOLD_SECONDS


_SETPOINT_MINI_BTN_W = 24.0
_SETPOINT_MINI_BTN_H = 28.0
_SETPOINT_SET_BTN_W = 48.0
_SETPOINT_SET_BTN_GAP = 6.0


@dataclass
class SetpointButton(StateButton):
    """Operator setpoint control: edit with [−]/input/[+], commit with [SET].

    ``state`` tracks the last confirmed (sent) value. ``_pending_value`` is the
    live-edited value that is only pushed to the stream when the operator presses
    [SET]. Pressing [SET] also triggers a momentary flash so the operator gets
    clear feedback that the value was committed.
    """

    KIND: ClassVar[str] = "setpoint_button"

    unit: str = ""
    step: float = 1.0
    min_value: float = 0.0
    max_value: float = 1000.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.state, bool) or not isinstance(self.state, (int, float)):
            raise TypeError("SetpointButton.state must be a numeric value (int or float).")
        if self.step <= 0:
            raise ValueError("SetpointButton.step must be positive.")
        if self.min_value >= self.max_value:
            raise ValueError("SetpointButton.min_value must be less than max_value.")
        self.unit = str(self.unit)
        self.state = max(self.min_value, min(self.max_value, float(self.state)))
        self._pending_value: float = float(self.state)
        self._press_timestamp: float | None = None

    def _state_text(self) -> str:
        text = f"{self.state:g}"
        return f"{text} {self.unit}" if self.unit else text

    def _is_on(self) -> bool:
        if self._press_timestamp is None:
            return False
        return (time.monotonic() - self._press_timestamp) < _MOMENTARY_HOLD_SECONDS

    def _meta_text(self, *, enabled: bool) -> str:
        confirmed = f"{self.state:g}" + (f" {self.unit}" if self.unit else "")
        if enabled:
            return f"last set: {confirmed} · state_id: {self.state_id}"
        reasons: list[str] = []
        if self.gate_id is not None:
            reasons.append(f"gate: {self.gate_id}")
        if self.interlock_ids:
            reasons.append(f"interlock×{len(self.interlock_ids)}" if len(self.interlock_ids) > 1 else f"interlock: {self.interlock_ids[0]}")
        if not reasons:
            reasons.append("interlock")
        return " · ".join(reasons)

    def _clamp(self, value: float) -> float:
        return max(self.min_value, min(self.max_value, value))

    def _commit(self) -> ButtonStateUpdate:
        self._pending_value = self._clamp(self._pending_value)
        self.state = self._pending_value
        self._press_timestamp = time.monotonic()
        return ButtonStateUpdate(button_id=self.button_id, state_id=self.state_id, state=self.state)

    def _set_button_colors(
        self, *, enabled: bool
    ) -> tuple[
        tuple[float, float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float, float],
    ]:
        if not enabled:
            return theme.BUTTON_DISABLED_BG, theme.BUTTON_DISABLED_BG, theme.BUTTON_DISABLED_BG
        if self._is_on():
            resolved = self._resolved_variant()
            if resolved == "crit":
                return theme.CRIT, theme.CRIT, theme.CRIT
            if resolved == "alert":
                return theme.ALERT, theme.ALERT, theme.ALERT
            return theme.ACID, theme.ACID, theme.ACID
        resolved = self._resolved_variant()
        if resolved == "crit":
            return theme.BUTTON_ON_CRIT_BASE, theme.BUTTON_ON_CRIT_HOVER, theme.BUTTON_ON_CRIT_ACTIVE
        if resolved == "alert":
            return theme.BUTTON_ON_ALERT_BASE, theme.BUTTON_ON_ALERT_HOVER, theme.BUTTON_ON_ALERT_ACTIVE
        return theme.BUTTON_ON_PRIMARY_BASE, theme.BUTTON_ON_PRIMARY_HOVER, theme.BUTTON_ON_PRIMARY_ACTIVE

    def _render_legacy(
        self,
        imgui: Any,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> ButtonStateUpdate | None:
        enabled = self.is_enabled(gate_states=gate_states, interlock_states=interlock_states)
        fmt = f"%.6g {self.unit}" if self.unit else "%.6g"

        adj_base, adj_hover, adj_active = self._button_colors(enabled=enabled)
        imgui.push_style_color(imgui.COLOR_BUTTON, *adj_base)
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *adj_hover)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *adj_active)
        decrement = imgui.button(f"-##{self.button_id}", width=28.0, height=28.0) and _ctrl_held(imgui)
        imgui.pop_style_color(3)

        imgui.same_line()
        imgui.set_next_item_width(110.0)
        input_changed, typed = imgui.input_float(f"##{self.button_id}_in", self._pending_value, 0.0, 0.0, fmt)
        imgui.same_line()

        imgui.push_style_color(imgui.COLOR_BUTTON, *adj_base)
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *adj_hover)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *adj_active)
        increment = imgui.button(f"+##{self.button_id}", width=28.0, height=28.0) and _ctrl_held(imgui)
        imgui.pop_style_color(3)

        imgui.same_line()
        set_base, set_hover, set_active = self._set_button_colors(enabled=enabled)
        imgui.push_style_color(imgui.COLOR_BUTTON, *set_base)
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *set_hover)
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *set_active)
        set_pressed = imgui.button(f"SET##{self.button_id}", width=40.0, height=28.0) and _ctrl_held(imgui)
        imgui.pop_style_color(3)

        if enabled:
            if decrement:
                self._pending_value = self._clamp(self._pending_value - self.step)
            if input_changed:
                self._pending_value = self._clamp(typed)
            if increment:
                self._pending_value = self._clamp(self._pending_value + self.step)

        self._render_status_text(imgui, enabled=enabled)

        if enabled and set_pressed:
            return self._commit()
        return None

    def _render_tau_ceti(
        self,
        imgui: Any,
        *,
        gate_states: Mapping[str, bool] | None = None,
        interlock_states: Mapping[str, bool] | None = None,
    ) -> ButtonStateUpdate | None:
        enabled = self.is_enabled(gate_states=gate_states, interlock_states=interlock_states)

        widget_width = available_width(imgui, fallback=280.0)
        widget_height = theme.BUTTON_HEIGHT
        x0, y0 = xy(imgui.get_cursor_screen_pos())
        x1 = x0 + widget_width
        y1 = y0 + widget_height

        draw_list = imgui.get_window_draw_list()

        # Background drawn first so controls appear on top
        base_bg, _, _ = self._button_colors(enabled=enabled)
        border = self._border_color(enabled=enabled)
        draw_list.add_rect_filled(x0, y0, x1, y1, rgba_u32(imgui, base_bg))

        # Layout geometry
        state_field_width = 56.0
        state_x0 = x1 - state_field_width
        led_x1 = x0 + theme.BUTTON_LED_TAB_PX
        label_x = led_x1 + 14.0
        
        # Controls group: [-], input, [+]
        # We ensure they don't bleed into the SET field or the label area
        controls_padding = 12.0
        max_controls_w = state_x0 - label_x - 110.0 - controls_padding
        mini_buttons_w = _SETPOINT_MINI_BTN_W * 2.0


        # controls the length of the input field, (ie where you intput the value for the sensor/actuator)
        input_w = min(60, max_controls_w - mini_buttons_w)
        
        controls_total_w = mini_buttons_w + input_w
        controls_x = state_x0 - controls_total_w - controls_padding
        controls_y = y0 + (widget_height - _SETPOINT_MINI_BTN_H) * 0.5

        fmt = f"%.6g {self.unit}" if self.unit else "%.6g"

        # Style vars — shared for [-] and [+]
        color_count = 0
        var_count = 0
        adj_base, adj_hover, adj_active = self._button_colors(enabled=enabled)
        imgui.push_style_color(imgui.COLOR_BUTTON, *adj_base)
        color_count += 1
        imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *adj_hover)
        color_count += 1
        imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *adj_active)
        color_count += 1
        if hasattr(imgui, "STYLE_FRAME_ROUNDING"):
            imgui.push_style_var(imgui.STYLE_FRAME_ROUNDING, 0.0)
            var_count += 1
        if hasattr(imgui, "STYLE_FRAME_BORDER_SIZE"):
            imgui.push_style_var(imgui.STYLE_FRAME_BORDER_SIZE, 1.0)
            var_count += 1

        imgui.set_cursor_screen_pos((controls_x, controls_y))
        decrement = imgui.button(f"-##{self.button_id}", width=_SETPOINT_MINI_BTN_W, height=_SETPOINT_MINI_BTN_H) and _ctrl_held(imgui)
        imgui.same_line()
        imgui.set_next_item_width(input_w)
        input_changed, typed = imgui.input_float(f"##{self.button_id}_in", self._pending_value, 0.0, 0.0, fmt)
        imgui.same_line()
        increment = imgui.button(f"+##{self.button_id}", width=_SETPOINT_MINI_BTN_W, height=_SETPOINT_MINI_BTN_H) and _ctrl_held(imgui)

        if var_count > 0:
            imgui.pop_style_var(var_count)
        if color_count > 0:
            imgui.pop_style_color(color_count)

        # SET field background and border (flush right, full height)
        state_bg, state_fg = self._state_field_colors(enabled=enabled)
        draw_list.add_rect_filled(state_x0, y0, x1, y1, rgba_u32(imgui, state_bg))
        draw_list.add_line(state_x0, y0, state_x0, y1, rgba_u32(imgui, border), 1.0)

        # Invisible button for click handling
        imgui.set_cursor_screen_pos((state_x0, y0))
        set_pressed = imgui.invisible_button(f"SET##{self.button_id}", state_field_width, widget_height) and _ctrl_held(imgui)

        # Draw "SET" text centered in the field
        text = "SET"
        tw, th = estimate_text_size(imgui, text)
        tx = state_x0 + (state_field_width - tw) * 0.5
        ty = y0 + (widget_height - th) * 0.5
        draw_list.add_text(tx, ty, rgba_u32(imgui, state_fg), text)

        if enabled:
            if decrement:
                self._pending_value = self._clamp(self._pending_value - self.step)
            if input_changed:
                self._pending_value = self._clamp(typed)
            if increment:
                self._pending_value = self._clamp(self._pending_value + self.step)

        # Restore cursor to below the widget
        imgui.set_cursor_screen_pos((x0, y1 + 4.0))

        # Draw overlays (appear on top of controls via draw_list ordering)
        led_color = self._led_color(enabled=enabled)
        draw_list.add_rect_filled(x0, y0, led_x1, y1, rgba_u32(imgui, led_color))

        label_y = y0 + 9.0
        meta_y = y0 + 28.0
        draw_list.add_text(label_x, label_y, rgba_u32(imgui, self._label_color(enabled=enabled)), self.label.upper())
        draw_list.add_text(label_x, meta_y, rgba_u32(imgui, theme.BUTTON_META_FG), self._meta_text(enabled=enabled))

        draw_hazard_strip(
            imgui,
            draw_list,
            x0=x0,
            y0=max(y0, y1 - theme.BUTTON_HAZARD_PX),
            x1=x1,
            y1=y1,
            stripe_color=led_color,
        )

        if enabled and set_pressed:
            return self._commit()
        return None

    def export(self, path: str | Path) -> Path:
        lines = toml_header(self.KIND)
        lines.extend(
            [
                f"button_id = {toml_string(self.button_id)}",
                f"label = {toml_string(self.label)}",
                f"state_id = {toml_string(self.state_id)}",
                f"state = {toml_scalar(self.state)}",
                f"min_value = {toml_scalar(self.min_value)}",
                f"max_value = {toml_scalar(self.max_value)}",
                f"step = {toml_scalar(self.step)}",
                f"enabled_by_default = {toml_bool(self.enabled_by_default)}",
            ]
        )
        if self.unit:
            lines.append(f"unit = {toml_string(self.unit)}")
        if self.gate_id is not None:
            lines.append(f"gate_id = {toml_string(self.gate_id)}")
        if self.interlock_ids:
            lines.append(f"interlock_ids = {toml_string_array(self.interlock_ids)}")
        if self.color_rgba is not None:
            lines.append(f"color_rgba = {toml_float_array(self.color_rgba)}")
        if self.theme_name != "legacy":
            lines.append(f"theme_name = {toml_string(self.theme_name)}")
        if self.variant is not None:
            lines.append(f"variant = {toml_string(self.variant)}")
        lines.append("")
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SetpointButton":
        require_keys(data, cls.KIND, "button_id", "label", "state_id", "state", "min_value", "max_value")
        return cls(
            button_id=str(data["button_id"]),
            label=str(data["label"]),
            state_id=str(data["state_id"]),
            state=parse_scalar_state(data["state"]),
            min_value=float(data["min_value"]),
            max_value=float(data["max_value"]),
            step=float(data.get("step", 1.0)),
            unit=str(data.get("unit", "")),
            gate_id=(str(data["gate_id"]) if data.get("gate_id") is not None else None),
            interlock_ids=parse_string_tuple(data.get("interlock_ids"), "interlock_ids"),
            enabled_by_default=bool(data.get("enabled_by_default", True)),
            color_rgba=parse_optional_color_rgba(data.get("color_rgba")),
            theme_name=str(data.get("theme_name", "legacy")),
            variant=(str(data["variant"]) if data.get("variant") is not None else None),
        )


def reconstruct_button(path: str | Path) -> StateButton:
    return StateButton.reconstruct(path)


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for button rendering. Install a Dear ImGui binding."
        ) from exc
    return imgui


def _ctrl_held(imgui: Any) -> bool:
    """Return True when the Ctrl modifier is currently held."""
    io = imgui.get_io()
    return bool(getattr(io, 'key_ctrl', False))
