from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
import pyarrow as pa

from viviian.datastorage_utils import ParquetDatabase


class TestParquetDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.schema = pa.schema(
            [
                pa.field("value_a", pa.float64()),
                pa.field("value_b", pa.float64()),
            ]
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validation_rejects_bad_schema_shape_and_rows_per_file(self) -> None:
        with self.assertRaises(ValueError):
            ParquetDatabase(self.root / "bad-width", self.schema, shape=(2, 3))

        with self.assertRaises(TypeError):
            ParquetDatabase(
                self.root / "bad-schema",
                pa.schema([pa.field("label", pa.string())]),
                shape=(2, 1),
            )

        with self.assertRaises(ValueError):
            ParquetDatabase(
                self.root / "bad-rows",
                self.schema,
                shape=(2, 2),
                rows_per_file=3,
            )

    def test_store_creates_multiple_files_and_manifest_entries(self) -> None:
        root = self.root / "store-manifest"
        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            db.store(np.array([[1.0, 2.0], [3.0, 4.0]]))
            db.store(np.array([[5.0, 6.0], [7.0, 8.0]]))
            db.store(np.array([[9.0, 10.0], [11.0, 12.0]]))

        manifest = _load_manifest(root / "manifest.jsonl")
        self.assertEqual(len(manifest), 2)
        self.assertEqual(manifest[0]["file_name"], "part-00000001.parquet")
        self.assertEqual(manifest[1]["file_name"], "part-00000002.parquet")
        self.assertEqual(manifest[0]["row_count"], 4)
        self.assertEqual(manifest[1]["row_count"], 2)
        self.assertLess(
            manifest[0]["end_database_timestamp_ns"],
            manifest[1]["start_database_timestamp_ns"],
        )

    def test_retrieve_within_one_file(self) -> None:
        root = self.root / "retrieve-one"
        batches = [
            np.array([[1.0, 2.0], [3.0, 4.0]]),
            np.array([[5.0, 6.0], [7.0, 8.0]]),
            np.array([[9.0, 10.0], [11.0, 12.0]]),
        ]

        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            for batch in batches:
                db.store(batch)

        manifest = _load_manifest(root / "manifest.jsonl")
        first_file = manifest[0]
        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            table = db.retrieve(
                start_ns=first_file["start_database_timestamp_ns"],
                end_ns=first_file["end_database_timestamp_ns"] + 1,
            )

        self.assertEqual(table.num_rows, 4)
        np.testing.assert_allclose(
            table.column("value_a").to_numpy(),
            np.array([1.0, 3.0, 5.0, 7.0]),
        )
        np.testing.assert_allclose(
            table.column("value_b").to_numpy(),
            np.array([2.0, 4.0, 6.0, 8.0]),
        )

    def test_retrieve_across_file_boundaries(self) -> None:
        root = self.root / "retrieve-many"
        all_batches = [
            np.array([[1.0, 2.0], [3.0, 4.0]]),
            np.array([[5.0, 6.0], [7.0, 8.0]]),
            np.array([[9.0, 10.0], [11.0, 12.0]]),
        ]

        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            for batch in all_batches:
                db.store(batch)

        manifest = _load_manifest(root / "manifest.jsonl")
        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            table = db.retrieve(
                start_ns=manifest[0]["start_database_timestamp_ns"],
                end_ns=manifest[-1]["end_database_timestamp_ns"] + 1,
            )

        self.assertEqual(table.num_rows, 6)
        np.testing.assert_allclose(
            table.column("value_a").to_numpy(),
            np.array([1.0, 3.0, 5.0, 7.0, 9.0, 11.0]),
        )
        np.testing.assert_allclose(
            table.column("value_b").to_numpy(),
            np.array([2.0, 4.0, 6.0, 8.0, 10.0, 12.0]),
        )

    def test_reopen_continues_append_and_partial_tail_flushes(self) -> None:
        root = self.root / "reopen"
        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            db.store(np.array([[1.0, 2.0], [3.0, 4.0]]))
            db.store(np.array([[5.0, 6.0], [7.0, 8.0]]))

        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            db.store(np.array([[9.0, 10.0], [11.0, 12.0]]))

        manifest = _load_manifest(root / "manifest.jsonl")
        self.assertEqual([entry["file_name"] for entry in manifest], [
            "part-00000001.parquet",
            "part-00000002.parquet",
        ])
        self.assertEqual([entry["row_count"] for entry in manifest], [4, 2])

        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            table = db.retrieve()

        self.assertEqual(table.num_rows, 6)
        np.testing.assert_allclose(
            table.column("value_a").to_numpy(),
            np.array([1.0, 3.0, 5.0, 7.0, 9.0, 11.0]),
        )

    def test_empty_time_range_returns_empty_table(self) -> None:
        root = self.root / "empty-range"
        with ParquetDatabase(root, self.schema, shape=(2, 2), rows_per_file=4) as db:
            db.store(np.array([[1.0, 2.0], [3.0, 4.0]]))
            table = db.retrieve(start_ns=10, end_ns=10)

        self.assertEqual(table.num_rows, 0)
        self.assertEqual(table.schema, pa.schema([
            pa.field("database_timestamp_ns", pa.int64()),
            *self.schema,
        ]))


def _load_manifest(path: Path) -> list[dict[str, int | str]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
