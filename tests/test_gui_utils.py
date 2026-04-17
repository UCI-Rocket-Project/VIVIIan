from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import numpy as np

from viviian.gui_utils.buttons import (
    ButtonStateUpdate,
    MomentaryButton,
    StateButton,
    ToggleButton,
)
from viviian.gui_utils.gauges import (
    AnalogNeedleGauge,
    LedBarGauge,
    SensorGauge,
    _advance_display_value,
)
from viviian.gui_utils.graphs import GraphSeries, SensorGraph


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


class FakeImgui:
    COLOR_BUTTON = 1
    COLOR_BUTTON_HOVERED = 2
    COLOR_BUTTON_ACTIVE = 3

    def __init__(self, presses: list[bool] | None = None, *, delta_time: float = 1.0 / 60.0):
        self._presses = list(presses or [])
        self._draw_list = FakeDrawList()
        self._io = FakeIO(delta_time)
        self.last_dummy: tuple[float, float] | None = None

    def push_style_color(self, *_args) -> None:
        return None

    def pop_style_color(self, *_args) -> None:
        return None

    def button(self, *_args, **_kwargs) -> bool:
        if self._presses:
            return self._presses.pop(0)
        return False

    def text_disabled(self, *_args) -> None:
        return None

    def text_colored(self, *_args) -> None:
        return None

    def text_unformatted(self, *_args) -> None:
        return None

    def same_line(self, *_args) -> None:
        return None

    def get_content_region_available(self) -> tuple[float, float]:
        return (320.0, 240.0)

    def get_cursor_screen_pos(self) -> tuple[float, float]:
        return (12.0, 18.0)

    def dummy(self, width: float, height: float) -> None:
        self.last_dummy = (float(width), float(height))

    def get_window_draw_list(self) -> FakeDrawList:
        return self._draw_list

    def get_color_u32_rgba(self, *rgba: float) -> int:
        r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
        return (a << 24) | (b << 16) | (g << 8) | r

    def get_io(self) -> FakeIO:
        return self._io

    def calc_text_size(self, text: str) -> tuple[float, float]:
        return (max(6.0, len(text) * 6.0), 10.0)


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

        panel_left = 12.0
        panel_top = 18.0
        panel_right = panel_left + gauge.width
        panel_bottom = panel_top + gauge.height
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


if __name__ == "__main__":
    unittest.main()
