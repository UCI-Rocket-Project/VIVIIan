from __future__ import annotations

"""
Manual ImGui runnable for the OBJ-backed 3D viewer path.

Run from the repo root:

    python tests/gui_runnables/rocket_viewer_lab.py

Desktop requirements:
    pip install glfw PyOpenGL

This example keeps the runtime path honest: the viewer still loads a compiled
mesh cache and consumes live timestamped streams. By default it discovers
exactly one OBJ asset under `gui_assets/cad/` and uses `gui_assets/compiled/`
for the cached triangles.
"""

import argparse
from pathlib import Path
import time
from typing import Any, Sequence

import numpy as np

from viviian.gui_utils import (
    ModelBodyBinding,
    ModelViewerConfig,
    discover_single_obj_asset,
    resolve_compiled_obj_assets,
)
from viviian.simulation_utils import (
    RotationMatrixSignalGenerator,
    SpectralSignalConfig,
    SpectralTerm,
    random_sparse_spectrum_generator,
)
from tests.gui_runnables._support import BufferedFrameReader

_WINDOW_TITLE = "Rocket Viewer Lab"
_WINDOW_SIZE = (1360, 960)
_DEFAULT_SAMPLE_RATE_HZ = 60.0
_DEFAULT_SAMPLES_PER_CYCLE = 2048
_DEFAULT_MAX_ROWS_PER_TICK = 24
_PRIMARY_MESH_PART_NAME = "g_Body1:20"
_SECONDARY_MESH_PART_NAME = "g_Body1:21"


class ModelViewerLabApp:
    """Small operator-desk runnable that drives a rotating OBJ model with live body colors."""

    def __init__(
        self,
        *,
        seed: int = 20260408,
        sample_rate_hz: float = _DEFAULT_SAMPLE_RATE_HZ,
        samples_per_cycle: int = _DEFAULT_SAMPLES_PER_CYCLE,
        max_rows_per_tick: int = _DEFAULT_MAX_ROWS_PER_TICK,
        cad_dir: str | Path | None = None,
        compiled_dir: str | Path | None = None,
    ) -> None:
        self.seed = int(seed)
        self.sample_rate_hz = float(sample_rate_hz)
        self.samples_per_cycle = int(samples_per_cycle)
        self.max_rows_per_tick = int(max_rows_per_tick)
        self._sample_remainder = 0.0
        obj_path = discover_single_obj_asset(cad_dir)
        cache_path, manifest_path = resolve_compiled_obj_assets(
            obj_path=obj_path,
            compiled_dir=compiled_dir,
        )

        self.readers = {
            "attitude": BufferedFrameReader(expected_rows=10, max_rows=self.max_rows_per_tick),
            "oxidizer_level": BufferedFrameReader(expected_rows=2, max_rows=self.max_rows_per_tick),
            "fuel_level": BufferedFrameReader(expected_rows=2, max_rows=self.max_rows_per_tick),
        }

        config = ModelViewerConfig(
            viewer_id="rocket_lab",
            title="Telemetry 3D Model Viewer",
            mesh_cache_path=str(cache_path),
            manifest_path=str(manifest_path),
            pose_stream_name="attitude",
            model_alignment_matrix=(1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0),
            camera_distance=14.0,
            camera_azimuth_deg=30.0,
            camera_elevation_deg=18.0,
            body_bindings=(
                _make_binding(
                    binding_id="body_20",
                    mesh_part_name=_PRIMARY_MESH_PART_NAME,
                    value_stream_name="oxidizer_level",
                    low_color=(0.180, 0.270, 0.420, 1.0),
                    high_color=(0.240, 0.720, 0.980, 1.0),
                    low_value=0.0,
                    high_value=100.0,
                ),
                _make_binding(
                    binding_id="body_21",
                    mesh_part_name=_SECONDARY_MESH_PART_NAME,
                    value_stream_name="fuel_level",
                    low_color=(0.360, 0.190, 0.120, 1.0),
                    high_color=(0.980, 0.460, 0.180, 1.0),
                    low_value=0.0,
                    high_value=100.0,
                ),
            ),
            default_body_color_rgba=(0.180, 0.205, 0.240, 1.0),
            other_body_alpha=0.34,
            show_labels=True,
            show_legend=True,
            show_axes=True,
        )
        self.viewer = config.build_viewer()
        self.viewer.bind(self.readers)

        self.orientation = _build_demo_orientation_generator(sample_rate_hz=self.sample_rate_hz)
        self.scalar_generators = {
            "oxidizer_level": random_sparse_spectrum_generator(
                signal_id="oxidizer_level",
                sample_rate_hz=self.sample_rate_hz,
                samples_per_cycle=self.samples_per_cycle,
                seed=self.seed + 1,
                nonzero_terms=4,
                min_bin=1,
                max_bin=16,
                coefficient_scale=8.0,
                offset=55.0,
                scale=1.0,
                noise_floor_std=0.0,
            ),
            "fuel_level": random_sparse_spectrum_generator(
                signal_id="fuel_level",
                sample_rate_hz=self.sample_rate_hz,
                samples_per_cycle=self.samples_per_cycle,
                seed=self.seed + 2,
                nonzero_terms=4,
                min_bin=1,
                max_bin=12,
                coefficient_scale=7.0,
                offset=45.0,
                scale=1.0,
                noise_floor_std=0.0,
            ),
        }

    def advance(self, elapsed_seconds: float) -> bool:
        if elapsed_seconds < 0.0:
            raise ValueError("elapsed_seconds must be non-negative.")
        self._sample_remainder += elapsed_seconds * self.sample_rate_hz
        rows_due = min(int(self._sample_remainder), self.max_rows_per_tick)
        self._sample_remainder -= rows_due
        if rows_due <= 0:
            return False

        self.readers["attitude"].prime(self.orientation.next_batch(rows_due))
        for stream_name, generator in self.scalar_generators.items():
            self.readers[stream_name].prime(generator.next_batch(rows_due))
        return self.viewer.consume()

    def render(self) -> None:
        imgui = _require_imgui()
        imgui.begin(_WINDOW_TITLE)
        imgui.text_colored("OBJ-Backed Telemetry Model Viewer", 0.320, 0.780, 0.960, 1.0)
        imgui.text_disabled(
            "The runtime loads a compiled mesh cache from gui_assets/compiled/ for the single OBJ file in gui_assets/cad/."
        )
        imgui.text_disabled(
            f"sample_rate {self.sample_rate_hz:.1f} Hz | cycle {self.samples_per_cycle / self.sample_rate_hz:.3f} s | seed {self.seed}"
        )
        imgui.text_disabled("The rocket should visibly rotate on its own; drag inside the viewport to orbit the camera.")
        imgui.spacing()
        self.viewer.render()
        imgui.end()

    def close(self) -> None:
        self.viewer.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone ImGui rocket viewer lab.")
    parser.add_argument("--seed", type=int, default=20260408, help="Base seed for the demo streams.")
    args = parser.parse_args(argv)
    run(seed=args.seed)
    return 0


def run(*, seed: int = 20260408) -> None:
    try:
        import glfw
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "glfw is required for tests/gui_runnables/rocket_viewer_lab.py. Install it with 'pip install glfw'."
        ) from exc

    try:
        import imgui
        from imgui.integrations.glfw import GlfwRenderer
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "imgui with the GLFW integration is required for the rocket viewer lab."
        ) from exc

    try:
        from OpenGL import GL as gl
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyOpenGL is required for tests/gui_runnables/rocket_viewer_lab.py. Install it with 'pip install PyOpenGL'."
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
    imgui.get_io().config_windows_move_from_title_bar_only = True
    _apply_imgui_theme(imgui)
    impl = GlfwRenderer(window)
    app = ModelViewerLabApp(seed=seed)
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

            framebuffer_width, framebuffer_height = glfw.get_framebuffer_size(window)
            gl.glViewport(0, 0, framebuffer_width, framebuffer_height)
            gl.glClearColor(0.018, 0.024, 0.034, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            impl.render(imgui.get_draw_data())
            glfw.swap_buffers(window)
    finally:
        app.close()
        impl.shutdown()
        glfw.terminate()


def _make_binding(
    *,
    binding_id: str,
    mesh_part_name: str,
    value_stream_name: str,
    low_color: tuple[float, float, float, float],
    high_color: tuple[float, float, float, float],
    low_value: float,
    high_value: float,
) -> ModelBodyBinding:
    return ModelBodyBinding(
        binding_id=binding_id,
        mesh_part_name=mesh_part_name,
        value_stream_name=value_stream_name,
        low_value=low_value,
        low_color_rgba=low_color,
        high_value=high_value,
        high_color_rgba=high_color,
        default_color_rgba=low_color,
    )


def _build_demo_orientation_generator(*, sample_rate_hz: float) -> RotationMatrixSignalGenerator:
    samples_per_cycle = int(round(sample_rate_hz * 6.0))
    return RotationMatrixSignalGenerator(
        roll=SpectralSignalConfig(
            signal_id="demo_roll",
            sample_rate_hz=sample_rate_hz,
            samples_per_cycle=samples_per_cycle,
            terms=(
                SpectralTerm(bin_index=1, real=0.0, imag=-120.0),
                SpectralTerm(bin_index=2, real=0.0, imag=-48.0),
            ),
        ).build_generator(),
        pitch=SpectralSignalConfig(
            signal_id="demo_pitch",
            sample_rate_hz=sample_rate_hz,
            samples_per_cycle=samples_per_cycle,
            terms=(
                SpectralTerm(bin_index=1, real=90.0, imag=0.0),
                SpectralTerm(bin_index=3, real=0.0, imag=-36.0),
            ),
        ).build_generator(),
        yaw=SpectralSignalConfig(
            signal_id="demo_yaw",
            sample_rate_hz=sample_rate_hz,
            samples_per_cycle=samples_per_cycle,
            terms=(
                SpectralTerm(bin_index=1, real=0.0, imag=-180.0),
                SpectralTerm(bin_index=2, real=72.0, imag=0.0),
            ),
        ).build_generator(),
    )


def _apply_imgui_theme(imgui: Any) -> None:
    style = imgui.get_style()
    style.window_rounding = 10.0
    style.child_rounding = 8.0
    style.frame_rounding = 6.0
    style.grab_rounding = 6.0
    style.window_border_size = 1.0
    style.frame_border_size = 1.0
    style.window_padding = (16.0, 14.0)
    colors = style.colors
    colors[imgui.COLOR_WINDOW_BACKGROUND] = (0.022, 0.028, 0.038, 1.0)
    colors[imgui.COLOR_CHILD_BACKGROUND] = (0.038, 0.046, 0.058, 1.0)
    colors[imgui.COLOR_TITLE_BACKGROUND] = (0.045, 0.060, 0.095, 1.0)
    colors[imgui.COLOR_TITLE_BACKGROUND_ACTIVE] = (0.070, 0.090, 0.145, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND] = (0.082, 0.100, 0.145, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND_HOVERED] = (0.125, 0.158, 0.220, 1.0)
    colors[imgui.COLOR_FRAME_BACKGROUND_ACTIVE] = (0.155, 0.195, 0.280, 1.0)
    colors[imgui.COLOR_BUTTON] = (0.110, 0.205, 0.315, 1.0)
    colors[imgui.COLOR_BUTTON_HOVERED] = (0.150, 0.285, 0.420, 1.0)
    colors[imgui.COLOR_BUTTON_ACTIVE] = (0.195, 0.355, 0.520, 1.0)
    colors[imgui.COLOR_BORDER] = (0.180, 0.235, 0.330, 1.0)


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError("imgui is required to render the rocket viewer lab.") from exc
    return imgui


RocketViewerLabApp = ModelViewerLabApp


if __name__ == "__main__":
    raise SystemExit(main())
