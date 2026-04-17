from __future__ import annotations

"""
Manual ImGui signal-lab runnable for the current GUI utils stack.

Run from the repo root:

    python tests/gui_runnables/signal_graph_lab.py

Desktop requirements:
    pip install glfw PyOpenGL

The runnable stays intentionally simple. It creates one graph, one one-shot
button that spawns a bank of eight random repeating signals, and eight toggle
buttons that control whether each signal is currently feeding the graph. A
disabled signal freezes in place until newer graph time pushes it out of the
configured graph window.
"""

import argparse
import time
from typing import Any, Sequence

import numpy as np

from viviian.gui_utils.buttons import MomentaryButton, ToggleButton
from viviian.gui_utils.graphs import GraphSeries, SensorGraph
from viviian.simulation_utils import (
    SpectralSignalGenerator,
    random_sparse_spectrum_generator,
)
from tests.gui_runnables._support import BufferedFrameReader, apply_operator_theme

_WINDOW_TITLE = "Signal Graph Lab"
_WINDOW_SIZE = (1280, 900)
_SIGNAL_COUNT = 8
_DEFAULT_BANK_SEED = 20260408
_DEFAULT_SAMPLE_RATE_HZ = 256.0
_DEFAULT_SAMPLES_PER_CYCLE = 2048
_DEFAULT_GRAPH_WINDOW_SECONDS = 12.0
_DEFAULT_MAX_ROWS_PER_TICK = 64
_DEFAULT_GRAPH_POINT_CAP = 4096
_GRID_COLUMNS = 4
_COLOR_PALETTE: tuple[tuple[float, float, float, float], ...] = (
    (0.920, 0.320, 0.290, 1.0),
    (0.960, 0.620, 0.220, 1.0),
    (0.950, 0.840, 0.260, 1.0),
    (0.420, 0.800, 0.340, 1.0),
    (0.160, 0.730, 0.780, 1.0),
    (0.280, 0.540, 0.920, 1.0),
    (0.560, 0.410, 0.920, 1.0),
    (0.890, 0.360, 0.690, 1.0),
)

class SignalGraphLabApp:
    """Controller for the manual operator-desk example.

    The generators always advance on the shared sample clock. When a signal is
    toggled off we discard its current batch rather than pausing the generator,
    which keeps timestamps coherent when the operator re-enables it later.
    """

    def __init__(
        self,
        *,
        bank_seed: int = _DEFAULT_BANK_SEED,
        sample_rate_hz: float = _DEFAULT_SAMPLE_RATE_HZ,
        samples_per_cycle: int = _DEFAULT_SAMPLES_PER_CYCLE,
        graph_window_seconds: float = _DEFAULT_GRAPH_WINDOW_SECONDS,
        max_rows_per_tick: int = _DEFAULT_MAX_ROWS_PER_TICK,
        graph_point_cap: int = _DEFAULT_GRAPH_POINT_CAP,
    ) -> None:
        if sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be greater than 0.")
        if samples_per_cycle <= 0:
            raise ValueError("samples_per_cycle must be greater than 0.")
        if max_rows_per_tick <= 0:
            raise ValueError("max_rows_per_tick must be greater than 0.")

        self.default_bank_seed = int(bank_seed)
        self.sample_rate_hz = float(sample_rate_hz)
        self.samples_per_cycle = int(samples_per_cycle)
        self.graph_window_seconds = float(graph_window_seconds)
        self.max_rows_per_tick = int(max_rows_per_tick)
        self.repeat_interval_seconds = self.samples_per_cycle / self.sample_rate_hz
        self._sample_remainder = 0.0

        self.generate_button = MomentaryButton(
            button_id="generate_bank",
            label="Generate 8 Random Signals",
            state_id="signal_bank.generate",
            state="generate",
            color_rgba=(0.150, 0.360, 0.560, 1.0),
        )
        self.signal_buttons = [
            ToggleButton(
                button_id=f"signal_{index}",
                label=f"signal_{index}",
                state_id=f"signal_{index}.enabled",
                state=False,
                enabled_by_default=False,
                color_rgba=_COLOR_PALETTE[index - 1],
            )
            for index in range(1, _SIGNAL_COUNT + 1)
        ]

        self.readers = {
            f"signal_{index}": BufferedFrameReader(max_rows=self.max_rows_per_tick)
            for index in range(1, _SIGNAL_COUNT + 1)
        }
        self.graph = SensorGraph(
            "signal_lab",
            title="Spectral Sensor Graph",
            series=tuple(
                GraphSeries(
                    series_id=f"signal_{index}",
                    label=f"signal_{index}",
                    stream_name=f"signal_{index}",
                    color_rgba=_COLOR_PALETTE[index - 1],
                    visible_by_default=True,
                )
                for index in range(1, _SIGNAL_COUNT + 1)
            ),
            window_seconds=self.graph_window_seconds,
            max_points_per_series=graph_point_cap,
            backpressure_mode="latest_only",
            show_axes=True,
            show_series_controls=False,
            stable_y=True,
        )
        self.graph.bind(self.readers)

        self.generated_bank = False
        self.bank_seed: int | None = None
        self.generators: list[SpectralSignalGenerator] = []

    def generate_signal_bank(self, seed: int | None = None) -> bool:
        if self.generated_bank:
            return False

        resolved_seed = self.default_bank_seed if seed is None else int(seed)
        spectrum_kwargs = self._bank_spectrum_kwargs()
        self.generators = [
            random_sparse_spectrum_generator(
                signal_id=f"signal_{index}",
                sample_rate_hz=self.sample_rate_hz,
                samples_per_cycle=self.samples_per_cycle,
                seed=resolved_seed + index,
                coefficient_scale=4.0,
                offset=0.0,
                scale=1.0,
                noise_floor_std=0.03,
                **spectrum_kwargs,
            )
            for index in range(1, _SIGNAL_COUNT + 1)
        ]
        self.generated_bank = True
        self.bank_seed = resolved_seed
        self.generate_button.enabled_by_default = False
        self._sample_remainder = 0.0
        for button in self.signal_buttons:
            button.enabled_by_default = True
            button.state = False
        for reader in self.readers.values():
            reader.clear()
        return True

    def set_signal_enabled(self, signal_index: int, enabled: bool) -> None:
        button = self.signal_buttons[signal_index - 1]
        button.state = bool(enabled)

    def advance(self, elapsed_seconds: float) -> bool:
        if elapsed_seconds < 0.0:
            raise ValueError("elapsed_seconds must be non-negative.")
        if not self.generated_bank:
            return False

        # The UI loop runs on wall-clock time, so we convert elapsed seconds
        # into integer sample rows and keep the fractional residue for the next tick.
        self._sample_remainder += elapsed_seconds * self.sample_rate_hz
        rows_due = min(int(self._sample_remainder), self.max_rows_per_tick)
        self._sample_remainder -= rows_due
        if rows_due <= 0:
            return False

        for button, generator in zip(self.signal_buttons, self.generators):
            batch = generator.next_batch(rows_due)
            reader = self.readers[button.button_id]
            if bool(button.state):
                reader.prime(batch)
            else:
                # Off means stop feeding the graph, not stop the simulated clock.
                # Existing samples remain visible until the shared graph clock
                # advances far enough to push them out of the configured window.
                reader.clear()

        return self.graph.consume()

    def _bank_spectrum_kwargs(self) -> dict[str, int | bool]:
        rfft_max_bin = self.samples_per_cycle // 2
        allowed_bins = [
            bin_index
            for bin_index in range(1, min(64, rfft_max_bin) + 1)
            if not (self.samples_per_cycle % 2 == 0 and bin_index == rfft_max_bin)
        ]
        if not allowed_bins:
            return {
                "nonzero_terms": 1,
                "min_bin": 0,
                "max_bin": 0,
                "allow_dc": True,
            }
        return {
            "nonzero_terms": min(6, len(allowed_bins)),
            "min_bin": allowed_bins[0],
            "max_bin": allowed_bins[-1],
        }

    def render(self) -> None:
        imgui = _require_imgui()

        imgui.begin(_WINDOW_TITLE)
        imgui.text_colored("Manual GUI Signal Lab", 0.290, 0.780, 0.960, 1.0)
        imgui.text_disabled(
            "One graph, one one-shot bank generator, and eight toggleable repeating signals."
        )
        seed_text = "pending" if self.bank_seed is None else str(self.bank_seed)
        imgui.text_unformatted(
            (
                f"sample_rate {self.sample_rate_hz:.1f} Hz | "
                f"cycle {self.repeat_interval_seconds:.3f} s | "
                f"seed {seed_text}"
            )
        )
        imgui.text_disabled(
            "Turning a signal off freezes its history until newer graph time pushes it out of the window."
        )
        imgui.spacing()

        generate_update = self.generate_button.render()
        if generate_update is not None:
            self.generate_signal_bank()

        imgui.spacing()
        self._render_signal_grid(imgui)
        imgui.spacing()
        self.graph.render()
        imgui.end()

    def _render_signal_grid(self, imgui: Any) -> None:
        for index, button in enumerate(self.signal_buttons):
            imgui.push_item_width(-1.0)
            button.render()
            imgui.pop_item_width()
            if (index + 1) % _GRID_COLUMNS != 0 and index < len(self.signal_buttons) - 1:
                imgui.same_line()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone ImGui signal graph lab.")
    parser.add_argument(
        "--seed",
        type=int,
        default=_DEFAULT_BANK_SEED,
        help="Base seed for the one-shot 8-signal bank.",
    )
    args = parser.parse_args(argv)
    run(seed=args.seed)
    return 0


def run(*, seed: int = _DEFAULT_BANK_SEED) -> None:
    try:
        import glfw
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "glfw is required for tests/gui_runnables/signal_graph_lab.py. "
            "Install it with 'pip install glfw'."
        ) from exc

    try:
        import imgui
        from imgui.integrations.glfw import GlfwRenderer
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "imgui with the GLFW integration is required for the signal graph lab."
        ) from exc

    try:
        from OpenGL import GL as gl
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyOpenGL is required for tests/gui_runnables/signal_graph_lab.py. "
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
    app = SignalGraphLabApp(bank_seed=seed)
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


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required to render the signal graph lab controls."
        ) from exc
    return imgui


if __name__ == "__main__":
    raise SystemExit(main())
