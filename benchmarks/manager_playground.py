# Run command:
# PYTHONPYCACHEPREFIX=/tmp/pycache python -m src.data_handeling.manager.testing.manager_playground
import numpy as np
import multiprocessing as mp
import time
import argparse

"""Scratchpad playground for Manager behavior.

This is intentionally not a unittest file.
Edit `main()` freely while building/testing manager functionality.
"""

from src.data_handeling.manager.manager import Manager, InitialState
from src.data_handeling.shared_ring_buffer import SharedRingBuffer

_PRODUCER_STATS = {"t0": None, "bytes_total": 0, "loops": 0, "window_t0": None, "window_bytes": 0}
_CONSUMER_STATS = {"t0": None, "bytes_total": 0, "loops": 0, "window_t0": None, "window_bytes": 0}
ROWS = 4096
COLUMNS = 8
RING_SIZE = 64 * 1024 * 1024
DTYPE = np.float64
BATCH_BYTES = ROWS * COLUMNS * np.dtype(DTYPE).itemsize
SOURCE_MODE = "random"
_ARROW_BATCH = None


def generate_data(rows=ROWS, columns=COLUMNS):
    rng = np.random.default_rng()
    data = rng.standard_normal((rows, columns))
    return data


def _build_pyarrow_batch(rows=ROWS, columns=COLUMNS):
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

def compute_fft(data):
    # FFT along the rows (time axis)
    fft_result = np.fft.rfft(data, axis=0)
    return fft_result

def producer_worker(write_locations:list[SharedRingBuffer]) -> None:
    if _PRODUCER_STATS["t0"] is None:
        _PRODUCER_STATS["t0"] = time.perf_counter()
    if _PRODUCER_STATS["window_t0"] is None:
        _PRODUCER_STATS["window_t0"] = time.perf_counter()
    rng = np.random.default_rng()
    global _ARROW_BATCH
    if SOURCE_MODE == "pyarrow" and _ARROW_BATCH is None:
        _ARROW_BATCH = _build_pyarrow_batch()
    for write_location in write_locations:
        writer_mv = write_location.expose_writer_mem_view(BATCH_BYTES)
        got = writer_mv[2]
        if got < BATCH_BYTES:
            writer_mv[0].release()
            if writer_mv[1] is not None:
                writer_mv[1].release()
            continue

        if SOURCE_MODE == "pyarrow":
            src_np = _table_to_numpy(_ARROW_BATCH)
        else:
            src_np = rng.standard_normal((ROWS, COLUMNS)).astype(DTYPE, copy=False)
        src_bytes = memoryview(src_np).cast("B")
        write_location.simple_write(writer_mv, src_bytes)
        write_location.inc_writer_pos(got)
        _PRODUCER_STATS["bytes_total"] += got
        _PRODUCER_STATS["window_bytes"] += got
        _PRODUCER_STATS["loops"] += 1
        elapsed = max(time.perf_counter() - _PRODUCER_STATS["t0"], 1e-9)
        avg_b_s = _PRODUCER_STATS["bytes_total"] / elapsed
        avg_mib_s = avg_b_s / (1024 * 1024)
        if _PRODUCER_STATS["loops"] % 1000 == 0:
            win_elapsed = max(time.perf_counter() - _PRODUCER_STATS["window_t0"], 1e-9)
            win_b_s = _PRODUCER_STATS["window_bytes"] / win_elapsed
            win_mib_s = win_b_s / (1024 * 1024)
            print(
                f"producer: bytes={_PRODUCER_STATS['bytes_total']} "
                f"elapsed_s={elapsed:.3f} avg_Bps={avg_b_s:.0f} avg_MiBps={avg_mib_s:.2f} "
                f"window1000_Bps={win_b_s:.0f} window1000_MiBps={win_mib_s:.2f}"
            )
            _PRODUCER_STATS["window_t0"] = time.perf_counter()
            _PRODUCER_STATS["window_bytes"] = 0
        writer_mv[0].release()
        if writer_mv[1] is not None:
            writer_mv[1].release()

def consumer_worker(read_locations:list[SharedRingBuffer]):
    if _CONSUMER_STATS["t0"] is None:
        _CONSUMER_STATS["t0"] = time.perf_counter()
    if _CONSUMER_STATS["window_t0"] is None:
        _CONSUMER_STATS["window_t0"] = time.perf_counter()
    for read_location in read_locations:
        data = np.empty((ROWS, COLUMNS), dtype=DTYPE)
        dst = memoryview(data).cast("B")
        reader_mv = read_location.expose_reader_mem_view(dst.nbytes)
        read_location.simple_read(reader_mv, dst)
        read_location.inc_reader_pos(reader_mv[2])
        _CONSUMER_STATS["bytes_total"] += reader_mv[2]
        _CONSUMER_STATS["window_bytes"] += reader_mv[2]
        _CONSUMER_STATS["loops"] += 1
        elapsed = max(time.perf_counter() - _CONSUMER_STATS["t0"], 1e-9)
        avg_b_s = _CONSUMER_STATS["bytes_total"] / elapsed
        avg_mib_s = avg_b_s / (1024 * 1024)
        _ = compute_fft(data=data)
        if _CONSUMER_STATS["loops"] % 1000 == 0:
            win_elapsed = max(time.perf_counter() - _CONSUMER_STATS["window_t0"], 1e-9)
            win_b_s = _CONSUMER_STATS["window_bytes"] / win_elapsed
            win_mib_s = win_b_s / (1024 * 1024)
            print(
                f"consumer: bytes={_CONSUMER_STATS['bytes_total']} "
                f"elapsed_s={elapsed:.3f} avg_Bps={avg_b_s:.0f} avg_MiBps={avg_mib_s:.2f} "
                f"window1000_Bps={win_b_s:.0f} window1000_MiBps={win_mib_s:.2f}"
            )
            _CONSUMER_STATS["window_t0"] = time.perf_counter()
            _CONSUMER_STATS["window_bytes"] = 0
        reader_mv[0].release()
        if reader_mv[1] is not None:
            reader_mv[1].release()


def _timed_worker_loop(name: str, worker, print_every: int = 1000) -> None:
    opened_in = [worker.shd_mem_init(**spec) for spec in worker.data_in]
    opened_out = [worker.shd_mem_init(**spec) for spec in worker.data_out]
    runtime_args = worker.args
    if not runtime_args:
        runtime_args = (opened_out,) if opened_out else ((opened_in,) if opened_in else tuple())

    wait_s = 0.0
    work_s = 0.0
    loops = 0
    t0 = time.perf_counter()
    try:
        while True:
            tw = time.perf_counter()
            for ev in worker.wait_events:
                ev.wait()
                ev.reset()
            wait_s += time.perf_counter() - tw

            tr = time.perf_counter()
            worker.proc_func(*runtime_args, **(worker.kwargs or {}))
            work_s += time.perf_counter() - tr
            loops += 1

            for ev in worker.signal_events:
                ev.signal()

            if loops % print_every == 0:
                elapsed = time.perf_counter() - t0
                duty = (work_s / max(wait_s + work_s, 1e-12)) * 100.0
                print(
                    f"{name}: loops={loops} wall_s={elapsed:.3f} "
                    f"wait_s={wait_s:.3f} work_s={work_s:.3f} duty_pct={duty:.2f}"
                )
    finally:
        for ring in opened_in + opened_out:
            try:
                ring.ring_buffer.release()
            except Exception:
                pass
            try:
                ring.close()
            except Exception:
                pass




    



def main() -> None:
    parser = argparse.ArgumentParser(description="Manager playground throughput runner")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--random", action="store_true", help="Use random numpy values (default)")
    mode.add_argument("--pyarrow", action="store_true", help="Use a prebuilt pyarrow table source")
    args = parser.parse_args()

    global SOURCE_MODE
    SOURCE_MODE = "pyarrow" if args.pyarrow else "random"

    manager = Manager()
    processes = []
    try:
        # Shared buffers used by the playground pipeline.
        manager.crt_shared_memory(name="raw_buf", size=RING_SIZE, num_readers=2, reader=0)

        raw_writer = manager.get_shared_memory_kwargs("raw_buf", create=False, reader=0)
        raw_fft1 = manager.get_shared_memory_kwargs("raw_buf", create=False, reader=0)
        raw_fft2 = manager.get_shared_memory_kwargs("raw_buf", create=False, reader=1)

        manager.crt_worker_event(name="writer_done_fft1", initial_state=InitialState.CLOSED)
        manager.crt_worker_event(name="writer_done_fft2", initial_state=InitialState.CLOSED)
        manager.crt_worker_event(name="fft1_done_writer", initial_state=InitialState.OPEN)
        manager.crt_worker_event(name="fft2_done_writer", initial_state=InitialState.OPEN)

        manager.crt_worker(
            name="writer",
            proc_func=producer_worker,
            data_in=[],
            data_out=[raw_writer],
            wait_events=[manager._worker_events["fft1_done_writer"], manager._worker_events["fft2_done_writer"]],
            signal_events=[manager._worker_events["writer_done_fft1"], manager._worker_events["writer_done_fft2"]],
            args=(),
            kwargs={},
        )
        manager.crt_worker(
            name="fft1",
            proc_func=consumer_worker,
            data_in=[raw_fft1],
            data_out=[],
            wait_events=[manager._worker_events["writer_done_fft1"]],
            signal_events=[manager._worker_events["fft1_done_writer"]],
            args=(),
            kwargs={},
        )
        manager.crt_worker(
            name="fft2",
            proc_func=consumer_worker,
            data_in=[raw_fft2],
            data_out=[],
            wait_events=[manager._worker_events["writer_done_fft2"]],
            signal_events=[manager._worker_events["fft2_done_writer"]],
            args=(),
            kwargs={},
        )

        print("Created workers: writer, fft1, fft2")

        print("BENCH shared ring throughput (playground)")
        print(
            f"ring={RING_SIZE / (1024 * 1024):.1f} MiB rows={ROWS} cols={COLUMNS} consumers=2 source={SOURCE_MODE}"
        )

        def start_one(worker_name: str) -> mp.Process:
            w = manager._workers[worker_name]
            print(f"[main] starting worker={worker_name}")
            p = mp.Process(
                target=_timed_worker_loop,
                args=(
                    worker_name,
                    w,
                ),
                daemon=True,
            )
            p.start()
            print(f"[main] started worker={worker_name} pid={p.pid}")
            return p

        p_writer = start_one("writer")
        p_fft1 = start_one("fft1")
        p_fft2 = start_one("fft2")
        processes = [p_writer, p_fft1, p_fft2]
        print(f"[main] started all workers -> writer={p_writer.pid}, fft1={p_fft1.pid}, fft2={p_fft2.pid}")
        print("[main] workers run indefinitely; press Ctrl+C to stop")

        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("[main] stopping workers...")
    finally:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
        for proc in processes:
            proc.join(timeout=2.0)
        manager.close_all_shared_memory()
        print("[main] workers stopped")

if __name__ == "__main__":
    main()
