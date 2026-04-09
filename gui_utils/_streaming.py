from __future__ import annotations

from typing import Any

import numpy as np


def validate_numeric_reader(stream_name: str, reader: Any) -> None:
    shape = getattr(reader, "shape", None)
    dtype = getattr(reader, "dtype", None)
    if shape is None or dtype is None:
        raise TypeError(
            f"Reader {stream_name!r} must expose shape and dtype attributes."
        )

    shape_tuple = tuple(shape)
    if len(shape_tuple) != 2 or shape_tuple[0] != 2 or shape_tuple[1] < 1:
        raise ValueError(
            f"Reader {stream_name!r} must have shape (2, rows), got {shape_tuple}."
        )

    numpy_dtype = np.dtype(dtype)
    if numpy_dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise ValueError(
            f"Reader {stream_name!r} must use float32 or float64, got {numpy_dtype}."
        )


def drain_numeric_reader(reader: Any) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    if hasattr(reader, "read"):
        while True:
            frame = reader.read()
            if frame is None:
                return frames
            frames.append(np.asarray(frame))
    if not hasattr(reader, "look") or not hasattr(reader, "increment"):
        raise TypeError("Reader must expose read() or look()/increment().")

    while True:
        view = reader.look()
        if view is None:
            return frames
        frame = np.frombuffer(view, dtype=np.dtype(reader.dtype)).reshape(tuple(reader.shape)).copy()
        del view
        reader.increment()
        frames.append(frame)


def normalize_numeric_batch(
    frame: np.ndarray,
    *,
    context_name: str,
) -> tuple[np.ndarray, np.ndarray]:
    batch = np.asarray(frame)
    if batch.ndim != 2 or batch.shape[0] != 2:
        raise ValueError(f"Expected {context_name} shape (2, rows), got {batch.shape}.")
    if batch.dtype not in (np.float32, np.float64):
        raise ValueError(
            f"Expected float32 or float64 {context_name}, got {batch.dtype}."
        )

    timestamps = np.asarray(batch[0], dtype=np.float64)
    values = np.asarray(batch[1], dtype=np.float64)
    if timestamps.size != values.size:
        raise ValueError("Timestamp and value rows must have equal length.")

    finite_mask = np.isfinite(timestamps) & np.isfinite(values)
    timestamps = timestamps[finite_mask]
    values = values[finite_mask]
    if timestamps.size == 0:
        return timestamps, values

    if timestamps.size > 1 and np.any(np.diff(timestamps) < 0.0):
        order = np.argsort(timestamps, kind="stable")
        timestamps = timestamps[order]
        values = values[order]

    return timestamps, values
