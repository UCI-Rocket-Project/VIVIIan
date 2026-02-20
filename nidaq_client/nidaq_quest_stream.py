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

from config import *


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nidaq_stream")

STREAM_HOST = "0.0.0.0"
STREAM_PORT = 50100
STREAM_BATCH_SAMPLES = 200  # Match network_sine_pipeline_test RAW_BATCH_POINTS.
STREAM_QUEUE_MAX_BATCHES = 128
IDLE_SLEEP_S = 0.00001


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


def make_timestamps(samples_available: int, sampling_rate: float) -> np.ndarray:
    period_ns = (1.0 / sampling_rate) * 1_000_000_000
    duration_ns = (samples_available - 1) * period_ns
    relative_ns = (np.arange(samples_available, dtype=np.float64) * period_ns) - duration_ns
    current_time_ns = time.time_ns()
    timestamps_ns = relative_ns.astype(np.int64) + current_time_ns
    return timestamps_ns.astype("datetime64[ns]")


def chunk_table(timestamps: np.ndarray, data_buffer: np.ndarray, start: int, end: int) -> pa.Table:
    cols = {"timestamps": pa.array(timestamps[start:end])}
    for i, name in enumerate(NIDAQ_CHANNELS):
        cols[name] = pa.array(data_buffer[i, start:end], type=pa.float64())
    return pa.table(cols)


def stream_nidaq_to_network() -> None:
    num_channels = len(NIDAQ_CHANNELS)
    sampling_rate = CHANNEL_SAMPLING_RATE * num_channels
    buffer_size_samples = int(sampling_rate * BUFFER_DURATION_SEC)

    logging.info(
        "Configuring NIDAQ: %d channels @ %d Hz (%d Hz total)",
        num_channels,
        CHANNEL_SAMPLING_RATE,
        sampling_rate,
    )

    stream_server = ArrowBatchStreamServer(STREAM_HOST, STREAM_PORT)
    stop_event = threading.Event()
    send_q, sender_thread, send_stats = start_stream_sender(stream_server, stop_event)
    try:
        with nidaqmx.Task() as task:
            for i, channel_name in enumerate(NIDAQ_CHANNELS):
                physical_channel = f"{NIDAQ_DEVICE}/ai{i}"
                task.ai_channels.add_ai_voltage_chan(
                    physical_channel=physical_channel,
                    name_to_assign_to_channel=channel_name,
                    terminal_config=TerminalConfiguration.DIFF,
                    min_val=-0.02,
                    max_val=0.02,
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
                logger.info("acquire_loop: begin")
                samples_available = task.in_stream.avail_samp_per_chan
                logger.info("acquire_loop: samples_available=%d", samples_available)
                if samples_available <= 0:
                    time.sleep(IDLE_SLEEP_S)
                    continue

                data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)
                logger.info("acquire_loop: reading NI-DAQ")
                reader.read_many_sample(
                    data=data_buffer,
                    number_of_samples_per_channel=samples_available,
                    timeout=10.0,
                )
                stats["read_rows"] += samples_available
                timestamps = make_timestamps(samples_available, sampling_rate)
                logger.info("acquire_loop: read complete rows=%d", samples_available)

                # Send network chunks in fixed-size batches to keep latency predictable.
                for start in range(0, samples_available, STREAM_BATCH_SAMPLES):
                    end = min(start + STREAM_BATCH_SAMPLES, samples_available)
                    logger.info("chunk_loop: start=%d end=%d", start, end)

                    table = chunk_table(timestamps, data_buffer, start, end)
                    # Never block acquisition on network I/O: queue + drop-oldest policy.
                    if send_q.full():
                        try:
                            send_q.get_nowait()
                            stats["drops"] += 1
                        except queue.Empty:
                            pass
                    try:
                        send_q.put_nowait(table)
                        logger.info("chunk_loop: queued rows=%d qsize=%d", table.num_rows, send_q.qsize())
                    except queue.Full:
                        stats["drops"] += 1
                        logger.info("chunk_loop: queue full drop rows=%d", table.num_rows)
                        pass

                now = time.monotonic()
                if now - last_metrics >= 1.0:
                    logging.info(
                        "perf read_rows/s=%d queue=%d drops=%d sent_batches/s=%d sent_rows/s=%d",
                        stats["read_rows"],
                        send_q.qsize(),
                        stats["drops"],
                        send_stats["sent_batches"],
                        send_stats["sent_rows"],
                    )
                    stats = {"read_rows": 0, "drops": 0}
                    send_stats["sent_batches"] = 0
                    send_stats["sent_rows"] = 0
                    last_metrics = now

                time.sleep(IDLE_SLEEP_S)
    finally:
        stop_event.set()
        sender_thread.join(timeout=1.0)
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
