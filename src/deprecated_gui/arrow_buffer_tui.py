from array import array
from collections import deque
import math
import random
import threading
import time

import glfw
import imgui
from imgui.integrations.glfw import GlfwRenderer
from OpenGL import GL


WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 780
PLOT_WIDTH = 1080
PLOT_HEIGHT = 240
PLOT_POINTS = 1200


def signal_worker(buffer: deque, lock: threading.Lock, stop_event: threading.Event) -> None:
    phase = 0.0
    while not stop_event.is_set():
        phase += 0.03
        y = 0.7 * math.sin(phase * 2.2) + 0.25 * math.sin(phase * 0.4 + 0.7)
        y += random.uniform(-0.05, 0.05)
        with lock:
            buffer.append(y)
        time.sleep(0.001)


def main() -> None:
    if not glfw.init():
        raise RuntimeError("Failed to initialize GLFW")
    window = glfw.create_window(WINDOW_WIDTH, WINDOW_HEIGHT, "Sinusoid TUI", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("Failed to create GLFW window")
    glfw.make_context_current(window)
    glfw.swap_interval(1)

    imgui.create_context()
    impl = GlfwRenderer(window)
    imgui.style_colors_classic()

    signal = deque([0.0] * PLOT_POINTS, maxlen=PLOT_POINTS)
    signal_lock = threading.Lock()
    stop_event = threading.Event()
    worker = threading.Thread(target=signal_worker, args=(signal, signal_lock, stop_event), daemon=True)
    worker.start()

    try:
        while not glfw.window_should_close(window):
            glfw.poll_events()
            impl.process_inputs()
            imgui.new_frame()

            with signal_lock:
                values = list(signal)
            vmin = min(values)
            vmax = max(values)
            pad = max(1e-6, (vmax - vmin) * 0.1)

            imgui.set_next_window_position(20, 20, condition=imgui.ONCE)
            imgui.set_next_window_size(1140, 320, condition=imgui.ONCE)
            imgui.begin("Signal")
            imgui.text(f"Latest: {values[-1]:.6f}")
            imgui.push_style_color(imgui.COLOR_FRAME_BACKGROUND, 0.01, 0.05, 0.01, 1.0)
            imgui.push_style_color(imgui.COLOR_PLOT_LINES, 0.25, 1.00, 0.25, 1.0)
            imgui.push_style_color(imgui.COLOR_PLOT_LINES_HOVERED, 0.65, 1.00, 0.65, 1.0)
            imgui.plot_lines(
                "##signal",
                array("f", values),
                graph_size=(PLOT_WIDTH, PLOT_HEIGHT),
                scale_min=vmin - pad,
                scale_max=vmax + pad,
            )
            imgui.pop_style_color(3)
            imgui.end()

            imgui.render()
            fb_width, fb_height = glfw.get_framebuffer_size(window)
            GL.glViewport(0, 0, fb_width, fb_height)
            GL.glClearColor(0.07, 0.08, 0.10, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            impl.render(imgui.get_draw_data())
            glfw.swap_buffers(window)
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
        impl.shutdown()
        glfw.terminate()


if __name__ == "__main__":
    main()
