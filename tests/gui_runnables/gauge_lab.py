from __future__ import annotations

"""
Manual ImGui runnable for the new gauge widgets.

Run from the repo root:

    python tests/gui_runnables/gauge_lab.py

Desktop requirements:
    pip install glfw PyOpenGL

The runnable shows one deterministic scalar signal in three views:

- an analog needle gauge
- a 10-segment LED bar gauge
- a line graph of the same source signal plus the low/high range guides
"""

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Sequence

import numpy as np

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
        _path_str = str(_path)
        if _path_str not in sys.path:
            sys.path.insert(0, _path_str)

from viviian.gui_utils.gauges import AnalogNeedleGauge, LedBarGauge
from viviian.gui_utils.graphs import GraphSeries, SensorGraph
from tests.gui_runnables._support import BufferedFrameReader, apply_operator_theme

_WINDOW_TITLE = "Gauge Lab"
_WINDOW_SIZE = (1360, 920)
_DEFAULT_SAMPLE_RATE_HZ = 120.0
_DEFAULT_CYCLE_SECONDS = 6.0
_DEFAULT_GRAPH_WINDOW_SECONDS = 10.0
_DEFAULT_MAX_ROWS_PER_TICK = 48
_DEFAULT_GRAPH_POINT_CAP = 4096
_DEFAULT_LOW_VALUE = 0.0
_DEFAULT_HIGH_VALUE = 100.0
_DEFAULT_OVERSHOOT_RATIO = 0.15
_SIGNAL_COLOR = (0.160, 0.730, 0.780, 1.0)
_LOW_GUIDE_COLOR = (0.250, 0.780, 0.330, 1.0)
_HIGH_GUIDE_COLOR = (0.920, 0.320, 0.260, 1.0)


class GaugeSignalGenerator:
    """Deterministic overshooting sine source for gauge demos."""

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        low_value: float,
        high_value: float,
        cycle_seconds: float,
        overshoot_ratio: float = _DEFAULT_OVERSHOOT_RATIO,
    ) -> None:
        if sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be greater than 0.")
        if high_value <= low_value:
            raise ValueError("high_value must be greater than low_value.")
        if cycle_seconds <= 0.0:
            raise ValueError("cycle_seconds must be greater than 0.")
        if overshoot_ratio < 0.0:
            raise ValueError("overshoot_ratio must be greater than or equal to 0.")

        self.sample_rate_hz = float(sample_rate_hz)
        self.low_value = float(low_value)
        self.high_value = float(high_value)
        self.cycle_seconds = float(cycle_seconds)
        self.overshoot_ratio = float(overshoot_ratio)
        self.sample_index = 0

        span = self.high_value - self.low_value
        self._midpoint = self.low_value + (0.5 * span)
        self._amplitude = (0.5 + self.overshoot_ratio) * span

    def next_batch(self, rows: int) -> np.ndarray:
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise ValueError("rows must be a positive integer.")

        sample_positions = np.arange(rows, dtype=np.int64) + self.sample_index
        timestamps = sample_positions.astype(np.float64) / self.sample_rate_hz
        phase = (2.0 * np.pi * timestamps) / self.cycle_seconds
        values = self._midpoint + (self._amplitude * np.sin(phase))
        self.sample_index += rows
        return np.vstack((timestamps, np.asarray(values, dtype=np.float64)))

    def reset(self) -> None:
        self.sample_index = 0


class GaugeLabApp:
    def __init__(
        self,
        *,
        sample_rate_hz: float = _DEFAULT_SAMPLE_RATE_HZ,
        cycle_seconds: float = _DEFAULT_CYCLE_SECONDS,
        graph_window_seconds: float = _DEFAULT_GRAPH_WINDOW_SECONDS,
        max_rows_per_tick: int = _DEFAULT_MAX_ROWS_PER_TICK,
        graph_point_cap: int = _DEFAULT_GRAPH_POINT_CAP,
        low_value: float = _DEFAULT_LOW_VALUE,
        high_value: float = _DEFAULT_HIGH_VALUE,
        overshoot_ratio: float = _DEFAULT_OVERSHOOT_RATIO,
    ) -> None:
        if sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be greater than 0.")
        if cycle_seconds <= 0.0:
            raise ValueError("cycle_seconds must be greater than 0.")
        if graph_window_seconds <= 0.0:
            raise ValueError("graph_window_seconds must be greater than 0.")
        if max_rows_per_tick <= 0:
            raise ValueError("max_rows_per_tick must be greater than 0.")
        if graph_point_cap <= 0:
            raise ValueError("graph_point_cap must be greater than 0.")

        self.sample_rate_hz = float(sample_rate_hz)
        self.cycle_seconds = float(cycle_seconds)
        self.graph_window_seconds = float(graph_window_seconds)
        self.max_rows_per_tick = int(max_rows_per_tick)
        self.graph_point_cap = int(graph_point_cap)
        self.low_value = float(low_value)
        self.high_value = float(high_value)
        self.overshoot_ratio = float(overshoot_ratio)
        self.running = True
        self._sample_remainder = 0.0
        self.latest_value: float | None = None

        self.signal = GaugeSignalGenerator(
            sample_rate_hz=self.sample_rate_hz,
            low_value=self.low_value,
            high_value=self.high_value,
            cycle_seconds=self.cycle_seconds,
            overshoot_ratio=self.overshoot_ratio,
        )

        self.readers = {
            "analog_signal": BufferedFrameReader(max_rows=self.max_rows_per_tick),
            "led_signal": BufferedFrameReader(max_rows=self.max_rows_per_tick),
            "graph_signal": BufferedFrameReader(max_rows=self.max_rows_per_tick),
            "graph_low": BufferedFrameReader(max_rows=self.max_rows_per_tick),
            "graph_high": BufferedFrameReader(max_rows=self.max_rows_per_tick),
            "pinned_high": BufferedFrameReader(max_rows=1),
        }
        self.readers["pinned_high"].prime(np.array([[0.0], [self.high_value * 0.92]]))

        self.analog_gauge = AnalogNeedleGauge(
            "analog_signal",
            label="Analog Needle Gauge",
            stream_name="analog_signal",
            low_value=self.low_value,
            high_value=self.high_value,
            width=360.0,
            height=190.0,
            animation_response_hz=9.0,
        )
        self.pinned_gauge = AnalogNeedleGauge(
            "pinned_high",
            label="Pinned High (92%)",
            stream_name="pinned_high",
            low_value=self.low_value,
            high_value=self.high_value,
            width=360.0,
            height=190.0,
            animation_response_hz=9.0,
        )
        self.led_gauge = LedBarGauge(
            "led_signal",
            label="LED Bar Gauge",
            stream_name="led_signal",
            low_value=self.low_value,
            high_value=self.high_value,
            width=360.0,
            height=72.0,
            segment_count=10,
            animation_response_hz=12.0,
        )
        self.graph = SensorGraph(
            "gauge_lab_signal",
            title="Source Signal Graph",
            series=(
                GraphSeries(
                    series_id="signal",
                    label="signal",
                    stream_name="graph_signal",
                    color_rgba=_SIGNAL_COLOR,
                ),
                GraphSeries(
                    series_id="low_guide",
                    label="low",
                    stream_name="graph_low",
                    color_rgba=_LOW_GUIDE_COLOR,
                    overlay=True,
                ),
                GraphSeries(
                    series_id="high_guide",
                    label="high",
                    stream_name="graph_high",
                    color_rgba=_HIGH_GUIDE_COLOR,
                    overlay=True,
                ),
            ),
            window_seconds=self.graph_window_seconds,
            max_points_per_series=self.graph_point_cap,
            backpressure_mode="latest_only",
            show_axes=True,
            show_series_controls=False,
            stable_y=True,
        )

        self.analog_gauge.bind(self.readers)
        self.pinned_gauge.bind(self.readers)
        self.led_gauge.bind(self.readers)
        self.graph.bind(self.readers)

    def reset(self) -> None:
        self.signal.reset()
        self._sample_remainder = 0.0
        self.latest_value = None
        for reader in self.readers.values():
            reader.clear()
        self.analog_gauge.reset_history()
        self.led_gauge.reset_history()
        self.graph.reset_history()

    def advance(self, elapsed_seconds: float) -> bool:
        if elapsed_seconds < 0.0:
            raise ValueError("elapsed_seconds must be non-negative.")
        if not self.running:
            return False

        self._sample_remainder += elapsed_seconds * self.sample_rate_hz
        rows_due = min(int(self._sample_remainder), self.max_rows_per_tick)
        self._sample_remainder -= rows_due
        if rows_due <= 0:
            return False

        batch = self.signal.next_batch(rows_due)
        self.latest_value = float(batch[1, -1])
        low_batch = _constant_series_batch(batch[0], self.low_value)
        high_batch = _constant_series_batch(batch[0], self.high_value)

        self.readers["analog_signal"].prime(batch)
        self.readers["led_signal"].prime(batch)
        self.readers["graph_signal"].prime(batch)
        self.readers["graph_low"].prime(low_batch)
        self.readers["graph_high"].prime(high_batch)

        analog_updated = self.analog_gauge.consume()
        self.pinned_gauge.consume()
        led_updated = self.led_gauge.consume()
        graph_updated = self.graph.consume()
        return analog_updated or led_updated or graph_updated

    def render(self) -> None:
        imgui = _require_imgui()

        imgui.begin(_WINDOW_TITLE)
        imgui.text_colored("Gauge Lab", 0.290, 0.780, 0.960, 1.0)
        imgui.text_disabled(
            "One deterministic scalar source feeds the analog gauge, the LED bar, and the line graph."
        )
        latest_text = "--" if self.latest_value is None else f"{self.latest_value:.3f}"
        imgui.text_unformatted(
            (
                f"sample_rate {self.sample_rate_hz:.1f} Hz | "
                f"cycle {self.cycle_seconds:.2f} s | "
                f"range [{self.low_value:.1f}, {self.high_value:.1f}] | "
                f"latest {latest_text}"
            )
        )
        imgui.text_disabled(
            "The signal intentionally overshoots the configured range so the gauges visibly clamp while the graph still shows the raw waveform."
        )
        imgui.spacing()

        if imgui.button("Pause" if self.running else "Resume", width=120.0, height=32.0):
            self.running = not self.running
        imgui.same_line()
        if imgui.button("Reset Waveform", width=150.0, height=32.0):
            self.reset()

        imgui.spacing()
        self.analog_gauge.render()
        imgui.same_line()
        self.pinned_gauge.render()
        imgui.same_line()
        self.led_gauge.render()
        imgui.spacing()
        self.graph.render()
        imgui.end()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone ImGui gauge lab.")
    args = parser.parse_args(argv)
    run()
    return 0


def run() -> None:
    try:
        import glfw
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "glfw is required for tests/gui_runnables/gauge_lab.py. "
            "Install it with 'pip install glfw'."
        ) from exc

    try:
        import imgui
        from imgui.integrations.glfw import GlfwRenderer
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "imgui with the GLFW integration is required for the gauge lab."
        ) from exc

    try:
        from OpenGL import GL as gl
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyOpenGL is required for tests/gui_runnables/gauge_lab.py. "
            "Install it with 'pip install PyOpenGL'."
        ) from exc

    if not glfw.init():
        raise SystemExit("failed to initialize glfw")

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 2)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)

    window = glfw.create_window(_WINDOW_SIZE[0], _WINDOW_SIZE[1], _WINDOW_TITLE, None, None)
    if not window:
        glfw.terminate()
        raise SystemExit("failed to create window")

    glfw.make_context_current(window)
    glfw.swap_interval(1)
    imgui.create_context()
    apply_operator_theme(imgui)
    impl = GlfwRenderer(window)
    app = GaugeLabApp()
    last_time = time.perf_counter()

    try:
        while not glfw.window_should_close(window):
            glfw.poll_events()
            impl.process_inputs()
            now = time.perf_counter()
            elapsed = now - last_time
            last_time = now
            app.advance(elapsed)

            imgui.new_frame()
            app.render()
            imgui.render()

            gl.glClearColor(0.020, 0.030, 0.050, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            impl.render(imgui.get_draw_data())
            glfw.swap_buffers(window)
    finally:
        impl.shutdown()
        glfw.terminate()


def _constant_series_batch(timestamps: np.ndarray, value: float) -> np.ndarray:
    return np.vstack(
        (
            np.asarray(timestamps, dtype=np.float64).copy(),
            np.full(np.asarray(timestamps).shape, float(value), dtype=np.float64),
        )
    )


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError("imgui is required to render the gauge lab.") from exc
    return imgui


if __name__ == "__main__":
    raise SystemExit(main())
