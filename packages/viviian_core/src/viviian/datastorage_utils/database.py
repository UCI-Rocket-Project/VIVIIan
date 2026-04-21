from __future__ import annotations

from numbers import Integral
from pathlib import Path
import json
import time
from typing import Self

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


_TIMESTAMP_COLUMN_NAME = "database_timestamp_ns"
_DEFAULT_COMPRESSION = "snappy"

_TYPE_NAME_TO_ARROW = {
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "uint32": pa.uint32(),
    "uint64": pa.uint64(),
    "float32": pa.float32(),
    "float64": pa.float64(),
}


class ParquetDatabase:
    def __init__(
        self,
        root: str | Path,
        schema: pa.Schema,
        shape: tuple[int, int],
        *,
        rows_per_file: int | None = None,
        compression: str = _DEFAULT_COMPRESSION,
    ) -> None:
        self.root = Path(root)
        self.schema = _normalize_user_schema(schema)
        self.shape = _normalize_shape(shape)
        self.row_count, self.column_count = self.shape
        if self.column_count != len(self.schema):
            raise ValueError(
                f"shape {self.shape} does not match schema field count "
                f"{len(self.schema)}."
            )

        if rows_per_file is None:
            rows_per_file = self.row_count * 1024
        self.rows_per_file = _normalize_rows_per_file(rows_per_file, self.row_count)
        if not compression:
            raise ValueError("compression must be non-empty.")
        self.compression = str(compression)

        self.storage_schema = pa.schema(
            [pa.field(_TIMESTAMP_COLUMN_NAME, pa.int64()), *self.schema]
        )
        self.metadata_path = self.root / "metadata.json"
        self.manifest_path = self.root / "manifest.jsonl"

        self.root.mkdir(parents=True, exist_ok=True)
        self._load_or_initialize_metadata()
        self.manifest = _load_manifest(self.manifest_path)

        self._buffer = np.empty((self.rows_per_file, self.column_count), dtype=np.float64)
        self._timestamp_buffer = np.empty(self.rows_per_file, dtype=np.int64)
        self._buffer_size = 0
        self._next_part_index = len(self.manifest) + 1
        self._next_timestamp_ns = (
            int(self.manifest[-1]["end_database_timestamp_ns"]) + 1
            if self.manifest
            else 0
        )
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False

    def store(self, batch: np.ndarray) -> None:
        if self._closed:
            raise RuntimeError("ParquetDatabase is closed.")

        batch = self._normalize_batch(batch)
        row_count = batch.shape[0]
        start = self._buffer_size
        stop = start + row_count

        np.copyto(self._buffer[start:stop], batch)
        self._timestamp_buffer[start:stop] = self._allocate_timestamps(row_count)
        self._buffer_size = stop

        if self._buffer_size == self.rows_per_file:
            self._flush_buffer()

    def retrieve(
        self,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> pa.Table:
        if self._closed:
            raise RuntimeError("ParquetDatabase is closed.")
        if start_ns is not None and not isinstance(start_ns, Integral):
            raise TypeError("start_ns must be an integer or None.")
        if end_ns is not None and not isinstance(end_ns, Integral):
            raise TypeError("end_ns must be an integer or None.")

        if self._buffer_size:
            self._flush_buffer()

        if start_ns is not None and end_ns is not None and int(start_ns) >= int(end_ns):
            return pa.Table.from_batches([], schema=self.storage_schema)

        candidate_paths = [
            self.root / entry["file_name"]
            for entry in self.manifest
            if _overlaps_range(entry, start_ns, end_ns)
        ]
        if not candidate_paths:
            return pa.Table.from_batches([], schema=self.storage_schema)

        dataset = ds.dataset([str(path) for path in candidate_paths], format="parquet")
        expression = None
        if start_ns is not None:
            expression = ds.field(_TIMESTAMP_COLUMN_NAME) >= int(start_ns)
        if end_ns is not None:
            end_expression = ds.field(_TIMESTAMP_COLUMN_NAME) < int(end_ns)
            expression = (
                end_expression
                if expression is None
                else expression & end_expression
            )
        return dataset.to_table(filter=expression)

    def close(self) -> None:
        if self._closed:
            return
        if self._buffer_size:
            self._flush_buffer()
        self._closed = True

    def _normalize_batch(self, batch: np.ndarray) -> np.ndarray:
        if not isinstance(batch, np.ndarray):
            raise TypeError("batch must be a numpy.ndarray.")
        if batch.ndim != 2:
            raise ValueError("batch must be a 2D numpy.ndarray.")
        if tuple(batch.shape) != self.shape:
            raise ValueError(
                f"batch shape mismatch: expected {self.shape}, got "
                f"{tuple(batch.shape)}."
            )
        if not np.issubdtype(batch.dtype, np.number):
            raise TypeError("batch dtype must be numeric.")

        batch = np.asarray(batch, dtype=np.float64)
        if not batch.flags.c_contiguous:
            batch = np.ascontiguousarray(batch)
        return batch

    def _allocate_timestamps(self, row_count: int) -> np.ndarray:
        start_ns = max(time.time_ns(), self._next_timestamp_ns)
        timestamps = start_ns + np.arange(row_count, dtype=np.int64)
        self._next_timestamp_ns = int(timestamps[-1]) + 1
        return timestamps

    def _flush_buffer(self) -> None:
        row_count = self._buffer_size
        file_name = f"part-{self._next_part_index:08d}.parquet"
        file_path = self.root / file_name
        table = self._buffer_to_table(row_count)
        pq.write_table(
            table,
            file_path,
            compression=self.compression,
            row_group_size=row_count,
        )

        entry = {
            "file_name": file_name,
            "row_count": row_count,
            "start_database_timestamp_ns": int(self._timestamp_buffer[0]),
            "end_database_timestamp_ns": int(self._timestamp_buffer[row_count - 1]),
        }
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        self.manifest.append(entry)
        self._next_part_index += 1
        self._buffer_size = 0

    def _buffer_to_table(self, row_count: int) -> pa.Table:
        arrays = [pa.array(self._timestamp_buffer[:row_count], type=pa.int64())]
        payload = self._buffer[:row_count]
        for index, field in enumerate(self.schema):
            arrays.append(pa.array(payload[:, index], type=field.type))
        return pa.Table.from_arrays(arrays, schema=self.storage_schema)

    def _load_or_initialize_metadata(self) -> None:
        if self.metadata_path.exists():
            with self.metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            stored_schema = _schema_from_json(metadata["schema"])
            stored_shape = _normalize_shape(metadata["shape"])
            stored_rows_per_file = int(metadata["rows_per_file"])
            stored_compression = str(metadata["compression"])
            stored_timestamp_column = metadata["timestamp_column"]

            if stored_schema != self.schema:
                raise ValueError("schema does not match existing database metadata.")
            if stored_shape != self.shape:
                raise ValueError("shape does not match existing database metadata.")
            if stored_rows_per_file != self.rows_per_file:
                raise ValueError(
                    "rows_per_file does not match existing database metadata."
                )
            if stored_compression != self.compression:
                raise ValueError(
                    "compression does not match existing database metadata."
                )
            if stored_timestamp_column != _TIMESTAMP_COLUMN_NAME:
                raise ValueError("timestamp column does not match existing metadata.")
            return

        metadata = {
            "shape": list(self.shape),
            "rows_per_file": self.rows_per_file,
            "compression": self.compression,
            "timestamp_column": _TIMESTAMP_COLUMN_NAME,
            "schema": _schema_to_json(self.schema),
        }
        with self.metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)


__all__ = ["ParquetDatabase"]


def _normalize_shape(shape: tuple[int, int]) -> tuple[int, int]:
    try:
        shape_tuple = tuple(shape)
    except TypeError as exc:
        raise TypeError("shape must be an iterable of positive integers.") from exc
    if len(shape_tuple) != 2:
        raise ValueError("shape must be a 2D (rows, columns) tuple.")

    normalized: list[int] = []
    for dim in shape_tuple:
        if isinstance(dim, bool) or not isinstance(dim, Integral):
            raise TypeError("shape dimensions must be integers.")
        if int(dim) < 1:
            raise ValueError("shape dimensions must be >= 1.")
        normalized.append(int(dim))
    return normalized[0], normalized[1]


def _normalize_rows_per_file(rows_per_file: int, batch_rows: int) -> int:
    if isinstance(rows_per_file, bool) or not isinstance(rows_per_file, Integral):
        raise TypeError("rows_per_file must be an integer.")
    rows_per_file = int(rows_per_file)
    if rows_per_file < 1:
        raise ValueError("rows_per_file must be >= 1.")
    if rows_per_file % batch_rows != 0:
        raise ValueError("rows_per_file must be a positive multiple of shape[0].")
    return rows_per_file


def _normalize_user_schema(schema: pa.Schema) -> pa.Schema:
    if not isinstance(schema, pa.Schema):
        raise TypeError("schema must be a pyarrow.Schema.")
    if len(schema) < 1:
        raise ValueError("schema must define at least one field.")

    normalized_fields: list[pa.Field] = []
    for field in schema:
        if not field.name:
            raise ValueError("schema fields must be named.")
        type_name = _arrow_type_to_name(field.type)
        if type_name is None:
            raise TypeError("schema fields must be numeric Arrow types.")
        normalized_fields.append(pa.field(field.name, _TYPE_NAME_TO_ARROW[type_name]))
    return pa.schema(normalized_fields)


def _arrow_type_to_name(data_type: pa.DataType) -> str | None:
    for type_name, arrow_type in _TYPE_NAME_TO_ARROW.items():
        if data_type == arrow_type:
            return type_name
    return None


def _schema_to_json(schema: pa.Schema) -> list[dict[str, str]]:
    return [
        {"name": field.name, "type": _arrow_type_to_name(field.type)}
        for field in schema
    ]


def _schema_from_json(data: list[dict[str, str]]) -> pa.Schema:
    fields = [
        pa.field(entry["name"], _TYPE_NAME_TO_ARROW[entry["type"]])
        for entry in data
    ]
    return pa.schema(fields)


def _load_manifest(path: Path) -> list[dict[str, int | str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _overlaps_range(
    entry: dict[str, int | str],
    start_ns: int | None,
    end_ns: int | None,
) -> bool:
    entry_start = int(entry["start_database_timestamp_ns"])
    entry_end = int(entry["end_database_timestamp_ns"])
    if start_ns is not None and entry_end < int(start_ns):
        return False
    if end_ns is not None and entry_start >= int(end_ns):
        return False
    return True
