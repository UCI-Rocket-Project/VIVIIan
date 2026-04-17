from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Protocol, runtime_checkable

from viviian.gui_utils import MomentaryButton, SensorGraph, SensorGauge, StateButton, ToggleButton

try:
    from viviian.gui_utils import ModelViewer
except ImportError:  # pragma: no cover - optional export
    ModelViewer = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class RenderContext:
    gate_states: Mapping[str, bool]
    interlock_states: Mapping[str, bool]


@runtime_checkable
class FrontendComponent(Protocol):
    component_id: str

    def required_streams(self) -> tuple[str, ...]:
        ...

    def bind(self, readers: Mapping[str, Any]) -> None:
        ...

    def consume(self) -> bool:
        ...

    def render(self) -> None:
        ...


@runtime_checkable
class WritableFrontendComponent(FrontendComponent, Protocol):
    def snapshot_value(self) -> float:
        ...

    def after_snapshot_written(self) -> bool:
        ...


@dataclass
class BaseComponentAdapter:
    component: Any
    component_id: str
    required_stream_names: tuple[str, ...]
    state_id: str | None = None
    is_writable: bool = False
    initial_output_value: float = 0.0

    def bind(self, readers: Mapping[str, Any]) -> None:
        binder = getattr(self.component, "bind", None)
        if callable(binder):
            binder(readers)

    def consume(self) -> bool:
        consumer = getattr(self.component, "consume", None)
        if callable(consumer):
            return bool(consumer())
        return False

    def render(self, context: RenderContext | None = None) -> bool:
        renderer = getattr(self.component, "render", None)
        if not callable(renderer):
            raise TypeError(f"Component {self.component_id!r} does not expose render().")
        renderer()
        return False

    def snapshot_value(self) -> float:
        raise RuntimeError(f"Component {self.component_id!r} is not writable.")

    def after_snapshot_written(self) -> bool:
        return False

    def bool_state(self) -> bool | None:
        return None

    def close(self) -> None:
        closer = getattr(self.component, "close", None)
        if callable(closer):
            closer()


class ButtonComponentAdapter(BaseComponentAdapter):
    def __init__(self, button: StateButton) -> None:
        self._pulse_value: float | None = None
        initial_value = self._initial_value(button)
        super().__init__(
            component=button,
            component_id=button.button_id,
            required_stream_names=(),
            state_id=button.state_id,
            is_writable=True,
            initial_output_value=initial_value,
        )
        self._current_value = float(initial_value)

    def render(self, context: RenderContext | None = None) -> bool:
        button: StateButton = self.component
        render_kwargs: dict[str, Any] = {}
        if context is not None and (button.gate_id is not None or button.interlock_ids):
            render_kwargs = {
                "gate_states": context.gate_states,
                "interlock_states": context.interlock_states,
            }

        update = button.render(**render_kwargs)
        if update is None:
            return False

        if isinstance(button, ToggleButton):
            self._current_value = _coerce_float64_state(
                button.state,
                component_id=button.button_id,
            )
            return True

        if isinstance(button, MomentaryButton):
            self._pulse_value = _coerce_float64_state(
                update.state,
                component_id=button.button_id,
            )
            return True

        self._current_value = _coerce_float64_state(
            update.state,
            component_id=button.button_id,
        )
        return True

    def snapshot_value(self) -> float:
        if self._pulse_value is not None:
            return self._pulse_value
        return self._current_value

    def after_snapshot_written(self) -> bool:
        if self._pulse_value is None:
            return False
        self._pulse_value = None
        return True

    def bool_state(self) -> bool | None:
        return bool(self.snapshot_value())

    @staticmethod
    def _initial_value(button: StateButton) -> float:
        if isinstance(button, MomentaryButton):
            _coerce_float64_state(button.state, component_id=button.button_id)
            return 0.0
        return _coerce_float64_state(button.state, component_id=button.button_id)


class GenericComponentAdapter(BaseComponentAdapter):
    def __init__(self, component: FrontendComponent) -> None:
        component_id = str(getattr(component, "component_id", "")).strip()
        if not component_id:
            raise ValueError("Custom frontend components must expose a non-empty component_id.")
        required_streams = tuple(str(name) for name in component.required_streams())
        if any(not name for name in required_streams):
            raise ValueError(f"Component {component_id!r} returned an empty stream name.")

        is_writable = isinstance(component, WritableFrontendComponent)
        initial_output = 0.0
        state_id = getattr(component, "state_id", None)
        if is_writable:
            initial_output = _coerce_float64_state(
                component.snapshot_value(),
                component_id=component_id,
            )
        super().__init__(
            component=component,
            component_id=component_id,
            required_stream_names=required_streams,
            state_id=(str(state_id) if state_id is not None else None),
            is_writable=is_writable,
            initial_output_value=initial_output,
        )

    def render(self, context: RenderContext | None = None) -> bool:
        renderer = getattr(self.component, "render", None)
        if not callable(renderer):
            raise TypeError(f"Component {self.component_id!r} does not expose render().")
        renderer()
        return False

    def snapshot_value(self) -> float:
        if not self.is_writable:
            return super().snapshot_value()
        return _coerce_float64_state(
            self.component.snapshot_value(),
            component_id=self.component_id,
        )

    def after_snapshot_written(self) -> bool:
        if not self.is_writable:
            return False
        writer_hook = getattr(self.component, "after_snapshot_written", None)
        if callable(writer_hook):
            return bool(writer_hook())
        return False

    def bool_state(self) -> bool | None:
        if not self.is_writable or self.state_id is None:
            return None
        return bool(self.snapshot_value())


def adapt_component(component: Any) -> BaseComponentAdapter:
    if isinstance(component, StateButton):
        return ButtonComponentAdapter(component)

    if isinstance(component, SensorGraph):
        return BaseComponentAdapter(
            component=component,
            component_id=component.graph_id,
            required_stream_names=_dedupe_preserve_order(
                tuple(item.stream_name for item in component.series)
            ),
        )

    if isinstance(component, SensorGauge):
        return BaseComponentAdapter(
            component=component,
            component_id=component.gauge_id,
            required_stream_names=(component.stream_name,),
        )

    if ModelViewer is not None and isinstance(component, ModelViewer):
        body_streams = tuple(binding.value_stream_name for binding in component.body_bindings)
        return BaseComponentAdapter(
            component=component,
            component_id=component.viewer_id,
            required_stream_names=_dedupe_preserve_order(body_streams + (component.pose_stream_name,)),
        )

    if isinstance(component, FrontendComponent):
        return GenericComponentAdapter(component)

    raise TypeError(
        "Unsupported frontend component type. "
        "Use gui_utils widgets or expose the FrontendComponent protocol."
    )


def _coerce_float64_state(value: Any, *, component_id: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"Component {component_id!r} produced a non-finite state value.")
        return float(value)
    raise TypeError(
        f"Component {component_id!r} must emit bool, int, or float state values for the frontend output stream."
    )


def _dedupe_preserve_order(values: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


__all__ = [
    "BaseComponentAdapter",
    "ButtonComponentAdapter",
    "FrontendComponent",
    "GenericComponentAdapter",
    "RenderContext",
    "WritableFrontendComponent",
    "adapt_component",
]
