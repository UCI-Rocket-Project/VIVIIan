from __future__ import annotations

import uuid

import numpy as np

from pythusa._buffers.ring import SharedRingBuffer
from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding


def _make_ring(*, size: int = 4096) -> tuple[SharedRingBuffer, SharedRingBuffer]:
    name = f"es{uuid.uuid4().hex[:10]}"
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


def _release_view(view: memoryview | None) -> None:
    if view is None:
        return
    try:
        view.release()
    except Exception:
        pass


def _close_ring(ring: SharedRingBuffer, *, unlink: bool) -> None:
    try:
        ring.close()
    finally:
        if unlink:
            try:
                ring.unlink()
            except FileNotFoundError:
                pass


def _read_with_overridden_size(
    stream,
    *,
    shape: tuple[int, ...],
    dtype: np.dtype,
) -> np.ndarray | None:
    view = stream.look()
    if view is None:
        return None
    try:
        return np.frombuffer(view, dtype=dtype).reshape(shape).copy()
    finally:
        _release_view(view)
        stream.increment()


def main() -> None:
    writer_ring, reader_ring = _make_ring()
    try:
        writer = make_writer_binding(
            writer_ring,
            name="samples",
            shape=(64,),
            dtype=np.float32,
        )
        reader = make_reader_binding(
            reader_ring,
            name="samples",
            shape=(64,),
            dtype=np.float32,
        )

        reader.frame_nbytes = 256 * np.dtype(np.float32).itemsize

        blocks = [
            np.arange(offset, offset + 64, dtype=np.float32)
            for offset in (0, 64, 128, 192)
        ]
        for block in blocks:
            if not writer.write(block):
                raise RuntimeError("Writer failed to publish a block.")

        aggregated = _read_with_overridden_size(
            reader,
            shape=(256,),
            dtype=np.float32,
        )
        if aggregated is None:
            raise RuntimeError("Reader did not see enough bytes for the larger local frame.")

        print("aggregated shape:", aggregated.shape)
        print(aggregated)
    finally:
        _close_ring(reader_ring, unlink=False)
        _close_ring(writer_ring, unlink=True)


if __name__ == "__main__":
    main()
