from __future__ import annotations

from dataclasses import dataclass
from math import cos, exp, floor, radians, sin
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping

from ._streaming import (
    drain_numeric_reader,
    normalize_numeric_batch,
    validate_numeric_reader,
)
from .configure import (
    parse_color_rgba,
    read_toml_document,
    require_keys,
    require_kind,
    toml_float_array,
    toml_header,
    toml_string,
    write_toml_document,
)

"""Single-value telemetry gauges with TOML persistence and lightweight ImGui rendering.

The gauge layer mirrors the graph layer's top-level lifecycle:

- bind a named numeric reader
- consume the latest available finite sample
- animate display state toward the latest target sample
- render a compact ImGui widget
- export and reconstruct configuration from TOML

`SensorGauge` owns the shared telemetry and persistence logic. Concrete subclasses
only supply face-specific geometry and extra configuration fields.
"""

_DEFAULT_LOW_COLOR = (0.200, 0.820, 0.320, 1.0)
_DEFAULT_HIGH_COLOR = (0.920, 0.220, 0.180, 1.0)
_DEFAULT_FRAME_BG = (0.028, 0.040, 0.065, 1.0)
_DEFAULT_FRAME_BORDER = (0.150, 0.235, 0.330, 1.0)
_DEFAULT_INACTIVE_SEGMENT = (0.090, 0.110, 0.145, 1.0)
_DEFAULT_TEXT_ACCENT = (0.650, 0.760, 0.860, 1.0)
_DEFAULT_PADDING = 12.0
_DEFAULT_ARC_SEGMENTS = 48
_DEFAULT_MAJOR_TICK_COUNT = 5
_DEFAULT_MINOR_TICKS_PER_INTERVAL = 3
_DEFAULT_CANONICAL_LED_SEGMENTS = 10
_LED_ORANGE_BLEND = 0.58
_LED_ORANGE_RED_BLEND = 0.78
_MIN_DELTA_TIME = 1.0e-3
_DEFAULT_ANIMATION_RESPONSE_HZ = 8.0
_DEFAULT_ANALOG_SWEEP_START_DEGREES = 210.0
_DEFAULT_ANALOG_SWEEP_END_DEGREES = -30.0
_DEFAULT_ANALOG_ARC_THICKNESS = 14.0
_DEFAULT_ANALOG_NEEDLE_THICKNESS = 3.0
_DEFAULT_LED_SEGMENT_COUNT = 10
_DEFAULT_LED_SEGMENT_GAP_RATIO = 0.12
_HALF_SWEEP_COSINE = 0.8660254037844386
_ANALOG_VERTICAL_RADIUS_SPAN = 1.5


@dataclass(frozen=True, slots=True)
class _AnalogLayout:
    center_x: float
    center_y: float
    radius: float
    major_tick_outer_radius: float
    major_tick_inner_radius: float
    minor_tick_outer_radius: float
    minor_tick_inner_radius: float
    label_radius: float
    needle_radius: float


class SensorGauge:
    """Serializable single-value telemetry widget with damped display state.

    Configuration fields are persistent. Runtime fields such as the bound reader,
    latest timestamp, target value, and display value are rebuilt each run.
    """

    KIND: ClassVar[str] = "sensor_gauge"
    DEFAULT_WIDTH: ClassVar[float] = 220.0
    DEFAULT_HEIGHT: ClassVar[float] = 160.0

    def __init__(
        self,
        gauge_id: str,
        *,
        label: str,
        stream_name: str,
        low_value: float,
        high_value: float,
        animation_response_hz: float = _DEFAULT_ANIMATION_RESPONSE_HZ,
        low_color_rgba: tuple[float, float, float, float] = _DEFAULT_LOW_COLOR,
        high_color_rgba: tuple[float, float, float, float] = _DEFAULT_HIGH_COLOR,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        if not gauge_id:
            raise ValueError("gauge_id must be non-empty.")
        if not label:
            raise ValueError("label must be non-empty.")
        if not stream_name:
            raise ValueError("stream_name must be non-empty.")

        low_float = float(low_value)
        high_float = float(high_value)
        if high_float <= low_float:
            raise ValueError("high_value must be greater than low_value.")
        response_hz = float(animation_response_hz)
        if response_hz <= 0.0:
            raise ValueError("animation_response_hz must be greater than 0.")

        resolved_width = self.DEFAULT_WIDTH if width is None else float(width)
        resolved_height = self.DEFAULT_HEIGHT if height is None else float(height)
        if resolved_width <= 0.0 or resolved_height <= 0.0:
            raise ValueError("width and height must be greater than 0.")

        self.gauge_id = gauge_id
        self.label = label
        self.stream_name = stream_name
        self.low_value = low_float
        self.high_value = high_float
        self.animation_response_hz = response_hz
        self.low_color_rgba = parse_color_rgba(low_color_rgba, field_name="low_color_rgba")
        self.high_color_rgba = parse_color_rgba(
            high_color_rgba, field_name="high_color_rgba"
        )
        self.width = resolved_width
        self.height = resolved_height

        self._reader: Any | None = None
        self._latest_timestamp: float | None = None
        self._target_value = self.low_value
        self._display_value = self.low_value
        self._has_value = False

    def __repr__(self) -> str:
        details = self._repr_details()
        suffix = f", {details}" if details else ""
        return (
            f"{type(self).__name__}("
            f"gauge_id={self.gauge_id!r}, "
            f"stream_name={self.stream_name!r}, "
            f"low_value={self.low_value!r}, "
            f"high_value={self.high_value!r}"
            f"{suffix})"
        )

    @property
    def target_value(self) -> float:
        return self._target_value

    @property
    def display_value(self) -> float:
        return self._display_value

    @property
    def latest_timestamp(self) -> float | None:
        return self._latest_timestamp

    @property
    def has_value(self) -> bool:
        return self._has_value

    def normalized_fraction(self, value: float) -> float:
        return _normalize_fraction(value, low_value=self.low_value, high_value=self.high_value)

    def displayed_fraction(self) -> float:
        return self.normalized_fraction(self._display_value)

    def target_fraction(self) -> float:
        return self.normalized_fraction(self._target_value)

    def bind(self, readers: Mapping[str, Any]) -> None:
        reader = readers.get(self.stream_name)
        if reader is None:
            raise KeyError(
                f"{type(self).__name__} {self.gauge_id!r} requires reader {self.stream_name!r}."
            )
        validate_numeric_reader(self.stream_name, reader)
        if hasattr(reader, "set_blocking"):
            reader.set_blocking(False)
        self._reader = reader
        self.reset_history()

    def reset_history(self) -> None:
        self._latest_timestamp = None
        self._target_value = self.low_value
        self._display_value = self.low_value
        self._has_value = False

    def consume(self) -> bool:
        if self._reader is None:
            return False

        had_update = False
        rewound = False
        for frame in drain_numeric_reader(self._reader):
            timestamps, values = normalize_numeric_batch(frame, context_name="gauge batch")
            if timestamps.size == 0:
                continue
            latest_timestamp = float(timestamps[-1])
            latest_value = float(values[-1])
            if self._latest_timestamp is not None and latest_timestamp < self._latest_timestamp:
                rewound = True
            self._latest_timestamp = latest_timestamp
            self._target_value = latest_value
            self._has_value = True
            had_update = True

        if had_update and rewound:
            self._display_value = self._target_value

        return had_update

    def build_dashboard_hooks(
        self,
        readers: Mapping[str, Any],
    ) -> tuple[Callable[[], bool], Callable[[], None]]:
        self.bind(readers)
        return self.consume, self.render

    def render(self) -> None:
        imgui = _require_imgui()
        self._display_value = _advance_display_value(
            current_value=self._display_value,
            target_value=self._target_value,
            response_hz=self.animation_response_hz,
            delta_time=_imgui_delta_time(imgui),
        )

        imgui.text_unformatted(self.label)
        status = "--" if not self._has_value else f"{self._display_value:.3f}"
        imgui.text_disabled(status)

        draw_list, outer_bounds, inner_bounds = _begin_gauge_panel(
            imgui,
            width=_resolved_widget_width(imgui, self.width),
            height=self.height,
        )
        self._render_face(
            imgui,
            draw_list,
            outer_bounds=outer_bounds,
            inner_bounds=inner_bounds,
        )

    def export(self, path: str | Path) -> Path:
        lines = toml_header(self.KIND)
        lines.extend(self._export_lines())
        lines.append("")
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "SensorGauge":
        data = read_toml_document(path)
        kind = require_kind(data, AnalogNeedleGauge.KIND, LedBarGauge.KIND)
        target_cls: type[SensorGauge]
        if kind == AnalogNeedleGauge.KIND:
            target_cls = AnalogNeedleGauge
        else:
            target_cls = LedBarGauge

        gauge = target_cls.from_dict(data)
        if cls is not SensorGauge and not isinstance(gauge, cls):
            raise ValueError(f"{path!s} contains kind {kind!r}, not {cls.KIND!r}.")
        return gauge

    @classmethod
    def _common_kwargs_from_dict(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        require_keys(
            data,
            str(data.get("kind", cls.KIND)),
            "gauge_id",
            "label",
            "stream_name",
            "low_value",
            "high_value",
        )
        return {
            "gauge_id": str(data["gauge_id"]),
            "label": str(data["label"]),
            "stream_name": str(data["stream_name"]),
            "low_value": float(data["low_value"]),
            "high_value": float(data["high_value"]),
            "animation_response_hz": float(data.get("animation_response_hz", 8.0)),
            "low_color_rgba": parse_color_rgba(
                data.get("low_color_rgba", _DEFAULT_LOW_COLOR),
                field_name="low_color_rgba",
            ),
            "high_color_rgba": parse_color_rgba(
                data.get("high_color_rgba", _DEFAULT_HIGH_COLOR),
                field_name="high_color_rgba",
            ),
            "width": float(data.get("width", cls.DEFAULT_WIDTH)),
            "height": float(data.get("height", cls.DEFAULT_HEIGHT)),
        }

    def _export_lines(self) -> list[str]:
        return [
            f"gauge_id = {toml_string(self.gauge_id)}",
            f"label = {toml_string(self.label)}",
            f"stream_name = {toml_string(self.stream_name)}",
            f"low_value = {self.low_value!r}",
            f"high_value = {self.high_value!r}",
            f"animation_response_hz = {self.animation_response_hz!r}",
            f"low_color_rgba = {toml_float_array(self.low_color_rgba)}",
            f"high_color_rgba = {toml_float_array(self.high_color_rgba)}",
            f"width = {self.width!r}",
            f"height = {self.height!r}",
        ]

    def _repr_details(self) -> str:
        return ""

    def _render_face(
        self,
        imgui: Any,
        draw_list: Any,
        *,
        outer_bounds: tuple[float, float, float, float],
        inner_bounds: tuple[float, float, float, float],
    ) -> None:
        raise NotImplementedError


class AnalogNeedleGauge(SensorGauge):
    """Circular telemetry dial with a static range sweep and animated needle."""

    KIND = "analog_needle_gauge"
    DEFAULT_WIDTH = 196.0
    DEFAULT_HEIGHT = 156.0

    def __init__(
        self,
        gauge_id: str,
        *,
        label: str,
        stream_name: str,
        low_value: float,
        high_value: float,
        animation_response_hz: float = _DEFAULT_ANIMATION_RESPONSE_HZ,
        low_color_rgba: tuple[float, float, float, float] = _DEFAULT_LOW_COLOR,
        high_color_rgba: tuple[float, float, float, float] = _DEFAULT_HIGH_COLOR,
        width: float | None = None,
        height: float | None = None,
        sweep_start_degrees: float = _DEFAULT_ANALOG_SWEEP_START_DEGREES,
        sweep_end_degrees: float = _DEFAULT_ANALOG_SWEEP_END_DEGREES,
        arc_thickness: float = _DEFAULT_ANALOG_ARC_THICKNESS,
        needle_thickness: float = _DEFAULT_ANALOG_NEEDLE_THICKNESS,
    ) -> None:
        super().__init__(
            gauge_id,
            label=label,
            stream_name=stream_name,
            low_value=low_value,
            high_value=high_value,
            animation_response_hz=animation_response_hz,
            low_color_rgba=low_color_rgba,
            high_color_rgba=high_color_rgba,
            width=width,
            height=height,
        )
        self.sweep_start_degrees = float(sweep_start_degrees)
        self.sweep_end_degrees = float(sweep_end_degrees)
        self.arc_thickness = float(arc_thickness)
        self.needle_thickness = float(needle_thickness)
        if self.arc_thickness <= 0.0 or self.needle_thickness <= 0.0:
            raise ValueError("Analog gauge thickness values must be greater than 0.")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnalogNeedleGauge":
        require_kind(data, cls.KIND)
        return cls(
            **cls._common_kwargs_from_dict(data),
            sweep_start_degrees=float(
                data.get("sweep_start_degrees", _DEFAULT_ANALOG_SWEEP_START_DEGREES)
            ),
            sweep_end_degrees=float(
                data.get("sweep_end_degrees", _DEFAULT_ANALOG_SWEEP_END_DEGREES)
            ),
            arc_thickness=float(data.get("arc_thickness", _DEFAULT_ANALOG_ARC_THICKNESS)),
            needle_thickness=float(
                data.get("needle_thickness", _DEFAULT_ANALOG_NEEDLE_THICKNESS)
            ),
        )

    def needle_angle_degrees(self, *, use_display_value: bool = True) -> float:
        fraction = self.displayed_fraction() if use_display_value else self.target_fraction()
        return _angle_for_fraction(
            fraction,
            start_degrees=self.sweep_start_degrees,
            end_degrees=self.sweep_end_degrees,
        )

    def _export_lines(self) -> list[str]:
        return [
            *super()._export_lines(),
            f"sweep_start_degrees = {self.sweep_start_degrees!r}",
            f"sweep_end_degrees = {self.sweep_end_degrees!r}",
            f"arc_thickness = {self.arc_thickness!r}",
            f"needle_thickness = {self.needle_thickness!r}",
        ]

    def _repr_details(self) -> str:
        return (
            f"sweep_start_degrees={self.sweep_start_degrees!r}, "
            f"sweep_end_degrees={self.sweep_end_degrees!r}"
        )

    def _render_face(
        self,
        imgui: Any,
        draw_list: Any,
        *,
        outer_bounds: tuple[float, float, float, float],
        inner_bounds: tuple[float, float, float, float],
    ) -> None:
        del outer_bounds
        layout = _compute_analog_layout(inner_bounds, arc_thickness=self.arc_thickness)

        for segment_index in range(_DEFAULT_ARC_SEGMENTS):
            start_fraction = segment_index / _DEFAULT_ARC_SEGMENTS
            end_fraction = (segment_index + 1) / _DEFAULT_ARC_SEGMENTS
            mid_fraction = 0.5 * (start_fraction + end_fraction)
            color = _rgba_u32(
                imgui,
                _mix_rgba(self.low_color_rgba, self.high_color_rgba, mid_fraction),
            )
            start_angle = radians(
                _angle_for_fraction(
                    start_fraction,
                    start_degrees=self.sweep_start_degrees,
                    end_degrees=self.sweep_end_degrees,
                )
            )
            end_angle = radians(
                _angle_for_fraction(
                    end_fraction,
                    start_degrees=self.sweep_start_degrees,
                    end_degrees=self.sweep_end_degrees,
                )
            )
            x0, y0 = _polar_to_screen(
                layout.center_x,
                layout.center_y,
                layout.radius,
                start_angle,
            )
            x1, y1 = _polar_to_screen(
                layout.center_x,
                layout.center_y,
                layout.radius,
                end_angle,
            )
            draw_list.add_line(x0, y0, x1, y1, color, self.arc_thickness)

        _draw_analog_ticks(
            imgui,
            draw_list,
            inner_bounds=inner_bounds,
            layout=layout,
            low_value=self.low_value,
            high_value=self.high_value,
            sweep_start_degrees=self.sweep_start_degrees,
            sweep_end_degrees=self.sweep_end_degrees,
        )

        needle_angle = radians(self.needle_angle_degrees())
        tip_x, tip_y = _polar_to_screen(
            layout.center_x,
            layout.center_y,
            layout.needle_radius,
            needle_angle,
        )
        draw_list.add_line(
            layout.center_x,
            layout.center_y,
            tip_x,
            tip_y,
            _rgba_u32(imgui, _DEFAULT_TEXT_ACCENT),
            self.needle_thickness,
        )

        center_circle = getattr(draw_list, "add_circle_filled", None)
        if center_circle is not None:
            center_circle(
                layout.center_x,
                layout.center_y,
                4.0,
                _rgba_u32(imgui, _DEFAULT_TEXT_ACCENT),
            )


class LedBarGauge(SensorGauge):
    """Segmented telemetry bar with canonical severity-band coloring."""

    KIND = "led_bar_gauge"
    DEFAULT_WIDTH = 208.0
    DEFAULT_HEIGHT = 60.0

    def __init__(
        self,
        gauge_id: str,
        *,
        label: str,
        stream_name: str,
        low_value: float,
        high_value: float,
        animation_response_hz: float = _DEFAULT_ANIMATION_RESPONSE_HZ,
        low_color_rgba: tuple[float, float, float, float] = _DEFAULT_LOW_COLOR,
        high_color_rgba: tuple[float, float, float, float] = _DEFAULT_HIGH_COLOR,
        width: float | None = None,
        height: float | None = None,
        segment_count: int = _DEFAULT_LED_SEGMENT_COUNT,
        segment_gap_ratio: float = _DEFAULT_LED_SEGMENT_GAP_RATIO,
    ) -> None:
        super().__init__(
            gauge_id,
            label=label,
            stream_name=stream_name,
            low_value=low_value,
            high_value=high_value,
            animation_response_hz=animation_response_hz,
            low_color_rgba=low_color_rgba,
            high_color_rgba=high_color_rgba,
            width=width,
            height=height,
        )
        self.segment_count = int(segment_count)
        self.segment_gap_ratio = float(segment_gap_ratio)
        if self.segment_count <= 0:
            raise ValueError("segment_count must be greater than 0.")
        if self.segment_gap_ratio < 0.0:
            raise ValueError("segment_gap_ratio must be greater than or equal to 0.")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LedBarGauge":
        require_kind(data, cls.KIND)
        return cls(
            **cls._common_kwargs_from_dict(data),
            segment_count=int(data.get("segment_count", _DEFAULT_LED_SEGMENT_COUNT)),
            segment_gap_ratio=float(
                data.get("segment_gap_ratio", _DEFAULT_LED_SEGMENT_GAP_RATIO)
            ),
        )

    def lit_segments(self, *, use_display_value: bool = True) -> int:
        fraction = self.displayed_fraction() if use_display_value else self.target_fraction()
        return _lit_segment_count(fraction, self.segment_count)

    def _export_lines(self) -> list[str]:
        return [
            *super()._export_lines(),
            f"segment_count = {self.segment_count}",
            f"segment_gap_ratio = {self.segment_gap_ratio!r}",
        ]

    def _repr_details(self) -> str:
        return f"segment_count={self.segment_count!r}"

    def _render_face(
        self,
        imgui: Any,
        draw_list: Any,
        *,
        outer_bounds: tuple[float, float, float, float],
        inner_bounds: tuple[float, float, float, float],
    ) -> None:
        del outer_bounds
        inner_left, inner_top, inner_right, inner_bottom = inner_bounds
        inner_width = max(1.0, inner_right - inner_left)
        inner_height = max(1.0, inner_bottom - inner_top)
        gap = self.segment_gap_ratio * inner_height
        total_gap = gap * max(0, self.segment_count - 1)
        segment_width = max(1.0, (inner_width - total_gap) / self.segment_count)
        segment_pad_y = max(2.0, inner_height * 0.08)
        segment_top = inner_top + segment_pad_y
        segment_bottom = inner_bottom - segment_pad_y
        lit_segments = self.lit_segments()

        for segment_index in range(self.segment_count):
            x0 = inner_left + (segment_index * (segment_width + gap))
            x1 = x0 + segment_width
            active_color = _led_segment_color(
                segment_index,
                self.segment_count,
                low_color=self.low_color_rgba,
                high_color=self.high_color_rgba,
            )
            fill_color = active_color if segment_index < lit_segments else _DEFAULT_INACTIVE_SEGMENT
            draw_list.add_rect_filled(
                x0,
                segment_top,
                x1,
                segment_bottom,
                _rgba_u32(imgui, fill_color),
            )
            draw_list.add_rect(
                x0,
                segment_top,
                x1,
                segment_bottom,
                _rgba_u32(imgui, _DEFAULT_FRAME_BORDER),
            )


def reconstruct_gauge(path: str | Path) -> SensorGauge:
    return SensorGauge.reconstruct(path)


def _compute_analog_layout(
    inner_bounds: tuple[float, float, float, float],
    *,
    arc_thickness: float,
) -> _AnalogLayout:
    inner_left, inner_top, inner_right, inner_bottom = inner_bounds
    inner_width = max(1.0, inner_right - inner_left)
    inner_height = max(1.0, inner_bottom - inner_top)
    face_clearance = max((0.5 * arc_thickness) + 4.0, 8.0)
    # The dial sweep occupies roughly +/-60 degrees around vertical, so the face
    # envelope is bounded by cos(30 deg) horizontally and 1.5 radii vertically.
    max_radius_x = ((0.5 * inner_width) - face_clearance) / _HALF_SWEEP_COSINE
    max_radius_y = (inner_height - (2.0 * face_clearance)) / _ANALOG_VERTICAL_RADIUS_SPAN
    radius = max(8.0, min(max_radius_x, max_radius_y))
    center_x = 0.5 * (inner_left + inner_right)
    center_y = inner_bottom - (0.5 * radius) - face_clearance

    major_tick_outer_radius = max(4.0, radius - (0.5 * arc_thickness) - 2.0)
    major_tick_inner_radius = max(
        2.0,
        major_tick_outer_radius - max(8.0, arc_thickness * 0.90),
    )
    minor_tick_outer_radius = major_tick_outer_radius
    minor_tick_inner_radius = max(
        2.0,
        major_tick_outer_radius - max(4.0, arc_thickness * 0.48),
    )
    label_radius = max(
        2.0,
        major_tick_inner_radius - max(12.0, arc_thickness + 2.0),
    )
    needle_radius = max(6.0, major_tick_inner_radius - 6.0)
    return _AnalogLayout(
        center_x=center_x,
        center_y=center_y,
        radius=radius,
        major_tick_outer_radius=major_tick_outer_radius,
        major_tick_inner_radius=major_tick_inner_radius,
        minor_tick_outer_radius=minor_tick_outer_radius,
        minor_tick_inner_radius=minor_tick_inner_radius,
        label_radius=label_radius,
        needle_radius=needle_radius,
    )


def _analog_major_tick_values(low_value: float, high_value: float) -> tuple[float, ...]:
    span = float(high_value) - float(low_value)
    return tuple(float(low_value) + (span * fraction) for fraction in (0.0, 0.25, 0.5, 0.75, 1.0))


def _normalize_fraction(value: float, *, low_value: float, high_value: float) -> float:
    normalized = (float(value) - low_value) / (high_value - low_value)
    if normalized <= 0.0:
        return 0.0
    if normalized >= 1.0:
        return 1.0
    return normalized


def _advance_display_value(
    *,
    current_value: float,
    target_value: float,
    response_hz: float,
    delta_time: float,
) -> float:
    if delta_time <= 0.0:
        return current_value
    if current_value == target_value:
        return target_value
    alpha = 1.0 - exp(-response_hz * delta_time)
    if alpha >= 1.0:
        return target_value
    next_value = current_value + ((target_value - current_value) * alpha)
    if abs(target_value - next_value) <= 1.0e-9:
        return target_value
    return next_value


def _angle_for_fraction(
    fraction: float,
    *,
    start_degrees: float,
    end_degrees: float,
) -> float:
    clamped = 0.0 if fraction <= 0.0 else 1.0 if fraction >= 1.0 else fraction
    return start_degrees + ((end_degrees - start_degrees) * clamped)


def _lit_segment_count(fraction: float, segment_count: int) -> int:
    clamped = 0.0 if fraction <= 0.0 else 1.0 if fraction >= 1.0 else fraction
    if clamped >= 1.0:
        return segment_count
    return max(0, min(segment_count, int(floor((clamped * segment_count) + 1.0e-9))))


def _canonical_led_palette(
    low_color: tuple[float, float, float, float],
    high_color: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], ...]:
    # The LED bar uses a fixed operator-facing severity palette:
    # 5 green, 2 orange, 1 orange-red, 2 red.
    orange = _mix_rgba(low_color, high_color, _LED_ORANGE_BLEND)
    orange_red = _mix_rgba(low_color, high_color, _LED_ORANGE_RED_BLEND)
    return (
        low_color,
        low_color,
        low_color,
        low_color,
        low_color,
        orange,
        orange,
        orange_red,
        high_color,
        high_color,
    )


def _led_segment_color(
    segment_index: int,
    segment_count: int,
    *,
    low_color: tuple[float, float, float, float],
    high_color: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    palette = _canonical_led_palette(low_color, high_color)
    if segment_count <= 1:
        return palette[0]
    normalized_index = segment_index / float(max(1, segment_count - 1))
    canonical_index = int(floor((normalized_index * (_DEFAULT_CANONICAL_LED_SEGMENTS - 1)) + 0.5))
    canonical_index = max(0, min(_DEFAULT_CANONICAL_LED_SEGMENTS - 1, canonical_index))
    return palette[canonical_index]


def _resolved_widget_width(imgui: Any, configured_width: float) -> float:
    avail = imgui.get_content_region_available()
    available_width = float(avail[0] if isinstance(avail, tuple) else avail.x)
    if available_width <= 0.0:
        return configured_width
    return min(configured_width, available_width)


def _begin_gauge_panel(
    imgui: Any,
    *,
    width: float,
    height: float,
) -> tuple[Any, tuple[float, float, float, float], tuple[float, float, float, float]]:
    draw_pos = imgui.get_cursor_screen_pos()
    imgui.dummy(width, height)

    draw_list = imgui.get_window_draw_list()
    x0, y0 = _xy(draw_pos)
    x1 = x0 + width
    y1 = y0 + height
    draw_list.add_rect_filled(x0, y0, x1, y1, _rgba_u32(imgui, _DEFAULT_FRAME_BG))
    draw_list.add_rect(x0, y0, x1, y1, _rgba_u32(imgui, _DEFAULT_FRAME_BORDER))
    inner_left = x0 + _DEFAULT_PADDING
    inner_top = y0 + _DEFAULT_PADDING
    inner_right = x1 - _DEFAULT_PADDING
    inner_bottom = y1 - _DEFAULT_PADDING
    return draw_list, (x0, y0, x1, y1), (inner_left, inner_top, inner_right, inner_bottom)


def _imgui_delta_time(imgui: Any) -> float:
    io = imgui.get_io()
    delta_time = getattr(io, "delta_time", 0.0)
    if delta_time <= 0.0:
        return _MIN_DELTA_TIME
    return float(delta_time)


def _mix_rgba(
    low_color: tuple[float, float, float, float],
    high_color: tuple[float, float, float, float],
    fraction: float,
) -> tuple[float, float, float, float]:
    clamped = 0.0 if fraction <= 0.0 else 1.0 if fraction >= 1.0 else fraction
    return tuple(
        low_channel + ((high_channel - low_channel) * clamped)
        for low_channel, high_channel in zip(low_color, high_color)
    )  # type: ignore[return-value]


def _draw_analog_ticks(
    imgui: Any,
    draw_list: Any,
    *,
    inner_bounds: tuple[float, float, float, float],
    layout: _AnalogLayout,
    low_value: float,
    high_value: float,
    sweep_start_degrees: float,
    sweep_end_degrees: float,
) -> None:
    tick_color = _rgba_u32(imgui, _DEFAULT_TEXT_ACCENT)
    major_labels = _analog_major_tick_values(low_value, high_value)
    total_minor_steps = (_DEFAULT_MAJOR_TICK_COUNT - 1) * (_DEFAULT_MINOR_TICKS_PER_INTERVAL + 1)
    draw_text = getattr(draw_list, "add_text", None)

    for step_index in range(total_minor_steps + 1):
        fraction = step_index / float(total_minor_steps)
        angle = radians(
            _angle_for_fraction(
                fraction,
                start_degrees=sweep_start_degrees,
                end_degrees=sweep_end_degrees,
            )
        )
        is_major = step_index % (_DEFAULT_MINOR_TICKS_PER_INTERVAL + 1) == 0
        outer_radius = (
            layout.major_tick_outer_radius if is_major else layout.minor_tick_outer_radius
        )
        inner_radius = (
            layout.major_tick_inner_radius if is_major else layout.minor_tick_inner_radius
        )
        x0, y0 = _polar_to_screen(layout.center_x, layout.center_y, outer_radius, angle)
        x1, y1 = _polar_to_screen(layout.center_x, layout.center_y, inner_radius, angle)
        draw_list.add_line(x0, y0, x1, y1, tick_color, 1.6 if is_major else 1.0)

        if not is_major or draw_text is None:
            continue

        label_index = step_index // (_DEFAULT_MINOR_TICKS_PER_INTERVAL + 1)
        label_text = _format_gauge_tick_value(major_labels[label_index])
        text_w, text_h = _estimate_text_size(imgui, label_text)
        label_x, label_y = _polar_to_screen(
            layout.center_x,
            layout.center_y,
            layout.label_radius,
            angle,
        )
        text_x = _clamp(
            label_x - (0.5 * text_w),
            inner_bounds[0],
            inner_bounds[2] - text_w,
        )
        text_y = _clamp(
            label_y - (0.5 * text_h),
            inner_bounds[1],
            inner_bounds[3] - text_h,
        )
        draw_text(text_x, text_y, tick_color, label_text)


def _format_gauge_tick_value(value: float) -> str:
    rounded = round(float(value))
    if abs(float(value) - rounded) <= 1.0e-9:
        return str(int(rounded))
    return f"{float(value):.1f}"


def _estimate_text_size(imgui: Any, text: str) -> tuple[float, float]:
    calc_text_size = getattr(imgui, "calc_text_size", None)
    if calc_text_size is not None:
        result = calc_text_size(text)
        if isinstance(result, tuple):
            return float(result[0]), float(result[1])
        return float(result.x), float(result.y)
    return (max(6.0, len(text) * 6.0), 10.0)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if value <= minimum:
        return minimum
    if value >= maximum:
        return maximum
    return value


def _polar_to_screen(
    center_x: float,
    center_y: float,
    radius: float,
    angle_radians: float,
) -> tuple[float, float]:
    return (
        center_x + (cos(angle_radians) * radius),
        center_y - (sin(angle_radians) * radius),
    )


def _rgba_u32(imgui: Any, rgba: tuple[float, float, float, float]) -> int:
    converter = getattr(imgui, "get_color_u32_rgba", None)
    if converter is not None:
        return converter(*rgba)
    r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
    return (a << 24) | (b << 16) | (g << 8) | r


def _xy(pos: Any) -> tuple[float, float]:
    if isinstance(pos, tuple):
        return float(pos[0]), float(pos[1])
    return float(pos.x), float(pos.y)


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for gauge rendering. Install a Dear ImGui binding."
        ) from exc
    return imgui
