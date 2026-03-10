"""
Run with:
python -B -m tests.test_ring_buffer_throughput

Examples:
python -B -m tests.test_ring_buffer_throughput --mode single --single-consumers 2 --single-ring-mib 128 --single-rows 10000 --columns 8 --fft-backend numpy
python -B -m tests.test_ring_buffer_throughput --mode single --single-consumers 2 --single-ring-mib 128 --single-rows 10000 --columns 8 --fft-backend torch --torch-device cuda
python -B -m tests.test_ring_buffer_throughput --mode single --single-consumers 2 --single-ring-mib 128 --single-rows 10000 --columns 8 --fft-backend jax
python -B -m tests.test_ring_buffer_throughput --mode search --columns 8 --target-mib 512 --consumers 1,2,4,8 --ring-mib 64,128,256,512 --rows 4096,8192,16384 --repeats 5 --top-k 5 --fft-backend auto
python -B -m tests.test_ring_buffer_throughput --mode generator --columns 8 --generator-rows 10000 --generator-repeats 200
"""

from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp
import statistics
import time
import uuid
from typing import Any

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    np = None

try:
    from viviian.ipc.ring_buffer import SharedRingBuffer
except ModuleNotFoundError:  # pragma: no cover - convenience for direct file execution
    SharedRingBuffer = None


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


def _build_fft_executor(backend: str, torch_device: str = "auto"):
    """
    Return (fft_executor, backend_used).

    fft_executor accepts a 2D numpy array shaped [num_rows, selected_channels]
    and returns a scalar reduction from the FFT result.
    """
    requested = backend.strip().lower()
    if requested not in {"auto", "numpy", "torch", "jax"}:
        raise ValueError(f"unsupported fft backend '{backend}'")

    def _numpy_exec(selected: np.ndarray) -> float:
        fft_vals = np.fft.rfft(selected, axis=0)
        return float(np.abs(fft_vals[0]).sum())

    if requested == "numpy":
        return _numpy_exec, "numpy"

    if requested in {"auto", "torch"}:
        try:
            import torch

            device = torch_device.strip().lower()
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            elif device == "cuda" and not torch.cuda.is_available():
                device = "cpu"
            elif device not in {"cpu", "cuda"}:
                raise ValueError(f"unsupported torch device '{torch_device}'")

            def _torch_exec(selected: np.ndarray) -> float:
                t = torch.as_tensor(selected, dtype=torch.float32, device=device)
                fft_vals = torch.fft.rfft(t, dim=0)
                return float(torch.abs(fft_vals[0]).sum().item())

            return _torch_exec, f"torch:{device}"
        except Exception as exc:
            if requested == "torch":
                raise RuntimeError(f"requested torch backend but initialization failed: {exc}") from exc

    if requested in {"auto", "jax"}:
        try:
            import jax
            import jax.numpy as jnp

            @jax.jit
            def _jax_rfft(x):
                return jnp.fft.rfft(x, axis=0)

            def _jax_exec(selected: np.ndarray) -> float:
                x = jnp.asarray(selected, dtype=jnp.float32)
                fft_vals = _jax_rfft(x)
                return float(jnp.abs(fft_vals[0]).sum().item())

            return _jax_exec, "jax"
        except Exception as exc:
            if requested == "jax":
                raise RuntimeError(f"requested jax backend but initialization failed: {exc}") from exc

    return _numpy_exec, "numpy"


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
    ring = SharedRingBuffer(
        name=name,
        create=False,
        size=ring_size,
        num_readers=num_readers,
        reader=0,
        cache_align=True,
        cache_size=64,
    )
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
    fft_backend: str = "auto",
    torch_device: str = "auto",
) -> None:
    """
    Consumer drains bytes from the ring using its own reader slot.

    Exit condition:
    - producer signaled done_event, and
    - this reader position reached final_write_pos
    """
    ring = SharedRingBuffer(
        name=name,
        create=False,
        size=ring_size,
        num_readers=num_readers,
        reader=reader_id,
        cache_align=True,
        cache_size=64,
    )
    t0 = time.perf_counter()
    bytes_read = 0
    checksum = 0
    selected_columns = split_sensor_columns(columns=columns, num_consumers=num_readers, reader_id=reader_id)
    selected_channel_count = len(selected_columns)
    bytes_per_frame = num_rows * (columns + 1) * np.dtype(np.int64).itemsize
    frame_buf = np.empty(bytes_per_frame, dtype=np.uint8)
    frame_fill = 0
    frames_processed = 0
    fft_calls = 0
    fft_accum = 0.0
    fft_input_samples = 0
    fft_input_bytes = 0
    fft_execute, fft_backend_used = _build_fft_executor(backend=fft_backend, torch_device=torch_device)

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

                    # Assemble fixed-size frames and run FFT on assigned columns.
                    src_off = 0
                    while src_off < got:
                        take = min(bytes_per_frame - frame_fill, got - src_off)
                        frame_buf[frame_fill:frame_fill + take] = dst[src_off:src_off + take]
                        frame_fill += take
                        src_off += take

                        if frame_fill == bytes_per_frame:
                            mat = frame_buf.view(np.int64).reshape(num_rows, columns + 1)
                            if selected_columns:
                                selected = mat[:, selected_columns]
                                fft_calls += 1
                                fft_input_samples += int(selected.size)
                                fft_input_bytes += int(selected.nbytes)
                                # Keep a small scalar reduction to prevent accidental dead code.
                                fft_accum += fft_execute(selected)
                            frames_processed += 1
                            frame_fill = 0

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
                "selected_channel_count": selected_channel_count,
                "frames_processed": frames_processed,
                "fft_calls": fft_calls,
                "fft_accum": fft_accum,
                "fft_input_samples": fft_input_samples,
                "fft_input_bytes": fft_input_bytes,
                "fft_backend_requested": fft_backend,
                "fft_backend_used": fft_backend_used,
                "pending_tail_bytes": frame_fill,
            }
        )
    finally:
        _close_ring(ring)


def run_benchmark(
    total_bytes_target: int =  1024 * 1024 * 1024,
    ring_size: int = 128 * 1024 * 1024,
    num_rows: int = 10000,
    columns: int = 8,
    num_consumers: int = 2,
    join_timeout_s: float = 60,
    print_summary: bool = True,
    fft_backend: str = "auto",
    torch_device: str = "auto",
) -> dict[str, Any]:
    """
    Start one producer and N consumers, then report throughput stats.
    """
    if num_consumers < 1:
        raise ValueError("num_consumers must be >= 1")

    ctx = mp.get_context("spawn")
    shm_name = f"rt{uuid.uuid4().hex[:10]}"
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
        cache_align=True,
        cache_size=64,
    )
    try:
        done_event = ctx.Event()
        final_write_pos = ctx.Value("Q", 0)
        result_q = ctx.Queue()

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
                    1000.0,
                    1 << 20,
                    0.0002,
                    fft_backend,
                    torch_device,
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
        consumer_drain_mib_s = [
            (c["bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
            for c in consumer_stats
        ]
        consumer_fft_mib_s = [
            (c["fft_input_bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
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
            # Throughput by bytes actually FFT'd per consumer (depends on assigned channels).
            "consumer_mib_s": consumer_fft_mib_s,
            "consumer_drain_mib_s": consumer_drain_mib_s,
            "consumer_fft_mib_s": consumer_fft_mib_s,
            "final_write_pos": int(final_write_pos.value),
            "fft_backend": fft_backend,
            "torch_device": torch_device,
        }

        if print_summary:
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
                drain_rate = (c["bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
                fft_rate = (c["fft_input_bytes"] / (1024 * 1024)) / max(c["elapsed_s"], 1e-12)
                print(
                    f"consumer[{c['reader_id']}]: {c['bytes'] / (1024 * 1024):.2f} MiB in "
                    f"{c['elapsed_s']:.3f}s -> drain={drain_rate:.2f} MiB/s, fft={fft_rate:.2f} MiB/s  "
                    f"checksum={c['checksum']}  fft_calls={c['fft_calls']}  "
                    f"fft_input={c['fft_input_bytes'] / (1024 * 1024):.2f} MiB  "
                    f"backend={c['fft_backend_used']}  cols={c['selected_columns']}"
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


def _parse_int_csv(value: str) -> list[int]:
    parts = [x.strip() for x in value.split(",") if x.strip()]
    if not parts:
        raise ValueError("expected at least one integer")
    out = [int(x) for x in parts]
    if any(x <= 0 for x in out):
        raise ValueError("all values must be > 0")
    return out


def run_benchmark_search(
    total_bytes_target: int,
    columns: int,
    consumer_options: list[int],
    ring_size_options: list[int],
    num_rows_options: list[int],
    repeats: int = 5,
    join_timeout_s: float = 60,
    top_k: int = 5,
    fft_backend: str = "auto",
    torch_device: str = "auto",
) -> dict[str, Any]:
    """
    Sweep combinations and report the best config by median total FFT throughput.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    combos = list(itertools.product(consumer_options, ring_size_options, num_rows_options))
    total_trials = len(combos) * repeats
    trial_idx = 0
    rows: list[dict[str, Any]] = []

    print(
        f"SEARCH shared ring throughput: combos={len(combos)} repeats={repeats} "
        f"total_trials={total_trials} backend={fft_backend}"
    )

    for num_consumers, ring_size, num_rows in combos:
        fft_totals: list[float] = []
        wall_times: list[float] = []
        errors: list[str] = []

        for rep in range(repeats):
            trial_idx += 1
            print(
                f"[{trial_idx}/{total_trials}] consumers={num_consumers} "
                f"ring={ring_size // (1024 * 1024)}MiB rows={num_rows} run={rep + 1}/{repeats}"
            )
            try:
                summary = run_benchmark(
                    total_bytes_target=total_bytes_target,
                    ring_size=ring_size,
                    num_rows=num_rows,
                    columns=columns,
                    num_consumers=num_consumers,
                    join_timeout_s=join_timeout_s,
                    print_summary=False,
                    fft_backend=fft_backend,
                    torch_device=torch_device,
                )
                fft_total_mib_s = float(sum(summary["consumer_fft_mib_s"]))
                fft_totals.append(fft_total_mib_s)
                wall_times.append(float(summary["total_wall_s"]))
                print(f"  -> fft_total={fft_total_mib_s:.2f} MiB/s wall={summary['total_wall_s']:.3f}s")
            except Exception as exc:
                errors.append(str(exc))
                print(f"  -> ERROR: {exc}")

        valid_runs = len(fft_totals)
        if valid_runs == 0:
            row = {
                "num_consumers": num_consumers,
                "ring_size_bytes": ring_size,
                "num_rows": num_rows,
                "valid_runs": 0,
                "failed_runs": repeats,
                "errors": errors,
                "median_fft_mib_s": float("-inf"),
                "mean_fft_mib_s": float("-inf"),
                "median_wall_s": float("inf"),
            }
        else:
            row = {
                "num_consumers": num_consumers,
                "ring_size_bytes": ring_size,
                "num_rows": num_rows,
                "valid_runs": valid_runs,
                "failed_runs": repeats - valid_runs,
                "errors": errors,
                "median_fft_mib_s": float(statistics.median(fft_totals)),
                "mean_fft_mib_s": float(statistics.fmean(fft_totals)),
                "median_wall_s": float(statistics.median(wall_times)),
            }
        rows.append(row)

    ranked = sorted(
        rows,
        key=lambda r: (r["median_fft_mib_s"], -r["median_wall_s"], r["valid_runs"]),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    top = ranked[:max(top_k, 1)]

    print("\nSEARCH results (top configs)")
    for i, r in enumerate(top, start=1):
        print(
            f"{i}. consumers={r['num_consumers']} ring={r['ring_size_bytes'] // (1024 * 1024)}MiB "
            f"rows={r['num_rows']} median_fft={r['median_fft_mib_s']:.2f} MiB/s "
            f"median_wall={r['median_wall_s']:.3f}s valid={r['valid_runs']}/{repeats}"
        )

    if best is not None:
        print("\nBEST configuration")
        print(
            f"consumers={best['num_consumers']}  ring_size={best['ring_size_bytes'] // (1024 * 1024)} MiB  "
            f"num_rows={best['num_rows']}  median_fft={best['median_fft_mib_s']:.2f} MiB/s"
        )

    return {
        "best": best,
        "ranked": ranked,
        "repeats": repeats,
        "columns": columns,
        "target_bytes": total_bytes_target,
        "fft_backend": fft_backend,
        "torch_device": torch_device,
    }


def run_generator_benchmark(
    columns: int,
    num_rows: int,
    repeats: int = 100,
    print_summary: bool = True,
) -> dict[str, Any]:
    """
    Measure pure numpy synthetic data generation throughput (no ring IO, no FFT).
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if columns < 1:
        raise ValueError("columns must be >= 1")
    if num_rows < 1:
        raise ValueError("num_rows must be >= 1")

    times_s: list[float] = []
    batch_nbytes = 0
    checksum = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        batch = generator(columns=columns, size=num_rows)
        dt = time.perf_counter() - t0
        times_s.append(dt)
        if batch_nbytes == 0:
            batch_nbytes = int(batch.nbytes)
        # Tiny reduction to ensure generated data is consumed.
        checksum = (checksum + int(batch[0, 0])) & 0xFFFFFFFF

    median_s = float(statistics.median(times_s))
    mean_s = float(statistics.fmean(times_s))
    min_s = float(min(times_s))
    max_s = float(max(times_s))
    p90_s = float(sorted(times_s)[int(0.90 * (len(times_s) - 1))])

    batch_mib = batch_nbytes / (1024 * 1024)
    median_mib_s = batch_mib / max(median_s, 1e-12)
    mean_mib_s = batch_mib / max(mean_s, 1e-12)
    p90_mib_s = batch_mib / max(p90_s, 1e-12)
    total_bytes = batch_nbytes * repeats
    total_elapsed_s = float(sum(times_s))
    aggregate_mib_s = (total_bytes / (1024 * 1024)) / max(total_elapsed_s, 1e-12)

    summary = {
        "columns": columns,
        "num_rows": num_rows,
        "repeats": repeats,
        "batch_nbytes": batch_nbytes,
        "batch_mib": batch_mib,
        "timing_s": {
            "median": median_s,
            "mean": mean_s,
            "p90": p90_s,
            "min": min_s,
            "max": max_s,
            "total": total_elapsed_s,
        },
        "throughput_mib_s": {
            "median": median_mib_s,
            "mean": mean_mib_s,
            "p90": p90_mib_s,
            "aggregate": aggregate_mib_s,
        },
        "checksum": checksum,
    }

    if print_summary:
        print("BENCH generator throughput (numpy only)")
        print(
            f"rows={num_rows} cols={columns + 1} batch={batch_mib:.3f} MiB repeats={repeats}"
        )
        print(
            f"latency: median={median_s * 1e3:.3f} ms mean={mean_s * 1e3:.3f} ms "
            f"p90={p90_s * 1e3:.3f} ms min={min_s * 1e3:.3f} ms max={max_s * 1e3:.3f} ms"
        )
        print(
            f"throughput: median={median_mib_s:.2f} MiB/s mean={mean_mib_s:.2f} MiB/s "
            f"p90={p90_mib_s:.2f} MiB/s aggregate={aggregate_mib_s:.2f} MiB/s"
        )
        print(f"checksum={checksum}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shared ring benchmark + parameter search")
    parser.add_argument("--mode", choices=("search", "single", "generator"), default="search")
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--target-mib", type=int, default=512)
    parser.add_argument("--join-timeout-s", type=float, default=60.0)
    parser.add_argument("--fft-backend", choices=("auto", "numpy", "torch", "jax"), default="auto")
    parser.add_argument("--torch-device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--consumers", type=str, default="1,2,4,8")
    parser.add_argument("--ring-mib", type=str, default="64,128,256,512")
    parser.add_argument("--rows", type=str, default="4096,8192,16384")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--single-consumers", type=int, default=2)
    parser.add_argument("--single-ring-mib", type=int, default=128)
    parser.add_argument("--single-rows", type=int, default=10000)
    parser.add_argument("--generator-rows", type=int, default=10000)
    parser.add_argument("--generator-repeats", type=int, default=200)
    args = parser.parse_args()

    total_bytes_target = int(args.target_mib) * 1024 * 1024
    if args.mode == "generator":
        run_generator_benchmark(
            columns=int(args.columns),
            num_rows=int(args.generator_rows),
            repeats=int(args.generator_repeats),
            print_summary=True,
        )
    elif args.mode == "single":
        run_benchmark(
            total_bytes_target=total_bytes_target,
            ring_size=int(args.single_ring_mib) * 1024 * 1024,
            num_rows=int(args.single_rows),
            columns=int(args.columns),
            num_consumers=int(args.single_consumers),
            join_timeout_s=float(args.join_timeout_s),
            print_summary=True,
            fft_backend=str(args.fft_backend),
            torch_device=str(args.torch_device),
        )
    else:
        run_benchmark_search(
            total_bytes_target=total_bytes_target,
            columns=int(args.columns),
            consumer_options=_parse_int_csv(args.consumers),
            ring_size_options=[x * 1024 * 1024 for x in _parse_int_csv(args.ring_mib)],
            num_rows_options=_parse_int_csv(args.rows),
            repeats=int(args.repeats),
            join_timeout_s=float(args.join_timeout_s),
            top_k=int(args.top_k),
            fft_backend=str(args.fft_backend),
            torch_device=str(args.torch_device),
        )
