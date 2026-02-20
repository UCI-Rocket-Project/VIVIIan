import multiprocessing as mp
import sys
import threading
import time
from collections import deque
from queue import Queue

import glfw
import imgui
from imgui.integrations.glfw import GlfwRenderer
from OpenGL import GL

# Graph rendering helpers: axes/labels/overlay drawing per graph cell.
from graph_widgets import render_graph_content
# Network/data pipeline workers: socket ingest + fixed-N averaging thread.
from network_pipeline import averaging_worker, network_receiver
# Signal helpers: decimation for plotting, optional notch filtering, FFT worker process.
from signal_processing import apply_notch_filters, downsample_for_plot, fft_worker
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


def main() -> None:
    nidaq_cfg, db_cfg, stream_cfg, signal_cfgs, graph_cells = load_toml_config(str(ROOT_DIR / "gse2_0.toml"))

    stop_event = threading.Event()
    stats = {"raw_samples": 0, "avg_samples": 0}
    source_column_by_signal = {name: cfg.source_column for name, cfg in signal_cfgs.items()}

    raw_queues = {name: Queue(maxsize=256) for name in signal_cfgs}
    averaged = {name: deque() for name in signal_cfgs}
    locks = {name: threading.Lock() for name in signal_cfgs}
    avg_refs = {name: {"value": cfg.avg_n} for name, cfg in signal_cfgs.items()}
    window_refs = {name: {"value": stream_cfg.default_window_s} for name in signal_cfgs}

    threads = [
        threading.Thread(
            target=network_receiver,
            args=(raw_queues, source_column_by_signal, stream_cfg.host, stream_cfg.port, stream_cfg.raw_batch_points, stop_event, stats),
            daemon=True,
        )
    ]
    for name in signal_cfgs:
        threads.append(
            threading.Thread(
                target=averaging_worker,
                args=(raw_queues[name], averaged[name], locks[name], stop_event, stats, avg_refs[name], window_refs[name]),
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
            "proc": None,
            "q": None,
            "status": "idle",
            "peaks": [],
            "active_notches_hz": [],
            "target_signal": c.signals[0] if c.signals else "",
            "window_s": 5.0,
        }
        for c in graph_cells
    }

    last_metrics_t = time.monotonic()
    last_raw_total = 0
    last_avg_total = 0
    raw_sps = 0.0
    avg_sps = 0.0
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
                last_metrics_t = now

            y = 20
            for cell_i, cell in enumerate(graph_cells):
                st = cell_fft[cell.title]
                wrapper_h = 370
                imgui.set_next_window_position(20, y, condition=imgui.ONCE)
                imgui.set_next_window_size(WINDOW_WIDTH - 40, wrapper_h, condition=imgui.ONCE)
                imgui.begin(f"{cell.title} Panel##{cell_i}")

                # Build per-signal series for this cell; never concatenate dissimilar signals.
                series_map = {}
                for s in cell.signals:
                    if s not in averaged:
                        continue
                    with locks[s]:
                        cutoff = now_wall - cell.window_s
                        while averaged[s] and averaged[s][0][0] < cutoff:
                            averaged[s].popleft()
                        vals = [v for _, v in averaged[s]]
                    series_map[s] = downsample_for_plot(vals, stream_cfg.plot_points) if vals else []
                # Primary series is the first configured signal in the cell.
                primary = cell.signals[0] if cell.signals else ""
                plot_base = series_map.get(primary, []) if primary else []
                if not plot_base:
                    plot_base = [0.0]
                # Optional red overlay: FFT-notch-filtered version of selected target signal.
                fft_overlay = None
                if st["active_notches_hz"] and avg_sps > 1.0:
                    target_vals = series_map.get(st["target_signal"], plot_base)
                    fft_overlay = apply_notch_filters(target_vals if target_vals else plot_base, avg_sps, st["active_notches_hz"])

                # Scale from all visible traces in this cell, then smooth to reduce jitter.
                all_vals = []
                for vals in series_map.values():
                    all_vals.extend(vals)
                if fft_overlay:
                    all_vals.extend(fft_overlay)
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
                imgui.text(f"stream={stream_cfg.host}:{stream_cfg.port}")
                changed_w, w = imgui.input_float(f"Window s##{cell.title}", float(cell.window_s), 10.0, 60.0, "%.1f")
                if changed_w:
                    cell.window_s = max(1.0, float(w))
                    for s in cell.signals:
                        if s in window_refs:
                            window_refs[s]["value"] = cell.window_s
                imgui.separator()
                imgui.text("Signal averaging")
                for s in cell.signals:
                    if s not in avg_refs:
                        continue
                    changed_n, new_n = imgui.input_int(f"Avg N: {s}##{cell.title}", int(avg_refs[s]["value"]), 1, 10)
                    if changed_n:
                        avg_refs[s]["value"] = max(1, int(new_n))
                imgui.end_child()

                imgui.same_line()

                # Center panel: graph.
                imgui.begin_child(f"graph##{cell.title}", center_w, child_h, border=True)
                render_graph_content(cell.title, center_w - 18.0, PLOT_HEIGHT, plot_base, fft_overlay, smin, smax, cell.window_s, now_wall)
                imgui.end_child()

                imgui.same_line()

                # Right panel: per-graph FFT controls/state.
                imgui.begin_child(f"fft##{cell.title}", right_w, child_h, border=True)
                options = [s for s in cell.signals if s in averaged]
                if options:
                    if st["target_signal"] not in options:
                        st["target_signal"] = options[0]
                    idx = options.index(st["target_signal"])
                    changed_combo, new_idx = imgui.combo(f"FFT signal##{cell.title}", idx, options)
                    if changed_combo:
                        st["target_signal"] = options[new_idx]
                changed_fft, new_win = imgui.input_float(f"FFT window (s)##{cell.title}", float(st["window_s"]), 1.0, 5.0, "%.2f")
                if changed_fft:
                    st["window_s"] = max(0.1, float(new_win))
                if imgui.button(f"Run FFT##{cell.title}") and st["target_signal"] in averaged:
                    with locks[st["target_signal"]]:
                        vals = [v for _, v in averaged[st["target_signal"]]]
                    n = int(max(8, st["window_s"] * max(1.0, avg_sps)))
                    window_vals = vals[-n:] if len(vals) >= n else vals
                    st["q"] = mp.Queue()
                    st["proc"] = mp.Process(target=fft_worker, args=(window_vals, max(1.0, avg_sps), stream_cfg.fft_top_n, st["q"]), daemon=True)
                    st["proc"].start()
                    st["status"] = f"running n={len(window_vals)}"
                imgui.same_line()
                if imgui.button(f"Clear##{cell.title}"):
                    st["active_notches_hz"].clear()
                if st["q"] is not None and not st["q"].empty():
                    msg = st["q"].get()
                    st["status"] = "done" if msg.get("ok") else f"error: {msg.get('error')}"
                    st["peaks"] = msg.get("peaks", []) if msg.get("ok") else []
                    if st["proc"] is not None:
                        st["proc"].join(timeout=0.2)
                    st["proc"] = None
                    st["q"] = None
                imgui.text(f"status: {st['status']}")
                for peak_i, (f_hz, amp) in enumerate(st["peaks"][: stream_cfg.fft_top_n], start=1):
                    if imgui.button(f"{peak_i}: {f_hz:.2f} Hz##{cell.title}"):
                        if f_hz in st["active_notches_hz"]:
                            st["active_notches_hz"].remove(f_hz)
                        else:
                            st["active_notches_hz"].append(f_hz)
                    imgui.same_line()
                    imgui.text(f"{amp:.4f}")
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
            if st["proc"] is not None and st["proc"].is_alive():
                st["proc"].terminate()
                st["proc"].join(timeout=0.2)
        impl.shutdown()
        glfw.terminate()


if __name__ == "__main__":
    main()
