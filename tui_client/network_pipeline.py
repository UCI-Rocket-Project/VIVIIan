import socket
import threading
import time
from collections import deque
from queue import Empty, Queue

import pyarrow as pa


def recv_exact(sock, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def network_receiver(raw_queues: dict[str, Queue], source_column_by_signal: dict[str, str], host: str, port: int, raw_batch_points: int, stop_event: threading.Event, stats: dict) -> None:
    source_to_signals: dict[str, list[str]] = {}
    for sig, col in source_column_by_signal.items():
        source_to_signals.setdefault(col, []).append(sig)

    while not stop_event.is_set():
        try:
            with socket.create_connection((host, port), timeout=2.0) as sock:
                while not stop_event.is_set():
                    payload_len = int.from_bytes(recv_exact(sock, 4), "big")
                    payload = recv_exact(sock, payload_len)
                    table = pa.ipc.open_stream(pa.BufferReader(payload)).read_all()
                    for col_name, signals in source_to_signals.items():
                        if col_name not in table.column_names:
                            continue
                        values = table[col_name].to_numpy(zero_copy_only=False).astype("float64", copy=False)
                        if values.size == 0:
                            continue
                        if values.size > raw_batch_points:
                            values = values[-raw_batch_points:]
                        vals = values.tolist()
                        stats["raw_samples"] += len(vals)
                        for sig in signals:
                            raw_queues[sig].put(vals)
        except Exception:
            time.sleep(0.1)


def averaging_worker(
    raw_q: Queue,
    averaged: deque,
    staging_ring: deque,
    lock: threading.Lock,
    stop_event: threading.Event,
    stats: dict,
    avg_n_ref: dict,
    network_batch_points: int,
    window_ref: dict | None = None,
    worker_key: str | None = None,
    avg_samples_by_worker: dict[str, int] | None = None,
    raw_samples_by_worker: dict[str, int] | None = None,
    stage_samples_by_worker: dict[str, int] | None = None,
) -> None:
    batch_points = max(1, int(network_batch_points))
    while not stop_event.is_set():
        try:
            vals = raw_q.get(timeout=0.1)
        except Empty:
            continue
        avg_n = max(1, int(avg_n_ref["value"]))
        window_s = max(1.0, float(window_ref["value"])) if window_ref is not None else None
        # Keep only the minimal unaveraged staging ring needed for robust chunking across batch/avg boundaries.
        ring_cap = max(
            avg_n + (batch_points % avg_n),
            batch_points + (avg_n % batch_points),
        )
        with lock:
            staging_ring.extend(float(v) for v in vals)
            while len(staging_ring) > ring_cap:
                staging_ring.popleft()
            if worker_key is not None and stage_samples_by_worker is not None:
                stage_samples_by_worker[worker_key] = len(staging_ring)
        if worker_key is not None and raw_samples_by_worker is not None:
            raw_samples_by_worker[worker_key] = raw_samples_by_worker.get(worker_key, 0) + len(vals)

        while True:
            with lock:
                if len(staging_ring) < avg_n:
                    break
                acc = 0.0
                for _ in range(avg_n):
                    acc += staging_ring.popleft()
                avg = float(acc / avg_n)
                now_t = time.time()
                averaged.append((now_t, avg))
                if window_s is not None:
                    cutoff = now_t - window_s
                    while averaged and averaged[0][0] < cutoff:
                        averaged.popleft()
                if worker_key is not None and stage_samples_by_worker is not None:
                    stage_samples_by_worker[worker_key] = len(staging_ring)
            stats["avg_samples"] += 1
            if worker_key is not None and avg_samples_by_worker is not None:
                avg_samples_by_worker[worker_key] = avg_samples_by_worker.get(worker_key, 0) + 1
