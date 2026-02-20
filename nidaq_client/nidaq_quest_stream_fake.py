import argparse
import logging
import queue
import socket
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nidaq_stream_fake")

STREAM_QUEUE_MAX_BATCHES = 128


@dataclass
class RuntimeConfig:
    host: str
    port: int
    raw_batch_points: int
    channel_sampling_rate: float
    channel_names: list[str]
    noise_std: float
    base_hz: float
    hz_step: float
    phase_step: float


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "gse2_0.toml"


def load_runtime_config(config_path: str) -> RuntimeConfig:
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    stream_cfg = cfg.get("nidaq_stream", {})
    nidaq_cfg = cfg.get("nidaq", {})
    fake_cfg = nidaq_cfg.get("fake", {})
    signal_cfgs = cfg.get("nidaq_signals", [])

    channel_names: list[str] = []
    for sig in signal_cfgs:
        source = str(sig.get("source_column", "")).strip()
        if source and source not in channel_names:
            channel_names.append(source)
    if not channel_names:
        channel_names = ["Load Cell", "PTS"]

    return RuntimeConfig(
        host=str(stream_cfg.get("host", "0.0.0.0")),
        port=int(stream_cfg.get("port", 50100)),
        raw_batch_points=max(1, int(stream_cfg.get("raw_batch_points", 200))),
        channel_sampling_rate=float(nidaq_cfg.get("channel_sampling_rate", 50000.0)),
        channel_names=channel_names,
        noise_std=float(fake_cfg.get("noise_std", 0.0008)),
        base_hz=float(fake_cfg.get("base_hz", 2.0)),
        hz_step=float(fake_cfg.get("hz_step", 0.6)),
        phase_step=float(fake_cfg.get("phase_step", 0.5)),
    )


class ArrowBatchStreamServer:
    """Minimal TCP server that broadcasts Arrow IPC frames to one client."""

    def __init__(self, host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        self._sock.setblocking(False)
        self._client = None
        logging.info("Arrow stream server listening on %s:%s", host, port)

    def _try_accept(self) -> None:
        if self._client is not None:
            return
        try:
            client, addr = self._sock.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._client = client
            logging.info("Arrow stream client connected: %s", addr)
        except BlockingIOError:
            return

    def send_table(self, table: pa.Table) -> None:
        self._try_accept()
        if self._client is None:
            return
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        payload = sink.getvalue().to_pybytes()
        frame = len(payload).to_bytes(4, "big") + payload
        try:
            self._client.sendall(frame)
        except OSError:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._sock.close()


def start_stream_sender(server: ArrowBatchStreamServer, stop_event: threading.Event):
    send_q: queue.Queue[pa.Table] = queue.Queue(maxsize=STREAM_QUEUE_MAX_BATCHES)
    stats = {"sent_batches": 0, "sent_rows": 0}

    def sender_loop() -> None:
        while not stop_event.is_set():
            try:
                table = send_q.get(timeout=0.05)
            except queue.Empty:
                continue
            server.send_table(table)
            stats["sent_batches"] += 1
            stats["sent_rows"] += table.num_rows

    t = threading.Thread(target=sender_loop, daemon=True)
    t.start()
    return send_q, t, stats


def make_timestamps(samples_available: int, sampling_rate: float) -> np.ndarray:
    period_ns = (1.0 / sampling_rate) * 1_000_000_000
    duration_ns = (samples_available - 1) * period_ns
    relative_ns = (np.arange(samples_available, dtype=np.float64) * period_ns) - duration_ns
    current_time_ns = time.time_ns()
    timestamps_ns = relative_ns.astype(np.int64) + current_time_ns
    return timestamps_ns.astype("datetime64[ns]")


def chunk_table(timestamps: np.ndarray, data_buffer: np.ndarray, channel_names: list[str]) -> pa.Table:
    cols = {"timestamps": pa.array(timestamps)}
    for i, name in enumerate(channel_names):
        cols[name] = pa.array(data_buffer[i, :], type=pa.float64())
    return pa.table(cols)


def stream_fake_to_network(rt: RuntimeConfig) -> None:
    num_channels = len(rt.channel_names)
    sampling_rate = rt.channel_sampling_rate * num_channels
    rng = np.random.default_rng(seed=42)
    phase = np.array([i * rt.phase_step for i in range(num_channels)], dtype=np.float64)
    freqs = np.array([rt.base_hz + i * rt.hz_step for i in range(num_channels)], dtype=np.float64)

    logging.info(
        "Starting fake stream channels=%s per_channel_hz=%.1f total_hz=%.1f batch=%d",
        rt.channel_names,
        rt.channel_sampling_rate,
        sampling_rate,
        rt.raw_batch_points,
    )

    stream_server = ArrowBatchStreamServer(rt.host, rt.port)
    stop_event = threading.Event()
    send_q, sender_thread, send_stats = start_stream_sender(stream_server, stop_event)
    last_metrics = time.monotonic()
    stats = {"gen_rows": 0, "drops": 0}
    next_tick = time.monotonic()

    try:
        while True:
            n = rt.raw_batch_points
            t = np.arange(n, dtype=np.float64) / max(1.0, sampling_rate)
            data_buffer = np.empty((num_channels, n), dtype=np.float64)
            for i in range(num_channels):
                ch_name = rt.channel_names[i].strip().lower()
                if "load" in ch_name:
                    # Slow, smoother load-cell-like motion with low-frequency drift.
                    wave = 0.015 * np.sin(2.0 * np.pi * 1.2 * t + phase[i])
                    drift = 0.004 * np.sin(2.0 * np.pi * 0.12 * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, rt.noise_std * 0.6, size=n)
                    data_buffer[i, :] = wave + drift + noise
                elif "pts" in ch_name:
                    # Sharper, higher-frequency structure for pressure/PTS-like behavior.
                    wave = 0.006 * np.sin(2.0 * np.pi * 9.0 * t + phase[i])
                    harm = 0.005 * np.sin(2.0 * np.pi * 22.0 * t + 0.3 * phase[i])
                    saw_phase = ((1.8 * t + (phase[i] / (2.0 * np.pi))) % 1.0)
                    saw = (2.0 * saw_phase) - 1.0
                    burst = 0.003 * saw
                    noise = rng.normal(0.0, rt.noise_std * 1.3, size=n)
                    data_buffer[i, :] = wave + harm + burst + noise
                else:
                    # Fallback synthetic profile.
                    wave = 0.01 * np.sin(2.0 * np.pi * freqs[i] * t + phase[i])
                    harm = 0.004 * np.sin(2.0 * np.pi * (freqs[i] * 0.27 + 0.8) * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, rt.noise_std, size=n)
                    data_buffer[i, :] = wave + harm + noise
            phase = (phase + (2.0 * np.pi * freqs * (n / max(1.0, sampling_rate)))) % (2.0 * np.pi)

            timestamps = make_timestamps(n, sampling_rate)
            table = chunk_table(timestamps, data_buffer, rt.channel_names)
            if send_q.full():
                try:
                    send_q.get_nowait()
                    stats["drops"] += 1
                except queue.Empty:
                    pass
            try:
                send_q.put_nowait(table)
            except queue.Full:
                stats["drops"] += 1
            stats["gen_rows"] += n

            now = time.monotonic()
            if now - last_metrics >= 1.0:
                logging.info(
                    "perf gen_rows/s=%d queue=%d drops=%d sent_batches/s=%d sent_rows/s=%d",
                    stats["gen_rows"],
                    send_q.qsize(),
                    stats["drops"],
                    send_stats["sent_batches"],
                    send_stats["sent_rows"],
                )
                stats = {"gen_rows": 0, "drops": 0}
                send_stats["sent_batches"] = 0
                send_stats["sent_rows"] = 0
                last_metrics = now

            batch_dt_s = n / max(1.0, sampling_rate)
            next_tick += batch_dt_s
            sleep_s = next_tick - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_tick = time.monotonic()
    finally:
        stop_event.set()
        sender_thread.join(timeout=1.0)
        stream_server.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fake NI-DAQ Arrow stream publisher")
    parser.add_argument("--config", default=str(_default_config_path()), help="Path to gse2_0.toml")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    rt = load_runtime_config(args.config)
    while True:
        try:
            stream_fake_to_network(rt)
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
