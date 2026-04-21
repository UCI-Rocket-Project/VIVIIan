from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


class SharedReaderHub:
    def __init__(self, reader: Any) -> None:
        self._reader = reader
        self._frames: list[Any] = []
        self._positions: dict[int, int] = {}

    def make_tap(self) -> "SharedReaderTap":
        tap = SharedReaderTap(self)
        self._positions[id(tap)] = 0
        return tap

    def read_for(self, tap_id: int) -> Any | None:
        cursor = self._positions[tap_id]
        if cursor >= len(self._frames):
            frame = self._reader.read()
            if frame is not None:
                self._frames.append(frame)
        if cursor >= len(self._frames):
            return None

        frame = self._frames[cursor]
        self._positions[tap_id] = cursor + 1
        self._trim_consumed_frames()
        return frame

    def set_blocking(self, blocking: bool) -> None:
        setter = getattr(self._reader, "set_blocking", None)
        if callable(setter):
            setter(bool(blocking))

    def _trim_consumed_frames(self) -> None:
        if not self._frames or not self._positions:
            return
        trim_count = min(self._positions.values())
        if trim_count <= 0:
            return
        del self._frames[:trim_count]
        for tap_id, cursor in tuple(self._positions.items()):
            self._positions[tap_id] = cursor - trim_count


class SharedReaderTap:
    def __init__(self, hub: SharedReaderHub) -> None:
        self._hub = hub

    def read(self) -> Any | None:
        return self._hub.read_for(id(self))

    def set_blocking(self, blocking: bool) -> None:
        self._hub.set_blocking(blocking)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hub._reader, name)


def fan_out_reader_groups(
    readers: Mapping[str, Any],
    required_stream_groups: Sequence[tuple[str, ...]],
) -> tuple[dict[str, Any], ...]:
    usage_counts: dict[str, int] = {}
    for stream_group in required_stream_groups:
        for stream_name in stream_group:
            usage_counts[stream_name] = usage_counts.get(stream_name, 0) + 1

    shared_hubs: dict[str, SharedReaderHub] = {}
    resolved_groups: list[dict[str, Any]] = []
    for stream_group in required_stream_groups:
        group_bindings: dict[str, Any] = {}
        for stream_name in stream_group:
            reader = readers[stream_name]
            if usage_counts.get(stream_name, 0) <= 1:
                group_bindings[stream_name] = reader
                continue
            hub = shared_hubs.get(stream_name)
            if hub is None:
                hub = SharedReaderHub(reader)
                shared_hubs[stream_name] = hub
            group_bindings[stream_name] = hub.make_tap()
        resolved_groups.append(group_bindings)
    return tuple(resolved_groups)


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
