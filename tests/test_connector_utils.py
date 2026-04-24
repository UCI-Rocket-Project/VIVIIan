from __future__ import annotations

import os
import socket
import time
import unittest
import uuid
from typing import Callable, TypeVar

import numpy as np
import pyarrow as pa

from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec
try:
    from pythusa import Pipeline
    from pythusa._buffers.ring import SharedRingBuffer
    from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding
except ModuleNotFoundError:  # pragma: no cover - integration env dependency
    Pipeline = None
    SharedRingBuffer = None
    make_reader_binding = None
    make_writer_binding = None

T = TypeVar("T")

_PIPELINE_CONNECTOR_PORT = 9129
_PIPELINE_STREAM_ID = "telemetry"
_PIPELINE_BATCH = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)
_MIRRORED_STREAM_NAME = "mirrored"
_MIRRORED_STREAM_BASE_SHAPE = _PIPELINE_BATCH.shape
_MIRRORED_STREAM_SHAPE = (2, 4)
_MIRRORED_STREAM_DTYPE = np.float64
_MIRRORED_LIVE_FRAME_COUNT = 1
_MIRRORED_TOTAL_FRAME_COUNT = 2
_MIRRORED_DONE_EVENT = "done"
_MIRRORED_TIMEOUT_SECONDS = 3.0
_CONNECTOR_STRESS_FRAME_COUNT = int(
    os.environ.get("CONNECTOR_STRESS_FRAME_COUNT", "100000")
)
_CONNECTOR_STRESS_TIMEOUT_SECONDS = 30.0
_RUN_CONNECTOR_STRESS_TESTS = os.environ.get("RUN_CONNECTOR_STRESS_TESTS") == "1"
_TIME_RECEIVED_COLUMN = 2
_CONNECTION_ALIVE_COLUMN = 3


def _local_flight_bind_available() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except OSError:
        return False
    finally:
        sock.close()
    return True


_LOCAL_FLIGHT_BIND_AVAILABLE = _local_flight_bind_available()


def _connector_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("signal", pa.float64()),
            pa.field("weight", pa.float64()),
        ]
    )


def _mirrored_frame_target_nbytes(frame_count: int) -> int:
    frame_nbytes = (
        int(np.prod(_MIRRORED_STREAM_SHAPE, dtype=np.int64))
        * np.dtype(_MIRRORED_STREAM_DTYPE).itemsize
    )
    return frame_nbytes * frame_count


def _wait_for_mirrored_frames(
    mirrored,
    expected_frame_count: int,
    *,
    timeout_seconds: float = _MIRRORED_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    target_nbytes = _mirrored_frame_target_nbytes(expected_frame_count)
    while time.monotonic() < deadline:
        if int(mirrored.raw.get_write_pos()) >= target_nbytes:
            return
        time.sleep(0.01)
    raise RuntimeError("timed out waiting for mirrored frames to be written")


def _release_view(view: memoryview | None) -> None:
    if view is None:
        return
    try:
        view.release()
    except Exception:
        pass


def _make_ring(
    *,
    size: int,
) -> tuple[SharedRingBuffer, SharedRingBuffer] | tuple[None, None]:
    if SharedRingBuffer is None:
        return None, None

    name = f"connector{uuid.uuid4().hex[:10]}"
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


def _close_ring(ring: SharedRingBuffer | None, *, unlink: bool) -> None:
    if ring is None:
        return
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
    stream.frame_nbytes = int(np.prod(shape, dtype=np.int64)) * np.dtype(dtype).itemsize
    view = stream.look()
    if view is None:
        return None
    try:
        return np.frombuffer(view, dtype=dtype).reshape(shape).copy()
    finally:
        _release_view(view)
        stream.increment()


def _assert_live_mirrored_frame(frame: np.ndarray) -> None:
    if frame.shape != _MIRRORED_STREAM_SHAPE:
        raise AssertionError(
            f"expected live mirrored frame shape {_MIRRORED_STREAM_SHAPE}, "
            f"got {tuple(frame.shape)}"
        )
    np.testing.assert_array_equal(
        frame[:, : _PIPELINE_BATCH.shape[1]],
        _PIPELINE_BATCH,
    )
    if not np.all(frame[:, _TIME_RECEIVED_COLUMN] == frame[0, _TIME_RECEIVED_COLUMN]):
        raise AssertionError("time_received column must be constant across the live frame")
    if not frame[0, _TIME_RECEIVED_COLUMN] > 0.0:
        raise AssertionError("time_received must be > 0 for the live frame")
    if not np.all(frame[:, _CONNECTION_ALIVE_COLUMN] == 1.0):
        raise AssertionError("connection_alive must be 1.0 for the live frame")


def _assert_disconnect_mirrored_frame(frame: np.ndarray) -> None:
    if frame.shape != _MIRRORED_STREAM_SHAPE:
        raise AssertionError(
            f"expected disconnect mirrored frame shape {_MIRRORED_STREAM_SHAPE}, "
            f"got {tuple(frame.shape)}"
        )
    if not np.all(np.isnan(frame[:, : _PIPELINE_BATCH.shape[1]])):
        raise AssertionError("disconnect mirrored payload must be NaN")
    if not np.all(frame[:, _TIME_RECEIVED_COLUMN] == frame[0, _TIME_RECEIVED_COLUMN]):
        raise AssertionError(
            "time_received column must be constant across the disconnect frame"
        )
    if not frame[0, _TIME_RECEIVED_COLUMN] > 0.0:
        raise AssertionError("time_received must be > 0 for the disconnect frame")
    if not np.all(frame[:, _CONNECTION_ALIVE_COLUMN] == 0.0):
        raise AssertionError("connection_alive must be 0.0 for the disconnect frame")


def _make_integrity_batch(sequence: int) -> np.ndarray:
    base = float(sequence * 10)
    return np.array(
        [
            [base + 1.0, base + 2.0],
            [base + 3.0, base + 4.0],
        ],
        dtype=np.float64,
    )


def _assert_integrity_frame(frame: np.ndarray, sequence: int) -> None:
    if frame.shape != _MIRRORED_STREAM_SHAPE:
        raise AssertionError(
            f"expected integrity mirrored frame shape {_MIRRORED_STREAM_SHAPE}, "
            f"got {tuple(frame.shape)}"
        )
    np.testing.assert_array_equal(
        frame[:, : _PIPELINE_BATCH.shape[1]],
        _make_integrity_batch(sequence),
    )
    if not np.all(frame[:, _TIME_RECEIVED_COLUMN] == frame[0, _TIME_RECEIVED_COLUMN]):
        raise AssertionError(
            "time_received column must be constant across the integrity frame"
        )
    if not frame[0, _TIME_RECEIVED_COLUMN] > 0.0:
        raise AssertionError("time_received must be > 0 for the integrity frame")
    if not np.all(frame[:, _CONNECTION_ALIVE_COLUMN] == 1.0):
        raise AssertionError("connection_alive must be 1.0 for the integrity frame")


def _read_next_mirrored_frame(
    reader,
    *,
    timeout_seconds: float,
) -> np.ndarray | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        frame = _read_with_overridden_size(
            reader,
            shape=_MIRRORED_STREAM_SHAPE,
            dtype=_MIRRORED_STREAM_DTYPE,
        )
        if frame is not None:
            return frame
        time.sleep(0)
    return None


def _connector_ingest_task(mirrored) -> None:
    spec = StreamSpec(
        stream_id=_PIPELINE_STREAM_ID,
        schema=_connector_schema(),
        shape=_PIPELINE_BATCH.shape,
        stream=mirrored,
    )

    with ReceiveConnector(spec, port=_PIPELINE_CONNECTOR_PORT):
        _wait_for_mirrored_frames(mirrored, _MIRRORED_TOTAL_FRAME_COUNT)


def _connector_mirror_sink_task(mirrored, done) -> None:
    deadline = time.monotonic() + _MIRRORED_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        live_frame = _read_with_overridden_size(
            mirrored,
            shape=_MIRRORED_STREAM_SHAPE,
            dtype=_MIRRORED_STREAM_DTYPE,
        )
        if live_frame is None:
            time.sleep(0.01)
            continue
        _assert_live_mirrored_frame(live_frame)
        break
    else:
        raise RuntimeError("timed out waiting for live mirrored frame")

    deadline = time.monotonic() + _MIRRORED_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        disconnect_frame = _read_with_overridden_size(
            mirrored,
            shape=_MIRRORED_STREAM_SHAPE,
            dtype=_MIRRORED_STREAM_DTYPE,
        )
        if disconnect_frame is None:
            time.sleep(0.01)
            continue
        _assert_disconnect_mirrored_frame(disconnect_frame)
        done.signal()
        return

    raise RuntimeError("timed out waiting for disconnect mirrored frame")


class CountingSendConnector(SendConnector):
    def __init__(
        self,
        stream_spec: StreamSpec,
        port: int,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.do_get_call_count = 0
        super().__init__(stream_spec, port)

    def do_get(self, _context, ticket):
        self.do_get_call_count += 1
        return super().do_get(_context, ticket)

    def _batch_to_record_batch(self, batch: np.ndarray) -> pa.RecordBatch:
        if self.delay_seconds > 0.0:
            time.sleep(self.delay_seconds)
        return super()._batch_to_record_batch(batch)


class RecordingStream:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []

    def write(self, batch: np.ndarray) -> None:
        self.frames.append(np.array(batch, copy=True))


class FakeChunk:
    def __init__(self, batch: pa.RecordBatch) -> None:
        self.data = batch


class FakeReader:
    def __init__(self, batches: list[pa.RecordBatch]) -> None:
        self._batches = list(batches)
        self.cancelled = False

    def read_chunk(self) -> FakeChunk:
        if not self._batches:
            raise StopIteration
        return FakeChunk(self._batches.pop(0))

    def cancel(self) -> None:
        self.cancelled = True


class ConnectorUtilsTests(unittest.TestCase):
    def make_schema(self) -> pa.Schema:
        return _connector_schema()

    def make_spec(
        self,
        *,
        stream_id: str = "telemetry",
        rows: int = 2,
        columns: int = 2,
        stream: object | None = None,
    ) -> StreamSpec:
        return StreamSpec(
            stream_id=stream_id,
            schema=self.make_schema(),
            shape=(rows, columns),
            stream=stream,
        )

    def wait_for_value(
        self,
        getter: Callable[[], T | None],
        *,
        timeout_seconds: float = 2.0,
    ) -> T | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            value = getter()
            if value is not None:
                return value
            time.sleep(0.01)
        return None

    def require_local_flight(self) -> None:
        if not _LOCAL_FLIGHT_BIND_AVAILABLE:
            self.skipTest("local Flight socket binding is not permitted in this environment")

    def test_stream_spec_requires_explicit_2d_shape(self) -> None:
        with self.assertRaises(TypeError):
            StreamSpec(stream_id="telemetry", schema=self.make_schema())  # type: ignore[call-arg]

        with self.subTest("1d shape"):
            with self.assertRaisesRegex(ValueError, "2D"):
                StreamSpec(
                    stream_id="telemetry",
                    schema=self.make_schema(),
                    shape=(4,),
                )

        with self.subTest("3d shape"):
            with self.assertRaisesRegex(ValueError, "2D"):
                StreamSpec(
                    stream_id="telemetry",
                    schema=self.make_schema(),
                    shape=(4, 2, 1),
                )

        with self.subTest("non-positive"):
            with self.assertRaisesRegex(ValueError, ">= 1"):
                StreamSpec(
                    stream_id="telemetry",
                    schema=self.make_schema(),
                    shape=(0, 2),
                )

        with self.subTest("width mismatch"):
            with self.assertRaisesRegex(ValueError, "does not match schema field count"):
                StreamSpec(
                    stream_id="telemetry",
                    schema=self.make_schema(),
                    shape=(4, 3),
                )

    def test_open_and_close_are_idempotent(self) -> None:
        self.require_local_flight()
        spec = self.make_spec()

        sender = SendConnector(spec, port=0)
        sender.open()
        opened_port = sender.port
        sender.open()
        self.assertEqual(sender.port, opened_port)
        sender.close()
        sender.close()

        receiver = ReceiveConnector(spec, port=opened_port)
        receiver.open()
        receiver.open()
        receiver.close()
        receiver.close()

    def test_send_connector_requires_numpy_and_exact_2d_shape(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=3)
        sender = SendConnector(spec, port=9107)

        with self.assertRaisesRegex(TypeError, "numpy.ndarray"):
            sender.send_numpy([[1.0, 2.0]])  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "2D numpy.ndarray"):
            sender.send_numpy(np.array([1.0, 2.0], dtype=np.float64))

        with self.assertRaisesRegex(ValueError, "batch shape mismatch"):
            sender.send_numpy(np.ones((2, 2), dtype=np.float64))

        with self.assertRaisesRegex(ValueError, "batch shape mismatch"):
            sender.send_numpy(np.ones((3, 3), dtype=np.float64))

    def test_round_trip_updates_latest_batch_and_has_batch(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        expected = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)

        with SendConnector(spec, port=0) as sender, ReceiveConnector(
            spec,
            port=sender.port,
        ) as receiver:
            self.assertFalse(receiver.has_batch)
            sender.send_numpy(expected.astype(np.float32))

            received = self.wait_for_value(
                lambda: receiver.batch.copy() if receiver.has_batch else None
            )

        self.assertIsNotNone(received)
        assert received is not None
        self.assertTrue(receiver.has_batch)
        np.testing.assert_array_equal(received, expected)

    def test_multiple_sends_leave_only_latest_batch(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        first = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)
        second = np.array([[3.0, 30.0], [4.0, 40.0]], dtype=np.float64)
        third = np.array([[5.0, 50.0], [6.0, 60.0]], dtype=np.float64)

        with SendConnector(spec, port=0) as sender, ReceiveConnector(
            spec,
            port=sender.port,
        ) as receiver:
            sender.send_numpy(first)
            sender.send_numpy(second)
            sender.send_numpy(third)

            received = self.wait_for_value(
                lambda: receiver.batch.copy()
                if receiver.has_batch and np.array_equal(receiver.batch, third)
                else None
            )

        self.assertIsNotNone(received)
        assert received is not None
        np.testing.assert_array_equal(received, third)

    def test_send_connector_is_nonblocking_under_slow_stream(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        batches = [
            np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64),
            np.array([[3.0, 30.0], [4.0, 40.0]], dtype=np.float64),
            np.array([[5.0, 50.0], [6.0, 60.0]], dtype=np.float64),
        ]

        with CountingSendConnector(
            spec,
            port=0,
            delay_seconds=0.25,
        ) as sender, ReceiveConnector(spec, port=sender.port) as receiver:
            start = time.monotonic()
            for batch in batches:
                sender.send_numpy(batch)
            elapsed = time.monotonic() - start

            received = self.wait_for_value(
                lambda: receiver.batch.copy()
                if receiver.has_batch and np.array_equal(receiver.batch, batches[-1])
                else None
            )

        self.assertLess(elapsed, 0.2)
        self.assertIsNotNone(received)
        assert received is not None
        np.testing.assert_array_equal(received, batches[-1])

    def test_receiver_auto_reconnects_when_sender_starts_later(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        expected = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)
        receiver = ReceiveConnector(spec, port=9117)
        receiver.open()

        try:
            time.sleep(0.1)
            with SendConnector(spec, port=9117) as sender:
                sender.send_numpy(expected)
                received = self.wait_for_value(
                    lambda: receiver.batch.copy() if receiver.has_batch else None
                )
        finally:
            receiver.close()

        self.assertIsNotNone(received)
        assert received is not None
        np.testing.assert_array_equal(received, expected)

    def test_receiver_auto_reconnects_after_sender_restart(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        first = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)
        second = np.array([[5.0, 50.0], [6.0, 60.0]], dtype=np.float64)

        with SendConnector(spec, port=0) as sender:
            receiver = ReceiveConnector(spec, port=sender.port)
            receiver.open()
            try:
                sender.send_numpy(first)
                self.wait_for_value(
                    lambda: receiver.batch.copy()
                    if receiver.has_batch and np.array_equal(receiver.batch, first)
                    else None
                )
            finally:
                receiver.close()
            sender_port = sender.port

        receiver = ReceiveConnector(spec, port=sender_port)
        receiver.open()
        try:
            with SendConnector(spec, port=sender_port) as restarted_sender:
                restarted_sender.send_numpy(second)
                received = self.wait_for_value(
                    lambda: receiver.batch.copy()
                    if receiver.has_batch and np.array_equal(receiver.batch, second)
                    else None
                )
        finally:
            receiver.close()

        self.assertIsNotNone(received)
        assert received is not None
        np.testing.assert_array_equal(received, second)

    def test_multiple_sends_use_one_do_get_stream(self) -> None:
        self.require_local_flight()
        spec = self.make_spec(rows=2)
        batches = [
            np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64),
            np.array([[3.0, 30.0], [4.0, 40.0]], dtype=np.float64),
            np.array([[5.0, 50.0], [6.0, 60.0]], dtype=np.float64),
        ]

        with CountingSendConnector(spec, port=0) as sender, ReceiveConnector(
            spec,
            port=sender.port,
        ) as receiver:
            for batch in batches:
                sender.send_numpy(batch)

            received = self.wait_for_value(
                lambda: receiver.batch.copy()
                if receiver.has_batch and np.array_equal(receiver.batch, batches[-1])
                else None
            )

        self.assertIsNotNone(received)
        assert received is not None
        np.testing.assert_array_equal(received, batches[-1])
        self.assertEqual(sender.do_get_call_count, 1)

    @unittest.skipIf(Pipeline is None, "pythusa pipeline runtime dependencies are required")
    def test_receiver_mirrors_into_real_pythusa_pipeline_stream(self) -> None:
        self.require_local_flight()
        sender = SendConnector(
            self.make_spec(stream_id=_PIPELINE_STREAM_ID),
            port=_PIPELINE_CONNECTOR_PORT,
        )
        pipe = Pipeline("connector-mirror-runtime")

        try:
            pipe.add_stream(
                _MIRRORED_STREAM_NAME,
                shape=_MIRRORED_STREAM_BASE_SHAPE,
                dtype=_MIRRORED_STREAM_DTYPE,
                frames=8,
                cache_align=False,
            )
            pipe.add_event(_MIRRORED_DONE_EVENT)
            pipe.add_task(
                "ingest",
                fn=_connector_ingest_task,
                writes={_MIRRORED_STREAM_NAME: _MIRRORED_STREAM_NAME},
            )
            pipe.add_task(
                "sink",
                fn=_connector_mirror_sink_task,
                reads={_MIRRORED_STREAM_NAME: _MIRRORED_STREAM_NAME},
                events={_MIRRORED_DONE_EVENT: _MIRRORED_DONE_EVENT},
            )

            sender.open()
            pipe.start()
            sender.send_numpy(_PIPELINE_BATCH)

            first_frame_written = self.wait_for_value(
                lambda: int(pipe._manager._rings[_MIRRORED_STREAM_NAME].get_write_pos())
                if int(pipe._manager._rings[_MIRRORED_STREAM_NAME].get_write_pos())
                >= _mirrored_frame_target_nbytes(_MIRRORED_LIVE_FRAME_COUNT)
                else None,
                timeout_seconds=_MIRRORED_TIMEOUT_SECONDS,
            )
            self.assertIsNotNone(first_frame_written)

            sender.close()

            self.assertTrue(
                pipe._manager._events[_MIRRORED_DONE_EVENT].wait(
                    timeout=_MIRRORED_TIMEOUT_SECONDS
                )
            )

            pipe.join(timeout=_MIRRORED_TIMEOUT_SECONDS)

            ingest_process = pipe._manager._processes["ingest"]
            sink_process = pipe._manager._processes["sink"]
            self.assertFalse(ingest_process.is_alive())
            self.assertFalse(sink_process.is_alive())
            self.assertEqual(ingest_process.exitcode, 0)
            self.assertEqual(sink_process.exitcode, 0)
        finally:
            sender.close()
            pipe.close()

    @unittest.skipUnless(
        _RUN_CONNECTOR_STRESS_TESTS,
        "connector stress tests are opt-in; set RUN_CONNECTOR_STRESS_TESTS=1",
    )
    @unittest.skipIf(
        SharedRingBuffer is None or make_reader_binding is None or make_writer_binding is None,
        "pythusa stream bindings are required",
    )
    def test_receiver_mirror_stream_preserves_100000_distinct_values(self) -> None:
        mirrored_frame_nbytes = _mirrored_frame_target_nbytes(1)
        writer_ring, reader_ring = _make_ring(size=mirrored_frame_nbytes * 32 + 4096)
        assert writer_ring is not None
        assert reader_ring is not None

        mirrored_writer = make_writer_binding(
            writer_ring,
            name=_MIRRORED_STREAM_NAME,
            shape=_MIRRORED_STREAM_BASE_SHAPE,
            dtype=_MIRRORED_STREAM_DTYPE,
        )
        mirrored_reader = make_reader_binding(
            reader_ring,
            name=_MIRRORED_STREAM_NAME,
            shape=_MIRRORED_STREAM_BASE_SHAPE,
            dtype=_MIRRORED_STREAM_DTYPE,
        )

        sender = SendConnector(
            self.make_spec(stream_id=_PIPELINE_STREAM_ID),
            port=0,
        )
        receiver: ReceiveConnector | None = None

        try:
            sender.open()
            receiver = ReceiveConnector(
                self.make_spec(stream_id=_PIPELINE_STREAM_ID, stream=mirrored_writer),
                port=sender.port,
            )
            receiver.open()

            for sequence in range(_CONNECTOR_STRESS_FRAME_COUNT):
                sender.send_numpy(_make_integrity_batch(sequence))
                frame = _read_next_mirrored_frame(
                    mirrored_reader,
                    timeout_seconds=_CONNECTOR_STRESS_TIMEOUT_SECONDS,
                )
                self.assertIsNotNone(
                    frame,
                    msg=f"timed out waiting for mirrored frame {sequence}",
                )
                assert frame is not None
                _assert_integrity_frame(frame, sequence)

            sender.close()
            disconnect_frame = _read_next_mirrored_frame(
                mirrored_reader,
                timeout_seconds=_CONNECTOR_STRESS_TIMEOUT_SECONDS,
            )
            self.assertIsNotNone(
                disconnect_frame,
                msg="timed out waiting for mirrored disconnect frame",
            )
            assert disconnect_frame is not None
            _assert_disconnect_mirrored_frame(disconnect_frame)
        finally:
            if receiver is not None:
                receiver.close()
            sender.close()
            _close_ring(reader_ring, unlink=False)
            _close_ring(writer_ring, unlink=True)

    def test_send_after_close_fails_clearly(self) -> None:
        self.require_local_flight()
        sender = SendConnector(self.make_spec(), port=9107)
        sender.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            sender.send_numpy(np.ones((2, 2), dtype=np.float64))

    def test_receive_connector_uses_stream_from_stream_spec_by_default(self) -> None:
        mirrored = RecordingStream()
        spec = self.make_spec(stream=mirrored)

        receiver = ReceiveConnector(spec, port=0)

        self.assertIs(receiver.stream, mirrored)
        receiver.close()

    def test_reader_loop_mirrors_batches_and_disconnect_into_stream(self) -> None:
        mirrored = RecordingStream()
        payload = np.array([[1.0, 10.0], [2.0, 20.0]], dtype=np.float64)
        batch = pa.record_batch(
            [pa.array(payload[:, 0]), pa.array(payload[:, 1])],
            names=["signal", "weight"],
        )
        spec = self.make_spec(stream=mirrored)
        receiver = ReceiveConnector(spec, port=0)
        attempts = 0

        def fake_do_get(_ticket):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return FakeReader([batch])
            receiver._closing = True
            raise pa.ArrowException("stop reconnect loop")

        receiver.do_get = fake_do_get  # type: ignore[method-assign]

        receiver._reader_loop()

        self.assertEqual(len(mirrored.frames), 2)
        np.testing.assert_array_equal(mirrored.frames[0], payload)
        self.assertEqual(mirrored.frames[0].dtype, np.float64)
        self.assertTrue(np.isnan(mirrored.frames[1]).all())
        self.assertEqual(mirrored.frames[1].shape, payload.shape)
        receiver.close()


if __name__ == "__main__":
    unittest.main()
