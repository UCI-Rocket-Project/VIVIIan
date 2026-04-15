from __future__ import annotations

import time
import unittest
from typing import Callable, TypeVar

import numpy as np
import pyarrow as pa

from connector_utils import ReceiveConnector, SendConnector, StreamSpec

T = TypeVar("T")


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


class ConnectorUtilsTests(unittest.TestCase):
    def make_schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("signal", pa.float64()),
                pa.field("weight", pa.float64()),
            ]
        )

    def make_spec(
        self,
        *,
        stream_id: str = "telemetry",
        rows: int = 2,
        columns: int = 2,
    ) -> StreamSpec:
        return StreamSpec(
            stream_id=stream_id,
            schema=self.make_schema(),
            shape=(rows, columns),
        )

    def wait_for_value(self, getter: Callable[[], T | None]) -> T | None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            value = getter()
            if value is not None:
                return value
            time.sleep(0.01)
        return None

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

    def test_send_after_close_fails_clearly(self) -> None:
        sender = SendConnector(self.make_spec(), port=9107)
        sender.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            sender.send_numpy(np.ones((2, 2), dtype=np.float64))


if __name__ == "__main__":
    unittest.main()
