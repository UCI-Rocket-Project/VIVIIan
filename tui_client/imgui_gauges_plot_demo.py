import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from array import array
from collections import deque
from pathlib import Path

import glfw
import imgui
from imgui.integrations.glfw import GlfwRenderer
from OpenGL import GL


WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 720
PLOT_HISTORY = 8000
PLOT_WIDTH = 720
PLOT_HEIGHT = 240
DB_TARGET_SAMPLE_HZ = 1000
DB_FETCH_HZ = 20
QUESTDB_HTTP_URL = "http://localhost:9000/exec"

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "nidaq_client"))
from config import NIDAQ_CHANNELS, QUESTDB_TABLE  # noqa: E402


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def draw_gauge(label: str, value: float, width: float = 280.0, height: float = 26.0) -> None:
    value = clamp01(value)
    pct = int(value * 100)
    x, y = imgui.get_cursor_screen_pos()
    draw_list = imgui.get_window_draw_list()

    # Reserve gauge region for custom drawing.
    total_h = height + 22
    imgui.invisible_button(f"##gauge_{label}", width, total_h)

    bg = imgui.get_color_u32_rgba(0.01, 0.08, 0.01, 1.0)
    border = imgui.get_color_u32_rgba(0.30, 0.95, 0.30, 0.95)
    text = imgui.get_color_u32_rgba(0.62, 1.00, 0.62, 1.0)
    seg_on = imgui.get_color_u32_rgba(0.22, 1.00, 0.22, 1.0)
    seg_off = imgui.get_color_u32_rgba(0.03, 0.18, 0.03, 1.0)
    warn = imgui.get_color_u32_rgba(1.00, 0.45, 0.20, 1.0)

    # Label line and digital readout box.
    draw_list.add_text(x + 4, y, text, label.upper())
    readout = f"{pct:03d}%"
    readout_w = imgui.calc_text_size(readout).x + 12
    rx1 = x + width - readout_w - 2
    ry1 = y - 2
    rx2 = x + width - 2
    ry2 = y + 16
    draw_list.add_rect_filled(rx1, ry1, rx2, ry2, bg)
    draw_list.add_rect(rx1, ry1, rx2, ry2, border, 0.0, 0, 1.0)
    draw_list.add_text(rx1 + 6, y, text, readout)

    # Segmented bar body.
    bar_x1 = x + 2
    bar_y1 = y + 20
    bar_x2 = x + width - 2
    bar_y2 = bar_y1 + height
    draw_list.add_rect_filled(bar_x1, bar_y1, bar_x2, bar_y2, bg)
    draw_list.add_rect(bar_x1, bar_y1, bar_x2, bar_y2, border, 0.0, 0, 1.0)

    segments = 24
    gap = 2.0
    inner_w = (bar_x2 - bar_x1) - gap * (segments + 1)
    seg_w = max(2.0, inner_w / segments)
    lit = int(round(value * segments))
    for i in range(segments):
        sx1 = bar_x1 + gap + i * (seg_w + gap)
        sx2 = sx1 + seg_w
        sy1 = bar_y1 + 3
        sy2 = bar_y2 - 3
        if i < lit:
            color = warn if i >= int(segments * 0.85) else seg_on
        else:
            color = seg_off
        draw_list.add_rect_filled(sx1, sy1, sx2, sy2, color)

    imgui.set_cursor_screen_pos((x, y + total_h + 2))


def qdb_exec(sql: str, timeout: float = 0.8) -> dict:
    params = urllib.parse.urlencode({"query": sql})
    with urllib.request.urlopen(f"{QUESTDB_HTTP_URL}?{params}", timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    import json
    return json.loads(payload)


def fetch_live_samples(target_hz: int, history_points: int, value_col: str) -> tuple[list[float], str]:
    sample_ms = max(1, int(round(1000.0 / max(1, target_hz))))
    window_sec = max(2, int(math.ceil(history_points / float(target_hz))) + 1)
    sql = (
        f"SELECT timestamp, avg(\"{value_col}\") AS v "
        f"FROM {QUESTDB_TABLE} "
        f"WHERE timestamp > dateadd('s', -{window_sec}, now()) "
        f"SAMPLE BY {sample_ms}ms"
    )
    data = qdb_exec(sql)
    rows = data.get("dataset", [])
    values = [float(r[1]) for r in rows if len(r) > 1 and r[1] is not None]
    return values[-history_points:], sql


def main() -> None:
    if not glfw.init():
        raise RuntimeError("Failed to initialize GLFW")

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)

    window = glfw.create_window(WINDOW_WIDTH, WINDOW_HEIGHT, "ImGui Gauges + Plot Demo", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("Failed to create GLFW window")

    glfw.make_context_current(window)
    glfw.swap_interval(1)

    imgui.create_context()
    impl = GlfwRenderer(window)
    io = imgui.get_io()

    # Classic old-Windows look.
    imgui.style_colors_classic()
    style = imgui.get_style()
    style.window_rounding = 0.0
    style.frame_rounding = 0.0
    style.grab_rounding = 0.0
    style.window_border_size = 1.0
    style.frame_border_size = 1.0

    # Prefer the old PowerShell-era Windows font if available.
    lucida_console = r"C:\Windows\Fonts\lucon.ttf"
    if os.path.exists(lucida_console):
        io.fonts.add_font_from_file_ttf(lucida_console, 16)
        impl.refresh_font_texture()

    phase = 0.0
    plot_data = deque([0.0] * PLOT_HISTORY, maxlen=PLOT_HISTORY)
    graph_bg_presets = [
        (0.01, 0.05, 0.01, 1.0),
        (0.01, 0.03, 0.06, 1.0),
        (0.05, 0.04, 0.01, 1.0),
    ]
    graph_bg_idx = 0

    live_view = True
    view_window = 240
    view_end = len(plot_data)
    last_fetch = 0.0
    fetch_period = 1.0 / DB_FETCH_HZ
    last_db_error = ""
    sample_hz = DB_TARGET_SAMPLE_HZ
    value_column = NIDAQ_CHANNELS[0]
    last_sql = ""

    keys_to_watch = [glfw.KEY_B, glfw.KEY_Z, glfw.KEY_X, glfw.KEY_LEFT, glfw.KEY_RIGHT, glfw.KEY_R]
    prev_key_down = {k: False for k in keys_to_watch}

    def key_pressed_once(key: int) -> bool:
        is_down = glfw.get_key(window, key) == glfw.PRESS
        was_down = prev_key_down[key]
        prev_key_down[key] = is_down
        return is_down and not was_down

    try:
        while not glfw.window_should_close(window):
            glfw.poll_events()
            impl.process_inputs()
            imgui.new_frame()

            phase += 0.03
            now_mono = time.monotonic()
            if now_mono - last_fetch >= fetch_period:
                try:
                    values, last_sql = fetch_live_samples(sample_hz, PLOT_HISTORY, value_column)
                    if values:
                        plot_data.clear()
                        plot_data.extend(values)
                    last_db_error = ""
                except Exception as e:
                    last_db_error = str(e)
                last_fetch = now_mono

            # Gauges derived from live signal + synthetic helpers.
            latest = float(plot_data[-1]) if plot_data else 0.0
            abs_latest = min(1.0, abs(latest))
            cpu = clamp01(0.55 + 0.35 * math.sin(phase))
            temp = clamp01(0.45 + 0.45 * math.sin(phase * 0.7 + 1.1))
            pressure = clamp01(0.60 + 0.25 * math.sin(phase * 1.4 + 2.0))
            data_len = len(plot_data)

            if key_pressed_once(glfw.KEY_B):
                graph_bg_idx = (graph_bg_idx + 1) % len(graph_bg_presets)

            if key_pressed_once(glfw.KEY_R):
                live_view = True
                view_end = data_len

            if key_pressed_once(glfw.KEY_Z):
                live_view = False
                view_window = max(40, int(view_window * 0.8))

            if key_pressed_once(glfw.KEY_X):
                live_view = False
                view_window = min(data_len, int(view_window * 1.25))

            if key_pressed_once(glfw.KEY_LEFT):
                live_view = False
                view_end = max(view_window, view_end - max(10, view_window // 10))

            if key_pressed_once(glfw.KEY_RIGHT):
                live_view = False
                view_end = min(data_len, view_end + max(10, view_window // 10))

            if live_view:
                view_end = data_len

            view_window = max(40, min(view_window, data_len))
            view_end = max(view_window, min(view_end, data_len))
            view_start = max(0, view_end - view_window)
            view_slice = list(plot_data)[view_start:view_end]

            imgui.set_next_window_position(20, 20, condition=imgui.ONCE)
            imgui.set_next_window_size(360, 290, condition=imgui.ONCE)
            imgui.begin("System Gauges")
            draw_gauge("CPU Load", cpu)
            draw_gauge("Temp Level", temp)
            draw_gauge("Pressure", pressure)
            changed, sample_hz = imgui.slider_int("DB Sample Hz", sample_hz, 50, 5000)
            if changed:
                sample_hz = max(1, sample_hz)
            imgui.text(f"Source: {QUESTDB_TABLE}.{value_column}")
            if last_db_error:
                imgui.text_colored(f"DB ERR: {last_db_error[:58]}", 1.0, 0.5, 0.35)
            else:
                imgui.text_colored(f"DB: LIVE @ {sample_hz} Hz", 0.45, 1.0, 0.45)
            imgui.separator()
            if imgui.button("Graph BG (B)"):
                graph_bg_idx = (graph_bg_idx + 1) % len(graph_bg_presets)
            imgui.same_line()
            if imgui.button("Zoom In (Z)"):
                live_view = False
                view_window = max(40, int(view_window * 0.8))
            imgui.same_line()
            if imgui.button("Zoom Out (X)"):
                live_view = False
                view_window = min(len(plot_data), int(view_window * 1.25))

            if imgui.button("Pan Left (<-)"):
                live_view = False
                view_end = max(view_window, view_end - max(10, view_window // 10))
            imgui.same_line()
            if imgui.button("Pan Right (->)"):
                live_view = False
                view_end = min(len(plot_data), view_end + max(10, view_window // 10))
            imgui.same_line()
            if imgui.button("Resume Live (R)"):
                live_view = True
                view_end = len(plot_data)
            imgui.end()

            imgui.set_next_window_position(400, 20, condition=imgui.ONCE)
            imgui.set_next_window_size(760, 320, condition=imgui.ONCE)
            imgui.begin("Signal Plot")
            mode = "LIVE" if live_view else "HISTORY"
            imgui.text(f"Signal mode: {mode} | window={view_window} samples | end={view_end}")
            imgui.text(f"Live Signal: {latest:.6f}")
            imgui.text(f"DB fetch={DB_FETCH_HZ}Hz, sample-by~{max(1, int(round(1000.0 / max(1, sample_hz))))}ms")
            imgui.text("Mouse: wheel=zoom at cursor, left-drag=pan, double-click=resume live")
            imgui.push_style_color(imgui.COLOR_FRAME_BACKGROUND, *graph_bg_presets[graph_bg_idx])
            imgui.push_style_color(imgui.COLOR_PLOT_LINES, 0.25, 1.00, 0.25, 1.0)
            imgui.push_style_color(imgui.COLOR_PLOT_LINES_HOVERED, 0.65, 1.00, 0.65, 1.0)
            imgui.plot_lines(
                "##signal",
                array("f", view_slice),
                graph_size=(PLOT_WIDTH, PLOT_HEIGHT),
                scale_min=-1.2,
                scale_max=1.2,
            )
            min_x, min_y = imgui.get_item_rect_min()
            max_x, max_y = imgui.get_item_rect_max()
            draw_list = imgui.get_window_draw_list()

            # Oscilloscope grid and frame overlay.
            grid_color = imgui.get_color_u32_rgba(0.10, 0.55, 0.10, 0.42)
            center_color = imgui.get_color_u32_rgba(0.35, 0.95, 0.35, 0.70)
            frame_color = imgui.get_color_u32_rgba(0.22, 0.80, 0.22, 0.85)
            glow_color = imgui.get_color_u32_rgba(0.10, 0.45, 0.10, 0.25)

            for i in range(1, 10):
                x = min_x + (max_x - min_x) * (i / 10.0)
                draw_list.add_line(x, min_y, x, max_y, grid_color, 1.0)
            for i in range(1, 8):
                y = min_y + (max_y - min_y) * (i / 8.0)
                draw_list.add_line(min_x, y, max_x, y, grid_color, 1.0)

            cx = min_x + (max_x - min_x) * 0.5
            cy = min_y + (max_y - min_y) * 0.5
            # draw_list.add_line(cx, min_y, cx, max_y, center_color, 1.2)
            # draw_list.add_line(min_x, cy, max_x, cy, center_color, 1.2)
            draw_list.add_rect(min_x, min_y, max_x, max_y, frame_color, 0.0, 0, 1.5)
            draw_list.add_rect_filled(min_x, min_y, max_x, max_y, glow_color, 0.0, 0)
            imgui.pop_style_color(3)

            # Intuitive plot interactions.
            if imgui.is_item_hovered():
                if imgui.is_mouse_double_clicked(0):
                    live_view = True
                    view_end = data_len

                wheel = io.mouse_wheel
                if wheel != 0.0:
                    live_view = False
                    old_window = view_window
                    if wheel > 0:
                        new_window = int(old_window * (0.85 ** wheel))
                    else:
                        new_window = int(old_window * (1.18 ** (-wheel)))
                    new_window = max(40, min(new_window, data_len))

                    min_x, _ = imgui.get_item_rect_min()
                    max_x, _ = imgui.get_item_rect_max()
                    mouse_x, _ = io.mouse_pos
                    plot_w = max(1.0, max_x - min_x)
                    anchor = max(0.0, min(1.0, (mouse_x - min_x) / plot_w))

                    anchor_index = view_start + int(anchor * max(1, old_window - 1))
                    view_window = new_window
                    view_end = anchor_index + int((1.0 - anchor) * new_window)

            if imgui.is_item_active() and imgui.is_mouse_dragging(0):
                live_view = False
                drag_dx, _ = imgui.get_mouse_drag_delta(0)
                if abs(drag_dx) >= 1.0:
                    shift = int(drag_dx * (view_window / float(PLOT_WIDTH)))
                    view_end -= shift
                    imgui.reset_mouse_drag_delta(0)

            imgui.end()

            imgui.render()
            fb_width, fb_height = glfw.get_framebuffer_size(window)
            GL.glViewport(0, 0, fb_width, fb_height)
            GL.glClearColor(0.07, 0.08, 0.10, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            impl.render(imgui.get_draw_data())
            glfw.swap_buffers(window)
    finally:
        impl.shutdown()
        glfw.terminate()


if __name__ == "__main__":
    main()
