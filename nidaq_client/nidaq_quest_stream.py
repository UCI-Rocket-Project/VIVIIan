import logging
import queue
import socket
import threading
import time

import nidaqmx
import numpy as np
import pyarrow as pa
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogMultiChannelReader
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
QUESTDB_FLUSH_ROWS = 5000
QUESTDB_FLUSH_INTERVAL_S = 0.05


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
        logger.debug("server.send_table: try_accept/start rows=%d", table.num_rows)
        self._try_accept()
        if self._client is None:
            logger.debug("server.send_table: no client connected, skip")
            return
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        payload = sink.getvalue().to_pybytes()
        frame = len(payload).to_bytes(4, "big") + payload
        try:
            self._client.sendall(frame)
            logger.debug("server.send_table: sent bytes=%d rows=%d", len(frame), table.num_rows)
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
            logger.debug("sender_loop: dequeued rows=%d qsize=%d", table.num_rows, send_q.qsize())
            server.send_table(table)
            stats["sent_batches"] += 1
            stats["sent_rows"] += table.num_rows

    t = threading.Thread(target=sender_loop, daemon=True)
    t.start()
    return send_q, t, stats


def start_questdb_sender(
    stop_event: threading.Event,
    QUESTDB_CONF: str,
    QUESTDB_TABLE: str,
    flush_rows: int = QUESTDB_FLUSH_ROWS,
    flush_interval_s: float = QUESTDB_FLUSH_INTERVAL_S,
):
    questdb_q: queue.Queue[pa.Table] = queue.Queue(maxsize=STREAM_QUEUE_MAX_BATCHES)
    stats = {
        "sent_batches": 0,
        "sent_rows": 0,
        "errors": 0,
        "flush_calls": 0,
        "flush_ms_total": 0.0,
        "encode_ms_total": 0.0,
    }

    def sender_loop() -> None:
        logger.info("questdb_loop: started")
        try:
            with Sender.from_conf(QUESTDB_CONF) as sender:
                pending_tables: list[pa.Table] = []
                pending_rows = 0
                last_flush_t = time.monotonic()

                def flush_pending() -> None:
                    nonlocal pending_rows, last_flush_t
                    if pending_rows <= 0:
                        return
                    try:
                        if len(pending_tables) == 1:
                            table = pending_tables[0]
                        else:
                            table = pa.concat_tables(pending_tables)
                        t0 = time.perf_counter()
                        df = table.to_pandas()
                        sender.dataframe(df, table_name=QUESTDB_TABLE, at="timestamps")
                        t1 = time.perf_counter()
                        sender.flush()
                        t2 = time.perf_counter()
                        stats["sent_batches"] += 1
                        stats["sent_rows"] += pending_rows
                        stats["flush_calls"] += 1
                        stats["encode_ms_total"] += (t1 - t0) * 1000.0
                        stats["flush_ms_total"] += (t2 - t1) * 1000.0
                    finally:
                        pending_tables.clear()
                        pending_rows = 0
                        last_flush_t = time.monotonic()

                while not stop_event.is_set():
                    try:
                        elapsed = time.monotonic() - last_flush_t
                        timeout_s = max(0.0, flush_interval_s - elapsed) if pending_rows > 0 else 0.05
                        timeout_s = min(0.05, timeout_s)
                        table = questdb_q.get(timeout=timeout_s)
                        pending_tables.append(table)
                        pending_rows += table.num_rows
                    except queue.Empty:
                        pass

                    should_flush = (
                        pending_rows >= max(1, int(flush_rows))
                        or (pending_rows > 0 and (time.monotonic() - last_flush_t) >= max(0.001, float(flush_interval_s)))
                    )
                    if not should_flush:
                        continue
                    try:
                        flush_pending()
                    except IngressError as e:
                        stats["errors"] += 1
                        logger.error("QuestDB Ingress Error: %s", e)
                    except Exception as e:
                        stats["errors"] += 1
                        logger.error("QuestDB General Error: %s", e)
                if pending_rows > 0:
                    try:
                        flush_pending()
                    except IngressError as e:
                        stats["errors"] += 1
                        logger.error("QuestDB Ingress Error (final flush): %s", e)
                    except Exception as e:
                        stats["errors"] += 1
                        logger.error("QuestDB General Error (final flush): %s", e)
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


def stream_nidaq_to_network() -> None:
    nidaq_cfg, db_cfg, stream_cfg, signal_cfgs, graph_cells = load_toml_config(str(ROOT_DIR / "gse2_0.toml"))
    
    nidaq_channels = [cfg.source_column for cfg in signal_cfgs.values()]
    num_channels = len(nidaq_channels)
    
    # Nidaq Stream configs
    STREAM_HOST = stream_cfg.host
    STREAM_PORT = stream_cfg.port
    GUI_TX_BUFFER_LEN = stream_cfg.raw_batch_points
    
    # Nidaq params
    NIDAQ_DEVICE = nidaq_cfg.device
    CHANNEL_SAMPLING_RATE = nidaq_cfg.channel_sampling_rate
    POLLING_FREQ = nidaq_cfg.polling_freq
    NIDAQ_BUFFER_DURATION_SEC = nidaq_cfg.buffer_duration_sec
    
    # Database configs
    QUESTDB_CONF = db_cfg.questdb_conf
    QUESTDB_TABLE = db_cfg.questdb_table

    sampling_rate = CHANNEL_SAMPLING_RATE * num_channels
    buffer_size_samples = int(sampling_rate * NIDAQ_BUFFER_DURATION_SEC)

    logging.info(
        "Configuring NIDAQ: %d channels @ %d Hz (%d Hz total)",
        num_channels,
        CHANNEL_SAMPLING_RATE,
        sampling_rate,
    )

    stream_server = ArrowBatchStreamServer(STREAM_HOST, STREAM_PORT)
    stop_event = threading.Event()
    send_q, sender_thread, send_stats = start_stream_sender(stream_server, stop_event)
    questdb_q, questdb_thread, questdb_stats = start_questdb_sender(
        stop_event,
        QUESTDB_CONF,
        QUESTDB_TABLE,
        flush_rows=max(QUESTDB_FLUSH_ROWS, GUI_TX_BUFFER_LEN),
        flush_interval_s=QUESTDB_FLUSH_INTERVAL_S,
    )
    
    try:
        with nidaqmx.Task() as task:
            for i, channel_name in enumerate(nidaq_channels):
                physical_channel = f"{NIDAQ_DEVICE}/ai{i}"
                task.ai_channels.add_ai_voltage_chan(
                    physical_channel=physical_channel,
                    name_to_assign_to_channel=channel_name,
                    terminal_config=TerminalConfiguration.DIFF,
                    min_val=-5,
                    max_val=5,
                )

            task.timing.cfg_samp_clk_timing(
                rate=sampling_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buffer_size_samples,
            )
            reader = AnalogMultiChannelReader(task.in_stream)

            task.start()
            logging.info("Acquisition started.")
            last_metrics = time.monotonic()
            stats = {
                "read_rows": 0,
                "drops": 0,
            }

            while True:
                logger.debug("acquire_loop: begin")
                samples_available = task.in_stream.avail_samp_per_chan
                logger.debug("acquire_loop: samples_available=%d", samples_available)
                if samples_available <= 0:
                    logger.debug("acquire_loop: 0 samples available after %.5fs", 1 / POLLING_FREQ)
                    time.sleep(1 / POLLING_FREQ)
                    continue

                data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)
                logger.debug("acquire_loop: reading NI-DAQ (%d samples available)", samples_available)
                reader.read_many_sample(
                    data=data_buffer,
                    number_of_samples_per_channel=samples_available,
                    timeout=10.0,
                )
                stats["read_rows"] += samples_available
                timestamps = make_timestamps(samples_available, sampling_rate)
                logger.debug("acquire_loop: read samples")

                # Send network chunks in fixed-size batches to keep latency predictable.
                for start in range(0, samples_available, GUI_TX_BUFFER_LEN):
                    end = min(start + GUI_TX_BUFFER_LEN, samples_available)
                    logger.debug("chunk_loop: start=%d end=%d", start, end)

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
                    flush_calls = max(1, int(questdb_stats.get("flush_calls", 0)))
                    avg_flush_ms = float(questdb_stats.get("flush_ms_total", 0.0)) / flush_calls
                    avg_encode_ms = float(questdb_stats.get("encode_ms_total", 0.0)) / flush_calls
                    logging.info(
                        "perf read_rows/s=%d stream_q=%d quest_q=%d drops=%d "
                        "stream_batches/s=%d stream_rows/s=%d "
                        "quest_batches/s=%d quest_rows/s=%d quest_errs=%d "
                        "quest_avg_encode_ms=%.2f quest_avg_flush_ms=%.2f",
                        stats["read_rows"],
                        send_q.qsize(),
                        questdb_q.qsize(),
                        stats["drops"],
                        send_stats["sent_batches"],
                        send_stats["sent_rows"],
                        questdb_stats["sent_batches"],
                        questdb_stats["sent_rows"],
                        questdb_stats.get("errors", 0),
                        avg_encode_ms,
                        avg_flush_ms,
                    )
                    stats = {"read_rows": 0, "drops": 0}
                    send_stats["sent_batches"] = 0
                    send_stats["sent_rows"] = 0
                    questdb_stats["sent_batches"] = 0
                    questdb_stats["sent_rows"] = 0
                    questdb_stats["errors"] = 0
                    questdb_stats["flush_calls"] = 0
                    questdb_stats["flush_ms_total"] = 0.0
                    questdb_stats["encode_ms_total"] = 0.0
                    last_metrics = now

                time.sleep(1 / POLLING_FREQ)
    finally:
        stop_event.set()
        sender_thread.join(timeout=1.0)
        questdb_thread.join(timeout=1.0)
        stream_server.close()


if __name__ == "__main__":
    while True:
        try:
            stream_nidaq_to_network()
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
