from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping

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

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"button_id={self.button_id!r}, "
            f"state_id={self.state_id!r}, "
            f"state={self.state!r}, "
            f"gate_id={self.gate_id!r}, "
            f"interlock_ids={self.interlock_ids!r})"
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
        if not enabled or not pressed:
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
        lines.append("")
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "StateButton":
        data = read_toml_document(path)
        kind = require_kind(data, cls.KIND, ToggleButton.KIND, MomentaryButton.KIND)
        target_cls: type[StateButton]
        if kind == cls.KIND:
            target_cls = StateButton
        elif kind == ToggleButton.KIND:
            target_cls = ToggleButton
        else:
            target_cls = MomentaryButton

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


class MomentaryButton(StateButton):
    KIND = "momentary_button"

    def _button_label(self) -> str:
        return self.label


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
