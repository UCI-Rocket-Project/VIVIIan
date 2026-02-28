import multiprocessing as mp
import time
from typing import Any

import numpy as np

try:
    from src.data_handeling.shared_ring_buffer import SharedRingBuffer
except ModuleNotFoundError:  # pragma: no cover - convenience for direct file execution
    from shared_ring_buffer import SharedRingBuffer


def generator(columns: int, size: int) -> np.ndarray:
    """Generate synthetic int64 data [timestamp | sensor_0..sensor_n]."""
    rng = np.random.default_rng()
    stepsize = 1500
    timestamps = np.arange(0, stepsize * size, stepsize, dtype=np.int64)
    jitter = rng.integers(-749, 750, size=size, dtype=np.int64)
    timestamps = timestamps + jitter
    noise = rng.integers(
        low=-10_000,
        high=10_000,
        size=(size, columns),
        dtype=np.int64,
    )
    return np.column_stack((timestamps, noise))


def split_sensor_columns(columns: int, num_consumers: int, reader_id: int) -> list[int]:
    """
    Split sensor columns (excluding timestamp column 0) across consumers.
    Returns full-matrix indices (1..columns).
    """
    if num_consumers < 1:
        raise ValueError("num_consumers must be >= 1")
    if reader_id < 0 or reader_id >= num_consumers:
        raise ValueError(f"reader_id must be in [0, {num_consumers - 1}]")

    sensors = np.arange(1, columns + 1, dtype=np.int64)
    splits = np.array_split(sensors, num_consumers)
    return [int(x) for x in splits[reader_id].tolist()]


def _close_ring(ring: SharedRingBuffer) -> None:
    """Release exported pointers before closing shared memory."""
    try:
        ring.ring_buffer.release()
    except Exception:
        pass
    try:
        del ring.ring_buffer
    except Exception:
        pass
    try:
        ring.header = None
    except Exception:
        pass
    try:
        del ring.header
    except Exception:
        pass
    ring.close()


def run_producer(
    total_bytes_target: int,
    num_rows: int,
    name: str,
    ring_size: int,
    num_readers: int,
    columns: int,
    done_event: mp.Event,
    final_write_pos: mp.Value,
    result_queue: mp.Queue,
    poll_sleep_s: float = 0.0002,
) -> None:
    """
    Producer writes raw bytes into the ring until total_bytes_target is reached.

    Shutdown signaling is out-of-band:
    - final_write_pos.value is set to the last committed write position
    - done_event is set after producer completes
    """
    ring = SharedRingBuffer(name=name, create=False, size=ring_size, num_readers=num_readers, reader=0)
    t0 = time.perf_counter()
    bytes_written = 0
    try:
        batch = generator(columns=columns, size=num_rows)
        batch_mv = memoryview(batch).cast("B")
        batch_nbytes = batch_mv.nbytes

        while bytes_written < total_bytes_target:
            remaining_total = total_bytes_target - bytes_written
            send_this_batch = min(batch_nbytes, remaining_total)
            src_off = 0
            while src_off < send_this_batch:
                req = send_this_batch - src_off
                wmv = ring.expose_writer_mem_view(req)
                avail = wmv[2]
                if avail == 0:
                    wmv[0].release()
                    if wmv[1] is not None:
                        wmv[1].release()
                    if done_event.is_set():
                        break
                    time.sleep(poll_sleep_s)
                    continue

                src_chunk = batch_mv[src_off:src_off + avail]
                ring.simple_write(wmv, src_chunk)
                ring.inc_writer_pos(avail)
                src_off += avail
                bytes_written += avail

                # Explicitly release temporary exported pointers.
                wmv[0].release()
                if wmv[1] is not None:
                    wmv[1].release()

        final_write_pos.value = int(ring.get_write_pos())
        done_event.set()
        elapsed = time.perf_counter() - t0
        result_queue.put(
            {
                "role": "producer",
                "elapsed_s": elapsed,
                "bytes": bytes_written,
            }
        )
    finally:
        _close_ring(ring)


def run_consumer(
    name: str,
    ring_size: int,
    num_readers: int,
    reader_id: int,
    num_rows: int,
    columns: int,
    done_event: mp.Event,
    final_write_pos: mp.Value,
    result_queue: mp.Queue,
    sample_rate_hz: float = 1000.0,
    max_chunk_bytes: int = 1 << 20,
    poll_sleep_s: float = 0.0002,
) -> None:
    """
    Consumer drains bytes from the ring using its own reader slot.

    Exit condition:
    - producer signaled done_event, and
    - this reader position reached final_write_pos
    """
    ring = SharedRingBuffer(name=name, create=False, size=ring_size, num_readers=num_readers, reader=reader_id)
    t0 = time.perf_counter()
    bytes_read = 0
    checksum = 0
    selected_columns = split_sensor_columns(columns=columns, num_consumers=num_readers, reader_id=reader_id)
    bytes_per_batch = num_rows * (columns + 1) * np.dtype(np.int64).itemsize
    pending = bytearray()
    frames_processed = 0
    fft_calls = 0
    fft_accum = 0.0

    # Precompute bins once per consumer; useful sanity metric and avoids per-frame setup.
    _ = np.fft.rfftfreq(num_rows, d=1.0 / sample_rate_hz)
    try:
        while True:
            write_pos = int(ring.get_write_pos())
            read_pos = int(ring.header[ring.reader_pos_index])
            unread = write_pos - read_pos

            if unread < 0:
                raise RuntimeError(
                    f"reader {reader_id} observed negative unread: "
                    f"write_pos={write_pos}, read_pos={read_pos}"
                )

            if unread > 0:
                req = min(unread, max_chunk_bytes)
                rmv = ring.expose_reader_mem_view(req)
                got = rmv[2]
                if got > 0:
                    dst = np.empty(got, dtype=np.uint8)
                    ring.simple_read(rmv, dst)
                    ring.inc_reader_pos(got)
                    bytes_read += got
                    # Tiny reduction so the read loop does real work even before FFT framing.
                    checksum = (checksum + int(dst[0])) & 0xFFFFFFFF
                    pending.extend(dst.tobytes())

                    # Decode full batches and run FFT on the consumer's assigned columns.
                    while len(pending) >= bytes_per_batch:
                        frame = memoryview(pending)[:bytes_per_batch]
                        mat = np.frombuffer(frame, dtype=np.int64).reshape(num_rows, columns + 1)
                        if selected_columns:
                            selected = mat[:, selected_columns].astype(np.float64, copy=False)
                            fft_vals = np.fft.rfft(selected, axis=0)
                            fft_calls += 1
                            # Keep a small scalar reduction to prevent accidental dead code.
                            fft_accum += float(np.abs(fft_vals[0]).sum())
                        frames_processed += 1
                        del pending[:bytes_per_batch]

                rmv[0].release()
                if rmv[1] is not None:
                    rmv[1].release()
                continue

            if done_event.is_set() and read_pos >= final_write_pos.value:
                break

            time.sleep(poll_sleep_s)

        elapsed = time.perf_counter() - t0
        result_queue.put(
            {
                "role": f"consumer-{reader_id}",
                "reader_id": reader_id,
                "elapsed_s": elapsed,
                "bytes": bytes_read,
                "checksum": checksum,
                "selected_columns": selected_columns,
                "frames_processed": frames_processed,
                "fft_calls": fft_calls,
                "fft_accum": fft_accum,
                "pending_tail_bytes": len(pending),
            }
        )
    finally:
        _close_ring(ring)


def run_benchmark(
    total_bytes_target: int = 40 * 1024 * 1024 * 1024,
    ring_size: int = 6 * 1024 * 1024 * 1024,
    num_rows: int = 20000,
    columns: int = 8,
    num_consumers: int = 2,
    join_timeout_s: float = 60,
) -> dict[str, Any]:
    """
    Start one producer and N consumers, then report throughput stats.
    """
    if num_consumers < 1:
        raise ValueError("num_consumers must be >= 1")

    ctx = mp.get_context("spawn")
    shm_name = f"rb_throughput_{int(time.time() * 1e6)}"
    bytes_per_batch = num_rows * (columns + 1) * np.dtype(np.int64).itemsize
    target_bytes_effective = (total_bytes_target // bytes_per_batch) * bytes_per_batch
    if target_bytes_effective == 0:
        target_bytes_effective = bytes_per_batch

    ring = SharedRingBuffer(
        name=shm_name,
        create=True,
        size=ring_size,
        num_readers=num_consumers,
        reader=0,
    )
    try:
        done_event = ctx.Event()
        final_write_pos = ctx.Value("Q", 0)
        result_q = ctx.Queue()

        producer = ctx.Process(
            target=run_producer,
            args=(
                total_bytes_target,
                num_rows,
                shm_name,
                ring_size,
                num_consumers,
                columns,
                done_event,
                final_write_pos,
                result_q,
            ),
            daemon=True,
        )

        producer = ctx.Process(
            target=run_producer,
            args=(
                target_bytes_effective,
                num_rows,
                shm_name,
                ring_size,
                num_consumers,
                columns,
                done_event,
                final_write_pos,
                result_q,
            ),
            daemon=True,
        )

        consumers = [
            ctx.Process(
                target=run_consumer,
                args=(
                    shm_name,
                    ring_size,
                    num_consumers,
                    rid,
                    num_rows,
                    columns,
                    done_event,
                    final_write_pos,
                    result_q,
                ),
                daemon=True,
            )
            for rid in range(num_consumers)
        ]

        t_start = time.perf_counter()
        for c in consumers:
            c.start()
        producer.start()

        # Progress-based timeout: only fail if producer makes no write progress
        # for join_timeout_s seconds (instead of absolute wall timeout).
        poll_s = 0.5
        last_progress_t = time.perf_counter()
        last_write_pos = int(ring.get_write_pos())
        while producer.is_alive():
            producer.join(timeout=poll_s)
            if not producer.is_alive():
                break

            # Fail fast if any consumer crashed while producer is still running.
            crashed = [c for c in consumers if c.exitcode not in (None, 0)]
            if crashed:
                producer.terminate()
                crashed_info = ", ".join(f"pid={c.pid} exitcode={c.exitcode}" for c in crashed)
                raise RuntimeError(
                    f"consumer crashed while producer running: {crashed_info}; "
                    f"producer_write_pos={int(ring.get_write_pos())}"
                )

            cur_write_pos = int(ring.get_write_pos())
            if cur_write_pos != last_write_pos:
                last_write_pos = cur_write_pos
                last_progress_t = time.perf_counter()

            if join_timeout_s is not None and (time.perf_counter() - last_progress_t) > join_timeout_s:
                producer.terminate()
                raise TimeoutError(
                    "producer made no write progress before timeout "
                    f"(timeout_s={join_timeout_s}, write_pos={cur_write_pos}, "
                    f"target_bytes={target_bytes_effective})"
                )

        for c in consumers:
            c.join(timeout=join_timeout_s)
            if c.is_alive():
                c.terminate()
                raise TimeoutError(f"consumer pid={c.pid} did not finish before timeout")

        elapsed_total = time.perf_counter() - t_start

        expected_msgs = 1 + num_consumers
        results = []
        for _ in range(expected_msgs):
            results.append(result_q.get(timeout=5))

        producer_stats = next(x for x in results if x["role"] == "producer")
        consumer_stats = sorted((x for x in results if x["role"].startswith("consumer-")), key=lambda x: x["reader_id"])

        producer_mib_s = (producer_stats["bytes"] / (1024 * 1024)) / max(producer_stats["elapsed_s"], 1e-12)
        consumer_mib_s = [
            (c["bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
            for c in consumer_stats
        ]

        summary = {
            "ring_size_bytes": ring_size,
            "target_bytes": target_bytes_effective,
            "num_rows": num_rows,
            "columns": columns,
            "num_consumers": num_consumers,
            "total_wall_s": elapsed_total,
            "producer": producer_stats,
            "consumers": consumer_stats,
            "producer_mib_s": producer_mib_s,
            "consumer_mib_s": consumer_mib_s,
            "final_write_pos": int(final_write_pos.value),
        }

        print("BENCH shared ring throughput")
        print(
            f"ring={ring_size / (1024 * 1024):.1f} MiB  target={target_bytes_effective / (1024 * 1024):.1f} MiB  "
            f"rows={num_rows}  cols={columns + 1}  consumers={num_consumers}"
        )
        print(
            f"producer: {producer_stats['bytes'] / (1024 * 1024):.2f} MiB in "
            f"{producer_stats['elapsed_s']:.3f}s -> {producer_mib_s:.2f} MiB/s"
        )
        for c in consumer_stats:
            rate = (c["bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
            print(
                f"consumer[{c['reader_id']}]: {c['bytes'] / (1024 * 1024):.2f} MiB in "
                f"{c['elapsed_s']:.3f}s -> {rate:.2f} MiB/s  checksum={c['checksum']}  "
                f"fft_calls={c['fft_calls']}  cols={c['selected_columns']}"
            )
        print(f"wall: {elapsed_total:.3f}s")
        return summary
    finally:
        try:
            _close_ring(ring)
        finally:
            try:
                ring.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    run_benchmark()
