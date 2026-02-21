import logging
import queue
import socket
import threading
import time
import tomllib
import numpy as np
import pyarrow as pa
import pandas as pd
from questdb.ingress import Sender, IngressError

import sys
from pathlib import Path

# Add shared_config to sys.path so we can import config_parser
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared_config.config_parser import load_toml_config


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nidaq_stream")

STREAM_HOST = "0.0.0.0"
STREAM_PORT = 50100
IDLE_SLEEP_S = 0.00001
STREAM_QUEUE_MAX_BATCHES = 128


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
        logger.info("server.send_table: try_accept/start rows=%d", table.num_rows)
        self._try_accept()
        if self._client is None:
            logger.info("server.send_table: no client connected, skip")
            return
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        payload = sink.getvalue().to_pybytes()
        frame = len(payload).to_bytes(4, "big") + payload
        try:
            self._client.sendall(frame)
            logger.info("server.send_table: sent bytes=%d rows=%d", len(frame), table.num_rows)
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
        logger.info("sender_loop: started")
        while not stop_event.is_set():
            try:
                table = send_q.get(timeout=0.05)
            except queue.Empty:
                continue
            logger.info("sender_loop: dequeued rows=%d qsize=%d", table.num_rows, send_q.qsize())
            server.send_table(table)
            stats["sent_batches"] += 1
            stats["sent_rows"] += table.num_rows

    t = threading.Thread(target=sender_loop, daemon=True)
    t.start()
    return send_q, t, stats


def start_questdb_sender(stop_event: threading.Event, QUESTDB_CONF: str, QUESTDB_TABLE: str):
    questdb_q: queue.Queue[pa.Table] = queue.Queue(maxsize=STREAM_QUEUE_MAX_BATCHES)
    stats = {"sent_batches": 0, "sent_rows": 0, "errors": 0}

    def sender_loop() -> None:
        logger.info("questdb_loop: started")
        try:
            with Sender.from_conf(QUESTDB_CONF) as sender:
                while not stop_event.is_set():
                    try:
                        table = questdb_q.get(timeout=0.05)
                    except queue.Empty:
                        continue

                    # logger.info("questdb_loop: dequeued rows=%d qsize=%d", table.num_rows, questdb_q.qsize())
                    try:
                        df = table.to_pandas()
                        sender.dataframe(df, table_name=QUESTDB_TABLE, at='timestamps')
                        sender.flush()
                        stats["sent_batches"] += 1
                        stats["sent_rows"] += table.num_rows
                    except IngressError as e:
                        stats["errors"] += 1
                        logger.error(f"QuestDB Ingress Error: {e}")
                    except Exception as e:
                        stats["errors"] += 1
                        logger.error(f"QuestDB General Error: {e}")
        except Exception as e:
            logger.error(f"QuestDB connection failed: {e}")

    t = threading.Thread(target=sender_loop, daemon=True)
    t.start()
    return questdb_q, t, stats


def make_timestamps(samples_available: int, sampling_rate: float) -> np.ndarray:
    period_ns = (1.0 / sampling_rate) * 1_000_000_000
    duration_ns = (samples_available - 1) * period_ns
    relative_ns = (np.arange(samples_available, dtype=np.float64) * period_ns) - duration_ns
    current_time_ns = time.time_ns()
    timestamps_ns = relative_ns.astype(np.int64) + current_time_ns
    return timestamps_ns.astype("datetime64[ns]")


def chunk_table(timestamps: np.ndarray, data_buffer: np.ndarray, start: int, end: int, nidaq_channels: list[str]) -> pa.Table:
    cols = {"timestamps": pa.array(timestamps[start:end])}
    for i, channel_name in enumerate(nidaq_channels):
        cols[channel_name] = pa.array(data_buffer[i, start:end], type=pa.float64())
    return pa.table(cols)


def stream_fake_to_network() -> None:
    nidaq_cfg, db_cfg, stream_cfg, signal_cfgs, graph_cells = load_toml_config(str(ROOT_DIR / "gse2_0.toml"))
    
    nidaq_channels = [cfg.source_column for cfg in signal_cfgs.values()]
    num_channels = len(nidaq_channels)
    
    # Nidaq Stream configs
    STREAM_HOST = stream_cfg.host
    STREAM_PORT = stream_cfg.port
    GUI_TX_BUFFER_LEN = stream_cfg.raw_batch_points
    
    # Nidaq params
    CHANNEL_SAMPLING_RATE = nidaq_cfg.channel_sampling_rate
    POLLING_FREQ = nidaq_cfg.polling_freq
    
    # Database configs
    QUESTDB_CONF = f"http::addr={db_cfg.server}:9009;"
    QUESTDB_TABLE = db_cfg.questdb_table

    sampling_rate = CHANNEL_SAMPLING_RATE * num_channels
    
    # Fake params
    with open(str(ROOT_DIR / "gse2_0.toml"), "rb") as f:
        cfg = tomllib.load(f)
    fake_cfg = cfg.get("nidaq", {}).get("fake", {})
    noise_std = float(fake_cfg.get("noise_std", 0.0008))
    base_hz = float(fake_cfg.get("base_hz", 2.0))
    hz_step = float(fake_cfg.get("hz_step", 0.6))
    phase_step = float(fake_cfg.get("phase_step", 0.5))

    rng = np.random.default_rng(seed=42)
    phase = np.array([i * phase_step for i in range(num_channels)], dtype=np.float64)
    freqs = np.array([base_hz + i * hz_step for i in range(num_channels)], dtype=np.float64)

    logging.info(
        "Configuring Fake NIDAQ: %d channels @ %d Hz (%d Hz total)",
        num_channels,
        CHANNEL_SAMPLING_RATE,
        sampling_rate,
    )

    stream_server = ArrowBatchStreamServer(STREAM_HOST, STREAM_PORT)
    stop_event = threading.Event()
    send_q, sender_thread, send_stats = start_stream_sender(stream_server, stop_event)
    questdb_q, questdb_thread, questdb_stats = start_questdb_sender(stop_event, QUESTDB_CONF, QUESTDB_TABLE)
    
    try:
        logging.info("Fake Acquisition started.")
        last_metrics = time.monotonic()
        stats = {
            "read_rows": 0,
            "drops": 0,
        }
        next_tick = time.monotonic()

        while True:
            logger.info("acquire_loop: begin (fake)")
            
            # Simulate waiting for samples
            n = GUI_TX_BUFFER_LEN
            batch_dt_s = n / max(1.0, sampling_rate)
            next_tick += batch_dt_s
            sleep_s = next_tick - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_tick = time.monotonic()

            samples_available = n
            
            t = np.arange(n, dtype=np.float64) / max(1.0, sampling_rate)
            data_buffer = np.empty((num_channels, n), dtype=np.float64)
            for i in range(num_channels):
                ch_name = nidaq_channels[i].strip().lower()
                if "load" in ch_name:
                    # Slow, smoother load-cell-like motion with low-frequency drift.
                    wave = 0.015 * np.sin(2.0 * np.pi * 1.2 * t + phase[i])
                    drift = 0.004 * np.sin(2.0 * np.pi * 0.12 * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, noise_std * 0.6, size=n)
                    data_buffer[i, :] = wave + drift + noise
                elif "pts" in ch_name:
                    # Sharper, higher-frequency structure for pressure/PTS-like behavior.
                    wave = 0.006 * np.sin(2.0 * np.pi * 9.0 * t + phase[i])
                    harm = 0.005 * np.sin(2.0 * np.pi * 22.0 * t + 0.3 * phase[i])
                    saw_phase = ((1.8 * t + (phase[i] / (2.0 * np.pi))) % 1.0)
                    saw = (2.0 * saw_phase) - 1.0
                    burst = 0.003 * saw
                    noise = rng.normal(0.0, noise_std * 1.3, size=n)
                    data_buffer[i, :] = wave + harm + burst + noise
                else:
                    # Fallback synthetic profile.
                    wave = 0.01 * np.sin(2.0 * np.pi * freqs[i] * t + phase[i])
                    harm = 0.004 * np.sin(2.0 * np.pi * (freqs[i] * 0.27 + 0.8) * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, noise_std, size=n)
                    data_buffer[i, :] = wave + harm + noise
            phase = (phase + (2.0 * np.pi * freqs * (n / max(1.0, sampling_rate)))) % (2.0 * np.pi)

            logger.info("acquire_loop: generated NI-DAQ (%d samples)", samples_available)
            stats["read_rows"] += samples_available
            timestamps = make_timestamps(samples_available, sampling_rate)
            logger.info("acquire_loop: generated samples")

            # Send network chunks in fixed-size batches to keep latency predictable.
            for start in range(0, samples_available, GUI_TX_BUFFER_LEN):
                end = min(start + GUI_TX_BUFFER_LEN, samples_available)
                logger.info("chunk_loop: start=%d end=%d", start, end)

                table = chunk_table(timestamps, data_buffer, start, end, nidaq_channels)
                # Never block acquisition on network I/O: queue + drop-oldest policy.
                for target_q, q_name in [(send_q, "stream"), (questdb_q, "questdb")]:
                    if target_q.full():
                        try:
                            target_q.get_nowait()
                            stats["drops"] += 1
                        except queue.Empty:
                            pass
                    try:
                        target_q.put_nowait(table)
                        logger.debug("chunk_loop: %s queued rows=%d qsize=%d", q_name, table.num_rows, target_q.qsize())
                    except queue.Full:
                        stats["drops"] += 1
                        logger.info("chunk_loop: %s queue full drop rows=%d", q_name, table.num_rows)
                        pass

            now = time.monotonic()
            if now - last_metrics >= 1.0:
                logging.info(
                    "perf read_rows/s=%d stream_q=%d quest_q=%d drops=%d "
                    "stream_batches/s=%d stream_rows/s=%d "
                    "quest_batches/s=%d quest_rows/s=%d quest_errs=%d",
                    stats["read_rows"],
                    send_q.qsize(),
                    questdb_q.qsize(),
                    stats["drops"],
                    send_stats["sent_batches"],
                    send_stats["sent_rows"],
                    questdb_stats["sent_batches"],
                    questdb_stats["sent_rows"],
                    questdb_stats.get("errors", 0)
                )
                stats = {"read_rows": 0, "drops": 0}
                send_stats["sent_batches"] = 0
                send_stats["sent_rows"] = 0
                questdb_stats["sent_batches"] = 0
                questdb_stats["sent_rows"] = 0
                questdb_stats["errors"] = 0
                last_metrics = now

    finally:
        stop_event.set()
        sender_thread.join(timeout=1.0)
        questdb_thread.join(timeout=1.0)
        stream_server.close()


if __name__ == "__main__":
    while True:
        try:
            stream_fake_to_network()
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
