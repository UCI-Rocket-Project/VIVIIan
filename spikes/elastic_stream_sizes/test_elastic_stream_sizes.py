from __future__ import annotations

import unittest
import uuid

import numpy as np

from pythusa._buffers.ring import SharedRingBuffer
from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding


def _release_view(view: memoryview | None) -> None:
    if view is None:
        return
    try:
        view.release()
    except Exception:
        pass


class ElasticStreamSizeTests(unittest.TestCase):
    def _make_ring(self, *, size: int = 4096) -> tuple[SharedRingBuffer, SharedRingBuffer]:
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
        self.addCleanup(self._close_ring, reader, False)
        self.addCleanup(self._close_ring, writer, True)
        return writer, reader

    @staticmethod
    def _close_ring(ring: SharedRingBuffer, unlink: bool) -> None:
        try:
            ring.close()
        finally:
            if unlink:
                try:
                    ring.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _read_override(stream, *, shape: tuple[int, ...], dtype: np.dtype) -> np.ndarray | None:
        view = stream.look()
        if view is None:
            return None
        try:
            return np.frombuffer(view, dtype=dtype).reshape(shape).copy()
        finally:
            _release_view(view)
            stream.increment()

    def test_reader_can_aggregate_multiple_normal_writes(self) -> None:
        writer_ring, reader_ring = self._make_ring()
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

        for offset in (0, 64, 128, 192):
            self.assertTrue(writer.write(np.arange(offset, offset + 64, dtype=np.float32)))

        aggregated = self._read_override(reader, shape=(256,), dtype=np.float32)

        self.assertIsNotNone(aggregated)
        np.testing.assert_array_equal(
            aggregated,
            np.arange(256, dtype=np.float32),
        )

    def test_reader_can_split_one_normal_write(self) -> None:
        writer_ring, reader_ring = self._make_ring()
        writer = make_writer_binding(
            writer_ring,
            name="samples",
            shape=(256,),
            dtype=np.float32,
        )
        reader = make_reader_binding(
            reader_ring,
            name="samples",
            shape=(256,),
            dtype=np.float32,
        )
        reader.frame_nbytes = 64 * np.dtype(np.float32).itemsize

        payload = np.arange(256, dtype=np.float32)
        self.assertTrue(writer.write(payload))

        chunks = [
            self._read_override(reader, shape=(64,), dtype=np.float32)
            for _ in range(4)
        ]

        for index, chunk in enumerate(chunks):
            self.assertIsNotNone(chunk)
            np.testing.assert_array_equal(
                chunk,
                payload[index * 64:(index + 1) * 64],
            )

    def test_look_waits_until_overridden_size_is_available(self) -> None:
        writer_ring, reader_ring = self._make_ring()
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
        reader.frame_nbytes = 128 * np.dtype(np.float32).itemsize

        self.assertTrue(writer.write(np.arange(64, dtype=np.float32)))
        self.assertIsNone(reader.look())
        self.assertTrue(writer.write(np.arange(64, 128, dtype=np.float32)))

        combined = self._read_override(reader, shape=(128,), dtype=np.float32)

        self.assertIsNotNone(combined)
        np.testing.assert_array_equal(combined, np.arange(128, dtype=np.float32))

    def test_look_preserves_current_wraparound_behavior(self) -> None:
        writer_ring, reader_ring = self._make_ring(size=1536)
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

        for offset in range(0, 320, 64):
            self.assertTrue(writer.write(np.arange(offset, offset + 64, dtype=np.float32)))
        for offset in range(0, 320, 64):
            chunk = self._read_override(reader, shape=(64,), dtype=np.float32)
            self.assertIsNotNone(chunk)
            np.testing.assert_array_equal(
                chunk,
                np.arange(offset, offset + 64, dtype=np.float32),
            )

        reader.frame_nbytes = 128 * np.dtype(np.float32).itemsize
        self.assertTrue(writer.write(np.arange(320, 384, dtype=np.float32)))
        self.assertTrue(writer.write(np.arange(384, 448, dtype=np.float32)))
        self.assertIsNone(reader.look())


if __name__ == "__main__":
    unittest.main()
