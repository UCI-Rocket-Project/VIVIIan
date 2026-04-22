from __future__ import annotations

from pathlib import Path
from typing import Mapping, Self

import numpy as np

from viviian.connector_utils import StreamSpec
from viviian.datastorage_utils import ParquetDatabase


class RawTelemetryRecorder:
    def __init__(
        self,
        root: str | Path,
        stream_specs: Mapping[str, StreamSpec],
        *,
        rows_per_file: int,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._databases = {
            stream_id: ParquetDatabase(
                self.root / stream_id,
                stream_spec.schema,
                stream_spec.shape,
                rows_per_file=rows_per_file,
            )
            for stream_id, stream_spec in stream_specs.items()
        }
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False

    def store(self, stream_id: str, batch: np.ndarray) -> None:
        if self._closed:
            raise RuntimeError("RawTelemetryRecorder is closed.")

        try:
            database = self._databases[stream_id]
        except KeyError as exc:
            raise ValueError(f"unknown raw stream: {stream_id!r}") from exc
        database.store(batch)

    def close(self) -> None:
        if self._closed:
            return
        for database in self._databases.values():
            database.close()
        self._closed = True


__all__ = ["RawTelemetryRecorder"]
