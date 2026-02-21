import multiprocessing as mp
import sys
import threading
import time
from array import array
from collections import deque
from queue import Queue

import glfw
import imgui
from imgui.integrations.glfw import GlfwRenderer
from OpenGL import GL

# Graph rendering helpers: axes/labels/overlay drawing per graph cell.
from graph_widgets import render_graph_content_multi
# Network/data pipeline workers: socket ingest + fixed-N averaging thread.
from network_pipeline import averaging_worker, network_receiver
# Signal helpers: decimation for plotting, optional notch filtering, FFT worker process.
from signal_processing import apply_notch_range_filters, downsample_for_plot, fft_worker
# TOML-driven runtime config for stream endpoint, signals, and graph-cell layout.

import sys
from pathlib import Path

# Add shared_config to sys.path so we can import config_parser
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared_config.config_parser import load_toml_config


WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 900
PLOT_HEIGHT = 220


def make_signal_fft_state() -> dict:
    return {
        "proc": None,
        "q": None,
        "status": "idle",
        "peaks": [],
        "active_notch_ranges_hz": [],
        "window_s": 5.0,
        "freqs_plot": [],
        "amps_plot": [],
        "freq_max": 0.0,
        "select_start_hz": None,
        "manual_lo_hz": 0.0,
        "manual_hi_hz": 0.0,
        "visible_fft": True,
    }


def main() -> None:
    nidaq_cfg, db_cfg, stream_cfg, signal_cfgs, graph_cells = load_toml_config(str(ROOT_DIR / "gse2_0.toml"))

    stop_event = threading.Event()
    stats = {"raw_samples": 0, "avg_samples": 0}
    source_column_by_signal: dict[str, str] = {}
    worker_key_by_cell_signal: dict[tuple[str, str], str] = {}
    raw_queues: dict[str, Queue] = {}
    averaged: dict[str, deque] = {}
    staging_ring: dict[str, deque] = {}
    locks: dict[str, threading.Lock] = {}
    avg_refs: dict[str, dict] = {}
    window_refs: dict[str, dict] = {}
    avg_samples_by_worker: dict[str, int] = {}
    raw_samples_by_worker: dict[str, int] = {}
    stage_samples_by_worker: dict[str, int] = {}
    for cell in graph_cells:
        for s in cell.signals:
            cfg = signal_cfgs.get(s)
            if cfg is None:
                continue
            wk = f"{cell.title}::{s}"
            worker_key_by_cell_signal[(cell.title, s)] = wk
            source_column_by_signal[wk] = cfg.source_column
            raw_queues[wk] = Queue(maxsize=256)
            averaged[wk] = deque()
            staging_ring[wk] = deque()
            locks[wk] = threading.Lock()
            avg_refs[wk] = {"value": cfg.avg_n}
            window_refs[wk] = {"value": cell.window_s}
            avg_samples_by_worker[wk] = 0
            raw_samples_by_worker[wk] = 0
            stage_samples_by_worker[wk] = 0

    threads = [
        threading.Thread(
            target=network_receiver,
            args=(raw_queues, source_column_by_signal, stream_cfg.host, stream_cfg.port, stream_cfg.raw_batch_points, stop_event, stats),
            daemon=True,
        )
    ]
    for wk in raw_queues:
        threads.append(
            threading.Thread(
                target=averaging_worker,
                args=(
                    raw_queues[wk],
                    averaged[wk],
                    staging_ring[wk],
                    locks[wk],
                    stop_event,
                    stats,
                    avg_refs[wk],
                    stream_cfg.raw_batch_points,
                    window_refs[wk],
                    wk,
                    avg_samples_by_worker,
                    raw_samples_by_worker,
                    stage_samples_by_worker,
                ),
                daemon=True,
            )
        )
    for t in threads:
        t.start()

    if not glfw.init():
        raise RuntimeError("Failed to initialize GLFW")
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    if sys.platform == "darwin":
        # Required by macOS when requesting a core profile context.
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
    window = glfw.create_window(WINDOW_WIDTH, WINDOW_HEIGHT, "Network NI-DAQ Pipeline (Modular)", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("Failed to create GLFW window")
    glfw.make_context_current(window)
    glfw.swap_interval(1)

    imgui.create_context()
    impl = GlfwRenderer(window)
    imgui.style_colors_classic()

    # Independent FFT/filter state per graph cell so controls do not interfere.
    cell_fft = {
        c.title: {
            "target_signal": c.signals[0] if c.signals else "",
            "signals": {s: make_signal_fft_state() for s in c.signals},
        }
        for c in graph_cells
    }
    cell_plot_visibility = {
        c.title: {
            "signals": {s: True for s in c.signals},
            "filtered_signals": {s: True for s in c.signals},
        }
        for c in graph_cells
    }

    last_metrics_t = time.monotonic()
    last_raw_total = 0
    last_avg_total = 0
    last_raw_total_by_worker = {wk: 0 for wk in raw_queues}
    last_avg_total_by_worker = {wk: 0 for wk in raw_queues}
    raw_sps = 0.0
    avg_sps = 0.0
    raw_sps_by_worker = {wk: 0.0 for wk in raw_queues}
    avg_sps_by_worker = {wk: 0.0 for wk in raw_queues}
    scale_state = {}  # per cell title -> (min,max)

    try:
        while not glfw.window_should_close(window):
            glfw.poll_events()
            impl.process_inputs()
            imgui.new_frame()

            now_wall = time.time()
            now = time.monotonic()
            if now - last_metrics_t >= 1.0:
                dt = max(1e-6, (now - last_metrics_t))
                raw_total = stats["raw_samples"]
                avg_total = stats["avg_samples"]
                raw_sps = (raw_total - last_raw_total) / dt
                avg_sps = (avg_total - last_avg_total) / dt
                last_raw_total = raw_total
                last_avg_total = avg_total
                for wk in raw_queues:
                    raw_total_wk = raw_samples_by_worker.get(wk, 0)
                    total_wk = avg_samples_by_worker.get(wk, 0)
                    raw_sps_by_worker[wk] = (raw_total_wk - last_raw_total_by_worker.get(wk, 0)) / dt
                    avg_sps_by_worker[wk] = (total_wk - last_avg_total_by_worker.get(wk, 0)) / dt
                    last_raw_total_by_worker[wk] = raw_total_wk
                    last_avg_total_by_worker[wk] = total_wk
                last_metrics_t = now

            y = 20
            for cell_i, cell in enumerate(graph_cells):
                st = cell_fft[cell.title]
                vis = cell_plot_visibility[cell.title]
                wrapper_h = 370
                imgui.set_next_window_position(20, y, condition=imgui.ONCE)
                imgui.set_next_window_size(WINDOW_WIDTH - 40, wrapper_h, condition=imgui.ONCE)
                imgui.begin(f"{cell.title} Panel##{cell_i}")

                # Build per-signal series for this cell; each graph+signal has independent buffers/state.
                series_map = {}
                graph_display_len = 0
                graph_stage_len = 0
                for s in cell.signals:
                    wk = worker_key_by_cell_signal.get((cell.title, s))
                    if wk is None:
                        continue
                    with locks[wk]:
                        avg_pairs = list(averaged[wk])
                        stage_len = len(staging_ring[wk])
                    vals = [v for _, v in avg_pairs]
                    graph_display_len += len(avg_pairs)
                    graph_stage_len += stage_len
                    series_map[s] = downsample_for_plot(vals, stream_cfg.plot_points) if vals else []
                series_items = []
                for s in cell.signals:
                    vals = series_map.get(s, [])
                    if vals and vis["signals"].get(s, True):
                        series_items.append((s, vals))
                # Per-signal filtered overlays: can show any subset, based on each signal's FFT notch ranges.
                filtered_map = {}
                for s in cell.signals:
                    s_state = st["signals"].setdefault(s, make_signal_fft_state())
                    if not vis["filtered_signals"].get(s, True):
                        continue
                    src_vals = series_map.get(s, [])
                    if not src_vals or not s_state["active_notch_ranges_hz"]:
                        continue
                    overlay_sr = max(1.0, len(src_vals) / max(1e-6, cell.window_s))
                    filtered_map[s] = apply_notch_range_filters(src_vals, overlay_sr, s_state["active_notch_ranges_hz"])
                for s in cell.signals:
                    fvals = filtered_map.get(s, [])
                    if fvals:
                        series_items.append((f"{s}_filtered", fvals))

                # Scale from all visible traces in this cell, then smooth to reduce jitter.
                all_vals = []
                for s, vals in series_map.items():
                    if not vis["signals"].get(s, True):
                        continue
                    all_vals.extend(vals)
                for fvals in filtered_map.values():
                    all_vals.extend(fvals)
                if not all_vals:
                    all_vals = [0.0]
                vmin = min(all_vals) if all_vals else -1.0
                vmax = max(all_vals) if all_vals else 1.0
                pad = max(1e-6, (vmax - vmin) * 0.1)
                tmin, tmax = vmin - pad, vmax + pad
                smin, smax = scale_state.get(cell.title, (tmin, tmax))
                smin = tmin if tmin < smin else (smin * 0.98 + tmin * 0.02)
                smax = tmax if tmax > smax else (smax * 0.98 + tmax * 0.02)
                scale_state[cell.title] = (smin, smax)

                avail_w = imgui.get_content_region_available()[0]
                spacing = imgui.get_style().item_spacing.x
                left_w = 250.0
                right_w = 320.0
                center_w = max(240.0, avail_w - left_w - right_w - (2.0 * spacing))
                child_h = wrapper_h - 46.0

                # Left panel: per-graph controls.
                imgui.begin_child(f"controls##{cell.title}", left_w, child_h, border=True)
                imgui.text(f"raw_sps={raw_sps:.1f} avg_sps={avg_sps:.1f}")
                display_mb = (graph_display_len * 16) / (1024.0 * 1024.0)
                total_mb = ((graph_display_len * 16) + (graph_stage_len * 8)) / (1024.0 * 1024.0)
                imgui.text(f"display_buffer={display_mb:.3f} MB")
                imgui.text(f"intermediate_buffer_len={graph_stage_len}")
                imgui.text(f"total_buffer={total_mb:.3f} MB")
                imgui.text(f"stream={stream_cfg.host}:{stream_cfg.port}")
                changed_w, w = imgui.input_float(f"Window s##{cell.title}", float(cell.window_s), 10.0, 60.0, "%.1f")
                if changed_w:
                    cell.window_s = max(1.0, float(w))
                    for s in cell.signals:
                        wk = worker_key_by_cell_signal.get((cell.title, s))
                        if wk is not None:
                            window_refs[wk]["value"] = cell.window_s
                imgui.separator()
                imgui.text("Signal averaging")
                for s in cell.signals:
                    wk = worker_key_by_cell_signal.get((cell.title, s))
                    if wk is None:
                        continue
                    imgui.text(f"{s} avg_sps={avg_sps_by_worker.get(wk, 0.0):.1f}")
                    changed_n, new_n = imgui.input_int(f"Avg N: {s}##{cell.title}", int(avg_refs[wk]["value"]), 1, 10)
                    if changed_n:
                        avg_refs[wk]["value"] = max(1, int(new_n))
                imgui.end_child()

                imgui.same_line()

                # Center panel: graph.
                imgui.begin_child(f"graph##{cell.title}", center_w, child_h, border=True)
                render_graph_content_multi(
                    cell.title,
                    center_w - 18.0,
                    PLOT_HEIGHT,
                    series_items,
                    None,
                    smin,
                    smax,
                    cell.window_s,
                    now_wall,
                )
                imgui.separator()
                for i, sig_name in enumerate(cell.signals):
                    is_on = vis["signals"].get(sig_name, True)
                    btn = f"{sig_name}{' [on]' if is_on else ' [off]'}##{cell.title}_{sig_name}_plotvis"
                    if imgui.button(btn):
                        vis["signals"][sig_name] = not is_on
                    if i < len(cell.signals) - 1:
                        imgui.same_line()
                if imgui.button(f"all_filtered_on##{cell.title}"):
                    for sig_name in cell.signals:
                        vis["filtered_signals"][sig_name] = True
                imgui.same_line()
                if imgui.button(f"all_filtered_off##{cell.title}"):
                    for sig_name in cell.signals:
                        vis["filtered_signals"][sig_name] = False
                for i, sig_name in enumerate(cell.signals):
                    if i > 0:
                        imgui.same_line()
                    f_on = vis["filtered_signals"].get(sig_name, True)
                    f_btn = f"{sig_name}_filtered{' [on]' if f_on else ' [off]'}##{cell.title}_{sig_name}_filtered_plotvis"
                    if imgui.button(f_btn):
                        vis["filtered_signals"][sig_name] = not f_on
                imgui.end_child()

                imgui.same_line()

                # Right panel: per-graph FFT controls/state.
                imgui.begin_child(f"fft##{cell.title}", right_w, child_h, border=True)
                options = [s for s in cell.signals if worker_key_by_cell_signal.get((cell.title, s)) is not None]
                if options:
                    if st["target_signal"] not in options:
                        st["target_signal"] = options[0]
                    idx = options.index(st["target_signal"])
                    changed_combo, new_idx = imgui.combo(f"FFT signal##{cell.title}", idx, options)
                    if changed_combo:
                        st["target_signal"] = options[new_idx]
                tstate = st["signals"].setdefault(st["target_signal"], make_signal_fft_state())
                changed_fft, new_win = imgui.input_float(f"FFT window (s)##{cell.title}", float(tstate["window_s"]), 1.0, 5.0, "%.2f")
                if changed_fft:
                    tstate["window_s"] = max(0.1, float(new_win))
                wk_target = worker_key_by_cell_signal.get((cell.title, st["target_signal"]))
                if imgui.button(f"Run FFT##{cell.title}") and wk_target is not None:
                    with locks[wk_target]:
                        avg_pairs = list(averaged[wk_target])
                    vals = [v for _, v in avg_pairs]
                    sr_est = max(1.0, avg_sps_by_worker.get(wk_target, avg_sps))
                    if len(avg_pairs) > 1:
                        dt = max(1e-6, avg_pairs[-1][0] - avg_pairs[0][0])
                        sr_est = max(1.0, len(avg_pairs) / dt)
                    n = int(max(8, tstate["window_s"] * sr_est))
                    window_vals = vals[-n:] if len(vals) >= n else vals
                    tstate["q"] = mp.Queue()
                    tstate["proc"] = mp.Process(target=fft_worker, args=(window_vals, sr_est, stream_cfg.fft_top_n, tstate["q"]), daemon=True)
                    tstate["proc"].start()
                    tstate["status"] = f"running n={len(window_vals)}"
                imgui.same_line()
                if imgui.button(f"Clear##{cell.title}"):
                    tstate["active_notch_ranges_hz"].clear()
                    tstate["select_start_hz"] = None
                for sig_name in options:
                    sig_st = st["signals"].setdefault(sig_name, make_signal_fft_state())
                    if sig_st["q"] is not None and not sig_st["q"].empty():
                        msg = sig_st["q"].get()
                        sig_st["status"] = "done" if msg.get("ok") else f"error: {msg.get('error')}"
                        sig_st["peaks"] = msg.get("peaks", []) if msg.get("ok") else []
                        sig_st["freqs_plot"] = msg.get("freqs_plot", []) if msg.get("ok") else []
                        sig_st["amps_plot"] = msg.get("amps_plot", []) if msg.get("ok") else []
                        sig_st["freq_max"] = float(msg.get("freq_max", 0.0)) if msg.get("ok") else 0.0
                        if sig_st["proc"] is not None:
                            sig_st["proc"].join(timeout=0.2)
                        sig_st["proc"] = None
                        sig_st["q"] = None
                imgui.text(f"status ({st['target_signal']}): {tstate['status']}")

                visible_fft = [s for s in options if st["signals"].setdefault(s, make_signal_fft_state())["visible_fft"]]
                amps_plot = []
                if visible_fft:
                    amps_plot = st["signals"][visible_fft[0]]["amps_plot"]
                if amps_plot:
                    plot_w = max(120.0, right_w - 24.0)
                    plot_h = 110.0
                    max_amp = max(
                        [max(st["signals"][s]["amps_plot"]) for s in visible_fft if st["signals"][s]["amps_plot"]] or [1.0]
                    )
                    imgui.plot_lines(
                        f"FFT Spectrum##{cell.title}",
                        array("f", amps_plot),
                        graph_size=(plot_w, plot_h),
                        scale_min=0.0,
                        scale_max=max(1e-9, max_amp * 1.05),
                    )
                    min_x, min_y = imgui.get_item_rect_min()
                    max_x, max_y = imgui.get_item_rect_max()
                    draw_list = imgui.get_window_draw_list()
                    for i, sig_name in enumerate(visible_fft[1:], start=1):
                        vals = st["signals"][sig_name]["amps_plot"]
                        if len(vals) < 2:
                            continue
                        n = len(vals) - 1
                        xr = max_x - min_x
                        yr = max_y - min_y
                        denom = max(1e-9, max_amp * 1.05)
                        color_cycle = [
                            imgui.get_color_u32_rgba(0.35, 0.95, 1.0, 0.95),
                            imgui.get_color_u32_rgba(1.0, 0.92, 0.35, 0.95),
                            imgui.get_color_u32_rgba(1.0, 0.55, 0.30, 0.95),
                            imgui.get_color_u32_rgba(0.95, 0.55, 1.0, 0.95),
                        ]
                        color = color_cycle[(i - 1) % len(color_cycle)]
                        for j in range(n):
                            x1 = min_x + xr * (j / n)
                            x2 = min_x + xr * ((j + 1) / n)
                            y1 = max_y - yr * (vals[j] / denom)
                            y2 = max_y - yr * (vals[j + 1] / denom)
                            draw_list.add_line(x1, y1, x2, y2, color, 1.2)
                    freq_max = max(1e-9, float(tstate["freq_max"]))
                    fill_col = imgui.get_color_u32_rgba(1.0, 0.35, 0.25, 0.18)
                    line_col = imgui.get_color_u32_rgba(1.0, 0.3, 0.2, 0.95)
                    for lo_hz, hi_hz in tstate["active_notch_ranges_hz"]:
                        lo = max(0.0, min(lo_hz, hi_hz))
                        hi = min(freq_max, max(lo_hz, hi_hz))
                        x1 = min_x + (lo / freq_max) * (max_x - min_x)
                        x2 = min_x + (hi / freq_max) * (max_x - min_x)
                        draw_list.add_rect_filled(x1, min_y, x2, max_y, fill_col)
                    if tstate["select_start_hz"] is not None:
                        sx = min_x + (max(0.0, min(float(tstate["select_start_hz"]), freq_max)) / freq_max) * (max_x - min_x)
                        draw_list.add_line(sx, min_y, sx, max_y, line_col, 1.5)

                    if imgui.is_item_hovered() and tstate["freq_max"] > 0.0:
                        if imgui.is_mouse_clicked(0):
                            mx = imgui.get_mouse_pos()[0]
                            ratio = 0.0 if max_x <= min_x else (mx - min_x) / (max_x - min_x)
                            ratio = max(0.0, min(1.0, ratio))
                            hz = ratio * tstate["freq_max"]
                            if tstate["select_start_hz"] is None:
                                tstate["select_start_hz"] = hz
                            else:
                                lo, hi = sorted((float(tstate["select_start_hz"]), float(hz)))
                                if hi - lo > 1e-6:
                                    tstate["active_notch_ranges_hz"].append((lo, hi))
                                tstate["select_start_hz"] = None
                        elif imgui.is_mouse_clicked(1):
                            tstate["select_start_hz"] = None

                imgui.text("click spectrum twice to add a band")
                if tstate["select_start_hz"] is not None:
                    imgui.text(f"selection start: {tstate['select_start_hz']:.2f} Hz")
                changed_lo, lo = imgui.input_float(f"Band lo (Hz)##{cell.title}", float(tstate["manual_lo_hz"]), 1.0, 10.0, "%.2f")
                if changed_lo:
                    tstate["manual_lo_hz"] = max(0.0, float(lo))
                changed_hi, hi = imgui.input_float(f"Band hi (Hz)##{cell.title}", float(tstate["manual_hi_hz"]), 1.0, 10.0, "%.2f")
                if changed_hi:
                    tstate["manual_hi_hz"] = max(0.0, float(hi))
                if imgui.button(f"Add band##{cell.title}"):
                    lo_hz, hi_hz = sorted((float(tstate["manual_lo_hz"]), float(tstate["manual_hi_hz"])))
                    if hi_hz - lo_hz > 1e-6:
                        tstate["active_notch_ranges_hz"].append((lo_hz, hi_hz))
                for band_i, (lo_hz, hi_hz) in enumerate(list(tstate["active_notch_ranges_hz"]), start=1):
                    if imgui.button(f"X##{cell.title}band{band_i}"):
                        tstate["active_notch_ranges_hz"].pop(band_i - 1)
                    imgui.same_line()
                    imgui.text(f"{band_i}: {lo_hz:.2f}-{hi_hz:.2f} Hz")
                imgui.separator()
                for i, sig_name in enumerate(options):
                    sig_st = st["signals"].setdefault(sig_name, make_signal_fft_state())
                    label = f"{sig_name}_fft"
                    btn = f"{label}{' [on]' if sig_st['visible_fft'] else ' [off]'}##{cell.title}_{sig_name}_fftvis"
                    if imgui.button(btn):
                        sig_st["visible_fft"] = not sig_st["visible_fft"]
                    if i < len(options) - 1:
                        imgui.same_line()
                imgui.end_child()

                imgui.end()
                y += wrapper_h + 12

            imgui.render()
            fb_width, fb_height = glfw.get_framebuffer_size(window)
            GL.glViewport(0, 0, fb_width, fb_height)
            GL.glClearColor(0.07, 0.08, 0.10, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            impl.render(imgui.get_draw_data())
            glfw.swap_buffers(window)
    finally:
        stop_event.set()
        # Ensure per-cell FFT subprocesses are terminated cleanly on exit.
        for st in cell_fft.values():
            for sig_st in st["signals"].values():
                if sig_st["proc"] is not None and sig_st["proc"].is_alive():
                    sig_st["proc"].terminate()
                    sig_st["proc"].join(timeout=0.2)
        impl.shutdown()
        glfw.terminate()


if __name__ == "__main__":
    main()
