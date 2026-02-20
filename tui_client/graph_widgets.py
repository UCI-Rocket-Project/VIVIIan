from array import array
import imgui


def draw_overlay_series(draw_list, min_x, min_y, max_x, max_y, values, scale_min, scale_max, color, thickness=1.2):
    if len(values) < 2:
        return
    xr = max_x - min_x
    yr = max_y - min_y
    denom = max(1e-9, (scale_max - scale_min))
    n = len(values) - 1
    for i in range(n):
        x1 = min_x + xr * (i / n)
        x2 = min_x + xr * ((i + 1) / n)
        y1 = max_y - yr * ((values[i] - scale_min) / denom)
        y2 = max_y - yr * ((values[i + 1] - scale_min) / denom)
        draw_list.add_line(x1, y1, x2, y2, color, thickness)


def draw_axes_overlay(draw_list, min_x, min_y, max_x, max_y, scale_min, scale_max, window_seconds, now_wall):
    y_ticks = 6
    tick_color = imgui.get_color_u32_rgba(0.35, 0.7, 0.35, 0.45)
    text_color = imgui.get_color_u32_rgba(0.70, 0.95, 0.70, 0.95)
    for i in range(y_ticks + 1):
        t = i / float(y_ticks)
        y = max_y - (max_y - min_y) * t
        v = scale_min + (scale_max - scale_min) * t
        draw_list.add_line(min_x, y, max_x, y, tick_color, 1.0)
        draw_list.add_text(min_x + 4, y - 8, text_color, f"{v:.3f}")

    x_ticks = 6
    import time
    for i in range(x_ticks + 1):
        t = i / float(x_ticks)
        x = min_x + (max_x - min_x) * t
        ts = now_wall - (window_seconds * (1.0 - t))
        draw_list.add_line(x, min_y, x, max_y, tick_color, 1.0)
        draw_list.add_text(x - 26, max_y - 18, text_color, time.strftime("%H:%M:%S", time.localtime(ts)))


def render_graph_cell(title, y_pos, plot_w, plot_h, base_values, overlay_values, scale_min, scale_max, window_s, now_wall):
    imgui.set_next_window_position(20, y_pos, condition=imgui.ONCE)
    imgui.set_next_window_size(1140, 320, condition=imgui.ONCE)
    imgui.begin(title)
    latest = base_values[-1] if base_values else 0.0
    imgui.text(f"Latest: {latest:.6f} points={len(base_values)}")
    imgui.push_style_color(imgui.COLOR_FRAME_BACKGROUND, 0.01, 0.05, 0.01, 1.0)
    imgui.push_style_color(imgui.COLOR_PLOT_LINES, 0.25, 1.00, 0.25, 1.0)
    imgui.push_style_color(imgui.COLOR_PLOT_LINES_HOVERED, 0.65, 1.00, 0.65, 1.0)
    imgui.plot_lines(
        "##base",
        array("f", base_values if base_values else [0.0]),
        graph_size=(plot_w, plot_h),
        scale_min=scale_min,
        scale_max=scale_max,
    )
    min_x, min_y = imgui.get_item_rect_min()
    max_x, max_y = imgui.get_item_rect_max()
    draw_list = imgui.get_window_draw_list()
    draw_axes_overlay(draw_list, min_x, min_y, max_x, max_y, scale_min, scale_max, window_s, now_wall)
    if overlay_values:
        red = imgui.get_color_u32_rgba(1.0, 0.2, 0.2, 0.95)
        draw_overlay_series(draw_list, min_x, min_y, max_x, max_y, overlay_values, scale_min, scale_max, red, 1.3)
    imgui.pop_style_color(3)
    imgui.end()
