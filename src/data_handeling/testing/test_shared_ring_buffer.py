"""Run with: python -m unittest src.data_handeling.testing.test_shared_ring_buffer -v"""

import math
import multiprocessing as mp
import os
import queue
import struct
import time
import unittest
import uuid

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    np = None

try:
    import pyarrow as pa
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    pa = None

from src.data_handeling.shared_ring_buffer import SharedRingBuffer


FRAME_HEADER_FMT = "<IQ"  # payload_len(uint32), seq(uint64)
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FMT)


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw


def _fmt_bytes(num_bytes):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TB"


def _fmt_mib_s(value):
    if value < 0:
        return "SKIPPED"
    return f"{value:,.2f} MiB/s"


def _split_channels_evenly(channel_names, n_consumers):
    groups = [[] for _ in range(n_consumers)]
    for i, name in enumerate(channel_names):
        groups[i % n_consumers].append(name)
    return groups


def _build_arrow_payload(pa_mod, np_mod, nrows, ncols, batch_index=0):
    arrays = []
    names = []
    base = batch_index * nrows
    for c in range(ncols):
        names.append(f"c{c}")
        if np_mod is not None:
            arr = np_mod.arange(base + c, base + c + nrows, dtype=np_mod.int64)
            arrays.append(pa_mod.array(arr))
        else:  # pragma: no cover - benchmark path is gated on numpy+pyarrow
            arrays.append(pa_mod.array([base + c + i for i in range(nrows)], type=pa_mod.int64()))
    batch = pa_mod.record_batch(arrays, names=names)
    sink = pa_mod.BufferOutputStream()
    with pa_mod.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


def _frame_message(payload_bytes, seq):
    return struct.pack(FRAME_HEADER_FMT, len(payload_bytes), seq) + payload_bytes


def _read_exact_from_ring(ring, nbytes, io_lock=None, poll_sleep_s=0.0005):
    out = bytearray()
    wait_s = 0.0
    while len(out) < nbytes:
        if io_lock is None:
            available = ring.get_available_read()
            if available > 0:
                out.extend(ring.read(min(available, nbytes - len(out))))
                continue
        else:
            with io_lock:
                available = ring.get_available_read()
                if available > 0:
                    out.extend(ring.read(min(available, nbytes - len(out))))
                    continue

        t0 = time.perf_counter()
        time.sleep(poll_sleep_s)
        wait_s += (time.perf_counter() - t0)
    return bytes(out), wait_s


def _fft_slowdown_from_channels(np_mod, table, channel_names, rng, mode):
    if mode == "none":
        return 0, 0.0
    if not channel_names:
        return 0, 0.0

    if mode == "fft_light":
        fft_len = 2048
        repeats = 1
    elif mode == "fft_heavy":
        fft_len = 8192
        repeats = 2
    else:
        raise ValueError(f"unknown throttle mode: {mode}")

    # Stress mode now applies FFT work to every assigned channel for every message.
    fft_ops = 0
    t0 = time.perf_counter()
    for channel_name in channel_names:
        chunked = table[channel_name]
        if len(chunked.chunks) == 0:
            continue
        if len(chunked.chunks) == 1:
            src = chunked.chunk(0).to_numpy(zero_copy_only=False)
        else:
            src = np_mod.concatenate([c.to_numpy(zero_copy_only=False) for c in chunked.chunks])
        if src.size == 0:
            continue

        for _ in range(repeats):
            if src.size >= fft_len:
                start = rng.randrange(0, src.size - fft_len + 1) if src.size > fft_len else 0
                window = src[start:start + fft_len]
                signal = window.astype(np_mod.float32, copy=False)
            else:
                signal = np_mod.zeros(fft_len, dtype=np_mod.float32)
                signal[:src.size] = src.astype(np_mod.float32, copy=False)
            _ = np_mod.fft.rfft(signal)
            fft_ops += 1
    return fft_ops, (time.perf_counter() - t0)


def _ring_consumer_process(
    shm_name,
    payload_size,
    num_consumers,
    reader_index,
    n_messages,
    expected_nrows,
    assigned_channels,
    *tail_args,
):
    """Supports both arg tails: (ready_q, result_q) and (throttle_mode, ready_q, result_q)."""
    import random as _random
    import traceback as _traceback
    import time as _time
    import struct as _struct
    import pyarrow as _pa
    import numpy as _np
    from src.data_handeling.shared_ring_buffer import SharedRingBuffer as _SharedRingBuffer

    if len(tail_args) == 2:
        throttle_mode = "none"
        io_lock = None
        ready_q, result_q = tail_args
    elif len(tail_args) == 3:
        if isinstance(tail_args[0], str):
            throttle_mode, ready_q, result_q = tail_args
            io_lock = None
        else:
            throttle_mode = "none"
            io_lock, ready_q, result_q = tail_args
    elif len(tail_args) == 4:
        throttle_mode, io_lock, ready_q, result_q = tail_args
    else:
        raise TypeError(f"_ring_consumer_process expected 2-4 trailing args, got {len(tail_args)}")

    ring = _SharedRingBuffer(
        name=shm_name,
        create=False,
        size=payload_size,
        num_consumers=num_consumers,
        reader=reader_index,
    )
    rng = _random.Random((reader_index + 1) * 9973 + n_messages * 17)
    rows = 0
    bytes_read = 0
    processed_signal_bytes = 0
    read_wait_s = 0.0
    fft_calls = 0
    fft_s = 0.0
    seq_errors = 0
    expected_seq = 0
    t_start = _time.perf_counter()
    try:
        if io_lock is None:
            ring.update_reader_pos(0)  # marks slot alive (alive=1 timestamp updated)
            ring.update_min_reader_pos()
        else:
            with io_lock:
                ring.update_reader_pos(0)
                ring.update_min_reader_pos()
        ready_q.put({"reader": reader_index, "status": "ready"})

        for _ in range(n_messages):
            frame_header, waited = _read_exact_from_ring(ring, FRAME_HEADER_SIZE, io_lock=io_lock)
            read_wait_s += waited
            payload_len, seq = _struct.unpack(FRAME_HEADER_FMT, frame_header)
            if seq != expected_seq:
                seq_errors += 1
                expected_seq = seq
            expected_seq += 1

            payload, waited = _read_exact_from_ring(ring, payload_len, io_lock=io_lock)
            read_wait_s += waited
            table = _pa.ipc.open_stream(_pa.BufferReader(payload)).read_all()
            rows += table.num_rows
            bytes_read += (FRAME_HEADER_SIZE + payload_len)
            processed_signal_bytes += table.num_rows * len(assigned_channels) * 8  # int64 signal channels

            calls, spent = _fft_slowdown_from_channels(_np, table, assigned_channels, rng, throttle_mode)
            fft_calls += calls
            fft_s += spent

        elapsed = max(_time.perf_counter() - t_start, 1e-12)
        result_q.put(
            {
                "reader": reader_index,
                "status": "ok",
                "rows": rows,
                "bytes": bytes_read,
                "processed_signal_bytes": processed_signal_bytes,
                "expected_rows": n_messages * expected_nrows,
                "seq_errors": seq_errors,
                "fft_calls": fft_calls,
                "fft_s": fft_s,
                "read_wait_s": read_wait_s,
                "elapsed_s": elapsed,
                "throttle_mode": throttle_mode,
                "assigned_channels": assigned_channels,
            }
        )
    except Exception:
        result_q.put(
            {
                "reader": reader_index,
                "status": "error",
                "error": _traceback.format_exc(),
                "throttle_mode": throttle_mode,
                "assigned_channels": assigned_channels,
            }
        )
        raise
    finally:
        ring.close()


class SharedRingBufferTests(unittest.TestCase):
    FULL_BUFFER_CYCLES = 10
    DEFAULT_MAX_RING_BYTES = 1024 * 1024 * 1024  # 1 GB default benchmark cap
    QUICK_MAX_RING_BYTES = 1024 * 1024 * 1024  # quick mode still includes 1 GB, but with a reduced matrix

    @staticmethod
    def _fmt_size_mb_or_kb(num_bytes):
        mb = num_bytes / (1024 * 1024)
        if mb >= 0.1:
            return f"{mb:>8.2f} MB"
        return f"{num_bytes / 1024:>8.1f} KB"

    def _make_name(self):
        return f"gse2_ring_{uuid.uuid4().hex}"

    def _consumer_proc_summary(self, procs):
        return ", ".join(
            f"r{i}:alive={p.is_alive()} exit={p.exitcode}"
            for i, p in enumerate(procs)
        )

    def _wait_for_ready_consumers(self, ready_q, result_q, procs, expected, timeout_s, case_label):
        ready = 0
        deadline = time.perf_counter() + timeout_s
        errors = []
        while ready < expected:
            # Surface child errors immediately if they already posted them.
            while True:
                try:
                    msg = result_q.get_nowait()
                except queue.Empty:
                    break
                else:
                    errors.append(msg)
            if errors:
                details = "\n\n".join(
                    m.get("error", repr(m)) if isinstance(m, dict) else repr(m) for m in errors
                )
                self.fail(f"{case_label}\nconsumer error before ready:\n{details}")

            now = time.perf_counter()
            if now >= deadline:
                self.fail(
                    f"{case_label}\nTimed out waiting for consumer ready messages after {timeout_s}s.\n"
                    f"Process states: {self._consumer_proc_summary(procs)}"
                )

            dead = [p for p in procs if (p.exitcode is not None and p.exitcode != 0)]
            if dead:
                self.fail(
                    f"{case_label}\nConsumer process exited before ready.\n"
                    f"Process states: {self._consumer_proc_summary(procs)}"
                )

            try:
                msg = ready_q.get(timeout=min(1.0, deadline - now))
            except queue.Empty:
                continue
            if not isinstance(msg, dict) or msg.get("status") != "ready":
                self.fail(f"{case_label}\nUnexpected ready message: {msg!r}")
            ready += 1

    def _collect_consumer_results(self, result_q, procs, expected, timeout_s, case_label):
        deadline = time.perf_counter() + timeout_s
        results = []
        errors = []
        while len(results) < expected:
            # Fail fast if any process crashed and no result is arriving.
            crashed = [p for p in procs if (p.exitcode is not None and p.exitcode != 0)]
            if crashed:
                # Drain any queued error payloads first.
                while True:
                    try:
                        msg = result_q.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        if isinstance(msg, dict) and msg.get("status") == "error":
                            errors.append(msg)
                        else:
                            results.append(msg)
                detail = ""
                if errors:
                    detail = "\n\n" + "\n\n".join(e.get("error", repr(e)) for e in errors)
                self.fail(
                    f"{case_label}\nConsumer process crashed before all results were collected.\n"
                    f"Process states: {self._consumer_proc_summary(procs)}{detail}"
                )

            now = time.perf_counter()
            if now >= deadline:
                # Drain any available messages for diagnostics.
                while True:
                    try:
                        msg = result_q.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        if isinstance(msg, dict) and msg.get("status") == "error":
                            errors.append(msg)
                        else:
                            results.append(msg)
                detail = ""
                if errors:
                    detail = "\n\nChild errors:\n" + "\n\n".join(e.get("error", repr(e)) for e in errors)
                self.fail(
                    f"{case_label}\nTimed out after {timeout_s}s waiting for consumer results "
                    f"({len(results)}/{expected} received).\n"
                    f"Process states: {self._consumer_proc_summary(procs)}{detail}"
                )

            try:
                msg = result_q.get(timeout=min(1.0, deadline - now))
            except queue.Empty:
                continue

            if not isinstance(msg, dict):
                self.fail(f"{case_label}\nUnexpected result payload type: {type(msg)} {msg!r}")
            if msg.get("status") == "error":
                self.fail(f"{case_label}\nChild consumer reported error:\n{msg.get('error', msg)!s}")
            if msg.get("status") != "ok":
                self.fail(f"{case_label}\nUnexpected consumer result status: {msg!r}")
            results.append(msg)
        return results

    def test_write_and_read_roundtrip_single_reader(self):
        name = self._make_name()
        ring = SharedRingBuffer(name=name, create=True, size=4096, num_consumers=1, reader=0)
        try:
            ring.update_reader_pos(0)
            payload = b"hello-ring"
            self.assertEqual(ring.write(payload), len(payload))
            self.assertEqual(ring.read(len(payload)), payload)
            self.assertEqual(ring.get_available_read(), 0)
        finally:
            ring.close()
            ring.unlink()

    def test_wraparound_read_write_single_reader(self):
        name = self._make_name()
        ring = SharedRingBuffer(name=name, create=True, size=32, num_consumers=1, reader=0)
        try:
            ring.update_reader_pos(0)
            self.assertEqual(ring.write(b"a" * 24), 24)
            self.assertEqual(ring.read(20), b"a" * 20)
            self.assertEqual(ring.write(b"b" * 20), 20)  # forces wrap
            self.assertEqual(ring.read(24), (b"a" * 4) + (b"b" * 20))
            self.assertGreater(ring.get_write_pos(), ring.payload_size)
        finally:
            ring.close()
            ring.unlink()

    def test_update_min_reader_pos_uses_slowest_active_reader(self):
        name = self._make_name()
        writer = SharedRingBuffer(name=name, create=True, size=256, num_consumers=2, reader=0)
        reader1 = SharedRingBuffer(name=name, create=False, size=256, num_consumers=2, reader=1)
        try:
            writer.update_reader_pos(10)
            reader1.update_reader_pos(3)
            self.assertEqual(writer.update_min_reader_pos(), 3)
        finally:
            reader1.close()
            writer.close()
            writer.unlink()

    @unittest.skipIf(pa is None or np is None, "pyarrow and numpy are required for shared ring benchmark/FFT tests")
    def test_benchmark_pyarrow_shared_ring_1_producer_up_to_5_consumers_fft_stress(self):
        ctx = mp.get_context("spawn")

        matrix_mode = _env_str("SHARED_RING_BENCH_MATRIX", "quick").lower()
        default_max_ring_bytes = (
            self.DEFAULT_MAX_RING_BYTES if matrix_mode in {"balanced", "full"} else self.QUICK_MAX_RING_BYTES
        )
        max_ring_bytes = _env_int("SHARED_RING_BENCH_MAX_RING_BYTES", default_max_ring_bytes)
        use_io_lock = _env_int("SHARED_RING_BENCH_USE_IO_LOCK", 1) != 0

        consumer_counts = [1, 2, 3, 4, 5]
        if matrix_mode == "full":
            throttle_modes = ["fft_light", "fft_heavy"]
            write_profiles = [
                {"name": "ws_small", "nrows": 16_384, "ncols": 10},
                {"name": "ws_medium", "nrows": 65_536, "ncols": 10},
                {"name": "ws_large", "nrows": 262_144, "ncols": 10},
            ]
            ring_size_candidates = [
                8 * 1024 * 1024,
                32 * 1024 * 1024,
                128 * 1024 * 1024,
                512 * 1024 * 1024,
                1024 * 1024 * 1024,
                2 * 1024 * 1024 * 1024,
            ]
        elif matrix_mode == "quick":
            # Quick benchmark: covers different write sizes and several ring sizes up to 1 GB,
            # but trims the case matrix aggressively so it finishes quickly.
            throttle_modes = ["fft_light"]
            write_profiles = [
                {"name": "ws_small", "nrows": 16_384, "ncols": 10},
                {"name": "ws_medium", "nrows": 65_536, "ncols": 10},
            ]
            ring_size_candidates = [
                8 * 1024 * 1024,
                32 * 1024 * 1024,
                128 * 1024 * 1024,
                1024 * 1024 * 1024,
            ]
        else:  # balanced default
            throttle_modes = ["fft_light", "fft_heavy"]
            write_profiles = [
                {"name": "ws_small", "nrows": 16_384, "ncols": 10},
                {"name": "ws_medium", "nrows": 65_536, "ncols": 10},
            ]
            ring_size_candidates = [
                8 * 1024 * 1024,
                32 * 1024 * 1024,
                128 * 1024 * 1024,
                512 * 1024 * 1024,
                max_ring_bytes,
            ]

        ring_size_candidates = sorted(set(s for s in ring_size_candidates if s <= max_ring_bytes and s > 0))

        results = []
        skipped = []

        for profile in write_profiles:
            payload = _build_arrow_payload(pa, np, profile["nrows"], profile["ncols"], batch_index=0)
            framed_msg_template = _frame_message(payload, 0)
            write_size = len(framed_msg_template)

            # Skip impossible write sizes for tiny rings before entering case loops.
            valid_ring_sizes = [s for s in ring_size_candidates if write_size < s]
            if not valid_ring_sizes:
                skipped.append(
                    {
                        "profile": profile["name"],
                        "reason": f"serialized write size {write_size} exceeds configured ring sizes",
                    }
                )
                continue

            for ring_size in valid_ring_sizes:
                # Enforce enough data to fill the buffer completely 10 times.
                required_total_bytes = ring_size * self.FULL_BUFFER_CYCLES
                case_messages = max(1, math.ceil(required_total_bytes / write_size))
                total_case_bytes = case_messages * write_size
                # Enforce wrap-around by construction and assert it explicitly.
                self.assertGreaterEqual(total_case_bytes, required_total_bytes)
                self.assertGreater(total_case_bytes, ring_size)

                for consumer_count in consumer_counts:
                    channel_names = [f"c{i}" for i in range(profile["ncols"])]
                    consumer_channel_groups = _split_channels_evenly(channel_names, consumer_count)
                    for throttle_mode in throttle_modes:
                        if matrix_mode == "quick":
                            # Trim quick mode by ring size while still covering 1..5 consumers,
                            # multiple write sizes, and a very large ring (1 GB) with FFT enabled.
                            if ring_size >= (1024 * 1024 * 1024):
                                if profile["name"] == "ws_small":
                                    if consumer_count not in (1, 5):
                                        continue
                                else:
                                    if consumer_count != 1:
                                        continue
                            elif ring_size >= (128 * 1024 * 1024):
                                if consumer_count not in (1, 3, 5):
                                    continue
                        if matrix_mode != "full" and ring_size >= (512 * 1024 * 1024):
                            # Keep large-ring capability but avoid exploding runtime in non-full modes.
                            if matrix_mode != "quick" and consumer_count != 1:
                                continue
                        with self.subTest(
                            profile=profile["name"],
                            ring_size=ring_size,
                            consumers=consumer_count,
                            throttle=throttle_mode,
                        ):
                            name = self._make_name()
                            try:
                                ring = SharedRingBuffer(
                                    name=name,
                                    create=True,
                                    size=ring_size,
                                    num_consumers=consumer_count,
                                    reader=0,
                                )
                            except (OSError, MemoryError) as exc:
                                skipped.append(
                                    {
                                        "profile": profile["name"],
                                        "ring_size": ring_size,
                                        "consumers": consumer_count,
                                        "throttle": throttle_mode,
                                        "reason": str(exc),
                                    }
                                )
                                continue

                            ready_q = ctx.Queue()
                            result_q = ctx.Queue()
                            io_lock = ctx.Lock() if use_io_lock else None
                            consumers = []
                            try:
                                case_label = (
                                    f"profile={profile['name']} ring={_fmt_bytes(ring_size)} "
                                    f"write={_fmt_bytes(write_size)} consumers={consumer_count} "
                                    f"throttle={throttle_mode} messages={case_messages} "
                                    f"channel_split={[len(g) for g in consumer_channel_groups]}"
                                )
                                for reader_index in range(consumer_count):
                                    p = ctx.Process(
                                        target=_ring_consumer_process,
                                        args=(
                                            name,
                                            ring_size,
                                            consumer_count,
                                            reader_index,
                                            case_messages,
                                            profile["nrows"],
                                            consumer_channel_groups[reader_index],
                                            throttle_mode,
                                            io_lock,
                                            ready_q,
                                            result_q,
                                        ),
                                    )
                                    p.start()
                                    consumers.append(p)

                                self._wait_for_ready_consumers(
                                    ready_q=ready_q,
                                    result_q=result_q,
                                    procs=consumers,
                                    expected=consumer_count,
                                    timeout_s=15,
                                    case_label=case_label,
                                )

                                if io_lock is None:
                                    ring.get_write_pos()
                                    ring.update_min_reader_pos()
                                else:
                                    with io_lock:
                                        ring.get_write_pos()
                                        ring.update_min_reader_pos()

                                writer_wait_s = 0.0
                                writer_stall_events = 0
                                writer_stall_drop_if_full_bytes = 0
                                t0 = time.perf_counter()
                                for seq in range(case_messages):
                                    framed_msg = _frame_message(payload, seq)
                                    t_wait_0 = None
                                    while True:
                                        if io_lock is None:
                                            writable = ring.max_writable()
                                        else:
                                            with io_lock:
                                                writable = ring.max_writable()
                                        if writable >= len(framed_msg):
                                            break
                                        if t_wait_0 is None:
                                            t_wait_0 = time.perf_counter()
                                        time.sleep(0.0005)
                                    if t_wait_0 is not None:
                                        writer_wait_s += (time.perf_counter() - t_wait_0)
                                        writer_stall_events += 1
                                        writer_stall_drop_if_full_bytes += len(framed_msg)
                                    if io_lock is None:
                                        ring.write(framed_msg)
                                    else:
                                        with io_lock:
                                            ring.write(framed_msg)
                                t1 = time.perf_counter()

                                # Enforce wrap/index exercise on the writer logical counter.
                                if io_lock is None:
                                    final_write_pos = ring.get_write_pos()
                                else:
                                    with io_lock:
                                        final_write_pos = ring.get_write_pos()
                                self.assertGreaterEqual(final_write_pos, total_case_bytes)
                                self.assertGreaterEqual(final_write_pos // ring.payload_size, self.FULL_BUFFER_CYCLES)

                                # Timeout grows with bytes written and consumer stress.
                                stress_factor = 1 if throttle_mode == "none" else (2 if throttle_mode == "fft_light" else 4)
                                mib_written = total_case_bytes / (1024 * 1024)
                                # Tighter cap for faster feedback; configurable for slower machines.
                                timeout_cap_s = _env_int("SHARED_RING_BENCH_TIMEOUT_CAP_S", 900)
                                timeout_s = max(
                                    30,
                                    min(
                                        timeout_cap_s,
                                        int((mib_written / 100.0) * stress_factor * max(1, consumer_count)) + 30,
                                    ),
                                )
                                consumer_results = self._collect_consumer_results(
                                    result_q=result_q,
                                    procs=consumers,
                                    expected=consumer_count,
                                    timeout_s=timeout_s,
                                    case_label=case_label,
                                )
                                t_done = time.perf_counter()

                                for p in consumers:
                                    p.join(timeout=30)
                                    self.assertEqual(
                                        p.exitcode,
                                        0,
                                        f"{case_label}\nChild consumer exitcode={p.exitcode}",
                                    )

                                for r in consumer_results:
                                    self.assertEqual(r["status"], "ok", r.get("error"))
                                    self.assertEqual(r["rows"], r["expected_rows"])
                                    self.assertEqual(r["seq_errors"], 0)

                                producer_write_elapsed_s = max(t1 - t0, 1e-12)
                                e2e_elapsed_s = max(t_done - t0, 1e-12)
                                producer_ingest_mib_s = (total_case_bytes / (1024 * 1024)) / producer_write_elapsed_s
                                producer_ingest_msgs_s = case_messages / producer_write_elapsed_s
                                total_consumer_wire_bytes = sum(r["bytes"] for r in consumer_results)
                                total_consumer_signal_bytes = sum(r["processed_signal_bytes"] for r in consumer_results)
                                fanout_wire_e2e_mib_s = (total_consumer_wire_bytes / (1024 * 1024)) / e2e_elapsed_s
                                useful_signal_e2e_mib_s = (total_consumer_signal_bytes / (1024 * 1024)) / e2e_elapsed_s
                                per_consumer_wire_mib_s = [
                                    (r["bytes"] / (1024 * 1024)) / max(r["elapsed_s"], 1e-12) for r in consumer_results
                                ]
                                avg_consumer_wire_mib_s = sum(per_consumer_wire_mib_s) / consumer_count
                                min_consumer_wire_mib_s = min(per_consumer_wire_mib_s)
                                consumer_elapsed_s_list = [r["elapsed_s"] for r in consumer_results]
                                consumer_wait_s_list = [r["read_wait_s"] for r in consumer_results]
                                avg_consume_time_s = sum(consumer_elapsed_s_list) / consumer_count
                                max_consume_time_s = max(consumer_elapsed_s_list)
                                avg_consumer_wait_s = sum(consumer_wait_s_list) / consumer_count
                                max_consumer_wait_s = max(consumer_wait_s_list)
                                max_reader_wait_pct = max((r["read_wait_s"] / max(r["elapsed_s"], 1e-12)) * 100.0 for r in consumer_results)
                                total_fft_calls = sum(r["fft_calls"] for r in consumer_results)
                                total_fft_s = sum(r["fft_s"] for r in consumer_results)
                                channel_split = tuple(len(g) for g in consumer_channel_groups)

                                results.append(
                                    {
                                        "profile": profile["name"],
                                        "ncols": profile["ncols"],
                                        "ring_size": ring_size,
                                        "write_size": write_size,
                                        "consumers": consumer_count,
                                        "throttle": throttle_mode,
                                        "channel_split": channel_split,
                                        "messages": case_messages,
                                        "total_bytes": total_case_bytes,
                                        "fill_cycles": total_case_bytes / max(ring_size, 1),
                                        "producer_write_elapsed_s": producer_write_elapsed_s,
                                        "e2e_elapsed_s": e2e_elapsed_s,
                                        "producer_ingest_mib_s": producer_ingest_mib_s,
                                        "producer_ingest_msgs_s": producer_ingest_msgs_s,
                                        "producer_wait_pct": (writer_wait_s / producer_write_elapsed_s) * 100.0,
                                        "producer_wait_s": writer_wait_s,
                                        "producer_stall_events": writer_stall_events,
                                        "producer_stall_drop_if_full_bytes": writer_stall_drop_if_full_bytes,
                                        "producer_stall_drop_if_full_mib": writer_stall_drop_if_full_bytes / (1024 * 1024),
                                        "producer_stall_drop_if_full_pct": (writer_stall_drop_if_full_bytes / total_case_bytes) * 100.0,
                                        "avg_consumer_wire_mib_s": avg_consumer_wire_mib_s,
                                        "min_consumer_wire_mib_s": min_consumer_wire_mib_s,
                                        "fanout_wire_e2e_mib_s": fanout_wire_e2e_mib_s,
                                        "useful_signal_e2e_mib_s": useful_signal_e2e_mib_s,
                                        "avg_consume_time_s": avg_consume_time_s,
                                        "max_consume_time_s": max_consume_time_s,
                                        "avg_consumer_wait_s": avg_consumer_wait_s,
                                        "max_consumer_wait_s": max_consumer_wait_s,
                                        "max_reader_wait_pct": max_reader_wait_pct,
                                        "fft_calls": total_fft_calls,
                                        "fft_cpu_s": total_fft_s,
                                    }
                                )
                            finally:
                                for p in consumers:
                                    if p.is_alive():
                                        p.terminate()
                                        p.join(timeout=5)
                                ring.close()
                                ring.unlink()

        results.sort(key=lambda x: x["producer_ingest_mib_s"], reverse=True)
        results_by_consumer = sorted(results, key=lambda x: x["fanout_wire_e2e_mib_s"], reverse=True)

        print("BENCH SharedRingBuffer PyArrow broadcast (1 producer, 1-5 consumers, FFT throttle modes)")
        print(
            f"Benchmark matrix mode: {matrix_mode} (set SHARED_RING_BENCH_MATRIX=full for exhaustive run), "
            f"io_lock={'on' if use_io_lock else 'off'}"
        )
        print("Consumers are broadcast readers on one ring (all read all bytes), but FFT/CPU work is split by channel assignment.")
        print("In FFT modes, every assigned channel is FFT-processed for every message (light/heavy changes FFT size/repeats).")
        print("drop-if-full = bytes that would be dropped under a 'drop write if full' policy (counts writes that had to wait).")
        print(f"Requirement enforced: each case writes at least {self.FULL_BUFFER_CYCLES}x ring capacity and wraps")
        print(
            "SORT: producer ingest throughput (producer write phase only)"
        )
        print(
            "rank | prof     | chans | split        | ring      | write     | cons | fft mode   | fills | writes   | writer MiB/s | consumer MiB/s | dropped MiB | prod wait s | cons avg s | cons wait avg s | cons wait max s"
        )
        for idx, r in enumerate(results, start=1):
            print(
                f"{idx:>4} | "
                f"{r['profile']:<8} | "
                f"{r['ncols']:>5} | "
                f"{str(r['channel_split']):<11} | "
                f"{_fmt_bytes(r['ring_size']):>9} | "
                f"{_fmt_bytes(r['write_size']):>9} | "
                f"{r['consumers']:>4} | "
                f"{r['throttle']:<9} | "
                f"{r['fill_cycles']:>5.1f} | "
                f"{r['messages']:>7} | "
                f"{r['producer_ingest_mib_s']:>11.2f} | "
                f"{r['fanout_wire_e2e_mib_s']:>14.2f} | "
                f"{r['producer_stall_drop_if_full_mib']:>10.2f} | "
                f"{r['producer_wait_s']:>11.2f} | "
                f"{r['avg_consume_time_s']:>10.2f} | "
                f"{r['avg_consumer_wait_s']:>15.2f} | "
                f"{r['max_consumer_wait_s']:>15.2f}"
            )

        print("SORT: consumer throughput (aggregate fanout wire bytes across consumers, end-to-end)")
        print(
            "rank | prof     | chans | split        | ring      | write     | cons | fft mode   | consumer MiB/s | writer MiB/s | dropped MiB | cons avg s | cons wait avg s | cons wait max s"
        )
        for idx, r in enumerate(results_by_consumer, start=1):
            print(
                f"{idx:>4} | "
                f"{r['profile']:<8} | "
                f"{r['ncols']:>5} | "
                f"{str(r['channel_split']):<11} | "
                f"{_fmt_bytes(r['ring_size']):>9} | "
                f"{_fmt_bytes(r['write_size']):>9} | "
                f"{r['consumers']:>4} | "
                f"{r['throttle']:<9} | "
                f"{r['fanout_wire_e2e_mib_s']:>14.2f} | "
                f"{r['producer_ingest_mib_s']:>11.2f} | "
                f"{r['producer_stall_drop_if_full_mib']:>10.2f} | "
                f"{r['avg_consume_time_s']:>10.2f} | "
                f"{r['avg_consumer_wait_s']:>15.2f} | "
                f"{r['max_consumer_wait_s']:>15.2f}"
            )

        if skipped:
            print("BENCH Skipped Cases / Allocation Limits")
            for s in skipped:
                pieces = []
                if "profile" in s:
                    pieces.append(f"profile={s['profile']}")
                if "ring_size" in s:
                    pieces.append(f"ring={_fmt_bytes(s['ring_size'])}")
                if "consumers" in s:
                    pieces.append(f"cons={s['consumers']}")
                if "throttle" in s:
                    pieces.append(f"throttle={s['throttle']}")
                print("  " + ", ".join(pieces) + f" -> {s['reason']}")


if __name__ == "__main__":
    unittest.main()
