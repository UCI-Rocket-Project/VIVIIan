import math
import socket
import struct
import threading
import time
from array import array
from collections import deque
from queue import Empty, Queue

import glfw
import imgui
from imgui.integrations.glfw import GlfwRenderer
from OpenGL import GL


HOST = "127.0.0.1"
PORT = 50200

RAW_BATCH_POINTS = 200
AVG_EVERY_N = 50
PLOT_POINTS = 1200

WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 780
PLOT_WIDTH = 1080
PLOT_HEIGHT = 240


def pack_batch(values: list[float]) -> bytes:
    payload = struct.pack(f"<{len(values)}f", *values)
    return len(payload).to_bytes(4, "big") + payload


def recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def sine_stream_server(stop_event: threading.Event) -> None:
    phase = 0.0
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(0.25)
    client = None
    try:
        while not stop_event.is_set():
            if client is None:
                try:
                    client, _ = srv.accept()
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except socket.timeout:
                    continue
            batch = []
            for _ in range(RAW_BATCH_POINTS):
                phase += 0.03
                y = 0.7 * math.sin(phase * 2.2) + 0.25 * math.sin(phase * 0.4 + 0.7)
                batch.append(y)
            try:
                client.sendall(pack_batch(batch))
            except OSError:
                try:
                    client.close()
                except OSError:
                    pass
                client = None
            time.sleep(0.002)
    finally:
        if client is not None:
            client.close()
        srv.close()


def network_receiver(raw_q: Queue, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            with socket.create_connection((HOST, PORT), timeout=2.0) as sock:
                while not stop_event.is_set():
                    header = recv_exact(sock, 4)
                    payload_len = int.from_bytes(header, "big")
                    payload = recv_exact(sock, payload_len)
                    vals = struct.unpack(f"<{payload_len // 4}f", payload)
                    raw_q.put(vals)
        except Exception:
            time.sleep(0.1)


def averaging_worker(raw_q: Queue, averaged: deque, lock: threading.Lock, stop_event: threading.Event) -> None:
    pending: list[float] = []
    while not stop_event.is_set():
        try:
            vals = raw_q.get(timeout=0.1)
        except Empty:
            continue
        pending.extend(vals)
        while len(pending) >= AVG_EVERY_N:
            chunk = pending[:AVG_EVERY_N]
            del pending[:AVG_EVERY_N]
            avg = float(sum(chunk) / len(chunk))
            with lock:
                averaged.append(avg)


def main() -> None:
    stop_event = threading.Event()
    raw_q: Queue = Queue(maxsize=256)
    averaged = deque([0.0] * PLOT_POINTS, maxlen=PLOT_POINTS)
    averaged_lock = threading.Lock()

    threads = [
        threading.Thread(target=sine_stream_server, args=(stop_event,), daemon=True),
        threading.Thread(target=network_receiver, args=(raw_q, stop_event), daemon=True),
        threading.Thread(target=averaging_worker, args=(raw_q, averaged, averaged_lock, stop_event), daemon=True),
    ]
    for t in threads:
        t.start()

    if not glfw.init():
        raise RuntimeError("Failed to initialize GLFW")
    window = glfw.create_window(WINDOW_WIDTH, WINDOW_HEIGHT, "Network Sine Pipeline Test", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("Failed to create GLFW window")
    glfw.make_context_current(window)
    glfw.swap_interval(1)

    imgui.create_context()
    impl = GlfwRenderer(window)
    imgui.style_colors_classic()

    try:
        while not glfw.window_should_close(window):
            glfw.poll_events()
            impl.process_inputs()
            imgui.new_frame()

            with averaged_lock:
                values = list(averaged)

            vmin = min(values)
            vmax = max(values)
            pad = max(1e-6, (vmax - vmin) * 0.1)

            imgui.set_next_window_position(20, 20, condition=imgui.ONCE)
            imgui.set_next_window_size(1140, 320, condition=imgui.ONCE)
            imgui.begin("Averaged Signal")
            imgui.text(f"Latest avg: {values[-1]:.6f}")
            imgui.text(f"Queue size: {raw_q.qsize()} | Avg every N: {AVG_EVERY_N}")
            imgui.push_style_color(imgui.COLOR_FRAME_BACKGROUND, 0.01, 0.05, 0.01, 1.0)
            imgui.push_style_color(imgui.COLOR_PLOT_LINES, 0.25, 1.00, 0.25, 1.0)
            imgui.push_style_color(imgui.COLOR_PLOT_LINES_HOVERED, 0.65, 1.00, 0.65, 1.0)
            imgui.plot_lines(
                "##avg_signal",
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
        impl.shutdown()
        glfw.terminate()


if __name__ == "__main__":
    main()
