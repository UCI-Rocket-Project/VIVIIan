from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from . import theme
from ._streaming import (
    drain_numeric_reader,
    normalize_numeric_batch,
    validate_numeric_reader,
)
from .chrome import draw_dashed_line, estimate_text_size, rgba_u32, xy
from .configure import (
    parse_color_rgba,
    read_toml_document,
    require_keys,
    require_kind,
    toml_bool,
    toml_float_array,
    toml_header,
    toml_string,
    write_toml_document,
)

BackpressureMode = Literal["latest_only", "blocking"]

_DEFAULT_PLOT_HEIGHT = 220.0
_GRAPH_PADDING_RATIO = 0.08
_GRAPH_PADDING_FLOOR_RATIO = 2.5e-5
_GRAPH_MIN_SPAN_RATIO = 1.0e-4
_GRAPH_RANGE_SHRINK_ALPHA = 0.18
_GRAPH_GRID_COLUMNS = 8
_GRAPH_GRID_ROWS = 6
_GRAPH_AXIS_LEFT_PAD = 46.0
_GRAPH_AXIS_BOTTOM_PAD = 22.0
_GRAPH_AXIS_INNER_PAD = 3.0


@dataclass(frozen=True, slots=True)
class GraphSeries:
    series_id: str
    label: str
    stream_name: str
    color_rgba: tuple[float, float, float, float]
    visible_by_default: bool = True
    overlay: bool = False

    def __post_init__(self) -> None:
        if not self.series_id:
            raise ValueError("GraphSeries.series_id must be non-empty.")
        if not self.label:
            raise ValueError("GraphSeries.label must be non-empty.")
        if not self.stream_name:
            raise ValueError("GraphSeries.stream_name must be non-empty.")
        object.__setattr__(
            self,
            "color_rgba",
            parse_color_rgba(self.color_rgba, field_name=f"{self.series_id}.color_rgba"),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GraphSeries":
        require_keys(
            data,
            "graph series",
            "series_id",
            "label",
            "stream_name",
            "color_rgba",
        )
        return cls(
            series_id=str(data["series_id"]),
            label=str(data["label"]),
            stream_name=str(data["stream_name"]),
            color_rgba=parse_color_rgba(data["color_rgba"]),
            visible_by_default=bool(data.get("visible_by_default", True)),
            overlay=bool(data.get("overlay", False)),
        )


class _SeriesRuntime:
    def __init__(self, series: GraphSeries, capacity: int) -> None:
        self.series = series
        self.capacity = capacity
        self.timestamps = np.empty(capacity, dtype=np.float64)
        self.values = np.empty(capacity, dtype=np.float64)
        self.count = 0
        self.write_index = 0
        self.visible = series.visible_by_default
        self.last_timestamp: float | None = None

    def reset(self) -> None:
        self.count = 0
        self.write_index = 0
        self.last_timestamp = None

    def append_batch(self, frame: np.ndarray) -> float | None:
        timestamps, values = normalize_numeric_batch(frame, context_name="graph batch")
        if timestamps.size == 0:
            return None

        if self.last_timestamp is not None and timestamps[0] < self.last_timestamp:
            self.reset()

        if timestamps.size >= self.capacity:
            timestamps = timestamps[-self.capacity :]
            values = values[-self.capacity :]

        self._write_vectors(timestamps, values)
        self.last_timestamp = float(timestamps[-1])
        return self.last_timestamp

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        if self.count == 0:
            return (
                np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.float64),
            )

        start = (self.write_index - self.count) % self.capacity
        end = start + self.count
        if end <= self.capacity:
            return (
                self.timestamps[start:end].copy(),
                self.values[start:end].copy(),
            )

        split = self.capacity - start
        return (
            np.concatenate((self.timestamps[start:], self.timestamps[: self.count - split])),
            np.concatenate((self.values[start:], self.values[: self.count - split])),
        )

    def _write_vectors(self, timestamps: np.ndarray, values: np.ndarray) -> None:
        size = timestamps.size
        end = self.write_index + size
        if end <= self.capacity:
            self.timestamps[self.write_index:end] = timestamps
            self.values[self.write_index:end] = values
        else:
            split = self.capacity - self.write_index
            self.timestamps[self.write_index:] = timestamps[:split]
            self.values[self.write_index:] = values[:split]
            self.timestamps[: size - split] = timestamps[split:]
            self.values[: size - split] = values[split:]

        self.write_index = end % self.capacity
        self.count = min(self.capacity, self.count + size)

    def trim_before(self, cutoff_timestamp: float) -> None:
        timestamps, values = self.snapshot()
        if timestamps.size == 0:
            return

        if float(timestamps[0]) >= cutoff_timestamp:
            return

        keep_mask = timestamps >= cutoff_timestamp
        timestamps = timestamps[keep_mask]
        values = values[keep_mask]

        if timestamps.size > self.capacity:
            timestamps = timestamps[-self.capacity :]
            values = values[-self.capacity :]

        self.reset()
        if timestamps.size:
            self._write_vectors(timestamps, values)
            self.last_timestamp = float(timestamps[-1])


class SensorGraph:
    def __init__(
        self,
        graph_id: str,
        *,
        title: str,
        series: Sequence[GraphSeries],
        window_seconds: float = 300.0,
        max_points_per_series: int = 65536,
        backpressure_mode: BackpressureMode = "latest_only",
        line_thickness: float = 1.7,
        overlay_thickness: float = 1.1,
        show_axes: bool = True,
        show_series_controls: bool = True,
        stable_y: bool = True,
        y_padding_ratio: float = _GRAPH_PADDING_RATIO,
        y_padding_floor_ratio: float = _GRAPH_PADDING_FLOOR_RATIO,
        y_min_span_ratio: float = _GRAPH_MIN_SPAN_RATIO,
        y_shrink_alpha: float = _GRAPH_RANGE_SHRINK_ALPHA,
        theme_name: theme.GuiThemeName = "legacy",
        plot_width: float | None = None,
        plot_height: float = _DEFAULT_PLOT_HEIGHT,
    ) -> None:
        if not graph_id:
            raise ValueError("graph_id must be non-empty.")
        if not title:
            raise ValueError("title must be non-empty.")
        if not series:
            raise ValueError("SensorGraph requires at least one GraphSeries.")
        if window_seconds <= 0.0:
            raise ValueError("window_seconds must be greater than 0.")
        if max_points_per_series <= 0:
            raise ValueError("max_points_per_series must be greater than 0.")
        if backpressure_mode not in ("latest_only", "blocking"):
            raise ValueError("backpressure_mode must be 'latest_only' or 'blocking'.")
        if line_thickness <= 0.0 or overlay_thickness <= 0.0:
            raise ValueError("Line thickness values must be greater than 0.")

        self.graph_id = graph_id
        self.title = title
        self.series = tuple(series)
        self.window_seconds = float(window_seconds)
        self.max_points_per_series = int(max_points_per_series)
        self.backpressure_mode = backpressure_mode
        self.line_thickness = float(line_thickness)
        self.overlay_thickness = float(overlay_thickness)
        self.show_axes = bool(show_axes)
        self.show_series_controls = bool(show_series_controls)
        self.stable_y = bool(stable_y)
        self.y_padding_ratio = float(y_padding_ratio)
        self.y_padding_floor_ratio = float(y_padding_floor_ratio)
        self.y_min_span_ratio = float(y_min_span_ratio)
        self.y_shrink_alpha = float(y_shrink_alpha)
        if theme_name not in ("legacy", "tau_ceti"):
            raise ValueError("theme_name must be 'legacy' or 'tau_ceti'.")
        self.theme_name = theme_name
        if plot_width is not None and float(plot_width) <= 0.0:
            raise ValueError("plot_width must be greater than 0.")
        self.plot_width = None if plot_width is None else float(plot_width)
        self.plot_height = float(plot_height)

        _validate_graph_series(self.series)

        self._series_runtime = {
            item.series_id: _SeriesRuntime(item, self.max_points_per_series)
            for item in self.series
        }
        self._readers: dict[str, Any] = {}
        self._latest_timestamp: float | None = None
        self._y_limits: tuple[float, float] | None = None

    def __repr__(self) -> str:
        series_ids = ",".join(item.series_id for item in self.series)
        return (
            "SensorGraph("
            f"graph_id={self.graph_id!r}, "
            f"title={self.title!r}, "
            f"series=[{series_ids}], "
            f"window_seconds={self.window_seconds}, "
            f"backpressure_mode={self.backpressure_mode!r})"
        )

    def bind(self, readers: Mapping[str, Any]) -> None:
        bound: dict[str, Any] = {}
        for item in self.series:
            reader = readers.get(item.stream_name)
            if reader is None:
                raise KeyError(
                    f"SensorGraph {self.graph_id!r} requires reader {item.stream_name!r}."
                )
            validate_numeric_reader(item.stream_name, reader)
            if hasattr(reader, "set_blocking"):
                reader.set_blocking(self.backpressure_mode == "blocking")
            bound[item.stream_name] = reader
        self._readers = bound
        self.reset_history()

    def reset_history(self) -> None:
        for runtime in self._series_runtime.values():
            runtime.reset()
            runtime.visible = runtime.series.visible_by_default
        self._latest_timestamp = None
        self._y_limits = None

    def consume(self) -> bool:
        had_update = False
        for item in self.series:
            reader = self._readers.get(item.stream_name)
            if reader is None:
                continue
            for frame in drain_numeric_reader(reader):
                frame_latest = self._series_runtime[item.series_id].append_batch(frame)
                if frame_latest is None:
                    continue
                had_update = True

        if had_update:
            latest_seen = max(
                (
                    runtime.last_timestamp
                    for runtime in self._series_runtime.values()
                    if runtime.last_timestamp is not None
                ),
                default=None,
            )
        else:
            latest_seen = None

        if had_update and latest_seen is not None:
            self._latest_timestamp = latest_seen
            cutoff_timestamp = latest_seen - self.window_seconds
            for runtime in self._series_runtime.values():
                runtime.trim_before(cutoff_timestamp)
            self._refresh_y_limits()

        return had_update

    def _refresh_y_limits(self) -> None:
        target = self._target_display_limits()
        if not self.stable_y:
            self._y_limits = target
            return
        self._y_limits = _stabilize_graph_limits(
            self._y_limits,
            target,
            shrink_alpha=self.y_shrink_alpha,
        )

    def render(self) -> None:
        imgui = _require_imgui()
        imgui.text_unformatted(self.title)
        if self.show_series_controls:
            self._render_visibility_controls(imgui)

        visible_series = self._visible_series_snapshots()
        if not visible_series:
            imgui.text_disabled("No visible series.")
            return

        x_limits, y_limits = self._resolve_plot_limits(visible_series)
        avail = imgui.get_content_region_available()
        plot_width = float(avail[0] if isinstance(avail, tuple) else avail.x)
        plot_height = self.plot_height
        if plot_width <= 0.0:
            plot_width = 640.0
        if self.plot_width is not None:
            plot_width = min(plot_width, self.plot_width)

        draw_pos = imgui.get_cursor_screen_pos()
        imgui.dummy(plot_width, plot_height)

        draw_list = imgui.get_window_draw_list()
        x0, y0 = _xy(draw_pos)
        x1 = x0 + plot_width
        y1 = y0 + plot_height
        if self.theme_name == "tau_ceti":
            bg = rgba_u32(imgui, theme.GRAPH_BG)
            border = rgba_u32(imgui, theme.GRAPH_BORDER)
        else:
            bg = _rgba_u32(imgui, (0.028, 0.040, 0.065, 1.0))
            border = _rgba_u32(imgui, (0.150, 0.235, 0.330, 1.0))
        draw_list.add_rect_filled(x0, y0, x1, y1, bg)
        draw_list.add_rect(x0, y0, x1, y1, border)

        inner_left = x0 + (_GRAPH_AXIS_LEFT_PAD if self.show_axes else _GRAPH_AXIS_INNER_PAD)
        inner_top = y0 + _GRAPH_AXIS_INNER_PAD
        inner_right = x1 - _GRAPH_AXIS_INNER_PAD
        inner_bottom = y1 - (_GRAPH_AXIS_BOTTOM_PAD if self.show_axes else _GRAPH_AXIS_INNER_PAD)
        inner_width = max(1.0, inner_right - inner_left)
        inner_height = max(1.0, inner_bottom - inner_top)

        x_min, x_max = x_limits
        y_min, y_max = y_limits
        x_scale = inner_width / max(x_max - x_min, 1e-9)
        y_scale = inner_height / max(y_max - y_min, 1e-9)

        _draw_plot_grid(
            draw_list,
            left=inner_left,
            top=inner_top,
            right=inner_right,
            bottom=inner_bottom,
            color=_graph_grid_color(imgui, theme_name=self.theme_name),
            columns=_GRAPH_GRID_COLUMNS,
            rows=_GRAPH_GRID_ROWS,
        )

        if self.show_axes:
            _draw_graph_axis_labels(
                imgui,
                draw_list,
                outer_left=x0,
                outer_top=y0,
                outer_right=x1,
                outer_bottom=y1,
                plot_left=inner_left,
                plot_top=inner_top,
                plot_right=inner_right,
                plot_bottom=inner_bottom,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
                rows=_GRAPH_GRID_ROWS,
                columns=_GRAPH_GRID_COLUMNS,
                color=rgba_u32(imgui, theme.GRAPH_TEXT),
            )

        if self.show_axes and y_min <= 0.0 <= y_max:
            zero_y = inner_bottom - ((0.0 - y_min) * y_scale)
            if self.theme_name == "tau_ceti":
                draw_dashed_line(
                    imgui,
                    draw_list,
                    x0=inner_left,
                    y0=zero_y,
                    x1=inner_right,
                    y1=zero_y,
                    rgba=theme.GRAPH_ZERO_LINE,
                    dash=4.0,
                    gap=4.0,
                    thickness=1.0,
                )
            else:
                draw_list.add_line(inner_left, zero_y, inner_right, zero_y, border, 1.0)

        # Draw overlay series last so their visual treatment stays lighter.
        ordered = sorted(visible_series, key=lambda item: item[0].overlay)
        for index, (series_cfg, timestamps, values) in enumerate(ordered):
            points = _map_segment_to_screen(
                timestamps,
                values,
                x_min=x_min,
                y_min=y_min,
                x_scale=x_scale,
                y_scale=y_scale,
                inner_left=inner_left,
                inner_bottom=inner_bottom,
            )
            if points.shape[0] < 2:
                continue
            color = _rgba_u32(imgui, series_cfg.color_rgba)
            thickness = self.overlay_thickness if series_cfg.overlay else self.line_thickness
            if self.theme_name == "tau_ceti" and index == 0 and not series_cfg.overlay:
                fill_points = np.vstack(
                    (
                        points,
                        np.array(
                            [[points[-1, 0], inner_bottom], [points[0, 0], inner_bottom]],
                            dtype=np.float32,
                        ),
                    )
                )
                fill_color = _rgba_u32(
                    imgui,
                    (
                        series_cfg.color_rgba[0],
                        series_cfg.color_rgba[1],
                        series_cfg.color_rgba[2],
                        0.14,
                    ),
                )
                fill = getattr(draw_list, "add_polyline", None)
                if fill is not None:
                    fill(fill_points.tolist(), fill_color, True, 1.0)
            _draw_polyline(draw_list, points, color, thickness)

        if self.theme_name == "tau_ceti":
            _draw_live_badges(
                imgui,
                draw_list,
                x0=x0,
                y0=y0,
                x1=x1,
                latest_timestamp=self._latest_timestamp,
            )

    def build_dashboard_hooks(
        self,
        readers: Mapping[str, Any],
    ) -> tuple[Callable[[], bool], Callable[[], None]]:
        self.bind(readers)
        return self.consume, self.render

    def export(self, path: str | Path) -> Path:
        lines = toml_header("sensor_graph")
        lines.extend(
            [
                f"graph_id = {toml_string(self.graph_id)}",
                f"title = {toml_string(self.title)}",
                f"window_seconds = {self.window_seconds!r}",
                f"max_points_per_series = {self.max_points_per_series}",
                f"backpressure_mode = {toml_string(self.backpressure_mode)}",
                f"line_thickness = {self.line_thickness!r}",
                f"overlay_thickness = {self.overlay_thickness!r}",
                f"show_axes = {toml_bool(self.show_axes)}",
                f"show_series_controls = {toml_bool(self.show_series_controls)}",
                f"stable_y = {toml_bool(self.stable_y)}",
                f"y_padding_ratio = {self.y_padding_ratio!r}",
                f"y_padding_floor_ratio = {self.y_padding_floor_ratio!r}",
                f"y_min_span_ratio = {self.y_min_span_ratio!r}",
                f"y_shrink_alpha = {self.y_shrink_alpha!r}",
                f"theme_name = {toml_string(self.theme_name)}",
                *( [f"plot_width = {self.plot_width!r}"] if self.plot_width is not None else [] ),
                f"plot_height = {self.plot_height!r}",
                "",
            ]
        )

        for item in self.series:
            lines.extend(
                [
                    "[[series]]",
                    f"series_id = {toml_string(item.series_id)}",
                    f"label = {toml_string(item.label)}",
                    f"stream_name = {toml_string(item.stream_name)}",
                    f"color_rgba = {toml_float_array(item.color_rgba)}",
                    f"visible_by_default = {toml_bool(item.visible_by_default)}",
                    f"overlay = {toml_bool(item.overlay)}",
                    "",
                ]
            )

        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "SensorGraph":
        data = read_toml_document(path)
        require_kind(data, "sensor_graph")
        require_keys(
            data,
            "sensor_graph",
            "graph_id",
            "title",
            "series",
        )
        return cls(
            graph_id=str(data["graph_id"]),
            title=str(data["title"]),
            series=tuple(GraphSeries.from_dict(item) for item in data["series"]),
            window_seconds=float(data.get("window_seconds", 300.0)),
            max_points_per_series=int(data.get("max_points_per_series", 65536)),
            backpressure_mode=str(data.get("backpressure_mode", "latest_only")),
            line_thickness=float(data.get("line_thickness", 1.7)),
            overlay_thickness=float(data.get("overlay_thickness", 1.1)),
            show_axes=bool(data.get("show_axes", True)),
            show_series_controls=bool(data.get("show_series_controls", True)),
            stable_y=bool(data.get("stable_y", True)),
            y_padding_ratio=float(data.get("y_padding_ratio", _GRAPH_PADDING_RATIO)),
            y_padding_floor_ratio=float(
                data.get("y_padding_floor_ratio", _GRAPH_PADDING_FLOOR_RATIO)
            ),
            y_min_span_ratio=float(data.get("y_min_span_ratio", _GRAPH_MIN_SPAN_RATIO)),
            y_shrink_alpha=float(data.get("y_shrink_alpha", _GRAPH_RANGE_SHRINK_ALPHA)),
            theme_name=str(data.get("theme_name", "legacy")),
            plot_width=(float(data["plot_width"]) if data.get("plot_width") is not None else None),
            plot_height=float(data.get("plot_height", _DEFAULT_PLOT_HEIGHT)),
        )

    def series_snapshot(self, series_id: str) -> np.ndarray:
        runtime = self._series_runtime[series_id]
        timestamps, values = runtime.snapshot()
        if timestamps.size == 0:
            return np.empty((2, 0), dtype=np.float64)
        return np.vstack((timestamps, values))

    def _render_visibility_controls(self, imgui: Any) -> None:
        for index, item in enumerate(self.series):
            runtime = self._series_runtime[item.series_id]
            button_label = item.label if self.theme_name == "tau_ceti" else f"{'[x]' if runtime.visible else '[ ]'} {item.label}"
            color_count, var_count = _push_visibility_button_colors(
                imgui,
                visible=runtime.visible,
                color=item.color_rgba,
                theme_name=self.theme_name,
            )
            pressed = imgui.button(button_label, width=0.0, height=28.0)
            if var_count > 0:
                imgui.pop_style_var(var_count)
            if color_count > 0:
                imgui.pop_style_color(color_count)
            if pressed and _ctrl_held(imgui):
                runtime.visible = not runtime.visible
                self._y_limits = self._target_display_limits()
            if index < len(self.series) - 1:
                imgui.same_line()

    def _visible_series_snapshots(
        self,
    ) -> list[tuple[GraphSeries, np.ndarray, np.ndarray]]:
        visible: list[tuple[GraphSeries, np.ndarray, np.ndarray]] = []
        for item in self.series:
            runtime = self._series_runtime[item.series_id]
            if not runtime.visible:
                continue
            timestamps, values = runtime.snapshot()
            if timestamps.size == 0:
                continue
            visible.append((item, timestamps, values))
        return visible

    def _resolve_plot_limits(
        self,
        visible_series: Sequence[tuple[GraphSeries, np.ndarray, np.ndarray]],
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        x_min = min(float(timestamps[0]) for _, timestamps, _ in visible_series)
        x_max = max(float(timestamps[-1]) for _, timestamps, _ in visible_series)
        if x_min == x_max:
            x_max = x_min + 1.0

        y_limits = self._y_limits if self.stable_y else self._target_display_limits()
        if y_limits is None:
            y_limits = (-1.0, 1.0)
        return (x_min, x_max), y_limits

    def _target_display_limits(self) -> tuple[float, float] | None:
        values: list[np.ndarray] = []
        for _, timestamps, series_values in self._visible_series_snapshots():
            if timestamps.size == 0:
                continue
            values.append(series_values)
        if not values:
            return None

        merged = np.concatenate(values)
        finite_values = merged[np.isfinite(merged)]
        if finite_values.size == 0:
            return None

        y_min = float(np.min(finite_values))
        y_max = float(np.max(finite_values))
        span = y_max - y_min
        scale = max(abs(y_min), abs(y_max), 1.0)
        min_span = max(scale * self.y_min_span_ratio, 1e-6)
        if span < min_span:
            center = 0.5 * (y_min + y_max)
            half_span = 0.5 * min_span
            y_min = center - half_span
            y_max = center + half_span
            span = min_span

        padding = max(
            span * self.y_padding_ratio,
            scale * self.y_padding_floor_ratio,
            1e-6,
        )
        return (y_min - padding, y_max + padding)


def _validate_graph_series(series: Sequence[GraphSeries]) -> None:
    series_ids = set()
    stream_names = set()
    for item in series:
        if item.series_id in series_ids:
            raise ValueError(f"Duplicate series_id {item.series_id!r}.")
        if item.stream_name in stream_names:
            raise ValueError(f"Duplicate stream_name {item.stream_name!r}.")
        series_ids.add(item.series_id)
        stream_names.add(item.stream_name)


def _stabilize_graph_limits(
    previous: tuple[float, float] | None,
    target: tuple[float, float] | None,
    *,
    shrink_alpha: float,
) -> tuple[float, float] | None:
    if target is None:
        return previous
    if previous is None:
        return target

    prev_min, prev_max = previous
    target_min, target_max = target
    next_min = (
        target_min
        if target_min <= prev_min
        else prev_min + (target_min - prev_min) * shrink_alpha
    )
    next_max = (
        target_max
        if target_max >= prev_max
        else prev_max + (target_max - prev_max) * shrink_alpha
    )
    if next_max <= next_min:
        return target
    return (next_min, next_max)


def _push_visibility_button_colors(
    imgui: Any,
    *,
    visible: bool,
    color: tuple[float, float, float, float],
    theme_name: theme.GuiThemeName = "legacy",
) -> tuple[int, int]:
    color_count = 0
    var_count = 0
    if theme_name == "tau_ceti":
        if visible:
            base = theme.PANEL_BG_2
            hovered = theme.PANEL_BG_3
            active = theme.BUTTON_OFF_ACTIVE
            text = color
            border = color
        else:
            base = theme.PANEL_BG
            hovered = theme.PANEL_BG_2
            active = theme.PANEL_BG_3
            text = theme.INK_3
            border = theme.PANEL_BORDER
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

    if visible:
        base = color
        hovered = tuple(min(1.0, channel + 0.12) for channel in color[:3]) + (1.0,)
        active = tuple(min(1.0, channel + 0.20) for channel in color[:3]) + (1.0,)
    else:
        base = (0.090, 0.115, 0.155, 1.0)
        hovered = (0.120, 0.150, 0.205, 1.0)
        active = (0.150, 0.185, 0.240, 1.0)
    imgui.push_style_color(imgui.COLOR_BUTTON, *base)
    color_count += 1
    imgui.push_style_color(imgui.COLOR_BUTTON_HOVERED, *hovered)
    color_count += 1
    imgui.push_style_color(imgui.COLOR_BUTTON_ACTIVE, *active)
    color_count += 1
    return color_count, var_count


def _draw_live_badges(
    imgui: Any,
    draw_list: Any,
    *,
    x0: float,
    y0: float,
    x1: float,
    latest_timestamp: float | None,
) -> None:
    left_text = "● LIVE"
    draw_list.add_rect_filled(
        x0 + 8.0,
        y0 + 8.0,
        x0 + 76.0,
        y0 + 24.0,
        rgba_u32(imgui, theme.PANEL_BG),
    )
    draw_list.add_rect(
        x0 + 8.0,
        y0 + 8.0,
        x0 + 76.0,
        y0 + 24.0,
        rgba_u32(imgui, theme.ACID),
    )
    draw_list.add_text(x0 + 14.0, y0 + 10.0, rgba_u32(imgui, theme.ACID), left_text)
    if latest_timestamp is None:
        return
    right_text = f"T={latest_timestamp:0.2f}"
    text_width = max(40.0, len(right_text) * 6.0)
    draw_list.add_rect_filled(
        x1 - text_width - 18.0,
        y0 + 8.0,
        x1 - 8.0,
        y0 + 24.0,
        rgba_u32(imgui, theme.PANEL_BG),
    )
    draw_list.add_rect(
        x1 - text_width - 18.0,
        y0 + 8.0,
        x1 - 8.0,
        y0 + 24.0,
        rgba_u32(imgui, theme.PANEL_BORDER),
    )
    draw_list.add_text(
        x1 - text_width - 12.0,
        y0 + 10.0,
        rgba_u32(imgui, theme.INK_2),
        right_text,
    )


def _draw_plot_grid(
    draw_list: Any,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    color: int,
    columns: int,
    rows: int,
) -> None:
    width = max(1.0, right - left)
    height = max(1.0, bottom - top)

    if columns > 1:
        x_step = width / columns
        for index in range(1, columns):
            x = left + (x_step * index)
            draw_list.add_line(x, top, x, bottom, color, 1.0)

    if rows > 1:
        y_step = height / rows
        for index in range(1, rows):
            y = top + (y_step * index)
            draw_list.add_line(left, y, right, y, color, 1.0)


def _draw_graph_axis_labels(
    imgui: Any,
    draw_list: Any,
    *,
    outer_left: float,
    outer_top: float,
    outer_right: float,
    outer_bottom: float,
    plot_left: float,
    plot_top: float,
    plot_right: float,
    plot_bottom: float,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    rows: int,
    columns: int,
    color: int,
) -> None:
    plot_width = max(1.0, plot_right - plot_left)
    plot_height = max(1.0, plot_bottom - plot_top)
    y_span = y_max - y_min
    x_span = x_max - x_min

    for index in range(rows + 1):
        fraction = index / max(rows, 1)
        y = plot_bottom - (plot_height * fraction)
        value = y_min + (y_span * fraction)
        label = _format_graph_tick_value(value)
        text_width, text_height = estimate_text_size(imgui, label)
        text_x = max(outer_left + 4.0, plot_left - 8.0 - text_width)
        text_y = min(
            max(outer_top + 4.0, y - (0.5 * text_height)),
            outer_bottom - text_height - 4.0,
        )
        draw_list.add_text(
            text_x,
            text_y,
            color,
            label,
        )

    for index in range(columns + 1):
        fraction = index / max(columns, 1)
        x = plot_left + (plot_width * fraction)
        label = _format_graph_time_tick((x_min + (x_span * fraction)) - x_max)
        text_width, text_height = estimate_text_size(imgui, label)
        text_x = min(
            max(outer_left + 4.0, x - (0.5 * text_width)),
            outer_right - text_width - 4.0,
        )
        text_y = min(plot_bottom + 6.0, outer_bottom - text_height - 4.0)
        draw_list.add_text(
            text_x,
            text_y,
            color,
            label,
        )


def _graph_grid_color(imgui: Any, *, theme_name: theme.GuiThemeName) -> int:
    if theme_name == "tau_ceti":
        return rgba_u32(imgui, (theme.GRAPH_GRIDLINE[0], theme.GRAPH_GRIDLINE[1], theme.GRAPH_GRIDLINE[2], 0.65))
    return _rgba_u32(imgui, (0.150, 0.235, 0.330, 0.22))


def _format_graph_tick_value(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1000.0:
        return f"{value:.0f}"
    if abs_value >= 100.0:
        return f"{value:.1f}"
    if abs_value >= 10.0:
        return f"{value:.1f}"
    if abs_value >= 1.0:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _format_graph_time_tick(offset_s: float) -> str:
    if abs(offset_s) < 0.05:
        return "now"
    abs_offset = abs(offset_s)
    if abs_offset >= 100.0:
        return f"-{abs_offset:.0f}s"
    if abs_offset >= 10.0:
        return f"-{abs_offset:.1f}s"
    return f"-{abs_offset:.2f}s"


def _map_segment_to_screen(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    x_min: float,
    y_min: float,
    x_scale: float,
    y_scale: float,
    inner_left: float,
    inner_bottom: float,
) -> np.ndarray:
    points = np.empty((timestamps.size, 2), dtype=np.float32)
    points[:, 0] = inner_left + (timestamps - x_min) * x_scale
    points[:, 1] = inner_bottom - (values - y_min) * y_scale
    return points


def _draw_polyline(draw_list: Any, points: np.ndarray, color: int, thickness: float) -> None:
    polyline = getattr(draw_list, "add_polyline", None)
    if polyline is not None:
        polyline(points.tolist(), color, False, thickness)
        return
    for start, end in zip(points[:-1], points[1:]):
        draw_list.add_line(
            float(start[0]),
            float(start[1]),
            float(end[0]),
            float(end[1]),
            color,
            thickness,
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
            "imgui is required for SensorGraph rendering. Install a Dear ImGui binding."
        ) from exc
    return imgui


def _ctrl_held(imgui: Any) -> bool:
    """Return True when the Ctrl modifier is currently held."""
    io = imgui.get_io()
    return bool(getattr(io, 'key_ctrl', False))
