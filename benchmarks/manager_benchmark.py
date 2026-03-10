"""
Run with:
python -B -m src.data_handeling.manager.testing.manager_benchmark

Examples:
python -B -m src.data_handeling.manager.testing.manager_benchmark --mode single --target-mib 512 --single-writer-workers 1 --single-ring-count 2 --single-consumers-per-ring 2 --single-ring-mib 128 --single-rows 4096 --columns 8 --single-cache-allign 1 --single-cache-size 64 --fft-backend numpy --source-mode random
python -B -m src.data_handeling.manager.testing.manager_benchmark --mode single --target-mib 512 --single-writer-workers 2 --single-ring-count 4 --single-consumers-per-ring 2 --single-ring-mib 128 --single-rows 8192 --columns 8 --single-cache-allign 0 --fft-backend auto --source-mode random
python -B -m src.data_handeling.manager.testing.manager_benchmark --mode search --target-mib 512 --writer-workers 1,2 --ring-count 1,2,4 --consumers-per-ring 1,2,4 --ring-mib 64,128,256 --rows 4096,8192 --cache-allign 1,0 --cache-size 64 --repeats 3 --top-k 5 --columns 8 --fft-backend auto --source-mode random
python -B -m src.data_handeling.manager.testing.manager_benchmark --mode search --target-mib 256 --writer-workers 1,2 --ring-count 2,4 --consumers-per-ring 1,2 --ring-mib 64,128 --rows 4096 --cache-allign 1 --cache-size 64,128 --repeats 2 --top-k 3 --columns 8 --fft-backend torch --torch-device cuda --source-mode pyarrow
"""

import argparse
import itertools
import multiprocessing as mp
import statistics
import time
from typing import Any

import numpy as np

from src.data_handeling.manager.manager import InitialState, Manager
from src.data_handeling.shared_ring_buffer import SharedRingBuffer

DTYPE = np.float64


class _ProcLocal:
    """Per-process cached state used by worker proc functions."""

    producer_cache: dict[str, np.ndarray] = {}
    consumer_fft_exec: dict[str, Any] = {}


def _build_pyarrow_batch(rows: int, columns: int):
    try:
        import pyarrow as pa
    except ModuleNotFoundError as exc:
        raise RuntimeError("pyarrow mode requested but pyarrow is not installed") from exc

    arrays = []
    for c in range(columns):
        col = np.linspace(0.0, 1.0, rows, dtype=DTYPE) + (c * 0.01)
        arrays.append(pa.array(col))
    names = [f"c{idx}" for idx in range(columns)]
    return pa.table(arrays, names=names)


def _table_to_numpy(table) -> np.ndarray:
    cols = [np.asarray(table.column(i).to_numpy(), dtype=DTYPE) for i in range(table.num_columns)]
    return np.column_stack(cols)


def _build_fft_executor(backend: str, torch_device: str = "auto"):
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


def _split_columns(columns: int, num_consumers: int, consumer_idx: int) -> list[int]:
    if num_consumers < 1:
        raise ValueError("num_consumers must be >= 1")
    if consumer_idx < 0 or consumer_idx >= num_consumers:
        raise ValueError(f"consumer_idx must be in [0, {num_consumers - 1}]")
    sensors = np.arange(0, columns, dtype=np.int64)
    splits = np.array_split(sensors, num_consumers)
    return [int(x) for x in splits[consumer_idx].tolist()]


def _cleanup_ring_ref(ring: SharedRingBuffer) -> None:
    try:
        ring.ring_buffer.release()
    except Exception:
        pass
    try:
        ring.close()
    except Exception:
        pass


def _timed_worker_loop(name: str, worker, stop_event: mp.Event, result_q: mp.Queue, print_every: int = 1000) -> None:
    opened_in = [worker.shd_mem_init(**spec) for spec in worker.data_in]
    opened_out = [worker.shd_mem_init(**spec) for spec in worker.data_out]
    runtime_args = worker.args
    if not runtime_args:
        runtime_args = (opened_out,) if opened_out else ((opened_in,) if opened_in else tuple())

    wait_s = 0.0
    work_s = 0.0
    loops = 0
    t0 = time.perf_counter()
    bytes_moved = 0
    fft_input_bytes = 0
    fft_calls = 0
    try:
        while True:
            tw = time.perf_counter()
            for ev in worker.wait_events:
                ev.wait()
                ev.reset()
            wait_s += time.perf_counter() - tw

            tr = time.perf_counter()
            stats = worker.proc_func(*runtime_args, **(worker.kwargs or {}))
            work_s += time.perf_counter() - tr
            loops += 1

            if isinstance(stats, dict):
                bytes_moved += int(stats.get("bytes", 0))
                fft_input_bytes += int(stats.get("fft_input_bytes", 0))
                fft_calls += int(stats.get("fft_calls", 0))
                should_stop = bool(stats.get("done", False))
            else:
                should_stop = bool(stats)

            for ev in worker.signal_events:
                ev.signal()

            if loops % print_every == 0:
                elapsed = time.perf_counter() - t0
                duty = (work_s / max(wait_s + work_s, 1e-12)) * 100.0
                print(
                    f"{name}: loops={loops} wall_s={elapsed:.3f} "
                    f"wait_s={wait_s:.3f} work_s={work_s:.3f} duty_pct={duty:.2f}"
                )

            if should_stop or stop_event.is_set():
                break
    finally:
        elapsed = time.perf_counter() - t0
        result_q.put(
            {
                "worker": name,
                "loops": loops,
                "elapsed_s": elapsed,
                "wait_s": wait_s,
                "work_s": work_s,
                "bytes": bytes_moved,
                "fft_input_bytes": fft_input_bytes,
                "fft_calls": fft_calls,
            }
        )
        for ring in opened_in + opened_out:
            _cleanup_ring_ref(ring)


def producer_worker(
    write_locations: list[SharedRingBuffer],
    ring_indices: list[int],
    bytes_target_per_ring: int,
    produced_bytes,
    ring_done,
    rows: int,
    columns: int,
    source_mode: str,
) -> dict[str, int | bool]:
    key = f"{rows}x{columns}:{source_mode}"
    if source_mode == "pyarrow":
        if key not in _ProcLocal.producer_cache:
            _ProcLocal.producer_cache[key] = _table_to_numpy(_build_pyarrow_batch(rows, columns))
        batch = _ProcLocal.producer_cache[key]
    else:
        rng = np.random.default_rng()
        batch = rng.standard_normal((rows, columns)).astype(DTYPE, copy=False)
    src = memoryview(batch).cast("B")

    bytes_written_now = 0
    done_count = 0
    for local_idx, ring in enumerate(write_locations):
        ring_idx = ring_indices[local_idx]
        if int(ring_done[ring_idx]):
            done_count += 1
            continue

        remaining = int(bytes_target_per_ring - int(produced_bytes[ring_idx]))
        if remaining <= 0:
            ring_done[ring_idx] = 1
            done_count += 1
            continue

        req = min(src.nbytes, remaining)
        writer_mv = ring.expose_writer_mem_view(req)
        got = int(writer_mv[2])
        if got > 0:
            ring.simple_write(writer_mv, src[:got])
            ring.inc_writer_pos(got)
            produced_bytes[ring_idx] = int(produced_bytes[ring_idx]) + got
            bytes_written_now += got

        writer_mv[0].release()
        if writer_mv[1] is not None:
            writer_mv[1].release()

        if int(produced_bytes[ring_idx]) >= bytes_target_per_ring:
            ring_done[ring_idx] = 1
            done_count += 1

    return {"bytes": bytes_written_now, "done": done_count == len(write_locations)}


def consumer_worker(
    read_locations: list[SharedRingBuffer],
    ring_indices: list[int],
    consumer_slots: list[int],
    bytes_target_per_ring: int,
    read_bytes,
    ring_done,
    rows: int,
    columns: int,
    consumers_per_ring: int,
    fft_backend: str,
    torch_device: str,
) -> dict[str, int | bool]:
    exec_key = f"{fft_backend}:{torch_device}"
    if exec_key not in _ProcLocal.consumer_fft_exec:
        _ProcLocal.consumer_fft_exec[exec_key] = _build_fft_executor(fft_backend, torch_device)
    fft_exec, _ = _ProcLocal.consumer_fft_exec[exec_key]

    bytes_read_now = 0
    fft_input_bytes = 0
    fft_calls = 0
    done_count = 0

    for local_idx, ring in enumerate(read_locations):
        ring_idx = ring_indices[local_idx]
        slot = consumer_slots[local_idx]

        if int(read_bytes[ring_idx][slot]) >= bytes_target_per_ring and int(ring_done[ring_idx]):
            done_count += 1
            continue

        req = min(rows * columns * np.dtype(DTYPE).itemsize, bytes_target_per_ring)
        reader_mv = ring.expose_reader_mem_view(req)
        got = int(reader_mv[2])
        if got > 0:
            dst = np.empty(got, dtype=np.uint8)
            ring.simple_read(reader_mv, dst)
            ring.inc_reader_pos(got)
            read_bytes[ring_idx][slot] = int(read_bytes[ring_idx][slot]) + got
            bytes_read_now += got

            # FFT only on complete row-major float64 frames.
            frame_bytes = rows * columns * np.dtype(DTYPE).itemsize
            usable = (got // frame_bytes) * frame_bytes
            if usable > 0:
                mat = dst[:usable].view(DTYPE).reshape(-1, columns)
                selected_cols = _split_columns(columns=columns, num_consumers=consumers_per_ring, consumer_idx=slot)
                if selected_cols:
                    selected = mat[:, selected_cols]
                    _ = fft_exec(selected)
                    fft_calls += 1
                    fft_input_bytes += int(selected.nbytes)

        reader_mv[0].release()
        if reader_mv[1] is not None:
            reader_mv[1].release()

        if int(read_bytes[ring_idx][slot]) >= bytes_target_per_ring and int(ring_done[ring_idx]):
            done_count += 1

    return {
        "bytes": bytes_read_now,
        "fft_input_bytes": fft_input_bytes,
        "fft_calls": fft_calls,
        "done": done_count == len(read_locations),
    }


def _assign_rings(num_rings: int, writer_workers: int) -> list[list[int]]:
    if writer_workers < 1:
        raise ValueError("writer_workers must be >= 1")
    if num_rings < 1:
        raise ValueError("num_rings must be >= 1")
    if writer_workers > num_rings:
        raise ValueError("writer_workers cannot exceed num_rings (single-writer per ring)")

    groups: list[list[int]] = [[] for _ in range(writer_workers)]
    for idx in range(num_rings):
        groups[idx % writer_workers].append(idx)
    return groups


def _parse_int_csv(value: str) -> list[int]:
    parts = [x.strip() for x in value.split(",") if x.strip()]
    if not parts:
        raise ValueError("expected at least one integer")
    out = [int(x) for x in parts]
    if any(x <= 0 for x in out):
        raise ValueError("all values must be > 0")
    return out


def _parse_bool_csv(value: str) -> list[bool]:
    token_map = {
        "1": True,
        "0": False,
        "true": True,
        "false": False,
        "yes": True,
        "no": False,
        "y": True,
        "n": False,
    }
    out: list[bool] = []
    for raw in [x.strip().lower() for x in value.split(",") if x.strip()]:
        if raw not in token_map:
            raise ValueError(f"invalid bool token '{raw}'")
        out.append(token_map[raw])
    if not out:
        raise ValueError("expected at least one boolean")
    return out


def run_manager_benchmark(
    total_bytes_target: int,
    writer_workers: int,
    num_rings: int,
    consumers_per_ring: int,
    ring_size: int,
    rows: int,
    columns: int,
    cache_allign: bool,
    cache_size: int,
    source_mode: str,
    fft_backend: str,
    torch_device: str,
    join_timeout_s: float,
    print_summary: bool = True,
) -> dict[str, Any]:
    if source_mode not in {"random", "pyarrow"}:
        raise ValueError("source_mode must be one of: random, pyarrow")

    bytes_per_batch = rows * columns * np.dtype(DTYPE).itemsize
    target_bytes_effective = (total_bytes_target // bytes_per_batch) * bytes_per_batch
    if target_bytes_effective == 0:
        target_bytes_effective = bytes_per_batch

    ring_to_writer = _assign_rings(num_rings=num_rings, writer_workers=writer_workers)
    ctx = mp.get_context("spawn")
    manager = Manager()
    processes: list[mp.Process] = []
    stop_event = ctx.Event()
    result_q = ctx.Queue()

    produced_bytes = ctx.Array("Q", [0] * num_rings)
    ring_done = ctx.Array("i", [0] * num_rings)
    read_bytes = [ctx.Array("Q", [0] * consumers_per_ring) for _ in range(num_rings)]

    try:
        for ring_idx in range(num_rings):
            manager.crt_shared_memory(
                name=f"bench_ring_{ring_idx}",
                size=ring_size,
                num_readers=consumers_per_ring,
                reader=0,
                cache_allign=cache_allign,
                cache_size=cache_size,
            )

        # Create per-ring, per-consumer handshake events.
        for ring_idx in range(num_rings):
            for slot in range(consumers_per_ring):
                manager.crt_worker_event(
                    name=f"writer_done_r{ring_idx}_c{slot}",
                    initial_state=InitialState.CLOSED,
                )
                manager.crt_worker_event(
                    name=f"consumer_done_r{ring_idx}_c{slot}",
                    initial_state=InitialState.OPEN,
                )

        # Writer workers.
        for writer_idx, assigned_rings in enumerate(ring_to_writer):
            data_out = [manager.get_shared_memory_kwargs(f"bench_ring_{ring_idx}", create=False, reader=0) for ring_idx in assigned_rings]
            wait_events = [manager._worker_events[f"consumer_done_r{ring_idx}_c{slot}"] for ring_idx in assigned_rings for slot in range(consumers_per_ring)]
            signal_events = [manager._worker_events[f"writer_done_r{ring_idx}_c{slot}"] for ring_idx in assigned_rings for slot in range(consumers_per_ring)]

            manager.crt_worker(
                name=f"writer_{writer_idx}",
                proc_func=producer_worker,
                data_in=[],
                data_out=data_out,
                wait_events=wait_events,
                signal_events=signal_events,
                args=(),
                kwargs={
                    "ring_indices": assigned_rings,
                    "bytes_target_per_ring": target_bytes_effective,
                    "produced_bytes": produced_bytes,
                    "ring_done": ring_done,
                    "rows": rows,
                    "columns": columns,
                    "source_mode": source_mode,
                },
            )

        # Consumer workers.
        for ring_idx in range(num_rings):
            for slot in range(consumers_per_ring):
                data_in = [manager.get_shared_memory_kwargs(f"bench_ring_{ring_idx}", create=False, reader=slot)]
                manager.crt_worker(
                    name=f"consumer_r{ring_idx}_c{slot}",
                    proc_func=consumer_worker,
                    data_in=data_in,
                    data_out=[],
                    wait_events=[manager._worker_events[f"writer_done_r{ring_idx}_c{slot}"]],
                    signal_events=[manager._worker_events[f"consumer_done_r{ring_idx}_c{slot}"]],
                    args=(),
                    kwargs={
                        "ring_indices": [ring_idx],
                        "consumer_slots": [slot],
                        "bytes_target_per_ring": target_bytes_effective,
                        "read_bytes": read_bytes,
                        "ring_done": ring_done,
                        "rows": rows,
                        "columns": columns,
                        "consumers_per_ring": consumers_per_ring,
                        "fft_backend": fft_backend,
                        "torch_device": torch_device,
                    },
                )

        def start_one(worker_name: str) -> mp.Process:
            w = manager._workers[worker_name]
            p = ctx.Process(target=_timed_worker_loop, args=(worker_name, w, stop_event, result_q), daemon=True)
            p.start()
            return p

        worker_names = list(manager._workers.keys())
        t0 = time.perf_counter()
        for name in worker_names:
            processes.append(start_one(name))

        # Join all workers with progress timeout.
        for proc in processes:
            proc.join(timeout=join_timeout_s)
            if proc.is_alive():
                stop_event.set()
                proc.terminate()
                raise TimeoutError(f"worker pid={proc.pid} did not finish before timeout")
            if proc.exitcode not in (0, None):
                raise RuntimeError(f"worker pid={proc.pid} exited with code {proc.exitcode}")

        elapsed_total = time.perf_counter() - t0
        stats = [result_q.get(timeout=5) for _ in worker_names]

        writer_stats = [s for s in stats if str(s["worker"]).startswith("writer_")]
        consumer_stats = [s for s in stats if str(s["worker"]).startswith("consumer_")]

        writer_mib_s = [
            (float(s["bytes"]) / (1024 * 1024)) / max(float(s["elapsed_s"]), 1e-12)
            for s in writer_stats
        ]
        consumer_drain_mib_s = [
            (float(s["bytes"]) / (1024 * 1024)) / max(float(s["elapsed_s"]), 1e-12)
            for s in consumer_stats
        ]
        consumer_fft_mib_s = [
            (float(s["fft_input_bytes"]) / (1024 * 1024)) / max(float(s["elapsed_s"]), 1e-12)
            for s in consumer_stats
        ]

        summary = {
            "target_bytes_per_ring": target_bytes_effective,
            "writer_workers": writer_workers,
            "num_rings": num_rings,
            "consumers_per_ring": consumers_per_ring,
            "ring_size_bytes": ring_size,
            "rows": rows,
            "columns": columns,
            "cache_allign": cache_allign,
            "cache_size": cache_size,
            "source_mode": source_mode,
            "fft_backend": fft_backend,
            "torch_device": torch_device,
            "total_workers": len(worker_names),
            "total_wall_s": elapsed_total,
            "writer_stats": writer_stats,
            "consumer_stats": consumer_stats,
            "writer_mib_s": writer_mib_s,
            "consumer_drain_mib_s": consumer_drain_mib_s,
            "consumer_fft_mib_s": consumer_fft_mib_s,
            "sum_writer_mib_s": float(sum(writer_mib_s)),
            "sum_consumer_drain_mib_s": float(sum(consumer_drain_mib_s)),
            "sum_consumer_fft_mib_s": float(sum(consumer_fft_mib_s)),
        }

        if print_summary:
            print("BENCH manager throughput")
            print(
                f"workers={summary['total_workers']} (writers={writer_workers}, consumers={num_rings * consumers_per_ring}) "
                f"rings={num_rings} ring={ring_size / (1024 * 1024):.1f}MiB rows={rows} cols={columns}"
            )
            print(
                f"target/ring={target_bytes_effective / (1024 * 1024):.1f}MiB cache_allign={cache_allign} "
                f"cache_size={cache_size} source={source_mode} fft={fft_backend}"
            )
            print(
                f"sum writer={summary['sum_writer_mib_s']:.2f} MiB/s  "
                f"sum consumer_drain={summary['sum_consumer_drain_mib_s']:.2f} MiB/s  "
                f"sum consumer_fft={summary['sum_consumer_fft_mib_s']:.2f} MiB/s"
            )
            print(f"wall: {elapsed_total:.3f}s")

        return summary
    finally:
        stop_event.set()
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
        for proc in processes:
            proc.join(timeout=1.0)
        manager.close_all_shared_memory()


def run_manager_benchmark_search(
    total_bytes_target: int,
    writer_workers_options: list[int],
    ring_count_options: list[int],
    consumers_per_ring_options: list[int],
    ring_size_options: list[int],
    rows_options: list[int],
    cache_allign_options: list[bool],
    cache_size_options: list[int],
    columns: int,
    source_mode: str,
    repeats: int,
    join_timeout_s: float,
    top_k: int,
    fft_backend: str,
    torch_device: str,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    combos = list(
        itertools.product(
            writer_workers_options,
            ring_count_options,
            consumers_per_ring_options,
            ring_size_options,
            rows_options,
            cache_allign_options,
            cache_size_options,
        )
    )
    total_trials = len(combos) * repeats
    trial_idx = 0
    rows_out: list[dict[str, Any]] = []

    print(
        f"SEARCH manager benchmark: combos={len(combos)} repeats={repeats} "
        f"total_trials={total_trials} source={source_mode} fft={fft_backend}"
    )

    for (
        writer_workers,
        num_rings,
        consumers_per_ring,
        ring_size,
        num_rows,
        cache_allign,
        cache_size,
    ) in combos:
        fft_totals: list[float] = []
        writer_totals: list[float] = []
        wall_times: list[float] = []
        errors: list[str] = []

        for rep in range(repeats):
            trial_idx += 1
            print(
                f"[{trial_idx}/{total_trials}] writers={writer_workers} rings={num_rings} "
                f"consumers/ring={consumers_per_ring} ring={ring_size // (1024 * 1024)}MiB "
                f"rows={num_rows} cache_allign={cache_allign} cache_size={cache_size} run={rep + 1}/{repeats}"
            )
            try:
                summary = run_manager_benchmark(
                    total_bytes_target=total_bytes_target,
                    writer_workers=writer_workers,
                    num_rings=num_rings,
                    consumers_per_ring=consumers_per_ring,
                    ring_size=ring_size,
                    rows=num_rows,
                    columns=columns,
                    cache_allign=cache_allign,
                    cache_size=cache_size,
                    source_mode=source_mode,
                    fft_backend=fft_backend,
                    torch_device=torch_device,
                    join_timeout_s=join_timeout_s,
                    print_summary=False,
                )
                fft_total = float(summary["sum_consumer_fft_mib_s"])
                writer_total = float(summary["sum_writer_mib_s"])
                fft_totals.append(fft_total)
                writer_totals.append(writer_total)
                wall_times.append(float(summary["total_wall_s"]))
                print(
                    f"  -> writer_total={writer_total:.2f} MiB/s "
                    f"fft_total={fft_total:.2f} MiB/s wall={summary['total_wall_s']:.3f}s"
                )
            except Exception as exc:
                errors.append(str(exc))
                print(f"  -> ERROR: {exc}")

        valid_runs = len(fft_totals)
        if valid_runs == 0:
            row = {
                "writer_workers": writer_workers,
                "num_rings": num_rings,
                "consumers_per_ring": consumers_per_ring,
                "ring_size_bytes": ring_size,
                "rows": num_rows,
                "cache_allign": cache_allign,
                "cache_size": cache_size,
                "valid_runs": 0,
                "failed_runs": repeats,
                "errors": errors,
                "median_writer_mib_s": float("-inf"),
                "mean_writer_mib_s": float("-inf"),
                "median_fft_mib_s": float("-inf"),
                "mean_fft_mib_s": float("-inf"),
                "median_wall_s": float("inf"),
            }
        else:
            row = {
                "writer_workers": writer_workers,
                "num_rings": num_rings,
                "consumers_per_ring": consumers_per_ring,
                "ring_size_bytes": ring_size,
                "rows": num_rows,
                "cache_allign": cache_allign,
                "cache_size": cache_size,
                "valid_runs": valid_runs,
                "failed_runs": repeats - valid_runs,
                "errors": errors,
                "median_writer_mib_s": float(statistics.median(writer_totals)),
                "mean_writer_mib_s": float(statistics.fmean(writer_totals)),
                "median_fft_mib_s": float(statistics.median(fft_totals)),
                "mean_fft_mib_s": float(statistics.fmean(fft_totals)),
                "median_wall_s": float(statistics.median(wall_times)),
            }
        rows_out.append(row)

    ranked = sorted(
        rows_out,
        key=lambda r: (r["median_fft_mib_s"], -r["median_wall_s"], r["valid_runs"]),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    top = ranked[: max(top_k, 1)]

    print("\nSEARCH results (top configs)")
    for i, r in enumerate(top, start=1):
        print(
            f"{i}. writers={r['writer_workers']} rings={r['num_rings']} consumers/ring={r['consumers_per_ring']} "
            f"ring={r['ring_size_bytes'] // (1024 * 1024)}MiB rows={r['rows']} "
            f"cache_allign={r['cache_allign']} cache_size={r['cache_size']} "
            f"median_writer={r['median_writer_mib_s']:.2f} MiB/s "
            f"median_fft={r['median_fft_mib_s']:.2f} MiB/s median_wall={r['median_wall_s']:.3f}s "
            f"valid={r['valid_runs']}/{repeats}"
        )

    if best is not None:
        print("\nBEST configuration")
        print(
            f"writers={best['writer_workers']} rings={best['num_rings']} consumers/ring={best['consumers_per_ring']} "
            f"ring={best['ring_size_bytes'] // (1024 * 1024)}MiB rows={best['rows']} "
            f"cache_allign={best['cache_allign']} cache_size={best['cache_size']} "
            f"median_writer={best['median_writer_mib_s']:.2f} MiB/s "
            f"median_fft={best['median_fft_mib_s']:.2f} MiB/s"
        )

    return {
        "best": best,
        "ranked": ranked,
        "repeats": repeats,
        "columns": columns,
        "target_bytes": total_bytes_target,
        "source_mode": source_mode,
        "fft_backend": fft_backend,
        "torch_device": torch_device,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manager benchmark + parameter search")
    parser.add_argument("--mode", choices=("search", "single"), default="search")

    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--target-mib", type=int, default=512)
    parser.add_argument("--join-timeout-s", type=float, default=120.0)
    parser.add_argument("--source-mode", choices=("random", "pyarrow"), default="random")
    parser.add_argument("--fft-backend", choices=("auto", "numpy", "torch", "jax"), default="auto")
    parser.add_argument("--torch-device", choices=("auto", "cpu", "cuda"), default="auto")

    # Search options.
    parser.add_argument("--writer-workers", type=str, default="1,2")
    parser.add_argument("--ring-count", type=str, default="1,2,4")
    parser.add_argument("--consumers-per-ring", type=str, default="1,2,4")
    parser.add_argument("--ring-mib", type=str, default="64,128,256")
    parser.add_argument("--rows", type=str, default="4096,8192")
    parser.add_argument("--cache-allign", type=str, default="1,0")
    parser.add_argument("--cache-size", type=str, default="64")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)

    # Single options.
    parser.add_argument("--single-writer-workers", type=int, default=1)
    parser.add_argument("--single-ring-count", type=int, default=2)
    parser.add_argument("--single-consumers-per-ring", type=int, default=2)
    parser.add_argument("--single-ring-mib", type=int, default=128)
    parser.add_argument("--single-rows", type=int, default=4096)
    parser.add_argument("--single-cache-allign", choices=("1", "0"), default="1")
    parser.add_argument("--single-cache-size", type=int, default=64)

    args = parser.parse_args()
    total_bytes_target = int(args.target_mib) * 1024 * 1024

    if args.mode == "single":
        run_manager_benchmark(
            total_bytes_target=total_bytes_target,
            writer_workers=int(args.single_writer_workers),
            num_rings=int(args.single_ring_count),
            consumers_per_ring=int(args.single_consumers_per_ring),
            ring_size=int(args.single_ring_mib) * 1024 * 1024,
            rows=int(args.single_rows),
            columns=int(args.columns),
            cache_allign=(args.single_cache_allign == "1"),
            cache_size=int(args.single_cache_size),
            source_mode=str(args.source_mode),
            fft_backend=str(args.fft_backend),
            torch_device=str(args.torch_device),
            join_timeout_s=float(args.join_timeout_s),
            print_summary=True,
        )
    else:
        run_manager_benchmark_search(
            total_bytes_target=total_bytes_target,
            writer_workers_options=_parse_int_csv(args.writer_workers),
            ring_count_options=_parse_int_csv(args.ring_count),
            consumers_per_ring_options=_parse_int_csv(args.consumers_per_ring),
            ring_size_options=[x * 1024 * 1024 for x in _parse_int_csv(args.ring_mib)],
            rows_options=_parse_int_csv(args.rows),
            cache_allign_options=_parse_bool_csv(args.cache_allign),
            cache_size_options=_parse_int_csv(args.cache_size),
            columns=int(args.columns),
            source_mode=str(args.source_mode),
            repeats=int(args.repeats),
            join_timeout_s=float(args.join_timeout_s),
            top_k=int(args.top_k),
            fft_backend=str(args.fft_backend),
            torch_device=str(args.torch_device),
        )
