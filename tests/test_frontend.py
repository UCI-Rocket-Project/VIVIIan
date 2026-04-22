from __future__ import annotations

import time
import unittest

import numpy as np

from viviian.frontend import Frontend, HeadlessBackend
from viviian.gui_utils import AnalogNeedleGauge, GraphSeries, MomentaryButton, SensorGraph, ToggleButton
from pythusa import Pipeline
from tests.gui_runnables.tau_ceti_showcase import (
    build_showcase_dashboard,
    build_showcase_frontend,
    build_showcase_pipeline,
)


_PIPELINE_SIGNAL_FRAME = np.array(
    [[0.0, 1.0, 2.0, 3.0], [10.0, 11.0, 12.0, 13.0]],
    dtype=np.float64,
)
_PIPELINE_EXPECTED_STATE = np.array([1.0], dtype=np.float64)


class FakeReader:
    def __init__(
        self,
        *,
        shape: tuple[int, ...],
        dtype: np.dtype,
        frames: list[np.ndarray] | None = None,
    ) -> None:
        self.shape = tuple(shape)
        self.dtype = np.dtype(dtype)
        self._frames = [
            np.asarray(frame, dtype=self.dtype).reshape(self.shape).copy()
            for frame in (frames or [])
        ]
        self.blocking_calls: list[bool] = []

    def set_blocking(self, blocking: bool) -> None:
        self.blocking_calls.append(bool(blocking))

    def read(self) -> np.ndarray | None:
        if not self._frames:
            return None
        return self._frames.pop(0).copy()


class RecordingWriter:
    def __init__(
        self,
        *,
        shape: tuple[int, ...],
        results: list[bool] | None = None,
    ) -> None:
        self.shape = tuple(shape)
        self.dtype = np.dtype(np.float64)
        self.write_calls: list[np.ndarray] = []
        self.writes: list[np.ndarray] = []
        self._results = list(results or [])

    def write(self, array: np.ndarray) -> bool:
        frame = np.asarray(array, dtype=self.dtype)
        if tuple(frame.shape) != self.shape:
            raise ValueError(f"Expected frame shape {self.shape}, got {tuple(frame.shape)}.")

        captured = frame.copy()
        self.write_calls.append(captured)
        result = self._results.pop(0) if self._results else True
        if result:
            self.writes.append(captured)
        return bool(result)


def _pipeline_frontend_source(signal) -> None:
    signal.write(_PIPELINE_SIGNAL_FRAME)


def _pipeline_frontend_sink(output, done) -> None:
    while True:
        frame = output.read()
        if frame is None:
            time.sleep(0.001)
            continue
        if frame.shape == _PIPELINE_EXPECTED_STATE.shape and np.all(np.isfinite(frame)):
            done.signal()
            return


class FrontendCompileTests(unittest.TestCase):
    def make_graph(self, *, stream_name: str = "signal_stream") -> SensorGraph:
        return SensorGraph(
            "signal_graph",
            title="Signal Graph",
            series=(
                GraphSeries(
                    series_id="signal",
                    label="Signal",
                    stream_name=stream_name,
                    color_rgba=(0.9, 0.2, 0.2, 1.0),
                ),
            ),
        )

    def test_compile_collects_required_reads_and_output_slots(self) -> None:
        frontend = Frontend("desk")
        frontend.add(self.make_graph(stream_name="signal_stream"))
        frontend.add(
            AnalogNeedleGauge(
                gauge_id="pressure_gauge",
                label="Pressure",
                stream_name="pressure_stream",
                low_value=0.0,
                high_value=100.0,
            )
        )
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        frontend.add(
            MomentaryButton(
                button_id="pulse_button",
                label="Pulse",
                state_id="desk.pulse",
                state=2.5,
            )
        )

        frontend.compile()

        self.assertEqual(frontend.required_reads, ("signal_stream", "pressure_stream"))
        self.assertEqual(frontend.output_shape, (2,))
        self.assertEqual(
            tuple(slot.component_id for slot in frontend.output_slots),
            ("arm_toggle", "pulse_button"),
        )
        self.assertEqual(
            tuple(slot.initial_value for slot in frontend.output_slots),
            (0.0, 0.0),
        )

    def test_compile_rejects_duplicate_component_ids(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            ToggleButton(
                button_id="shared_id",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        frontend.add(
            ToggleButton(
                button_id="shared_id",
                label="Hold",
                state_id="desk.hold",
                state=False,
            )
        )

        with self.assertRaisesRegex(ValueError, "unique"):
            frontend.compile()

    def test_compile_rejects_string_button_state_for_float64_output(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            MomentaryButton(
                button_id="bad_pulse",
                label="Pulse",
                state_id="desk.pulse",
                state="pulse",
            )
        )

        with self.assertRaisesRegex(TypeError, "bool, int, or float"):
            frontend.compile()

    def test_add_rejects_mutation_after_compile(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        frontend.compile()

        with self.assertRaisesRegex(RuntimeError, "compiled"):
            frontend.add(
                ToggleButton(
                    button_id="hold_toggle",
                    label="Hold",
                    state_id="desk.hold",
                    state=False,
                )
            )

    def test_output_ring_size_scales_with_output_slots(self) -> None:
        frontend = Frontend("sz")
        frontend.add(ToggleButton(button_id="b", label="B", state_id="s", state=False))
        frontend.add(MomentaryButton(button_id="m", label="M", state_id="s2", state=1.0))
        # 2 slots × 8 bytes = 16 bytes/frame; max(4096, 16 × 9 + 4096) = 4240
        expected = max(4096, 2 * 8 * (8 + 1) + 4096)
        self.assertEqual(frontend.output_ring_size(), expected)

    def test_output_ring_size_no_output_slots_returns_minimum(self) -> None:
        frontend = Frontend("ro")
        frontend.add(
            AnalogNeedleGauge(
                gauge_id="g",
                label="G",
                stream_name="x",
                low_value=0.0,
                high_value=1.0,
            )
        )
        self.assertEqual(frontend.output_ring_size(), 4096)


class FrontendRuntimeTests(unittest.TestCase):
    def make_graph(self) -> SensorGraph:
        return SensorGraph(
            "signal_graph",
            title="Signal Graph",
            series=(
                GraphSeries(
                    series_id="signal",
                    label="Signal",
                    stream_name="signal_stream",
                    color_rgba=(0.9, 0.2, 0.2, 1.0),
                ),
            ),
            stable_y=False,
        )

    def make_gauge(self) -> AnalogNeedleGauge:
        return AnalogNeedleGauge(
            gauge_id="pressure_gauge",
            label="Pressure",
            stream_name="pressure_stream",
            low_value=0.0,
            high_value=100.0,
        )

    def test_readonly_widgets_bind_consume_and_render(self) -> None:
        graph = self.make_graph()
        gauge = self.make_gauge()
        frontend = Frontend("desk")
        frontend.add(graph)
        frontend.add(gauge)
        task = frontend.build_task(backend=HeadlessBackend(max_frames=1))

        graph_reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[
                np.array(
                    [[0.0, 1.0, 2.0, 3.0], [10.0, 11.0, 12.0, 13.0]],
                    dtype=np.float64,
                )
            ],
        )
        gauge_reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[
                np.array(
                    [[0.0, 1.0, 2.0, 3.0], [40.0, 45.0, 50.0, 55.0]],
                    dtype=np.float64,
                )
            ],
        )

        task(signal_stream=graph_reader, pressure_stream=gauge_reader)

        np.testing.assert_allclose(
            graph.series_snapshot("signal"),
            np.array(
                [[0.0, 1.0, 2.0, 3.0], [10.0, 11.0, 12.0, 13.0]],
                dtype=np.float64,
            ),
        )
        self.assertEqual(gauge.target_value, 55.0)
        self.assertEqual(graph_reader.blocking_calls, [False])
        self.assertEqual(gauge_reader.blocking_calls, [False])

    def test_tau_ceti_gauge_display_moves_across_frames(self) -> None:
        gauge = AnalogNeedleGauge(
            gauge_id="pressure_gauge",
            label="Pressure",
            stream_name="pressure_stream",
            low_value=0.0,
            high_value=100.0,
            theme_name="tau_ceti",
            animation_response_hz=4.0,
        )
        frontend = Frontend("desk")
        frontend.add(gauge)
        task = frontend.build_task(
            backend=HeadlessBackend(max_frames=3, delta_time=0.1, theme_name="tau_ceti"),
        )
        gauge_reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[
                np.array(
                    [[0.0, 1.0, 2.0, 3.0], [10.0, 30.0, 60.0, 80.0]],
                    dtype=np.float64,
                )
            ],
        )

        task(pressure_stream=gauge_reader)

        self.assertEqual(gauge.target_value, 80.0)
        self.assertGreater(gauge.display_value, 0.0)
        self.assertLess(gauge.display_value, 80.0)
        self.assertEqual(gauge.formatted_rate(), "Δ +20.00 / SEC")

    def test_frontend_fans_out_shared_stream_reader_to_multiple_widgets(self) -> None:
        graph = SensorGraph(
            "signal_graph",
            title="Signal Graph",
            series=(
                GraphSeries(
                    series_id="signal",
                    label="Signal",
                    stream_name="signal_stream",
                    color_rgba=(0.9, 0.2, 0.2, 1.0),
                ),
            ),
            stable_y=False,
        )
        gauge = AnalogNeedleGauge(
            gauge_id="signal_gauge",
            label="Signal",
            stream_name="signal_stream",
            low_value=0.0,
            high_value=100.0,
            theme_name="tau_ceti",
        )
        frontend = Frontend("desk")
        frontend.add(graph)
        frontend.add(gauge)
        task = frontend.build_task(backend=HeadlessBackend(max_frames=1, theme_name="tau_ceti"))
        reader = FakeReader(
            shape=(2, 4),
            dtype=np.float64,
            frames=[
                np.array(
                    [[0.0, 1.0, 2.0, 3.0], [10.0, 25.0, 50.0, 75.0]],
                    dtype=np.float64,
                )
            ],
        )

        task(signal_stream=reader)

        np.testing.assert_allclose(
            graph.series_snapshot("signal"),
            np.array(
                [[0.0, 1.0, 2.0, 3.0], [10.0, 25.0, 50.0, 75.0]],
                dtype=np.float64,
            ),
        )
        self.assertEqual(gauge.target_value, 75.0)

    def test_initial_snapshot_emits_current_control_state(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        task = frontend.build_task(backend=HeadlessBackend(max_frames=1))
        writer = RecordingWriter(shape=frontend.output_shape)

        task(output=writer)

        self.assertEqual(len(writer.writes), 1)
        np.testing.assert_allclose(writer.writes[0], np.array([0.0], dtype=np.float64))

    def test_toggle_latches_and_emits_new_snapshot(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        task = frontend.build_task(
            backend=HeadlessBackend(max_frames=1, button_presses=(True,)),
        )
        writer = RecordingWriter(shape=frontend.output_shape)

        task(output=writer)

        self.assertEqual(len(writer.writes), 2)
        np.testing.assert_allclose(writer.writes[0], np.array([0.0], dtype=np.float64))
        np.testing.assert_allclose(writer.writes[1], np.array([1.0], dtype=np.float64))

    def test_momentary_button_emits_pulse_then_reset(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            MomentaryButton(
                button_id="pulse_button",
                label="Pulse",
                state_id="desk.pulse",
                state=3.5,
            )
        )
        task = frontend.build_task(
            backend=HeadlessBackend(max_frames=2, button_presses=(True, False)),
        )
        writer = RecordingWriter(shape=frontend.output_shape)

        task(output=writer)

        self.assertEqual(len(writer.writes), 3)
        np.testing.assert_allclose(writer.writes[0], np.array([0.0], dtype=np.float64))
        np.testing.assert_allclose(writer.writes[1], np.array([3.5], dtype=np.float64))
        np.testing.assert_allclose(writer.writes[2], np.array([0.0], dtype=np.float64))

    def test_writer_retries_latest_snapshot_without_blocking(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        task = frontend.build_task(
            backend=HeadlessBackend(max_frames=2, button_presses=(True, False)),
        )
        writer = RecordingWriter(
            shape=frontend.output_shape,
            results=[False, False, True],
        )

        task(output=writer)

        self.assertEqual(len(writer.writes), 1)
        np.testing.assert_allclose(writer.write_calls[0], np.array([0.0], dtype=np.float64))
        np.testing.assert_allclose(writer.write_calls[1], np.array([1.0], dtype=np.float64))
        np.testing.assert_allclose(writer.write_calls[2], np.array([1.0], dtype=np.float64))
        np.testing.assert_allclose(writer.writes[0], np.array([1.0], dtype=np.float64))


class FrontendPipelineIntegrationTests(unittest.TestCase):
    def test_tau_ceti_showcase_frontend_compiles_as_generic_component(self) -> None:
        frontend = build_showcase_frontend()

        frontend.compile()

        self.assertEqual(
            frontend.required_reads,
            (
                "px_chamber",
                "t_engine",
                "lox_level",
                "v_axial",
                "i_bus28",
                "t_bearing",
                "lox_flow",
                "p_feedline",
                "t_nozzle",
                "n2_pressure",
            ),
        )
        self.assertEqual(frontend.output_shape, (0,))
        self.assertEqual(len(frontend.output_slots), 0)

    def test_tau_ceti_showcase_dashboard_consumes_live_batches(self) -> None:
        dashboard = build_showcase_dashboard()
        readers = {
            "px_chamber": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [100.0, 150.0, 200.0, 250.0]])],
            ),
            "t_engine": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [600.0, 650.0, 700.0, 750.0]])],
            ),
            "lox_level": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [90.0, 88.0, 86.0, 84.0]])],
            ),
            "v_axial": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [0.2, 0.3, 0.4, 0.5]])],
            ),
            "i_bus28": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [10.0, 12.0, 14.0, 16.0]])],
            ),
            "t_bearing": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [70.0, 72.0, 74.0, 76.0]])],
            ),
            "lox_flow": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [45.0, 46.0, 47.0, 48.0]])],
            ),
            "p_feedline": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [320.0, 330.0, 340.0, 350.0]])],
            ),
            "t_nozzle": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [410.0, 420.0, 430.0, 440.0]])],
            ),
            "n2_pressure": FakeReader(
                shape=(2, 4),
                dtype=np.float64,
                frames=[np.array([[0.0, 1.0, 2.0, 3.0], [230.0, 232.0, 234.0, 236.0]])],
            ),
        }
        dashboard.bind(readers)

        changed = dashboard.consume()

        self.assertTrue(changed)
        self.assertEqual(dashboard.pressure_gauge.target_value, 250.0)
        self.assertEqual(dashboard.temperature_gauge.target_value, 750.0)
        self.assertEqual(dashboard.level_gauge.target_value, 84.0)
        self.assertIn("PX_CHAMBER", dashboard.ticker.items[0])
        self.assertGreaterEqual(len(dashboard.event_log.records), 1)

    def test_tau_ceti_showcase_pipeline_declares_expected_tasks_and_streams(self) -> None:
        pipe = build_showcase_pipeline()

        self.assertEqual(set(pipe._tasks), {"source", "frontend"})
        self.assertEqual(
            set(pipe._streams),
            {
                "px_chamber",
                "t_engine",
                "lox_level",
                "v_axial",
                "i_bus28",
                "t_bearing",
                "lox_flow",
                "p_feedline",
                "t_nozzle",
                "n2_pressure",
            },
        )

    def test_frontend_task_runs_inside_pipeline(self) -> None:
        frontend = Frontend("desk")
        frontend.add(
            SensorGraph(
                "signal_graph",
                title="Signal Graph",
                series=(
                    GraphSeries(
                        series_id="signal",
                        label="Signal",
                        stream_name="signal",
                        color_rgba=(0.9, 0.2, 0.2, 1.0),
                    ),
                ),
                stable_y=False,
            )
        )
        frontend.add(
            ToggleButton(
                button_id="arm_toggle",
                label="Arm",
                state_id="desk.arm",
                state=False,
            )
        )
        task = frontend.build_task(
            backend=HeadlessBackend(
                max_frames=50,
                button_presses=(True, False, False),
                frame_sleep_s=0.02,
            )
        )

        pipe = Pipeline("frontend-runtime")
        try:
            pipe.add_stream("signal", shape=(2, 4), dtype=np.float64, cache_align=False)
            pipe.add_stream("ui_state", shape=frontend.output_shape, dtype=np.float64, cache_align=False)
            pipe.add_event("done")
            pipe.add_task("source", fn=_pipeline_frontend_source, writes={"signal": "signal"})
            pipe.add_task(
                "frontend",
                fn=task,
                reads=frontend.read_bindings(),
                writes={"output": "ui_state"},
            )
            pipe.add_task(
                "sink",
                fn=_pipeline_frontend_sink,
                reads={"output": "ui_state"},
                events={"done": "done"},
            )

            pipe.start()

            self.assertTrue(pipe._manager._events["done"].wait(timeout=5.0))
        finally:
            pipe.close()


if __name__ == "__main__":
    unittest.main()
