from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import numpy as np

from viviian.gui_utils.buttons import (
    ButtonStateUpdate,
    MomentaryButton,
    SetpointButton,
    StateButton,
    ToggleButton,
    reconstruct_button,
)
from viviian.gui_utils.gauges import (
    AnalogNeedleGauge,
    LedBarGauge,
    SensorGauge,
    _advance_display_value,
)
from viviian.gui_utils.graphs import GraphSeries, SensorGraph
from viviian.gui_utils.operator import (
    EventLogPanel,
    EventRecord,
    MicroButton,
    ProcedureCarousel,
    ProcedureStep,
    ReadoutCard,
    TelemetryCard,
    TelemetryFilmstrip,
    TelemetryTicker,
)


class FakeReader:
    def __init__(self, *, shape: tuple[int, int], dtype: np.dtype, frames: list[np.ndarray] | None = None):
        self.shape = shape
        self.dtype = np.dtype(dtype)
        self._frames = [np.asarray(frame, dtype=self.dtype).reshape(self.shape) for frame in (frames or [])]
        self.blocking_calls: list[bool] = []

    def set_blocking(self, blocking: bool) -> None:
        self.blocking_calls.append(bool(blocking))

    def push(self, frame: np.ndarray) -> None:
        self._frames.append(np.asarray(frame, dtype=self.dtype).reshape(self.shape))

    def read(self) -> np.ndarray | None:
        if not self._frames:
            return None
        return self._frames.pop(0).copy()


class FakeDrawList:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def add_rect_filled(self, *args) -> None:
        self.calls.append(("add_rect_filled", args))

    def add_rect(self, *args) -> None:
        self.calls.append(("add_rect", args))

    def add_line(self, *args) -> None:
        self.calls.append(("add_line", args))

    def add_polyline(self, *args) -> None:
        self.calls.append(("add_polyline", args))

    def add_circle_filled(self, *args) -> None:
        self.calls.append(("add_circle_filled", args))

    def add_text(self, *args) -> None:
        self.calls.append(("add_text", args))


class FakeIO:
    def __init__(self, delta_time: float) -> None:
        self.delta_time = float(delta_time)
        self.display_size = (1280.0, 900.0)
        self.fonts = self
        self.font_default = None

    def add_font_default(self):
        return object()

    def add_font_from_file_ttf(self, *_args, **_kwargs):
        return object()

    def clear_fonts(self) -> None:
        return None


class FakeImgui:
    COLOR_BUTTON = 1
    COLOR_BUTTON_HOVERED = 2
    COLOR_BUTTON_ACTIVE = 3
    COLOR_TEXT = 4
    COLOR_TEXT_DISABLED = 5
    COLOR_BORDER = 6
    STYLE_FRAME_BORDER_SIZE = 1
    STYLE_FRAME_ROUNDING = 2
    STYLE_FRAME_PADDING = 3

    def __init__(
        self,
        presses: list[bool] | None = None,
        *,
        delta_time: float = 1.0 / 60.0,
        input_float_values: list[tuple[bool, float]] | None = None,
    ):
        self._presses = list(presses or [])
        self._input_float_values: list[tuple[bool, float]] = list(input_float_values or [])
        self._draw_list = FakeDrawList()
        self._io = FakeIO(delta_time)
        self.last_dummy: tuple[float, float] | None = None
        self._cursor_x = 12.0
        self._cursor_y = 18.0
        self._last_item_min = (12.0, 18.0)
        self._last_item_max = (12.0, 18.0)
        self._same_line = False

    def push_style_color(self, *_args) -> None:
        return None

    def pop_style_color(self, *_args) -> None:
        return None

    def push_style_var(self, *_args) -> None:
        return None

    def pop_style_var(self, *_args) -> None:
        return None

    def button(self, *_args, **_kwargs) -> bool:
        width = float(_kwargs.get("width", 120.0) or 120.0)
        height = float(_kwargs.get("height", 28.0) or 28.0)
        self._place_item(width, height)
        if self._presses:
            return self._presses.pop(0)
        return False

    def text_disabled(self, *_args) -> None:
        self._place_item(120.0, 16.0)
        return None

    def text_colored(self, *_args) -> None:
        self._place_item(120.0, 16.0)
        return None

    def text_unformatted(self, *_args) -> None:
        self._place_item(120.0, 16.0)
        return None

    def same_line(self, *_args) -> None:
        self._cursor_x = self._last_item_max[0] + 8.0
        self._cursor_y = self._last_item_min[1]
        self._same_line = True

    def spacing(self) -> None:
        self._same_line = False
        self._cursor_x = 12.0
        self._cursor_y = self._last_item_max[1] + 8.0

    def input_text(self, _label: str, value: str, *_args, **_kwargs) -> tuple[bool, str]:
        self._place_item(220.0, 26.0)
        return False, value

    def separator(self) -> None:
        self._place_item(240.0, 8.0)

    def begin_group(self) -> None:
        return None

    def end_group(self) -> None:
        return None

    def push_font(self, *_args) -> None:
        return None

    def pop_font(self) -> None:
        return None

    def get_content_region_available(self) -> tuple[float, float]:
        return (max(120.0, 1260.0 - self._cursor_x), 900.0 - self._cursor_y)

    def get_cursor_screen_pos(self) -> tuple[float, float]:
        return (self._cursor_x, self._cursor_y)

    def dummy(self, width: float, height: float) -> None:
        self.last_dummy = (float(width), float(height))
        self._place_item(width, height)

    def get_window_draw_list(self) -> FakeDrawList:
        return self._draw_list

    def get_item_rect_min(self) -> tuple[float, float]:
        return self._last_item_min

    def get_item_rect_max(self) -> tuple[float, float]:
        return self._last_item_max

    def get_color_u32_rgba(self, *rgba: float) -> int:
        r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
        return (a << 24) | (b << 16) | (g << 8) | r

    def get_io(self) -> FakeIO:
        return self._io

    def set_cursor_screen_pos(self, pos: tuple[float, float]) -> None:
        self._cursor_x = float(pos[0])
        self._cursor_y = float(pos[1])
        self._same_line = False

    def set_next_item_width(self, _width: float) -> None:
        return None

    def input_float(
        self, _label: str, value: float, *_args, **_kwargs
    ) -> tuple[bool, float]:
        self._place_item(90.0, 26.0)
        if self._input_float_values:
            return self._input_float_values.pop(0)
        return False, float(value)

    def calc_text_size(self, text: str) -> tuple[float, float]:
        return (max(6.0, len(text) * 6.0), 10.0)

    def _place_item(self, width: float, height: float) -> None:
        x0 = self._cursor_x
        y0 = self._cursor_y
        x1 = x0 + max(1.0, float(width))
        y1 = y0 + max(1.0, float(height))
        self._last_item_min = (x0, y0)
        self._last_item_max = (x1, y1)
        if self._same_line:
            self._cursor_x = x1 + 8.0
        else:
            self._cursor_x = 12.0
            self._cursor_y = y1 + 6.0
        self._same_line = False


class MissingFrameBorderFakeImgui(FakeImgui):
    def __getattribute__(self, name: str):
        if name == "STYLE_FRAME_BORDER_SIZE":
            raise AttributeError(name)
        return super().__getattribute__(name)


class SensorGraphTests(unittest.TestCase):
    def make_graph(self, **overrides) -> SensorGraph:
        series = (
            GraphSeries(
                series_id="copv",
                label="COPV",
                stream_name="copv_stream",
                color_rgba=(1.0, 0.0, 0.0, 1.0),
            ),
        )
        params = {
            "graph_id": "pressures",
            "title": "Pressures",
            "series": series,
        }
        params.update(overrides)
        return SensorGraph(**params)

    def test_sensor_graph_round_trip(self) -> None:
        graph = SensorGraph(
            "graph_a",
            title="Main Pressure Deck",
            series=(
                GraphSeries(
                    series_id="copv",
                    label="COPV",
                    stream_name="copv_stream",
                    color_rgba=(1.0, 0.0, 0.0, 1.0),
                ),
                GraphSeries(
                    series_id="lox",
                    label="LOX",
                    stream_name="lox_stream",
                    color_rgba=(0.0, 1.0, 0.0, 1.0),
                    overlay=True,
                ),
            ),
            backpressure_mode="blocking",
            show_series_controls=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = graph.export(f"{tmpdir}/graph.toml")
            rebuilt = SensorGraph.reconstruct(path)

        self.assertEqual(rebuilt.graph_id, "graph_a")
        self.assertEqual(rebuilt.title, "Main Pressure Deck")
        self.assertEqual(rebuilt.backpressure_mode, "blocking")
        self.assertFalse(rebuilt.show_series_controls)
        self.assertEqual(tuple(item.series_id for item in rebuilt.series), ("copv", "lox"))
        self.assertTrue(rebuilt.series[1].overlay)

    def test_graph_clock_resets_when_single_stream_restarts_at_earlier_timestamp(self) -> None:
        graph = self.make_graph(window_seconds=2.0, max_points_per_series=16)
        reader = FakeReader(shape=(2, 2), dtype=np.float64)
        graph.bind({"copv_stream": reader})
        reader.push(np.array([[99.0, 100.0], [1.0, 2.0]], dtype=np.float64))
        graph.consume()
        reader.push(np.array([[0.0, 1.0], [3.0, 4.0]], dtype=np.float64))

        graph.consume()

        np.testing.assert_allclose(
            graph.series_snapshot("copv"),
            np.array([[0.0, 1.0], [3.0, 4.0]], dtype=np.float64),
        )

    def test_bind_rejects_missing_reader(self) -> None:
        graph = self.make_graph()
        with self.assertRaises(KeyError):
            graph.bind({})

    def test_bind_rejects_invalid_shape(self) -> None:
        graph = self.make_graph()
        reader = FakeReader(shape=(4, 2), dtype=np.float32)
        with self.assertRaises(ValueError):
            graph.bind({"copv_stream": reader})

    def test_bind_rejects_invalid_dtype(self) -> None:
        graph = self.make_graph()
        reader = FakeReader(shape=(2, 4), dtype=np.int32)
        with self.assertRaises(ValueError):
            graph.bind({"copv_stream": reader})

    def test_bind_sets_latest_only_reader_non_blocking(self) -> None:
        graph = self.make_graph(backpressure_mode="latest_only")
        reader = FakeReader(shape=(2, 4), dtype=np.float32)
        graph.bind({"copv_stream": reader})
        self.assertEqual(reader.blocking_calls, [False])

    def test_bind_sets_blocking_reader_active(self) -> None:
        graph = self.make_graph(backpressure_mode="blocking")
        reader = FakeReader(shape=(2, 4), dtype=np.float32)
        graph.bind({"copv_stream": reader})
        self.assertEqual(reader.blocking_calls, [True])

    def test_consume_trims_window(self) -> None:
        graph = self.make_graph(window_seconds=2.5, max_points_per_series=16)
        reader = FakeReader(
            shape=(2, 4),
            dtype=np.float32,
            frames=[
                np.array([[0, 1, 2, 3], [10, 11, 12, 13]], dtype=np.float32),
                np.array([[4, 5, 6, 7], [14, 15, 16, 17]], dtype=np.float32),
            ],
        )
        graph.bind({"copv_stream": reader})

        self.assertTrue(graph.consume())
        snapshot = graph.series_snapshot("copv")
        np.testing.assert_allclose(snapshot[0], np.array([5.0, 6.0, 7.0]))
        np.testing.assert_allclose(snapshot[1], np.array([15.0, 16.0, 17.0]))

    def test_consume_enforces_hard_point_cap(self) -> None:
        graph = self.make_graph(window_seconds=100.0, max_points_per_series=4)
        reader = FakeReader(
            shape=(2, 8),
            dtype=np.float32,
            frames=[
                np.array(
                    [
                        [0, 1, 2, 3, 4, 5, 6, 7],
                        [10, 11, 12, 13, 14, 15, 16, 17],
                    ],
                    dtype=np.float32,
                )
            ],
        )
        graph.bind({"copv_stream": reader})

        graph.consume()
        snapshot = graph.series_snapshot("copv")
        np.testing.assert_allclose(snapshot[0], np.array([4.0, 5.0, 6.0, 7.0]))
        np.testing.assert_allclose(snapshot[1], np.array([14.0, 15.0, 16.0, 17.0]))

    def test_inactive_series_ages_out_when_other_series_advances_graph_clock(self) -> None:
        graph = SensorGraph(
            "deck",
            title="Deck",
            series=(
                GraphSeries(
                    series_id="signal_1",
                    label="Signal 1",
                    stream_name="signal_1",
                    color_rgba=(1.0, 0.0, 0.0, 1.0),
                ),
                GraphSeries(
                    series_id="signal_2",
                    label="Signal 2",
                    stream_name="signal_2",
                    color_rgba=(0.0, 1.0, 0.0, 1.0),
                ),
            ),
            window_seconds=2.5,
            max_points_per_series=16,
        )
        signal_1_reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[np.array([[0, 1, 2, 3], [100, 100, 100, 100]], dtype=np.float64)],
        )
        signal_2_reader = FakeReader(shape=(2, 4), dtype=np.float64)
        graph.bind({"signal_1": signal_1_reader, "signal_2": signal_2_reader})

        self.assertTrue(graph.consume())
        signal_2_reader.push(np.array([[4, 5, 6, 7], [1, 2, 1, 2]], dtype=np.float64))

        self.assertTrue(graph.consume())
        self.assertEqual(graph.series_snapshot("signal_1").shape, (2, 0))
        np.testing.assert_allclose(
            graph.series_snapshot("signal_2"),
            np.array([[5.0, 6.0, 7.0], [2.0, 1.0, 2.0]], dtype=np.float64),
        )

    def test_stale_series_stops_affecting_y_limits_after_window_expiry(self) -> None:
        graph = SensorGraph(
            "deck",
            title="Deck",
            series=(
                GraphSeries(
                    series_id="signal_1",
                    label="Signal 1",
                    stream_name="signal_1",
                    color_rgba=(1.0, 0.0, 0.0, 1.0),
                ),
                GraphSeries(
                    series_id="signal_2",
                    label="Signal 2",
                    stream_name="signal_2",
                    color_rgba=(0.0, 1.0, 0.0, 1.0),
                ),
            ),
            window_seconds=2.5,
            max_points_per_series=16,
            stable_y=False,
        )
        signal_1_reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[np.array([[0, 1, 2, 3], [100, 100, 100, 100]], dtype=np.float64)],
        )
        signal_2_reader = FakeReader(shape=(2, 4), dtype=np.float64)
        graph.bind({"signal_1": signal_1_reader, "signal_2": signal_2_reader})

        graph.consume()
        signal_2_reader.push(np.array([[4, 5, 6, 7], [1, 2, 1, 2]], dtype=np.float64))
        graph.consume()

        self.assertEqual(graph._y_limits, (0.92, 2.08))

    def test_hiding_outlier_series_recomputes_y_limits_immediately(self) -> None:
        graph = SensorGraph(
            "deck",
            title="Deck",
            series=(
                GraphSeries(
                    series_id="outlier",
                    label="Outlier",
                    stream_name="outlier",
                    color_rgba=(1.0, 0.0, 0.0, 1.0),
                ),
                GraphSeries(
                    series_id="normal",
                    label="Normal",
                    stream_name="normal",
                    color_rgba=(0.0, 1.0, 0.0, 1.0),
                ),
            ),
            stable_y=True,
        )
        outlier_reader = FakeReader(
            shape=(2, 2),
            dtype=np.float64,
            frames=[np.array([[0.0, 1.0], [0.0, 100.0]], dtype=np.float64)],
        )
        normal_reader = FakeReader(
            shape=(2, 2),
            dtype=np.float64,
            frames=[np.array([[0.0, 1.0], [1.0, 2.0]], dtype=np.float64)],
        )
        graph.bind({"outlier": outlier_reader, "normal": normal_reader})
        graph.consume()

        with mock.patch("viviian.gui_utils.graphs._require_imgui", return_value=FakeImgui([True, False])):
            graph._render_visibility_controls(FakeImgui([True, False]))

        self.assertEqual(graph._y_limits, (0.92, 2.08))


class SensorGaugeTests(unittest.TestCase):
    def make_analog_gauge(self, **overrides) -> AnalogNeedleGauge:
        params = {
            "gauge_id": "pressure",
            "label": "Pressure",
            "stream_name": "pressure_stream",
            "low_value": 0.0,
            "high_value": 100.0,
        }
        params.update(overrides)
        return AnalogNeedleGauge(**params)

    def make_led_gauge(self, **overrides) -> LedBarGauge:
        params = {
            "gauge_id": "temperature",
            "label": "Temperature",
            "stream_name": "temperature_stream",
            "low_value": 0.0,
            "high_value": 100.0,
        }
        params.update(overrides)
        return LedBarGauge(**params)

    def test_analog_gauge_round_trip(self) -> None:
        gauge = self.make_analog_gauge(
            animation_response_hz=12.0,
            width=260.0,
            arc_thickness=12.0,
            theme_name="tau_ceti",
            layout_style="radial",
            unit_label="PSI",
            display_precision=1,
            status_text="TRACK",
            status_severity="warn",
            footer_left="AUTO",
            footer_right="P-01",
            secondary_label="DELTA",
            secondary_value="+12.0",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gauge.export(f"{tmpdir}/analog.toml")
            rebuilt = SensorGauge.reconstruct(path)

        self.assertIsInstance(rebuilt, AnalogNeedleGauge)
        self.assertEqual(rebuilt.gauge_id, "pressure")
        self.assertEqual(rebuilt.stream_name, "pressure_stream")
        self.assertEqual(rebuilt.width, 260.0)
        self.assertEqual(rebuilt.arc_thickness, 12.0)
        self.assertEqual(rebuilt.animation_response_hz, 12.0)
        self.assertEqual(rebuilt.theme_name, "tau_ceti")
        self.assertEqual(rebuilt.layout_style, "radial")
        self.assertEqual(rebuilt.unit_label, "PSI")
        self.assertEqual(rebuilt.display_precision, 1)
        self.assertEqual(rebuilt.status_text, "TRACK")
        self.assertEqual(rebuilt.status_severity, "warn")
        self.assertEqual(rebuilt.footer_left, "AUTO")
        self.assertEqual(rebuilt.footer_right, "P-01")
        self.assertEqual(rebuilt.secondary_label, "DELTA")
        self.assertEqual(rebuilt.secondary_value, "+12.0")

    def test_analog_reconstruct_uses_constructor_default_arc_thickness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/analog.toml"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        (
                            "format_version = 1",
                            'kind = "analog_needle_gauge"',
                            'gauge_id = "pressure"',
                            'label = "Pressure"',
                            'stream_name = "pressure_stream"',
                            "low_value = 0.0",
                            "high_value = 100.0",
                        )
                    )
                    + "\n"
                )
            rebuilt = SensorGauge.reconstruct(path)

        self.assertIsInstance(rebuilt, AnalogNeedleGauge)
        self.assertEqual(rebuilt.arc_thickness, AnalogNeedleGauge("probe", label="Probe", stream_name="probe_stream", low_value=0.0, high_value=1.0).arc_thickness)

    def test_led_gauge_round_trip(self) -> None:
        gauge = self.make_led_gauge(
            animation_response_hz=6.0,
            segment_count=12,
            segment_gap_ratio=0.2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gauge.export(f"{tmpdir}/led.toml")
            rebuilt = SensorGauge.reconstruct(path)

        self.assertIsInstance(rebuilt, LedBarGauge)
        self.assertEqual(rebuilt.segment_count, 12)
        self.assertEqual(rebuilt.segment_gap_ratio, 0.2)
        self.assertEqual(rebuilt.animation_response_hz, 6.0)

    def test_base_reconstruct_dispatches_correct_subclass(self) -> None:
        analog = self.make_analog_gauge()
        led = self.make_led_gauge()

        with tempfile.TemporaryDirectory() as tmpdir:
            analog_path = analog.export(f"{tmpdir}/analog.toml")
            led_path = led.export(f"{tmpdir}/led.toml")
            rebuilt_analog = SensorGauge.reconstruct(analog_path)
            rebuilt_led = SensorGauge.reconstruct(led_path)

        self.assertIsInstance(rebuilt_analog, AnalogNeedleGauge)
        self.assertIsInstance(rebuilt_led, LedBarGauge)

    def test_gauge_repr_includes_identifying_fields(self) -> None:
        analog = self.make_analog_gauge()
        led = self.make_led_gauge(segment_count=14)

        self.assertIn("gauge_id='pressure'", repr(analog))
        self.assertIn("stream_name='pressure_stream'", repr(analog))
        self.assertIn("segment_count=14", repr(led))

    def test_gauge_defaults_are_tightened(self) -> None:
        analog = self.make_analog_gauge()
        led = self.make_led_gauge()

        self.assertEqual(analog.width, 196.0)
        self.assertEqual(analog.height, 156.0)
        self.assertEqual(analog.arc_thickness, 14.0)
        self.assertEqual(led.width, 208.0)
        self.assertEqual(led.height, 60.0)

    def test_rejects_invalid_ranges_and_segment_count(self) -> None:
        with self.assertRaises(ValueError):
            self.make_analog_gauge(high_value=0.0)
        with self.assertRaises(ValueError):
            self.make_led_gauge(segment_count=0)
        with self.assertRaises(ValueError):
            self.make_analog_gauge(animation_response_hz=0.0)

    def test_bind_rejects_missing_reader(self) -> None:
        gauge = self.make_analog_gauge()
        with self.assertRaises(KeyError):
            gauge.bind({})

    def test_bind_rejects_invalid_shape(self) -> None:
        gauge = self.make_analog_gauge()
        reader = FakeReader(shape=(4, 2), dtype=np.float32)
        with self.assertRaises(ValueError):
            gauge.bind({"pressure_stream": reader})

    def test_bind_rejects_invalid_dtype(self) -> None:
        gauge = self.make_analog_gauge()
        reader = FakeReader(shape=(2, 4), dtype=np.int32)
        with self.assertRaises(ValueError):
            gauge.bind({"pressure_stream": reader})

    def test_bind_sets_reader_non_blocking(self) -> None:
        gauge = self.make_analog_gauge()
        reader = FakeReader(shape=(2, 1), dtype=np.float64)
        gauge.bind({"pressure_stream": reader})
        self.assertEqual(reader.blocking_calls, [False])

    def test_consume_uses_latest_finite_sample(self) -> None:
        gauge = self.make_analog_gauge()
        reader = FakeReader(
            shape=(2, 3),
            dtype=np.float64,
            frames=[
                np.array([[0.0, 1.0, 2.0], [np.nan, 25.0, np.nan]], dtype=np.float64),
                np.array([[3.0, 4.0, 5.0], [45.0, np.nan, 55.0]], dtype=np.float64),
            ],
        )
        gauge.bind({"pressure_stream": reader})

        self.assertTrue(gauge.consume())
        self.assertEqual(gauge.latest_timestamp, 5.0)
        self.assertEqual(gauge.target_value, 55.0)
        self.assertTrue(gauge.has_value)

    def test_consume_updates_latest_rate_from_newest_samples(self) -> None:
        gauge = self.make_analog_gauge(display_precision=1)
        reader = FakeReader(
            shape=(2, 3),
            dtype=np.float64,
            frames=[
                np.array([[0.0, 1.0, 2.0], [10.0, 18.0, 30.0]], dtype=np.float64),
            ],
        )
        gauge.bind({"pressure_stream": reader})

        gauge.consume()

        self.assertEqual(gauge.latest_rate, 12.0)
        self.assertEqual(gauge.formatted_rate(), "Δ +12.0 / SEC")

    def test_timestamp_rewind_snaps_display_value_to_new_target(self) -> None:
        gauge = self.make_analog_gauge()
        reader = FakeReader(
            shape=(2, 2),
            dtype=np.float64,
            frames=[np.array([[10.0, 11.0], [80.0, 90.0]], dtype=np.float64)],
        )
        gauge.bind({"pressure_stream": reader})
        gauge.consume()
        gauge._display_value = 72.5
        reader.push(np.array([[0.0, 1.0], [20.0, 30.0]], dtype=np.float64))

        gauge.consume()

        self.assertEqual(gauge.latest_timestamp, 1.0)
        self.assertEqual(gauge.target_value, 30.0)
        self.assertEqual(gauge.display_value, 30.0)

    def test_normalized_fraction_clamps_to_range(self) -> None:
        gauge = self.make_analog_gauge()

        self.assertEqual(gauge.normalized_fraction(-10.0), 0.0)
        self.assertEqual(gauge.normalized_fraction(0.0), 0.0)
        self.assertEqual(gauge.normalized_fraction(50.0), 0.5)
        self.assertEqual(gauge.normalized_fraction(100.0), 1.0)
        self.assertEqual(gauge.normalized_fraction(120.0), 1.0)

    def test_led_segment_activation_matches_fraction_thresholds(self) -> None:
        gauge = self.make_led_gauge()

        gauge._display_value = 0.0
        self.assertEqual(gauge.lit_segments(), 0)
        gauge._display_value = 10.0
        self.assertEqual(gauge.lit_segments(), 1)
        gauge._display_value = 50.0
        self.assertEqual(gauge.lit_segments(), 5)
        gauge._display_value = 100.0
        self.assertEqual(gauge.lit_segments(), 10)

    def test_analog_angle_mapping_matches_low_mid_and_high(self) -> None:
        gauge = self.make_analog_gauge()

        gauge._display_value = 0.0
        self.assertEqual(gauge.needle_angle_degrees(), 210.0)
        gauge._display_value = 50.0
        self.assertEqual(gauge.needle_angle_degrees(), 90.0)
        gauge._display_value = 100.0
        self.assertEqual(gauge.needle_angle_degrees(), -30.0)

    def test_animation_helper_moves_toward_target_with_delta_time(self) -> None:
        next_short = _advance_display_value(
            current_value=0.0,
            target_value=100.0,
            response_hz=8.0,
            delta_time=0.02,
        )
        next_long = _advance_display_value(
            current_value=0.0,
            target_value=100.0,
            response_hz=8.0,
            delta_time=0.10,
        )

        self.assertGreater(next_short, 0.0)
        self.assertLess(next_short, 100.0)
        self.assertGreater(next_long, next_short)

    def test_analog_render_smoke_test(self) -> None:
        gauge = self.make_analog_gauge()
        gauge._has_value = True
        gauge._target_value = 60.0
        gauge._display_value = 60.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        call_names = [name for name, _args in fake_imgui.get_window_draw_list().calls]
        self.assertIn("add_rect_filled", call_names)
        self.assertIn("add_rect", call_names)
        self.assertIn("add_line", call_names)
        self.assertIn("add_circle_filled", call_names)
        self.assertEqual(call_names.count("add_text"), 5)
        tick_labels = [args[3] for name, args in fake_imgui.get_window_draw_list().calls if name == "add_text"]
        self.assertEqual(tick_labels, ["0", "25", "50", "75", "100"])

        panel_fill = next(
            args for name, args in fake_imgui.get_window_draw_list().calls if name == "add_rect_filled"
        )
        panel_left, panel_top, panel_right, panel_bottom = (
            float(panel_fill[0]),
            float(panel_fill[1]),
            float(panel_fill[2]),
            float(panel_fill[3]),
        )
        for name, args in fake_imgui.get_window_draw_list().calls:
            if name != "add_line":
                continue
            x0, y0, x1, y1 = (float(args[0]), float(args[1]), float(args[2]), float(args[3]))
            self.assertGreaterEqual(x0, panel_left - 1.0e-6)
            self.assertGreaterEqual(x1, panel_left - 1.0e-6)
            self.assertLessEqual(x0, panel_right + 1.0e-6)
            self.assertLessEqual(x1, panel_right + 1.0e-6)
            self.assertGreaterEqual(y0, panel_top - 1.0e-6)
            self.assertGreaterEqual(y1, panel_top - 1.0e-6)
            self.assertLessEqual(y0, panel_bottom + 1.0e-6)
            self.assertLessEqual(y1, panel_bottom + 1.0e-6)

    def test_led_render_smoke_test(self) -> None:
        gauge = self.make_led_gauge()
        gauge._has_value = True
        gauge._target_value = 75.0
        gauge._display_value = 75.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        call_names = [name for name, _args in fake_imgui.get_window_draw_list().calls]
        self.assertIn("add_rect_filled", call_names)
        self.assertIn("add_rect", call_names)

    def test_led_render_uses_canonical_severity_bands(self) -> None:
        gauge = self.make_led_gauge()
        gauge._has_value = True
        gauge._target_value = 100.0
        gauge._display_value = 100.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        fill_calls = [
            args for name, args in fake_imgui.get_window_draw_list().calls if name == "add_rect_filled"
        ]
        segment_colors = [args[4] for args in fill_calls[1:]]
        self.assertEqual(len(segment_colors), 10)
        self.assertEqual(len(set(segment_colors[:5])), 1)
        self.assertEqual(segment_colors[5], segment_colors[6])
        self.assertNotEqual(segment_colors[6], segment_colors[7])
        self.assertEqual(segment_colors[8], segment_colors[9])

    def test_led_render_maps_non_ten_segment_counts_by_bucket(self) -> None:
        gauge = self.make_led_gauge(segment_count=4)
        gauge._has_value = True
        gauge._target_value = 100.0
        gauge._display_value = 100.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        fill_calls = [
            args for name, args in fake_imgui.get_window_draw_list().calls if name == "add_rect_filled"
        ]
        segment_colors = [args[4] for args in fill_calls[1:]]
        self.assertEqual(len(segment_colors), 4)
        self.assertEqual(segment_colors[0], segment_colors[1])
        self.assertNotEqual(segment_colors[1], segment_colors[2])
        self.assertNotEqual(segment_colors[2], segment_colors[3])


class ButtonTests(unittest.TestCase):
    def test_state_button_round_trip(self) -> None:
        button = StateButton(
            button_id="generic",
            label="Generic",
            state_id="generic.state",
            state="set",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = button.export(f"{tmpdir}/button.toml")
            rebuilt = StateButton.reconstruct(path)

        self.assertIsInstance(rebuilt, StateButton)
        self.assertNotIsInstance(rebuilt, ToggleButton)
        self.assertEqual(rebuilt.state, "set")

    def test_toggle_round_trip(self) -> None:
        button = ToggleButton(
            button_id="vent",
            label="Vent",
            state_id="ecu.vent",
            state=False,
            gate_id="operator_gate",
            interlock_ids=("armed",),
            theme_name="tau_ceti",
            variant="alert",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = button.export(f"{tmpdir}/button.toml")
            rebuilt = StateButton.reconstruct(path)

        self.assertIsInstance(rebuilt, ToggleButton)
        self.assertEqual(rebuilt.button_id, "vent")
        self.assertEqual(rebuilt.state_id, "ecu.vent")
        self.assertFalse(rebuilt.state)
        self.assertEqual(rebuilt.gate_id, "operator_gate")
        self.assertEqual(rebuilt.interlock_ids, ("armed",))
        self.assertEqual(rebuilt.theme_name, "tau_ceti")
        self.assertEqual(rebuilt.variant, "alert")

    def test_toggle_render_flips_state_and_emits_update(self) -> None:
        button = ToggleButton(
            button_id="fill",
            label="Fill",
            state_id="gse.fill",
            state=False,
        )

        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=FakeImgui([True])):
            update = button.render()

        self.assertEqual(
            update,
            ButtonStateUpdate(button_id="fill", state_id="gse.fill", state=True),
        )
        self.assertTrue(button.state)

    def test_momentary_render_emits_configured_state(self) -> None:
        button = MomentaryButton(
            button_id="purge",
            label="Purge",
            state_id="gse.purge",
            state="pulse",
        )

        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=FakeImgui([True])):
            update = button.render()

        self.assertEqual(
            update,
            ButtonStateUpdate(button_id="purge", state_id="gse.purge", state="pulse"),
        )

    def test_gate_and_interlocks_disable_emission(self) -> None:
        button = MomentaryButton(
            button_id="vent",
            label="Vent",
            state_id="ecu.vent",
            state=1,
            gate_id="control_gate",
            interlock_ids=("armed",),
        )

        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=FakeImgui([True, True])):
            update_gate_blocked = button.render(
                gate_states={"control_gate": False},
                interlock_states={"armed": True},
            )
            update_interlock_blocked = button.render(
                gate_states={"control_gate": True},
                interlock_states={"armed": False},
            )

        self.assertIsNone(update_gate_blocked)
        self.assertIsNone(update_interlock_blocked)

    def test_tau_ceti_button_renders_custom_draw_list_chrome(self) -> None:
        button = ToggleButton(
            button_id="ignite",
            label="Ignite Primary",
            state_id="ign.main",
            state=False,
            theme_name="tau_ceti",
            variant="primary",
        )

        fake_imgui = FakeImgui([True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake_imgui):
            update = button.render()

        self.assertEqual(
            update,
            ButtonStateUpdate(button_id="ignite", state_id="ign.main", state=True),
        )
        draw_calls = fake_imgui.get_window_draw_list().calls
        fill_calls = [args for name, args in draw_calls if name == "add_rect_filled"]
        text_calls = [args[3] for name, args in draw_calls if name == "add_text"]
        self.assertGreaterEqual(len(fill_calls), 2)
        self.assertIn("IGNITE PRIMARY", text_calls)
        self.assertIn("OFF", text_calls)

    def test_tau_ceti_button_handles_imgui_without_frame_border_size_constant(self) -> None:
        button = ToggleButton(
            button_id="ignite",
            label="Ignite Primary",
            state_id="ign.main",
            state=False,
            theme_name="tau_ceti",
            variant="primary",
        )

        fake_imgui = MissingFrameBorderFakeImgui([True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake_imgui):
            update = button.render()

        self.assertEqual(
            update,
            ButtonStateUpdate(button_id="ignite", state_id="ign.main", state=True),
        )


class SetpointButtonTests(unittest.TestCase):
    def make_button(self, **overrides) -> SetpointButton:
        params = dict(
            button_id="chamber_pressure",
            label="Chamber Pressure",
            state_id="ecu.chamber_pressure",
            state=500.0,
            unit="psi",
            step=50.0,
            min_value=0.0,
            max_value=1000.0,
        )
        params.update(overrides)
        return SetpointButton(**params)

    # --- construction ---

    def test_defaults(self) -> None:
        btn = SetpointButton(
            button_id="sp", label="SP", state_id="sp.val", state=10.0,
        )
        self.assertEqual(btn.unit, "")
        self.assertEqual(btn.step, 1.0)
        self.assertEqual(btn.min_value, 0.0)
        self.assertEqual(btn.max_value, 1000.0)

    def test_initial_state_is_float(self) -> None:
        btn = self.make_button(state=200)
        self.assertIsInstance(btn.state, float)
        self.assertEqual(btn.state, 200.0)

    def test_initial_state_clamped_to_range(self) -> None:
        btn = self.make_button(state=2000.0)
        self.assertEqual(btn.state, 1000.0)
        btn_low = self.make_button(state=-100.0)
        self.assertEqual(btn_low.state, 0.0)

    def test_rejects_bool_state(self) -> None:
        with self.assertRaises(TypeError):
            self.make_button(state=True)

    def test_rejects_string_state(self) -> None:
        with self.assertRaises(TypeError):
            self.make_button(state="500")

    def test_rejects_non_positive_step(self) -> None:
        with self.assertRaises(ValueError):
            self.make_button(step=0.0)
        with self.assertRaises(ValueError):
            self.make_button(step=-1.0)

    def test_rejects_inverted_range(self) -> None:
        with self.assertRaises(ValueError):
            self.make_button(min_value=500.0, max_value=100.0)
        with self.assertRaises(ValueError):
            self.make_button(min_value=100.0, max_value=100.0)

    # --- state text ---

    def test_state_text_with_unit(self) -> None:
        btn = self.make_button(state=500.0, unit="psi")
        self.assertEqual(btn._state_text(), "500 psi")

    def test_state_text_without_unit(self) -> None:
        btn = self.make_button(state=3.14, unit="")
        self.assertIn("3.14", btn._state_text())

    # --- legacy render: decrement ---

    def test_legacy_decrement_emits_update(self) -> None:
        btn = self.make_button(state=500.0, step=50.0)
        fake = FakeImgui(presses=[True, False, False])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 450.0)
        self.assertEqual(btn.state, 450.0)

    def test_legacy_increment_emits_update(self) -> None:
        btn = self.make_button(state=500.0, step=50.0)
        # presses[0] → [-] button, presses[1] → [+] button
        fake = FakeImgui(presses=[False, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 550.0)

    def test_legacy_input_float_emits_update(self) -> None:
        btn = self.make_button(state=500.0)
        fake = FakeImgui(presses=[False, False], input_float_values=[(True, 750.0)])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 750.0)

    def test_legacy_no_change_returns_none(self) -> None:
        btn = self.make_button(state=500.0)
        fake = FakeImgui(presses=[False, False])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNone(update)

    def test_legacy_clamps_decrement_at_min(self) -> None:
        btn = self.make_button(state=20.0, step=50.0, min_value=0.0)
        fake = FakeImgui(presses=[True, False, False])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertEqual(update.state, 0.0)

    def test_legacy_clamps_increment_at_max(self) -> None:
        btn = self.make_button(state=980.0, step=50.0, max_value=1000.0)
        fake = FakeImgui(presses=[False, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 1000.0)

    def test_legacy_clamps_typed_value(self) -> None:
        btn = self.make_button(state=500.0, min_value=0.0, max_value=1000.0)
        fake = FakeImgui(presses=[False, False], input_float_values=[(True, 9999.0)])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertEqual(update.state, 1000.0)

    # --- gate / interlock ---

    def test_gate_blocks_emission(self) -> None:
        btn = self.make_button(gate_id="arm_gate")
        fake = FakeImgui(presses=[True, False, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render(gate_states={"arm_gate": False}, interlock_states={})
        self.assertIsNone(update)

    def test_interlock_blocks_emission(self) -> None:
        btn = self.make_button(interlock_ids=("safe_mode",))
        fake = FakeImgui(presses=[True, False, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render(gate_states={}, interlock_states={"safe_mode": False})
        self.assertIsNone(update)

    # --- tau_ceti render ---

    def test_tau_ceti_decrement_emits_update(self) -> None:
        btn = self.make_button(state=500.0, step=50.0, theme_name="tau_ceti")
        # presses: [-] presses True, input_float unchanged, [+] presses False
        fake = FakeImgui(presses=[True, False])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 450.0)

    def test_tau_ceti_increment_emits_update(self) -> None:
        btn = self.make_button(state=500.0, step=50.0, theme_name="tau_ceti")
        # presses: [-] False, input_float unchanged, [+] True
        fake = FakeImgui(presses=[False, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 550.0)

    def test_tau_ceti_input_float_emits_update(self) -> None:
        btn = self.make_button(state=500.0, theme_name="tau_ceti")
        fake = FakeImgui(presses=[False, False], input_float_values=[(True, 300.0)])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render()
        self.assertIsNotNone(update)
        self.assertEqual(update.state, 300.0)

    def test_tau_ceti_draws_label_and_led(self) -> None:
        btn = self.make_button(theme_name="tau_ceti")
        fake = FakeImgui(presses=[False, False])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            btn.render()
        text_calls = [args[3] for name, args in fake.get_window_draw_list().calls if name == "add_text"]
        self.assertIn("CHAMBER PRESSURE", text_calls)

    def test_tau_ceti_gate_blocked_returns_none(self) -> None:
        btn = self.make_button(gate_id="gate", theme_name="tau_ceti")
        fake = FakeImgui(presses=[True, True])
        with mock.patch("viviian.gui_utils.buttons._require_imgui", return_value=fake):
            update = btn.render(gate_states={"gate": False}, interlock_states={})
        self.assertIsNone(update)

    # --- TOML round-trip ---

    def test_round_trip_preserves_all_fields(self) -> None:
        btn = self.make_button(
            state=250.0,
            unit="psi",
            step=25.0,
            min_value=0.0,
            max_value=500.0,
            gate_id="arm",
            interlock_ids=("safe",),
            theme_name="tau_ceti",
            variant="alert",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = btn.export(f"{tmpdir}/sp.toml")
            rebuilt = reconstruct_button(path)

        self.assertIsInstance(rebuilt, SetpointButton)
        self.assertEqual(rebuilt.state, 250.0)
        self.assertEqual(rebuilt.unit, "psi")
        self.assertEqual(rebuilt.step, 25.0)
        self.assertEqual(rebuilt.min_value, 0.0)
        self.assertEqual(rebuilt.max_value, 500.0)
        self.assertEqual(rebuilt.gate_id, "arm")
        self.assertEqual(rebuilt.interlock_ids, ("safe",))
        self.assertEqual(rebuilt.theme_name, "tau_ceti")
        self.assertEqual(rebuilt.variant, "alert")

    def test_reconstruct_dispatches_to_setpoint_button(self) -> None:
        btn = self.make_button()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = btn.export(f"{tmpdir}/sp.toml")
            rebuilt = StateButton.reconstruct(path)
        self.assertIsInstance(rebuilt, SetpointButton)

    def test_round_trip_without_optional_fields(self) -> None:
        btn = SetpointButton(
            button_id="sp", label="SP", state_id="sp.val",
            state=10.0, min_value=0.0, max_value=100.0,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = btn.export(f"{tmpdir}/sp.toml")
            rebuilt = reconstruct_button(path)
        self.assertIsInstance(rebuilt, SetpointButton)
        self.assertEqual(rebuilt.unit, "")
        self.assertEqual(rebuilt.step, 1.0)


class TauCetiRenderTests(unittest.TestCase):
    def test_tau_ceti_analog_gauge_adds_header_text(self) -> None:
        gauge = AnalogNeedleGauge(
            gauge_id="pressure",
            label="Pressure",
            stream_name="pressure_stream",
            low_value=0.0,
            high_value=100.0,
            theme_name="tau_ceti",
        )
        gauge._has_value = True
        gauge._target_value = 72.0
        gauge._display_value = 72.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        text_calls = [args[3] for name, args in fake_imgui.get_window_draw_list().calls if name == "add_text"]
        self.assertIn("PRESSURE", text_calls)
        self.assertIn("STREAM / pressure_stream", text_calls)

    def test_tau_ceti_radial_gauge_renders_range_labels(self) -> None:
        gauge = AnalogNeedleGauge(
            gauge_id="temperature",
            label="Temperature",
            stream_name="temperature_stream",
            low_value=0.0,
            high_value=100.0,
            theme_name="tau_ceti",
            layout_style="radial",
        )
        gauge._has_value = True
        gauge._target_value = 48.0
        gauge._display_value = 48.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        text_calls = [args[3] for name, args in fake_imgui.get_window_draw_list().calls if name == "add_text"]
        self.assertIn("MIN 0", text_calls)
        self.assertIn("MAX 100", text_calls)

    def test_tau_ceti_led_gauge_renders_secondary_readouts(self) -> None:
        gauge = LedBarGauge(
            gauge_id="level",
            label="Level",
            stream_name="level_stream",
            low_value=0.0,
            high_value=100.0,
            theme_name="tau_ceti",
            unit_label="%",
            secondary_label="CAPACITY",
            secondary_value="12,480 L",
            footer_left="RANGE LOCK",
            footer_right="RESP · 8.0 HZ",
        )
        gauge._has_value = True
        gauge._target_value = 62.0
        gauge._display_value = 62.0
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.gauges._require_imgui", return_value=fake_imgui):
            gauge.render()

        text_calls = [args[3] for name, args in fake_imgui.get_window_draw_list().calls if name == "add_text"]
        self.assertIn("CAPACITY", text_calls)
        self.assertIn("12,480 L", text_calls)
        self.assertIn("RANGE LOCK", text_calls)
        self.assertIn("RESP · 8.0 HZ", text_calls)

    def test_tau_ceti_graph_renders_live_badge(self) -> None:
        graph = SensorGraph(
            "telem",
            title="Telemetry",
            series=(
                GraphSeries(
                    series_id="s",
                    label="Signal",
                    stream_name="signal",
                    color_rgba=(0.9, 0.2, 0.2, 1.0),
                ),
            ),
            theme_name="tau_ceti",
        )
        reader = FakeReader(shape=(2, 4), dtype=np.float64)
        graph.bind({"signal": reader})
        reader.push(np.array([[0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0]], dtype=np.float64))
        graph.consume()
        fake_imgui = FakeImgui(delta_time=0.05)

        with mock.patch("viviian.gui_utils.graphs._require_imgui", return_value=fake_imgui):
            graph.render()

        text_calls = [args[3] for name, args in fake_imgui.get_window_draw_list().calls if name == "add_text"]
        self.assertIn("● LIVE", text_calls)

    def test_tau_ceti_graph_visibility_controls_handle_imgui_without_frame_border_size_constant(self) -> None:
        graph = SensorGraph(
            "telem",
            title="Telemetry",
            series=(
                GraphSeries(
                    series_id="s",
                    label="Signal",
                    stream_name="signal",
                    color_rgba=(0.9, 0.2, 0.2, 1.0),
                ),
            ),
            theme_name="tau_ceti",
        )
        reader = FakeReader(shape=(2, 2), dtype=np.float64)
        graph.bind({"signal": reader})
        reader.push(np.array([[0.0, 1.0], [1.0, 2.0]], dtype=np.float64))
        graph.consume()
        fake_imgui = MissingFrameBorderFakeImgui([False])

        with mock.patch("viviian.gui_utils.graphs._require_imgui", return_value=fake_imgui):
            graph.render()

        self.assertTrue(fake_imgui.get_window_draw_list().calls)


class OperatorWidgetTests(unittest.TestCase):
    def test_micro_button_toggles_on_press(self) -> None:
        button = MicroButton(component_id="micro", label="", icon="●", active=False)

        with mock.patch("viviian.gui_utils.operator._require_imgui", return_value=FakeImgui([True])):
            changed = button.render()

        self.assertTrue(changed)
        self.assertTrue(button.active)

    def test_event_log_filter_toggles_severity(self) -> None:
        panel = EventLogPanel(
            component_id="events",
            records=[EventRecord("00:00", "info", "SRC", "Message")],
        )

        with mock.patch("viviian.gui_utils.operator._require_imgui", return_value=FakeImgui([True, False, False, False])):
            panel.render()

        self.assertNotIn("info", panel.active_filters)

    def test_toolbar_button_handles_imgui_without_frame_border_size_constant(self) -> None:
        button = MicroButton(component_id="micro", label="", icon="●", active=False)

        with mock.patch(
            "viviian.gui_utils.operator._require_imgui",
            return_value=MissingFrameBorderFakeImgui([True]),
        ):
            changed = button.render()

        self.assertTrue(changed)

    def test_procedure_carousel_prev_button_moves_active_index(self) -> None:
        carousel = ProcedureCarousel(
            component_id="proc",
            steps=[
                ProcedureStep("One", "First", "done"),
                ProcedureStep("Two", "Second", "active"),
                ProcedureStep("Three", "Third", "pending"),
            ],
            active_index=1,
        )

        with mock.patch("viviian.gui_utils.operator._require_imgui", return_value=FakeImgui([False, False, False, True, False])):
            carousel.render()

        self.assertEqual(carousel.active_index, 0)

    def test_telemetry_filmstrip_consume_advances_offset(self) -> None:
        strip = TelemetryFilmstrip(
            component_id="film",
            cards=[
                TelemetryCard("A", "1"),
                TelemetryCard("B", "2"),
                TelemetryCard("C", "3"),
                TelemetryCard("D", "4"),
            ],
            cards_per_view=2,
            auto_scroll=True,
            scroll_period_s=0.0,
        )

        changed = strip.consume()

        self.assertTrue(changed)
        self.assertEqual([item.label for item in strip._visible_cards()], ["B", "C"])

    def test_telemetry_ticker_consume_advances_offset(self) -> None:
        ticker = TelemetryTicker(
            component_id="ticker",
            items=["A", "B", "C"],
            visible_items=2,
            auto_scroll=True,
            scroll_period_s=0.0,
        )

        changed = ticker.consume()

        self.assertTrue(changed)
        self.assertEqual(ticker._visible_items(), ["B", "C"])


if __name__ == "__main__":
    unittest.main()
