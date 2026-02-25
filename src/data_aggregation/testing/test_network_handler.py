"""Run with: python -m unittest src.data_aggregation.testing.test_network_handler -v"""

import queue
import socket
import struct
import time
import unittest
import multiprocessing as mp
import threading
from unittest.mock import patch

try:
    import pyarrow as pa
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    pa = None

if pa is not None:
    from src.data_aggregation.network_handler import Membufs, NetworkReader


def _build_dummy_batch(pa_mod, batch_index, nrows, ncols):
    arrays = []
    names = []
    base = batch_index * nrows
    for c in range(ncols):
        names.append(f"c{c}")
        arrays.append(pa_mod.array([base + i + c for i in range(nrows)], type=pa_mod.int64()))
    return pa_mod.record_batch(arrays, names=names)


def _arrow_sender_process(port_queue, ready_queue, n_batches, nrows, ncols, stats_queue=None):
    import pyarrow as _pa

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_queue.put(srv.getsockname()[1])
    conn, _ = srv.accept()
    ready_queue.put("accepted")
    total_payload_bytes = 0
    total_wire_bytes = 0
    try:
        payloads = []
        for b in range(n_batches):
            batch = _build_dummy_batch(_pa, b, nrows, ncols)
            sink = _pa.BufferOutputStream()
            with _pa.ipc.new_stream(sink, batch.schema) as writer:
                writer.write_batch(batch)
            payload = sink.getvalue().to_pybytes()
            payloads.append(payload)
            total_payload_bytes += len(payload)
            total_wire_bytes += len(payload) + 4
        for payload in payloads:
            conn.sendall(struct.pack("!I", len(payload)))
            conn.sendall(payload)
    finally:
        if stats_queue is not None:
            stats_queue.put(
                {
                    "payload_bytes": total_payload_bytes,
                    "wire_bytes": total_wire_bytes,
                    "batches": n_batches,
                }
            )
        conn.close()
        srv.close()


def _network_reader_process(port, membuf_queue, status_queue, n_batches, nrows, ncols):
    import pyarrow as _pa
    from src.data_aggregation.network_handler import Membufs as _Membufs, NetworkReader as _NetworkReader

    schema = _pa.schema([(f"c{i}", _pa.int64()) for i in range(ncols)])
    membufs = [_Membufs(data_set=["c0"], queue=membuf_queue)]
    reader = _NetworkReader("bench-reader", "127.0.0.1", port, schema, nrows, membufs)
    processed = 0
    try:
        for _ in range(n_batches):
            reader._recv_header()
            payload_len = int.from_bytes(reader._header_mv, "big")
            reader._resize_payload_capacity(payload_len)
            reader._recv_exact_into_payload(payload_len)
            with _pa.ipc.open_stream(_pa.BufferReader(reader._payload_mv[:payload_len])) as ipc_reader:
                batch = ipc_reader.read_next_batch()
            for membuf in reader.membufs:
                reader._split_columns_on_membufs(membuf, batch)
            processed += batch.num_rows
        status_queue.put({"processed_rows": processed})
    finally:
        try:
            reader.socket.close()
        finally:
            membuf_queue.put(None)


def _membuf_consumer_process(membuf_queue, result_queue):
    import pyarrow as _pa

    total_rows = 0
    final_value = None
    batches = 0
    while True:
        payload = membuf_queue.get()
        if payload is None:
            break
        table = _pa.ipc.open_stream(_pa.BufferReader(payload)).read_all()
        col = table["c0"].to_pylist()
        if col:
            final_value = col[-1]
            total_rows += len(col)
            batches += 1
    result_queue.put({"total_rows": total_rows, "final_value": final_value, "batches": batches})


def _arrow_sender_loop_process(port_queue, ready_queue, control_queue, stats_queue):
    import pyarrow as _pa

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_queue.put(srv.getsockname()[1])
    conn, _ = srv.accept()
    ready_queue.put("accepted")
    try:
        while True:
            cmd = control_queue.get()
            if cmd["op"] == "stop":
                break
            if cmd["op"] != "run_case":
                raise ValueError(f"unknown sender op: {cmd['op']}")
            case_id = cmd["case_id"]
            n_batches = cmd["n_batches"]
            nrows = cmd["nrows"]
            ncols = cmd["ncols"]

            total_payload_bytes = 0
            total_wire_bytes = 0
            payloads = []
            for b in range(n_batches):
                batch = _build_dummy_batch(_pa, b, nrows, ncols)
                sink = _pa.BufferOutputStream()
                with _pa.ipc.new_stream(sink, batch.schema) as writer:
                    writer.write_batch(batch)
                payload = sink.getvalue().to_pybytes()
                payloads.append(payload)
                total_payload_bytes += len(payload)
                total_wire_bytes += len(payload) + 4
            for payload in payloads:
                conn.sendall(struct.pack("!I", len(payload)))
                conn.sendall(payload)
            stats_queue.put(
                {
                    "case_id": case_id,
                    "payload_bytes": total_payload_bytes,
                    "wire_bytes": total_wire_bytes,
                    "batches": n_batches,
                }
            )
    finally:
        conn.close()
        srv.close()


def _network_reader_loop_process(port, membuf_queue, control_queue, status_queue):
    import pyarrow as _pa
    from src.data_aggregation.network_handler import Membufs as _Membufs, NetworkReader as _NetworkReader

    schema = _pa.schema([("c0", _pa.int64())])
    membufs = [_Membufs(data_set=["c0"], queue=membuf_queue)]
    reader = _NetworkReader("bench-reader", "127.0.0.1", port, schema, 0, membufs)
    try:
        while True:
            cmd = control_queue.get()
            if cmd["op"] == "stop":
                break
            if cmd["op"] != "run_case":
                raise ValueError(f"unknown reader op: {cmd['op']}")
            case_id = cmd["case_id"]
            n_batches = cmd["n_batches"]
            processed = 0
            for _ in range(n_batches):
                reader._recv_header()
                payload_len = int.from_bytes(reader._header_mv, "big")
                reader._resize_payload_capacity(payload_len)
                reader._recv_exact_into_payload(payload_len)
                with _pa.ipc.open_stream(_pa.BufferReader(reader._payload_mv[:payload_len])) as ipc_reader:
                    batch = ipc_reader.read_next_batch()
                for membuf in reader.membufs:
                    reader._split_columns_on_membufs(membuf, batch)
                processed += batch.num_rows
            membuf_queue.put(("case_end", case_id))
            status_queue.put({"case_id": case_id, "processed_rows": processed})
    finally:
        try:
            reader.socket.close()
        finally:
            membuf_queue.put(("stop", None))


def _membuf_consumer_loop_process(membuf_queue, result_queue):
    import pyarrow as _pa

    total_rows = 0
    final_value = None
    batches = 0
    current_case_id = None

    while True:
        item = membuf_queue.get()
        if isinstance(item, tuple):
            tag, case_id = item
            if tag == "stop":
                break
            if tag == "case_end":
                result_queue.put(
                    {
                        "case_id": case_id,
                        "total_rows": total_rows,
                        "final_value": final_value,
                        "batches": batches,
                    }
                )
                total_rows = 0
                final_value = None
                batches = 0
                current_case_id = None
                continue
        payload = item
        table = _pa.ipc.open_stream(_pa.BufferReader(payload)).read_all()
        col = table["c0"].to_pylist()
        if col:
            final_value = col[-1]
            total_rows += len(col)
            batches += 1


def _arrow_sender_loop_thread(port_queue, ready_queue, control_queue, stats_queue, stop_event):
    import pyarrow as _pa

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_queue.put(srv.getsockname()[1])
    conn, _ = srv.accept()
    ready_queue.put("accepted")
    try:
        while not stop_event.is_set():
            try:
                cmd = control_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if cmd["op"] == "stop":
                break
            if cmd["op"] != "run_case":
                raise ValueError(f"unknown sender op: {cmd['op']}")
            case_id = cmd["case_id"]
            n_batches = cmd["n_batches"]
            nrows = cmd["nrows"]
            ncols = cmd["ncols"]

            payloads = []
            total_payload_bytes = 0
            total_wire_bytes = 0
            for b in range(n_batches):
                batch = _build_dummy_batch(_pa, b, nrows, ncols)
                sink = _pa.BufferOutputStream()
                with _pa.ipc.new_stream(sink, batch.schema) as writer:
                    writer.write_batch(batch)
                payload = sink.getvalue().to_pybytes()
                payloads.append(payload)
                total_payload_bytes += len(payload)
                total_wire_bytes += len(payload) + 4
            for payload in payloads:
                conn.sendall(struct.pack("!I", len(payload)))
                conn.sendall(payload)
            stats_queue.put(
                {
                    "case_id": case_id,
                    "payload_bytes": total_payload_bytes,
                    "wire_bytes": total_wire_bytes,
                    "batches": n_batches,
                }
            )
    finally:
        conn.close()
        srv.close()


def _network_reader_loop_thread(port, membuf_queue, control_queue, status_queue, stop_event):
    import pyarrow as _pa
    from src.data_aggregation.network_handler import Membufs as _Membufs, NetworkReader as _NetworkReader

    schema = _pa.schema([("c0", _pa.int64())])
    membufs = [_Membufs(data_set=["c0"], queue=membuf_queue)]
    reader = _NetworkReader("thread-bench-reader", "127.0.0.1", port, schema, 0, membufs)
    try:
        while not stop_event.is_set():
            try:
                cmd = control_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if cmd["op"] == "stop":
                break
            if cmd["op"] != "run_case":
                raise ValueError(f"unknown reader op: {cmd['op']}")
            case_id = cmd["case_id"]
            n_batches = cmd["n_batches"]
            processed = 0
            for _ in range(n_batches):
                reader._recv_header()
                payload_len = int.from_bytes(reader._header_mv, "big")
                reader._resize_payload_capacity(payload_len)
                reader._recv_exact_into_payload(payload_len)
                with _pa.ipc.open_stream(_pa.BufferReader(reader._payload_mv[:payload_len])) as ipc_reader:
                    batch = ipc_reader.read_next_batch()
                for membuf in reader.membufs:
                    reader._split_columns_on_membufs(membuf, batch)
                processed += batch.num_rows
            membuf_queue.put(("case_end", case_id))
            status_queue.put({"case_id": case_id, "processed_rows": processed})
    finally:
        try:
            reader.socket.close()
        finally:
            membuf_queue.put(("stop", None))


def _membuf_consumer_loop_thread(membuf_queue, result_queue, stop_event):
    import pyarrow as _pa

    total_rows = 0
    final_value = None
    batches = 0
    while not stop_event.is_set():
        try:
            item = membuf_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if isinstance(item, tuple):
            tag, case_id = item
            if tag == "stop":
                break
            if tag == "case_end":
                result_queue.put(
                    {
                        "case_id": case_id,
                        "total_rows": total_rows,
                        "final_value": final_value,
                        "batches": batches,
                    }
                )
                total_rows = 0
                final_value = None
                batches = 0
                continue
        payload = item
        table = _pa.ipc.open_stream(_pa.BufferReader(payload)).read_all()
        col = table["c0"].to_pylist()
        if col:
            final_value = col[-1]
            total_rows += len(col)
            batches += 1


class FakeSocket:
    def __init__(self, chunks=None):
        self._chunks = list(chunks or [])

    def recv_into(self, mv, nbytes):
        if not self._chunks:
            return 0
        chunk = self._chunks.pop(0)
        chunk = chunk[:nbytes]
        mv[: len(chunk)] = chunk
        return len(chunk)


@unittest.skipIf(pa is None, "pyarrow is required for network_handler tests")
class NetworkReaderTests(unittest.TestCase):
    BENCH_REPEATS = 5
    INTEGRATION_BENCH_REPEATS = 3
    INTEGRATION_CASE_SECONDS = 5.0

    @staticmethod
    def _fmt_size_mb_or_kb(num_bytes):
        mb = num_bytes / (1024 * 1024)
        if mb >= 0.1:
            return f"{mb:>8.2f} MB"
        return f"{num_bytes / 1024:>8.1f} KB"

    def _make_reader(self, sock=None):
        sock = sock or FakeSocket()
        schema = pa.schema([("a", pa.int64()), ("b", pa.string())])
        with patch("src.data_aggregation.network_handler.socket.create_connection", return_value=sock):
            return NetworkReader(
                name="test-reader",
                sender_addr="127.0.0.1",
                sender_port=9999,
                schema=schema,
                batch_size=10,
                membufs=[],
            )

    def test_recv_header_reads_exactly_four_bytes_across_partial_reads(self):
        header = struct.pack("!I", 1234)
        sock = FakeSocket([header[:2], header[2:]])
        reader = self._make_reader(sock)

        reader._recv_header()

        self.assertEqual(bytes(reader._header), header)

    def test_recv_exact_into_payload_handles_partial_reads(self):
        sock = FakeSocket([b"ab", b"cde"])
        reader = self._make_reader(sock)

        reader._recv_exact_into_payload(5)

        self.assertEqual(bytes(reader._payload[:5]), b"abcde")

    def test_resize_payload_capacity_grows_buffer_and_rebuilds_memoryview(self):
        reader = self._make_reader()
        old_size = reader.cur_payload_buf_size

        reader._resize_payload_capacity(old_size + 1)

        self.assertGreaterEqual(reader.cur_payload_buf_size, (old_size + 1) * 2)
        self.assertEqual(len(reader._payload), reader.cur_payload_buf_size)
        self.assertEqual(len(reader._payload_mv), reader.cur_payload_buf_size)

    def test_split_columns_on_membufs_enqueues_arrow_ipc_for_selected_columns(self):
        reader = self._make_reader()
        q = queue.Queue()
        membuf = Membufs(data_set=["a"], queue=q)
        batch = pa.record_batch([pa.array([1, 2, 3]), pa.array(["x", "y", "z"])], names=["a", "b"])

        reader._split_columns_on_membufs(membuf, batch)

        payload = q.get_nowait()
        table = pa.ipc.open_stream(pa.BufferReader(payload)).read_all()
        self.assertEqual(table.column_names, ["a"])
        self.assertEqual(table["a"].to_pylist(), [1, 2, 3])

    def test_recv_header_raises_on_closed_socket(self):
        reader = self._make_reader(FakeSocket([]))

        with self.assertRaises(ConnectionError):
            reader._recv_header()

    def test_benchmark_arrow_ipc_rows_per_second_by_batch_and_column_count(self):
        batch_sizes = [10, 100, 1_000, 5_000]
        column_counts = [1, 5, 10, 25, 50, 75, 100]
        results = []

        for nrows in batch_sizes:
            for ncols in column_counts:
                with self.subTest(nrows=nrows, ncols=ncols):
                    build_t0 = time.perf_counter()
                    arrays = []
                    names = []
                    for c in range(ncols):
                        names.append(f"c{c}")
                        # Mix simple column types to better resemble real payloads.
                        if c % 3 == 0:
                            arrays.append(pa.array(list(range(nrows)), type=pa.int64()))
                        elif c % 3 == 1:
                            arrays.append(pa.array([float(i) * 1.1 for i in range(nrows)], type=pa.float64()))
                        else:
                            arrays.append(pa.array([f"v{i % 100}" for i in range(nrows)], type=pa.string()))

                    batch = pa.record_batch(arrays, names=names)
                    build_t1 = time.perf_counter()

                    write_total_s = 0.0
                    read_total_s = 0.0
                    roundtrip_total_s = 0.0
                    payload = b""
                    table = None

                    for _ in range(self.BENCH_REPEATS):
                        t0 = time.perf_counter()
                        sink = pa.BufferOutputStream()
                        with pa.ipc.new_stream(sink, batch.schema) as writer:
                            writer.write_batch(batch)
                        payload = sink.getvalue().to_pybytes()
                        t1 = time.perf_counter()

                        table = pa.ipc.open_stream(pa.BufferReader(payload)).read_all()
                        t2 = time.perf_counter()

                        write_total_s += (t1 - t0)
                        read_total_s += (t2 - t1)
                        roundtrip_total_s += (t2 - t0)

                    self.assertIsNotNone(table)
                    self.assertEqual(table.num_rows, nrows)
                    self.assertEqual(table.num_columns, ncols)

                    write_s = max(write_total_s / self.BENCH_REPEATS, 1e-12)
                    read_s = max(read_total_s / self.BENCH_REPEATS, 1e-12)
                    roundtrip_s = max(roundtrip_total_s / self.BENCH_REPEATS, 1e-12)
                    payload_mb = len(payload) / (1024 * 1024)
                    results.append(
                            {
                                "nrows": nrows,
                                "ncols": ncols,
                                "repeats": self.BENCH_REPEATS,
                                "bytes": len(payload),
                            "payload_mb": payload_mb,
                            "build_s": (build_t1 - build_t0),
                            "write_rps": nrows / write_s,
                            "read_rps": nrows / read_s,
                            "roundtrip_rps": nrows / roundtrip_s,
                            "write_mbps": payload_mb / write_s,
                                "read_mbps": payload_mb / read_s,
                                "roundtrip_mbps": payload_mb / roundtrip_s,
                                "build_rps": nrows / max((build_t1 - build_t0), 1e-12),
                            }
                        )

        # Compact matrix-style summary for quick human scanning.
        print(
            f"BENCH Arrow IPC throughput (avg of {self.BENCH_REPEATS} runs per case)"
        )
        for nrows in batch_sizes:
            row_results = [r for r in results if r["nrows"] == nrows]
            print(f"batch={nrows} rows")
            print("  cols |    payload | build ms | build rows/s | write rows/s | read rows/s | roundtrip rows/s | roundtrip MB/s")
            for r in row_results:
                payload_str = self._fmt_size_mb_or_kb(r["bytes"])
                print(
                    "  "
                    f"{r['ncols']:>4} | "
                    f"{payload_str} | "
                    f"{r['build_s'] * 1e3:>7.2f} | "
                    f"{r['build_rps']:>11.0f} | "
                    f"{r['write_rps']:>12.0f} | "
                    f"{r['read_rps']:>11.0f} | "
                    f"{r['roundtrip_rps']:>16.0f} | "
                    f"{r['roundtrip_mbps']:>13.2f}"
                )

    def test_integration_socket_sender_network_reader_and_membuf_consumer(self):
        ctx = mp.get_context("spawn")
        port_queue = ctx.Queue()
        sender_ready_queue = ctx.Queue()
        membuf_queue = ctx.Queue()
        reader_status_queue = ctx.Queue()
        consumer_result_queue = ctx.Queue()

        n_batches = 4
        nrows = 50
        ncols = 20

        sender = ctx.Process(
            target=_arrow_sender_process,
            args=(port_queue, sender_ready_queue, n_batches, nrows, ncols),
        )
        sender.start()

        port = port_queue.get(timeout=5)

        reader = ctx.Process(
            target=_network_reader_process,
            args=(port, membuf_queue, reader_status_queue, n_batches, nrows, ncols),
        )
        consumer = ctx.Process(
            target=_membuf_consumer_process,
            args=(membuf_queue, consumer_result_queue),
        )
        reader.start()
        consumer.start()

        self.assertEqual(sender_ready_queue.get(timeout=5), "accepted")
        reader_status = reader_status_queue.get(timeout=10)
        consumer_result = consumer_result_queue.get(timeout=10)

        sender.join(timeout=5)
        reader.join(timeout=5)
        consumer.join(timeout=5)

        self.assertEqual(sender.exitcode, 0)
        self.assertEqual(reader.exitcode, 0)
        self.assertEqual(consumer.exitcode, 0)

        expected_rows = n_batches * nrows
        # c0 values are base + row_index where base = batch_index * nrows
        expected_final_value = expected_rows - 1

        self.assertEqual(reader_status["processed_rows"], expected_rows)
        self.assertEqual(consumer_result["total_rows"], expected_rows)
        self.assertEqual(consumer_result["batches"], n_batches)
        self.assertEqual(consumer_result["final_value"], expected_final_value)

    def test_benchmark_integration_socket_to_reader_to_membuf_consumer(self):
        ctx = mp.get_context("spawn")
        batch_sizes = [100, 1_000]
        column_counts = [1, 10, 50, 100]
        n_batches_per_case = 5
        results = []

        port_queue = ctx.Queue()
        sender_ready_queue = ctx.Queue()
        sender_control_queue = ctx.Queue()
        sender_stats_queue = ctx.Queue()
        reader_control_queue = ctx.Queue()
        reader_status_queue = ctx.Queue()
        membuf_queue = ctx.Queue()
        consumer_result_queue = ctx.Queue()

        sender = ctx.Process(
            target=_arrow_sender_loop_process,
            args=(port_queue, sender_ready_queue, sender_control_queue, sender_stats_queue),
        )
        sender.start()
        port = port_queue.get(timeout=5)

        reader = ctx.Process(
            target=_network_reader_loop_process,
            args=(port, membuf_queue, reader_control_queue, reader_status_queue),
        )
        consumer = ctx.Process(
            target=_membuf_consumer_loop_process,
            args=(membuf_queue, consumer_result_queue),
        )
        reader.start()
        consumer.start()
        self.assertEqual(sender_ready_queue.get(timeout=5), "accepted")

        case_id = 0
        try:
            for nrows in batch_sizes:
                for ncols in column_counts:
                    with self.subTest(nrows=nrows, ncols=ncols):
                        elapsed_total = 0.0
                        payload_total_bytes = 0
                        wire_total_bytes = 0
                        rows_total = 0
                        windows = 0
                        sender_wait_total = 0.0
                        reader_wait_total = 0.0
                        consumer_wait_total = 0.0

                        # Warm-up one unmeasured case.
                        warm_case_id = case_id
                        case_id += 1
                        warm_cmd = {
                            "op": "run_case",
                            "case_id": warm_case_id,
                            "n_batches": n_batches_per_case,
                            "nrows": nrows,
                            "ncols": ncols,
                        }
                        sender_control_queue.put(warm_cmd)
                        reader_control_queue.put(warm_cmd)
                        _ = sender_stats_queue.get(timeout=30)
                        _ = reader_status_queue.get(timeout=30)
                        _ = consumer_result_queue.get(timeout=30)

                        t_window_start = time.perf_counter()
                        while (time.perf_counter() - t_window_start) < self.INTEGRATION_CASE_SECONDS:
                            run_case_id = case_id
                            case_id += 1
                            cmd = {
                                "op": "run_case",
                                "case_id": run_case_id,
                                "n_batches": n_batches_per_case,
                                "nrows": nrows,
                                "ncols": ncols,
                            }
                            t0 = time.perf_counter()
                            sender_control_queue.put(cmd)
                            reader_control_queue.put(cmd)

                            tw0 = time.perf_counter()
                            sender_stats = sender_stats_queue.get(timeout=60)
                            tw1 = time.perf_counter()
                            reader_status = reader_status_queue.get(timeout=60)
                            tw2 = time.perf_counter()
                            consumer_result = consumer_result_queue.get(timeout=60)
                            t1 = time.perf_counter()

                            self.assertEqual(sender_stats["case_id"], run_case_id)
                            self.assertEqual(reader_status["case_id"], run_case_id)
                            self.assertEqual(consumer_result["case_id"], run_case_id)

                            expected_rows = n_batches_per_case * nrows
                            self.assertEqual(reader_status["processed_rows"], expected_rows)
                            self.assertEqual(consumer_result["total_rows"], expected_rows)
                            self.assertEqual(consumer_result["batches"], n_batches_per_case)
                            self.assertEqual(consumer_result["final_value"], expected_rows - 1)

                            elapsed_total += (t1 - t0)
                            sender_wait_total += (tw1 - tw0)
                            reader_wait_total += (tw2 - tw1)
                            consumer_wait_total += (t1 - tw2)
                            payload_total_bytes += sender_stats["payload_bytes"]
                            wire_total_bytes += sender_stats["wire_bytes"]
                            rows_total += expected_rows
                            windows += 1

                        stage_totals = {
                            "sender_wait": sender_wait_total,
                            "reader_wait": reader_wait_total,
                            "consumer_wait": consumer_wait_total,
                        }
                        longest_stage = max(stage_totals, key=stage_totals.get)
                        downstream_elapsed_total = max(reader_wait_total + consumer_wait_total, 1e-12)
                        avg_elapsed_s = max(elapsed_total / max(windows, 1), 1e-12)
                        avg_payload_bytes = payload_total_bytes / max(windows, 1)
                        avg_wire_bytes = wire_total_bytes / max(windows, 1)
                        avg_rows = rows_total / max(windows, 1)
                        results.append(
                            {
                                "nrows": nrows,
                                "ncols": ncols,
                                "n_batches": n_batches_per_case,
                                "windows": windows,
                                "avg_case_s": avg_elapsed_s,
                                "payload_mb": avg_payload_bytes / (1024 * 1024),
                                "wire_mb": avg_wire_bytes / (1024 * 1024),
                                "rows_per_s_downstream": rows_total / downstream_elapsed_total,
                                "payload_mbps_downstream": (payload_total_bytes / (1024 * 1024)) / downstream_elapsed_total,
                                "wire_mbps_downstream": (wire_total_bytes / (1024 * 1024)) / downstream_elapsed_total,
                                "rows_per_s_full": rows_total / max(elapsed_total, 1e-12),
                                "payload_mbps_full": (payload_total_bytes / (1024 * 1024)) / max(elapsed_total, 1e-12),
                                "wire_mbps_full": (wire_total_bytes / (1024 * 1024)) / max(elapsed_total, 1e-12),
                                "longest_stage": longest_stage,
                                "sender_wait_pct": (sender_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "reader_wait_pct": (reader_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "consumer_wait_pct": (consumer_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "downstream_pct": (downstream_elapsed_total / max(elapsed_total, 1e-12)) * 100.0,
                            }
                        )
        finally:
            sender_control_queue.put({"op": "stop"})
            reader_control_queue.put({"op": "stop"})
            sender.join(timeout=10)
            reader.join(timeout=10)
            consumer.join(timeout=10)
            self.assertEqual(sender.exitcode, 0)
            self.assertEqual(reader.exitcode, 0)
            self.assertEqual(consumer.exitcode, 0)

        print(
            f"BENCH Integration steady-state socket->NetworkReader->membuf->consumer (~{self.INTEGRATION_CASE_SECONDS:.0f}s/case)"
        )
        for nrows in batch_sizes:
            print(f"batch={nrows} rows x {n_batches_per_case} batches/case")
            print("  cols | payload/run |   wire/run | full rows/s | rd+cons rows/s | full MB/s | rd+cons MB/s | longest")
            for r in [x for x in results if x["nrows"] == nrows]:
                payload_str = self._fmt_size_mb_or_kb(int(r["payload_mb"] * 1024 * 1024))
                wire_str = self._fmt_size_mb_or_kb(int(r["wire_mb"] * 1024 * 1024))
                print(
                    "  "
                    f"{r['ncols']:>4} | "
                    f"{payload_str} | "
                    f"{wire_str} | "
                    f"{r['rows_per_s_full']:>10.0f} | "
                    f"{r['rows_per_s_downstream']:>13.0f} | "
                    f"{r['wire_mbps_full']:>9.2f} | "
                    f"{r['wire_mbps_downstream']:>12.2f} | "
                    f"{r['longest_stage']}"
                )
                print(
                    "       stage share: "
                    f"sender={r['sender_wait_pct']:.0f}% "
                    f"reader={r['reader_wait_pct']:.0f}% "
                    f"consumer={r['consumer_wait_pct']:.0f}% "
                    f"(downstream={r['downstream_pct']:.0f}%)"
                )

    def test_benchmark_threaded_socket_to_reader_to_queue_consumer(self):
        batch_sizes = [100, 1_000]
        column_counts = [1, 10, 50, 100]
        n_batches_per_case = 5
        results = []

        stop_event = threading.Event()
        sender_port_queue = queue.Queue()
        sender_ready_queue = queue.Queue()
        sender_control_queue = queue.Queue()
        sender_stats_queue = queue.Queue()
        reader_control_queue = queue.Queue()
        reader_status_queue = queue.Queue()
        membuf_queue = queue.Queue()
        consumer_result_queue = queue.Queue()

        sender_thread = threading.Thread(
            target=_arrow_sender_loop_thread,
            args=(sender_port_queue, sender_ready_queue, sender_control_queue, sender_stats_queue, stop_event),
            daemon=True,
        )
        consumer_thread = threading.Thread(
            target=_membuf_consumer_loop_thread,
            args=(membuf_queue, consumer_result_queue, stop_event),
            daemon=True,
        )

        sender_thread.start()
        port = sender_port_queue.get(timeout=5)
        reader_thread = threading.Thread(
            target=_network_reader_loop_thread,
            args=(port, membuf_queue, reader_control_queue, reader_status_queue, stop_event),
            daemon=True,
        )
        reader_thread.start()
        consumer_thread.start()
        self.assertEqual(sender_ready_queue.get(timeout=5), "accepted")

        case_id = 0
        try:
            for nrows in batch_sizes:
                for ncols in column_counts:
                    with self.subTest(nrows=nrows, ncols=ncols):
                        elapsed_total = 0.0
                        payload_total_bytes = 0
                        wire_total_bytes = 0
                        rows_total = 0
                        windows = 0
                        sender_wait_total = 0.0
                        reader_wait_total = 0.0
                        consumer_wait_total = 0.0

                        warm_case_id = case_id
                        case_id += 1
                        warm_cmd = {
                            "op": "run_case",
                            "case_id": warm_case_id,
                            "n_batches": n_batches_per_case,
                            "nrows": nrows,
                            "ncols": ncols,
                        }
                        sender_control_queue.put(warm_cmd)
                        reader_control_queue.put(warm_cmd)
                        _ = sender_stats_queue.get(timeout=30)
                        _ = reader_status_queue.get(timeout=30)
                        _ = consumer_result_queue.get(timeout=30)

                        t_window_start = time.perf_counter()
                        while (time.perf_counter() - t_window_start) < self.INTEGRATION_CASE_SECONDS:
                            run_case_id = case_id
                            case_id += 1
                            cmd = {
                                "op": "run_case",
                                "case_id": run_case_id,
                                "n_batches": n_batches_per_case,
                                "nrows": nrows,
                                "ncols": ncols,
                            }
                            t0 = time.perf_counter()
                            sender_control_queue.put(cmd)
                            reader_control_queue.put(cmd)
                            tw0 = time.perf_counter()
                            sender_stats = sender_stats_queue.get(timeout=60)
                            tw1 = time.perf_counter()
                            reader_status = reader_status_queue.get(timeout=60)
                            tw2 = time.perf_counter()
                            consumer_result = consumer_result_queue.get(timeout=60)
                            t1 = time.perf_counter()

                            self.assertEqual(sender_stats["case_id"], run_case_id)
                            self.assertEqual(reader_status["case_id"], run_case_id)
                            self.assertEqual(consumer_result["case_id"], run_case_id)

                            expected_rows = n_batches_per_case * nrows
                            self.assertEqual(reader_status["processed_rows"], expected_rows)
                            self.assertEqual(consumer_result["total_rows"], expected_rows)
                            self.assertEqual(consumer_result["batches"], n_batches_per_case)
                            self.assertEqual(consumer_result["final_value"], expected_rows - 1)

                            elapsed_total += (t1 - t0)
                            sender_wait_total += (tw1 - tw0)
                            reader_wait_total += (tw2 - tw1)
                            consumer_wait_total += (t1 - tw2)
                            payload_total_bytes += sender_stats["payload_bytes"]
                            wire_total_bytes += sender_stats["wire_bytes"]
                            rows_total += expected_rows
                            windows += 1

                        stage_totals = {
                            "sender_wait": sender_wait_total,
                            "reader_wait": reader_wait_total,
                            "consumer_wait": consumer_wait_total,
                        }
                        longest_stage = max(stage_totals, key=stage_totals.get)
                        downstream_elapsed_total = max(reader_wait_total + consumer_wait_total, 1e-12)
                        results.append(
                            {
                                "nrows": nrows,
                                "ncols": ncols,
                                "n_batches": n_batches_per_case,
                                "windows": windows,
                                "payload_mb": (payload_total_bytes / max(windows, 1)) / (1024 * 1024),
                                "wire_mb": (wire_total_bytes / max(windows, 1)) / (1024 * 1024),
                                "rows_per_s_downstream": rows_total / downstream_elapsed_total,
                                "payload_mbps_downstream": (payload_total_bytes / (1024 * 1024)) / downstream_elapsed_total,
                                "wire_mbps_downstream": (wire_total_bytes / (1024 * 1024)) / downstream_elapsed_total,
                                "rows_per_s_full": rows_total / max(elapsed_total, 1e-12),
                                "payload_mbps_full": (payload_total_bytes / (1024 * 1024)) / max(elapsed_total, 1e-12),
                                "wire_mbps_full": (wire_total_bytes / (1024 * 1024)) / max(elapsed_total, 1e-12),
                                "longest_stage": longest_stage,
                                "sender_wait_pct": (sender_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "reader_wait_pct": (reader_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "consumer_wait_pct": (consumer_wait_total / max(elapsed_total, 1e-12)) * 100.0,
                                "downstream_pct": (downstream_elapsed_total / max(elapsed_total, 1e-12)) * 100.0,
                            }
                        )
        finally:
            stop_event.set()
            sender_control_queue.put({"op": "stop"})
            reader_control_queue.put({"op": "stop"})
            sender_thread.join(timeout=10)
            reader_thread.join(timeout=10)
            consumer_thread.join(timeout=10)

        print(
            f"BENCH Threaded socket->NetworkReader->queue->consumer (~{self.INTEGRATION_CASE_SECONDS:.0f}s/case)"
        )
        for nrows in batch_sizes:
            print(f"batch={nrows} rows x {n_batches_per_case} batches/case")
            print("  cols | payload/run |   wire/run | full rows/s | rd+cons rows/s | full MB/s | rd+cons MB/s | longest")
            for r in [x for x in results if x["nrows"] == nrows]:
                payload_str = self._fmt_size_mb_or_kb(int(r["payload_mb"] * 1024 * 1024))
                wire_str = self._fmt_size_mb_or_kb(int(r["wire_mb"] * 1024 * 1024))
                print(
                    "  "
                    f"{r['ncols']:>4} | "
                    f"{payload_str} | "
                    f"{wire_str} | "
                    f"{r['rows_per_s_full']:>10.0f} | "
                    f"{r['rows_per_s_downstream']:>13.0f} | "
                    f"{r['wire_mbps_full']:>9.2f} | "
                    f"{r['wire_mbps_downstream']:>12.2f} | "
                    f"{r['longest_stage']}"
                )
                print(
                    "       stage share: "
                    f"sender={r['sender_wait_pct']:.0f}% "
                    f"reader={r['reader_wait_pct']:.0f}% "
                    f"consumer={r['consumer_wait_pct']:.0f}% "
                    f"(downstream={r['downstream_pct']:.0f}%)"
                )


if __name__ == "__main__":
    unittest.main()
