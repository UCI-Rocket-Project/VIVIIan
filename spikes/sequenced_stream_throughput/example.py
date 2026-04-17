from __future__ import annotations

import argparse
import time
import uuid

import numpy as np

from pythusa._buffers.ring import SharedRingBuffer
from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding


_FRAME_SHAPE = (2, 2)
_FRAME_DTYPE = np.float64
_STREAM_NAME = "sequenced"


def _frame_nbytes() -> int:
    return int(np.prod(_FRAME_SHAPE, dtype=np.int64)) * np.dtype(_FRAME_DTYPE).itemsize


def _ring_size(frame_count: int) -> int:
    return max(4096, _frame_nbytes() * (frame_count + 8) + 4096)


def _make_ring(*, size: int) -> tuple[SharedRingBuffer, SharedRingBuffer]:
    name = f"sst{uuid.uuid4().hex[:10]}"
    writer = SharedRingBuffer(
        name=name,
        create=True,
        size=size,
        num_readers=1,
        reader=SharedRingBuffer._NO_READER,
        cache_align=False,
    )
    reader = SharedRingBuffer(
        name=name,
        create=False,
        size=size,
        num_readers=1,
        reader=0,
        cache_align=False,
    )
    return writer, reader


def _close_ring(ring: SharedRingBuffer, *, unlink: bool) -> None:
    try:
        ring.close()
    finally:
        if unlink:
            try:
                ring.unlink()
            except FileNotFoundError:
                pass


def _release_view(view: memoryview | None) -> None:
    if view is None:
        return
    try:
        view.release()
    except Exception:
        pass


def _fill_frame(frame: np.ndarray, sequence: int) -> None:
    base = float(sequence * 10)
    frame[0, 0] = base + 1.0
    frame[0, 1] = base + 2.0
    frame[1, 0] = base + 3.0
    frame[1, 1] = base + 4.0


def _assert_frame(frame: np.ndarray, sequence: int) -> None:
    base = float(sequence * 10)
    if frame[0, 0] != base + 1.0:
        raise AssertionError(f"frame {sequence} field 0 mismatch: {frame[0, 0]}")
    if frame[0, 1] != base + 2.0:
        raise AssertionError(f"frame {sequence} field 1 mismatch: {frame[0, 1]}")
    if frame[1, 0] != base + 3.0:
        raise AssertionError(f"frame {sequence} field 2 mismatch: {frame[1, 0]}")
    if frame[1, 1] != base + 4.0:
        raise AssertionError(f"frame {sequence} field 3 mismatch: {frame[1, 1]}")


def run(
    *,
    count: int = 100_000,
    max_seconds: float | None = None,
) -> dict[str, float | int]:
    writer_ring, reader_ring = _make_ring(size=_ring_size(count))
    try:
        writer = make_writer_binding(
            writer_ring,
            name=_STREAM_NAME,
            shape=_FRAME_SHAPE,
            dtype=_FRAME_DTYPE,
        )
        reader = make_reader_binding(
            reader_ring,
            name=_STREAM_NAME,
            shape=_FRAME_SHAPE,
            dtype=_FRAME_DTYPE,
        )
        frame = np.empty(_FRAME_SHAPE, dtype=_FRAME_DTYPE)

        start = time.perf_counter()

        for sequence in range(count):
            _fill_frame(frame, sequence)
            if not writer.write(frame):
                raise RuntimeError(f"writer failed at frame {sequence}")

        for sequence in range(count):
            view = reader.look()
            if view is None:
                raise RuntimeError(f"reader failed at frame {sequence}")
            try:
                read_frame = np.frombuffer(view, dtype=_FRAME_DTYPE).reshape(_FRAME_SHAPE)
                _assert_frame(read_frame, sequence)
                del read_frame
            finally:
                _release_view(view)
                reader.increment()

        elapsed_seconds = time.perf_counter() - start
        frames_per_second = count / elapsed_seconds if elapsed_seconds > 0.0 else float("inf")
        result = {
            "count": count,
            "elapsed_seconds": elapsed_seconds,
            "frames_per_second": frames_per_second,
        }
        if max_seconds is not None and elapsed_seconds > max_seconds:
            raise AssertionError(
                f"elapsed_seconds={elapsed_seconds:.6f} exceeded max_seconds={max_seconds:.6f}"
            )
        return result
    finally:
        _close_ring(reader_ring, unlink=False)
        _close_ring(writer_ring, unlink=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure sequenced pythusa ring throughput with distinct-frame validation."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100_000,
        help="Number of distinct frames to write and validate.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Optional hard upper bound for elapsed runtime.",
    )
    args = parser.parse_args()

    result = run(count=args.count, max_seconds=args.max_seconds)
    print(f"validated {result['count']} frames")
    print(f"elapsed_seconds={result['elapsed_seconds']:.6f}")
    print(f"frames_per_second={result['frames_per_second']:.2f}")


if __name__ == "__main__":
    main()
