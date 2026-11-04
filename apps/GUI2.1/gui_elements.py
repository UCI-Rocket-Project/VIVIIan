from __future__ import annotations

from generic_connector import LatestServer
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
import numpy as np
import time
from imgui_bundle import imgui, implot

RGBA = tuple[float, float, float, float]

COLOR_BLACK: RGBA = (0.0, 0.0, 0.0, 1.0)
COLOR_WHITE: RGBA = (1.0, 1.0, 1.0, 1.0)

APP_BACKGROUND_COLOR: RGBA = (0.1, 0.1, 0.1, 1.0)

BUTTON_BACKGROUND_COLOR: RGBA = (0.18, 0.18, 0.20, 1.0)
BUTTON_HOVER_COLOR: RGBA = (0.24, 0.24, 0.27, 1.0)
BUTTON_ACTIVE_COLOR: RGBA = (0.12, 0.34, 0.16, 1.0)
BUTTON_STATE_ON_COLOR: RGBA = (0.0, 0.55, 0.15, 1.0)
BUTTON_DISABLED_COLOR: RGBA = (0.11, 0.11, 0.12, 1.0)
BUTTON_BORDER_COLOR: RGBA = COLOR_BLACK
BUTTON_TEXT_COLOR: RGBA = COLOR_WHITE
BUTTON_DISABLED_TEXT_COLOR: RGBA = (0.45, 0.45, 0.48, 1.0)
BUTTON_STATE_MISMATCH_COLOR: RGBA = (0.85, 0.35, 0.0, 1.0)
BUTTON_STATUS_DEFAULT_COLOR: RGBA = (0.12, 0.55, 0.18, 1.0)
BUTTON_STATUS_OFF_COLOR: RGBA = (0.18, 0.18, 0.18, 1.0)
BUTTON_STATUS_ON_COLOR: RGBA = (0.0, 0.7, 0.15, 1.0)
BUTTON_STATUS_TEXT_COLOR: RGBA = COLOR_WHITE
BUTTON_STATUS_DISABLED_COLOR: RGBA = (0.08, 0.08, 0.09, 1.0)
BUTTON_STATUS_DISABLED_TEXT_COLOR: RGBA = (0.38, 0.38, 0.40, 1.0)


@dataclass
class Button:
    button_id: str
    text: str
    state: bool = False
    width: float = 180.0
    height: float = 42.0
    color: RGBA = BUTTON_BACKGROUND_COLOR
    hover_color: RGBA = BUTTON_HOVER_COLOR
    active_color: RGBA = BUTTON_ACTIVE_COLOR
    state_on_color: RGBA = BUTTON_STATE_ON_COLOR
    disabled_color: RGBA = BUTTON_DISABLED_COLOR
    text_color: RGBA = BUTTON_TEXT_COLOR
    disabled_text_color: RGBA = BUTTON_DISABLED_TEXT_COLOR
    status_text: callable[[Button], str] | None = None
    internal_status_value: callable[[Button], int | str] | None = None
    status_color: RGBA = BUTTON_STATUS_DEFAULT_COLOR
    status_text_color: RGBA = BUTTON_STATUS_TEXT_COLOR
    disabled_status_color: RGBA = BUTTON_STATUS_DISABLED_COLOR
    disabled_status_text_color: RGBA = BUTTON_STATUS_DISABLED_TEXT_COLOR
    status_width: float = 56.0
    toggle_on_click: bool = False
    enabled: Any = True
    on_click: Callable[[Button], None] | None = None
    momentary_seconds: float | None = None
    momentary_until: float | None = None

    def render(self, imgui, status_text: int | str | None = None) -> bool:
        enabled = self.is_enabled()
        pressed = imgui.invisible_button(f"##{self.button_id}", imgui.ImVec2(self.width, self.height))
        if self.momentary_until is not None and self.momentary_until < time.monotonic():
            was_on = self.state
            self.momentary_until = None
            self.state = False
            if was_on and self.on_click is not None:
                self.on_click(self)


        accepted = pressed and enabled
        if accepted:
            if self.momentary_seconds is not None:
                self.state = True
                self.momentary_until = time.monotonic() + self.momentary_seconds
            elif self.toggle_on_click:
                self.state = not self.state
            if self.on_click is not None:
                self.on_click(self)

        x0, y0 = _xy(imgui.get_item_rect_min())
        x1, y1 = _xy(imgui.get_item_rect_max())
        status_x0 = max(x0, x1 - self.status_width)
        draw_list = imgui.get_window_draw_list()

        if self.state:
            body_color = self.state_on_color
        elif not enabled:
            body_color = self.disabled_color
        elif imgui.is_item_active():
            body_color = self.active_color
        elif imgui.is_item_hovered():
            body_color = self.hover_color
        else:
            body_color = self.color
        text_color = self.text_color if enabled else self.disabled_text_color
        status_color = self.status_color if bool(self.internal_status_value())==self.state else BUTTON_STATE_MISMATCH_COLOR
        status_text_color = (
            self.status_text_color if enabled else self.disabled_status_text_color
        )

        draw_list.add_rect_filled(
            imgui.ImVec2(x0, y0),
            imgui.ImVec2(status_x0, y1),
            _rgba(imgui, body_color),
        )
        draw_list.add_rect_filled(
            imgui.ImVec2(status_x0, y0),
            imgui.ImVec2(x1, y1),
            _rgba(imgui, status_color),
        )
        draw_list.add_rect(
            imgui.ImVec2(x0, y0),
            imgui.ImVec2(x1, y1),
            _rgba(imgui, BUTTON_BORDER_COLOR),
        )

        _draw_centered_text(
            imgui,
            draw_list,
            self.text,
            x0,
            y0,
            status_x0,
            y1,
            text_color,
        )
        _draw_centered_text(
            imgui,
            draw_list,
            self.status_text() ,
            status_x0,
            y0,
            x1,
            y1,
            status_text_color,
        )
        return accepted

    def is_enabled(self) -> bool:
        if callable(self.enabled):
            return bool(self.enabled())
        return bool(self.enabled)

    def set_text(self, text: str) -> None:
        self.text = text

    def set_status_text(self, text: str | None) -> None:
        self.status_text = text

    def set_state(self, state: bool) -> None:
        self.state = state

    def set_color(self, color: RGBA) -> None:
        self.color = color

    def set_colors(
        self,
        color: RGBA,
        hover_color: RGBA | None = None,
        active_color: RGBA | None = None,
    ) -> None:
        self.color = color
        if hover_color is not None:
            self.hover_color = hover_color
        if active_color is not None:
            self.active_color = active_color

    def set_status_color(self, color: RGBA) -> None:
        self.status_color = color

    def set_enabled(self, enabled: Any) -> None:
        self.enabled = enabled













@dataclass
class valve_state:
    server: LatestServer
    field: str | list[str]
    label: str = "Valve"
    invert: bool = False
    width: float = 126.0
    height: float = 42.0
    on_text: str = "N/A"
    off_text: str = "N/A"
    unknown_text: str = "UNKNOWN"
    on_color: RGBA = BUTTON_STATUS_ON_COLOR
    off_color: RGBA = BUTTON_STATUS_OFF_COLOR
    unknown_color: RGBA = BUTTON_STATE_MISMATCH_COLOR
    text_color: RGBA = BUTTON_TEXT_COLOR

    def render(self, imgui) -> None:
        state = self._read_latest_state()
        if state is not None and self.invert:
            state = not state

        if state is True:
            color = self.on_color
            status_text = self.on_text
        elif state is False:
            color = self.off_color
            status_text = self.off_text
        else:
            color = self.unknown_color
            status_text = self.unknown_text

        imgui.invisible_button(
            f"##valve_state_{self.server.name}_{self.field}",
            imgui.ImVec2(self.width, self.height),
        )
        x0, y0 = _xy(imgui.get_item_rect_min())
        x1, y1 = _xy(imgui.get_item_rect_max())
        draw_list = imgui.get_window_draw_list()

        draw_list.add_rect_filled(
            imgui.ImVec2(x0, y0),
            imgui.ImVec2(x1, y1),
            _rgba(imgui, color),
        )
        draw_list.add_rect(
            imgui.ImVec2(x0, y0),
            imgui.ImVec2(x1, y1),
            _rgba(imgui, BUTTON_BORDER_COLOR),
        )
        _draw_centered_text(
            imgui,
            draw_list,
            f"{self.label}: {status_text}",
            x0,
            y0,
            x1,
            y1,
            self.text_color,
        )

    def _read_latest_state(self) -> bool | None:
        latest = self.server.latest
        if latest is None:
            return None

        fields = (self.field,) if isinstance(self.field, str) else tuple(self.field)
        states = []
        for field in fields:
            if field not in latest:
                return None
            state = self._read_state(latest.get(field))
            if state is None:
                return None
            states.append(state)

        return all(states) if states else None

    def _read_state(self, value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "open", "opened", "on"):
                return True
            if normalized in ("0", "false", "closed", "close", "off"):
                return False
        return None


def _xy(pos) -> tuple[float, float]:
    if isinstance(pos, tuple):
        return float(pos[0]), float(pos[1])
    return float(pos.x), float(pos.y)


def _rgba(imgui, color: RGBA) -> int:
    return imgui.get_color_u32(imgui.ImVec4(*color))


def _draw_centered_text(
    imgui,
    draw_list,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    color: RGBA,
) -> None:
    text_w, text_h = _xy(imgui.calc_text_size(text))
    draw_list.add_text(
        imgui.ImVec2(
            x0 + max(4.0, (x1 - x0 - text_w) * 0.5),
            y0 + max(2.0, (y1 - y0 - text_h) * 0.5),
        ),
        _rgba(imgui, color),
        text,
    )

def draw_table(
    imgui, server: LatestServer,
    field_validity: Callable[[dict[str, float], str], bool] | None = None,
) -> None:
    imgui.text_unformatted(server.name)
    latest = server.latest
    if latest is None:
        imgui.text_disabled("waiting")
        return

    imgui.columns(2, f"{server.name}_table", borders=True)
    for field in server.fields:
        imgui.text_unformatted(field)
        imgui.next_column()
        if field_validity is not None and not field_validity(latest, field):
            imgui.text_colored(imgui.ImVec4(*BUTTON_STATE_MISMATCH_COLOR), "INVALID")
        else:
            imgui.text_unformatted(str(latest.get(field, "")))
        imgui.next_column()
    imgui.columns(1)





class NidaqGraph:
    WINDOW_SECONDS = 180.0
    REGRESSION_SAMPLE_POINTS = 100

    def __init__(
        self,
        server: LatestServer,
        field_names: dict[str, tuple[float, float]] | None = None,
        max_points: int = 30000,
        title: str = "nidaq graph",
        graph_id: str | None = None,
        field_validity: Callable[[dict[str, float], str], bool] | None = None,
    ) -> None:
        self.server = server
        self.max_points = max_points
        self.title = title
        self.graph_id = graph_id or title
        self.field_validity = field_validity


        self.history: deque[list[float]] = deque(maxlen=max_points)
        self.times: deque[float] = deque(maxlen=max_points)

        self._last_seen: dict[str, float] | None = None
        if field_names == None: 
            self.fields = list(self.server.fields)
            self.scales = [(1,0)]*len(self.fields)
        else: 
            self.fields = list(field_names.keys())
            self.scales = list(field_names.values())
        self._regression_started_at: float | None = None
        self._regression_values_per_min: list[float] = [float("nan")] * len(self.fields)




    def update(self) -> None:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        while self.times and self.times[0] < cutoff:
            self.times.popleft()
            self.history.popleft()

        latest = self.server.latest
        if latest is None or latest is self._last_seen:
            return

        self._last_seen = latest

        # Both receivers use the same host monotonic clock.
        received_at = getattr(self.server, "latest_update_time", None)
        self.times.append(received_at if received_at is not None else now)

        self.history.append(
            [
                (float(latest[field]) * scale[0]) + scale[1]
                if self.field_validity is None or self.field_validity(latest, field)
                else float("nan")
                for field, scale in zip(self.fields, self.scales)
            ]
        )

    def _clear_regression_values(self) -> None:
        self._regression_started_at = None
        self._regression_values_per_min = [float("nan")] * len(self.fields)

    def _update_regression_timer(
        self,
        now: float,
        times_list: list[float],
        history_list: list[list[float]],
    ) -> None:
        if self._regression_started_at is None:
            return
        if now - self._regression_started_at < self.WINDOW_SECONDS:
            return

        self._regression_values_per_min = self._calculate_regression_per_min(
            times_list,
            history_list,
        )
        self._regression_started_at = None

    def _calculate_regression_per_min(
        self,
        times_list: list[float],
        history_list: list[list[float]],
    ) -> list[float]:
        if len(history_list) < 2:
            return [float("nan")] * len(self.fields)

        sample_count = min(self.REGRESSION_SAMPLE_POINTS, len(history_list))
        step = max(1, len(history_list) // sample_count)
        sample_times = times_list[-step * sample_count::step]
        sample_history = history_list[-step * sample_count::step]

        if len(sample_history) > self.REGRESSION_SAMPLE_POINTS:
            sample_times = sample_times[-self.REGRESSION_SAMPLE_POINTS:]
            sample_history = sample_history[-self.REGRESSION_SAMPLE_POINTS:]
        if len(sample_history) < 2:
            return [float("nan")] * len(self.fields)

        x = np.asarray(sample_times, dtype=np.float64)
        x = (x - x[0]) / 60.0
        x = x - x.mean()
        denominator = float(np.dot(x, x))
        if denominator == 0.0:
            return [float("nan")] * len(self.fields)

        y = np.asarray(sample_history, dtype=np.float64)
        slopes = []
        for i in range(len(self.fields)):
            values = y[:, i]
            centered_values = values - values.mean()
            slopes.append(float(np.dot(x, centered_values) / denominator))
        return slopes

    def _format_regression_value(self, value: float) -> str:
        if np.isnan(value):
            return "NAN"
        return f"{value:.2f}/min"

    def _calculate_thrust_impulse(
        self,
        times_list: list[float],
        data: np.ndarray,
    ) -> float:
        if "Thrust" not in self.fields or len(times_list) < 2:
            return float("nan")

        thrust_index = self.fields.index("Thrust")
        times = np.asarray(times_list, dtype=np.float64)
        thrust = np.asarray(data[:, thrust_index], dtype=np.float64)
        thrust = np.where(thrust >= 1, thrust, 0)

        if hasattr(np, "trapezoid"):
            return float(np.trapezoid(thrust, times))

        return float(np.trapz(thrust, times))

    def draw(self, imgui) -> None:
        if not hasattr(self, "_view"):
            self._view = SensorGraph({"": self}, title=self.title, graph_id=self.graph_id)
        self._view.draw(imgui)


class SensorGraph:
    """Plot independently sampled sources on a shared time axis."""

    WINDOW_SECONDS = NidaqGraph.WINDOW_SECONDS

    def __init__(
        self,
        sources: dict[str, NidaqGraph],
        title: str = "Sensors",
        graph_id: str = "sensor_graph",
    ) -> None:
        self.sources = sources
        self.title = title
        self.graph_id = graph_id
        self._reset_view = True
        self._scroll_enabled = False

    def _get_width(self, imgui) -> float:
        available = imgui.get_content_region_avail()

        if isinstance(available, tuple):
            return float(available[0])

        return float(available.x)

    def _cond_always(self, imgui):
        if hasattr(imgui, "Cond_"):
            return imgui.Cond_.always

        return imgui.ALWAYS

    def _axis_x1(self):
        if hasattr(implot, "ImAxis_"):
            return implot.ImAxis_.x1

        return implot.ImAxis_X1

    def _axis_y1(self):
        if hasattr(implot, "ImAxis_"):
            return implot.ImAxis_.y1

        return implot.ImAxis_Y1

    def _plot_no_inputs_flag(self):
        if hasattr(implot, "Flags_"):
            return implot.Flags_.no_inputs

        return implot.ImPlotFlags_NoInputs

    @staticmethod
    def _label(source: str, field: str) -> str:
        return f"{source} · {field}" if source else field

    def draw(self, imgui) -> None:
        imgui.push_id(self.graph_id)
        for graph in self.sources.values():
            graph.update()

        imgui.text_unformatted(self.title)
        imgui.same_line()
        if imgui.button("Reset View"):
            self._reset_view = True
        imgui.same_line()
        if imgui.button("Scroll: On" if self._scroll_enabled else "Scroll: Off"):
            self._scroll_enabled = not self._scroll_enabled

        now = time.monotonic()
        snapshots = []
        for source, graph in self.sources.items():
            times = list(graph.times)
            history = list(graph.history)
            graph._update_regression_timer(now, times, history)
            data = np.asarray(history, dtype=np.float64).reshape(-1, len(graph.fields))
            snapshots.append((source, graph, times, data))

        if not any(len(times) >= 2 for _, _, times, _ in snapshots):
            imgui.text_disabled("waiting")
            imgui.pop_id()
            return

        finite_values = np.concatenate([data[np.isfinite(data)] for _, _, _, data in snapshots])
        scale_min = float(finite_values.min()) if finite_values.size else 0.0
        scale_max = float(finite_values.max()) if finite_values.size else 1.0
        padding = (scale_max - scale_min) * 0.10 if scale_min != scale_max else 1.0
        scale_min -= padding
        scale_max += padding

        width = self._get_width(imgui)
        value_panel_width = 300.0
        plot_width = max(200.0, width - value_panel_width)

        cond_always = self._cond_always(imgui)
        if self._reset_view:
            implot.set_next_axes_limits(
                -self.WINDOW_SECONDS,
                0.0,
                scale_min,
                scale_max,
                cond_always,
            )
            self._reset_view = False
        plot_flags = 0 if self._scroll_enabled else self._plot_no_inputs_flag()
        if implot.begin_plot(
            f"{self.title} plot##{self.graph_id}",
            size=imgui.ImVec2(plot_width, 600), flags=plot_flags,
        ):
            implot.setup_axes("seconds from now", "value")
            implot.setup_axis_limits_constraints(self._axis_x1(), -self.WINDOW_SECONDS, 0.0)
            implot.setup_axis_limits(self._axis_y1(), scale_min, scale_max, cond_always)
            for source, graph, times, data in snapshots:
                if not times:
                    continue
                xs = np.ascontiguousarray([t - now for t in times], dtype=np.float64)
                for i, field in enumerate(graph.fields):
                    ys = np.ascontiguousarray(data[:, i], dtype=np.float64)
                    implot.plot_line(self._label(source, field), xs, ys)
            implot.end_plot()

        imgui.same_line()
        # Keep all channels within the graph's height.
        imgui.begin_child("sensor_values", imgui.ImVec2(value_panel_width, 600))
        imgui.text_unformatted("Latest")
        for source, graph, times, data in snapshots:
            for i, field in enumerate(graph.fields):
                label = self._label(source, field)
                if not times:
                    imgui.text_disabled(f"{label}: waiting")
                elif not np.isfinite(data[-1, i]):
                    imgui.text_colored(
                        imgui.ImVec4(*BUTTON_STATE_MISMATCH_COLOR), f"{label}: INVALID",
                    )
                else:
                    imgui.text_unformatted(f"{label}: {data[-1, i]:.2f}")

        imgui.separator()
        if imgui.button("Start 3m avg"):
            for graph in self.sources.values():
                graph._regression_started_at = now
                graph._regression_values_per_min = [float("nan")] * len(graph.fields)
        imgui.same_line()
        if imgui.button("Clear"):
            for graph in self.sources.values():
                graph._clear_regression_values()
        for graph in self.sources.values():
            if graph._regression_started_at is not None:
                remaining = max(0.0, self.WINDOW_SECONDS - (now - graph._regression_started_at))
                imgui.text_unformatted(f"Timer: {remaining:.0f}s")
                break
        imgui.text_unformatted("Avg change/min")
        for source, graph, _, _ in snapshots:
            for field, value in zip(graph.fields, graph._regression_values_per_min):
                imgui.text_unformatted(
                    f"{self._label(source, field)}: {graph._format_regression_value(value)}",
                )

        imgui.separator()
        imgui.text_unformatted("3 min change")
        for source, graph, times, data in snapshots:
            for i, field in enumerate(graph.fields):
                value = data[-1, i] - data[0, i] if times else float("nan")
                imgui.text_unformatted(f"{self._label(source, field)}: {value:.2f}")

        imgui.separator()
        imgui.text_unformatted("3 min impulse")
        for source, graph, times, data in snapshots:
            if "Thrust" in graph.fields:
                impulse = graph._calculate_thrust_impulse(times, data)
                value = f"{impulse:.2f}" if np.isfinite(impulse) else "NAN"
                imgui.text_unformatted(f"{self._label(source, 'Thrust')}: {value} lbf s")
        imgui.end_child()
        imgui.pop_id()










@dataclass
class MVAS_STATE(valve_state):
    latest_toggled_value: bool | None = None
    
    label: str = "MVAS"
    field: None = None


    def _read_latest_state(self) -> bool | None:
        #lngpot on top of lox pot means closed
        self.latest = self.server.latest
        if self.latest is None:
            return None
        if self.latest.get("LNGPOT") is None or self.latest.get("LOXPOT") is None:
            return None
        if self.latest.get("LNGPOT") < self.latest.get("LOXPOT"):
            return False
        return True



