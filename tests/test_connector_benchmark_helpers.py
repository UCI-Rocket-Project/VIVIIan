from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = ROOT / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _load_module(module_name: str, relative_path: str):
    module_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module {relative_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


BENCH = _load_module(
    "test_connector_throughput_benchmark_module",
    "benchmarks/connector_throughput_benchmark.py",
)
REPORTING = _load_module(
    "test_connector_benchmark_reporting_module",
    "benchmarks/_reporting.py",
)


class ConnectorBenchmarkHelperTests(unittest.TestCase):
    def test_parse_int_csv_accepts_valid_values(self) -> None:
        values = BENCH._parse_int_csv("512, 2048,8192", name="rows", minimum=1)
        self.assertEqual(values, (512, 2048, 8192))

    def test_parse_int_csv_rejects_values_below_minimum(self) -> None:
        with self.assertRaises(SystemExit):
            BENCH._parse_int_csv("1,0", name="columns", minimum=2)

    def test_prepare_batch_writes_metadata_columns(self) -> None:
        batch = np.zeros((4, 5), dtype=np.float64)
        BENCH._prepare_batch(batch, sequence=7, sent_offset_ns=12345)

        self.assertTrue(np.all(batch[:, BENCH.SEQUENCE_COLUMN] == 7.0))
        self.assertTrue(np.all(batch[:, BENCH.SENT_AT_NS_COLUMN] == 12345.0))

    def test_metric_matrix_preserves_shape_order(self) -> None:
        results = [
            BENCH.BenchmarkResult(
                rows=512,
                columns=4,
                payload_columns=2,
                batch_bytes=16384,
                published_batches=10,
                observed_batches=9,
                published_batches_s=100.0,
                observed_batches_s=90.0,
                published_mb_s=1.0,
                observed_mb_s=0.9,
                overwrite_fraction=0.1,
                latency_mean_ms=1.0,
                latency_p50_ms=0.9,
                latency_p95_ms=1.1,
                latency_p99_ms=1.2,
                latency_max_ms=1.3,
            ),
            BENCH.BenchmarkResult(
                rows=512,
                columns=8,
                payload_columns=6,
                batch_bytes=32768,
                published_batches=10,
                observed_batches=9,
                published_batches_s=200.0,
                observed_batches_s=180.0,
                published_mb_s=2.0,
                observed_mb_s=1.8,
                overwrite_fraction=0.1,
                latency_mean_ms=2.0,
                latency_p50_ms=1.9,
                latency_p95_ms=2.1,
                latency_p99_ms=2.2,
                latency_max_ms=2.3,
            ),
            BENCH.BenchmarkResult(
                rows=2048,
                columns=4,
                payload_columns=2,
                batch_bytes=65536,
                published_batches=10,
                observed_batches=9,
                published_batches_s=300.0,
                observed_batches_s=270.0,
                published_mb_s=3.0,
                observed_mb_s=2.7,
                overwrite_fraction=0.1,
                latency_mean_ms=3.0,
                latency_p50_ms=2.9,
                latency_p95_ms=3.1,
                latency_p99_ms=3.2,
                latency_max_ms=3.3,
            ),
            BENCH.BenchmarkResult(
                rows=2048,
                columns=8,
                payload_columns=6,
                batch_bytes=131072,
                published_batches=10,
                observed_batches=9,
                published_batches_s=400.0,
                observed_batches_s=360.0,
                published_mb_s=4.0,
                observed_mb_s=3.6,
                overwrite_fraction=0.1,
                latency_mean_ms=4.0,
                latency_p50_ms=3.9,
                latency_p95_ms=4.1,
                latency_p99_ms=4.2,
                latency_max_ms=4.3,
            ),
        ]

        matrix = BENCH._metric_matrix(
            results,
            rows=(512, 2048),
            columns=(4, 8),
            value_attr="observed_mb_s",
        )

        np.testing.assert_array_equal(
            matrix,
            np.asarray([[0.9, 1.8], [2.7, 3.6]], dtype=np.float64),
        )

    def test_build_and_emit_payload_writes_json(self) -> None:
        payload = REPORTING.build_payload(
            benchmark="connector_throughput_benchmark",
            config={"rows": [512], "columns": [4]},
            results=[],
            summary={"best_observed_mb_s": np.float64(12.5)},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "connector-benchmark.json"
            REPORTING.emit_payload(payload, json_stdout=False, json_out=str(out_path))
            self.assertTrue(out_path.exists())
            text = out_path.read_text(encoding="utf-8")
            self.assertIn('"benchmark": "connector_throughput_benchmark"', text)
            self.assertIn('"best_observed_mb_s": 12.5', text)


if __name__ == "__main__":
    unittest.main()
