import socket
import threading
import time
import logging

import numpy as np
import pyarrow as pa

from stream_buffer import SharedRingBuffer


STREAM_HOST = "127.0.0.1"
STREAM_PORT = 50100
MAX_ROWS_PER_BATCH = 2000
AGGREGATE_EVERY_N_POINTS = 100
logger = logging.getLogger("arrow_stream_reader")


class ReaderMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.batches = 0
        self.rows = 0
        self.bytes = 0
        self.errors = 0
        self._last_snapshot = {"batches_per_s": 0, "rows_per_s": 0, "bytes_per_s": 0, "errors_total": 0}
        self._last_ts = time.monotonic()

    def add_batch(self, rows: int, payload_bytes: int):
        with self._lock:
            self.batches += 1
            self.rows += rows
            self.bytes += payload_bytes

    def add_error(self):
        with self._lock:
            self.errors += 1

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            if now - self._last_ts >= 1.0:
                self._last_snapshot = {
                    "batches_per_s": self.batches,
                    "rows_per_s": self.rows,
                    "bytes_per_s": self.bytes,
                    "errors_total": self.errors,
                }
                self.batches = 0
                self.rows = 0
                self.bytes = 0
                self._last_ts = now
            return dict(self._last_snapshot)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _aggregate_numeric_columns(table: pa.Table, n: int) -> tuple[list[str], dict[str, list[float]], int]:
    """Aggregate numeric columns by averaging each N-point chunk using NumPy."""
    if table.num_rows == 0:
        return table.column_names, {}, 0
    series_arrays: dict[str, list[float]] = {}
    out_rows = 0
    for col_name in table.column_names:
        if col_name.lower() == "timestamps":
            continue
        col = table[col_name]
        arr = col.to_numpy(zero_copy_only=False)
        if arr.size == 0:
            continue
        arr = arr.astype(np.float64, copy=False)
        if n > 1 and arr.size >= n:
            usable = (arr.size // n) * n
            reduced = arr[:usable].reshape(-1, n).mean(axis=1)
            if usable < arr.size:
                tail = arr[usable:]
                reduced = np.concatenate([reduced, np.array([tail.mean()], dtype=np.float64)])
        elif n > 1:
            reduced = np.array([arr.mean()], dtype=np.float64)
        else:
            reduced = arr
        series_arrays[col_name] = reduced.tolist()
        out_rows = max(out_rows, reduced.size)
    return table.column_names, series_arrays, int(out_rows)


def read_stream_forever(shared: SharedRingBuffer, host: str = STREAM_HOST, port: int = STREAM_PORT, metrics: ReaderMetrics | None = None) -> None:
    while True:
        try:
            logger.info("reader: connecting host=%s port=%d", host, port)
            with socket.create_connection((host, port), timeout=5.0) as sock:
                logger.info("reader: connected")
                while True:
                    logger.info("reader: waiting header")
                    header = _recv_exact(sock, 4)
                    payload_len = int.from_bytes(header, "big")
                    logger.info("reader: payload_len=%d", payload_len)
                    payload = _recv_exact(sock, payload_len)

                    logger.info("reader: decode arrow start")
                    table = pa.ipc.open_stream(pa.BufferReader(payload)).read_all()
                    logger.info("reader: decode arrow done rows=%d cols=%d", table.num_rows, table.num_columns)
                    columns, series_arrays, reduced_rows = _aggregate_numeric_columns(table, AGGREGATE_EVERY_N_POINTS)
                    logger.info("reader: aggregated reduced_rows=%d signals=%d", reduced_rows, len(series_arrays))
                    if series_arrays:
                        shared.push_series_arrays(series_arrays)
                        logger.info("reader: pushed series")
                    if metrics is not None:
                        metrics.add_batch(reduced_rows, payload_len)
        except Exception:
            logger.exception("reader: exception, retrying")
            if metrics is not None:
                metrics.add_error()
            time.sleep(0.25)


def start_reader_thread(shared: SharedRingBuffer, host: str = STREAM_HOST, port: int = STREAM_PORT, metrics: ReaderMetrics | None = None) -> threading.Thread:
    t = threading.Thread(target=read_stream_forever, args=(shared, host, port, metrics), daemon=True)
    t.start()
    return t
