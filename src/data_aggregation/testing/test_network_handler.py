"""Run with: python -m unittest src.data_aggregation.testing.test_network_handler -v"""

import queue
import struct
import time
import unittest
from unittest.mock import patch

try:
    import pyarrow as pa
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    pa = None

if pa is not None:
    from src.data_aggregation.network_handler import Membufs, NetworkReader


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
                            "write_rps": nrows / write_s,
                            "read_rps": nrows / read_s,
                            "roundtrip_rps": nrows / roundtrip_s,
                            "write_mbps": payload_mb / write_s,
                            "read_mbps": payload_mb / read_s,
                            "roundtrip_mbps": payload_mb / roundtrip_s,
                        }
                    )

        # Compact matrix-style summary for quick human scanning.
        print(
            f"BENCH Arrow IPC throughput (avg of {self.BENCH_REPEATS} runs per case)"
        )
        for nrows in batch_sizes:
            row_results = [r for r in results if r["nrows"] == nrows]
            print(f"batch={nrows} rows")
            print("  cols | payload | write MB/s | read MB/s | roundtrip MB/s | roundtrip rows/s")
            for r in row_results:
                payload_str = (
                    f"{r['payload_mb']:.2f} MB" if r["payload_mb"] >= 0.1 else f"{r['bytes'] / 1024:.1f} KB"
                )
                print(
                    "  "
                    f"{r['ncols']:>4} | "
                    f"{payload_str:>7} | "
                    f"{r['write_mbps']:>10.2f} | "
                    f"{r['read_mbps']:>9.2f} | "
                    f"{r['roundtrip_mbps']:>14.2f} | "
                    f"{r['roundtrip_rps']:>16.0f}"
                )


if __name__ == "__main__":
    unittest.main()
