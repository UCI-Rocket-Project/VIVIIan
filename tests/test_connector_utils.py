from __future__ import annotations

import time
import unittest
from typing import Callable, TypeVar

import numpy as np
import pyarrow as pa

from connector_utils import DefaultConnector, ReceiveConnector, SendConnector, StreamSpec

T = TypeVar("T")


class ConnectorUtilsTests(unittest.TestCase):
    def make_schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("timestamp", pa.timestamp("ns")),
                pa.field("value", pa.float64()),
            ]
        )

    def make_scalar_schema(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.float64())])

    def make_spec(
        self,
        *,
        stream_id: str = "telemetry",
        frames: int = 2,
    ) -> StreamSpec:
        return StreamSpec(
            stream_id=stream_id,
            schema_version=1,
            schema=self.make_schema(),
            shape=(2, frames),
        )

    def make_scalar_spec(
        self,
        *,
        stream_id: str = "scalar",
        frames: int = 3,
    ) -> StreamSpec:
        return StreamSpec(
            stream_id=stream_id,
            schema_version=1,
            schema=self.make_scalar_schema(),
            shape=(frames,),
        )

    def make_table(self, values: list[float]) -> pa.Table:
        return pa.table(
            {
                "timestamp": pa.array(range(len(values)), type=pa.timestamp("ns")),
                "value": pa.array(values, type=pa.float64()),
            },
            schema=self.make_schema(),
        )

    def wait_for_value(self, getter: Callable[[], T | None]) -> T | None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            value = getter()
            if value is not None:
                return value
            time.sleep(0.01)
        return None

    def test_stream_spec_rejects_invalid_shapes(self) -> None:
        with self.subTest("1d multi-field"):
            with self.assertRaisesRegex(ValueError, "exactly one schema field"):
                StreamSpec(
                    stream_id="telemetry",
                    schema_version=1,
                    schema=self.make_schema(),
                    shape=(4,),
                )

        with self.subTest("2d single-field"):
            with self.assertRaisesRegex(ValueError, "Single-field streams must use shape"):
                StreamSpec(
                    stream_id="scalar",
                    schema_version=1,
                    schema=self.make_scalar_schema(),
                    shape=(1, 4),
                )

        with self.subTest("unsupported ndim"):
            with self.assertRaisesRegex(ValueError, "shape must be"):
                StreamSpec(
                    stream_id="telemetry",
                    schema_version=1,
                    schema=self.make_schema(),
                    shape=(2, 2, 2),
                )

        with self.subTest("field-count mismatch"):
            with self.assertRaisesRegex(ValueError, "does not match schema field count"):
                StreamSpec(
                    stream_id="telemetry",
                    schema_version=1,
                    schema=self.make_schema(),
                    shape=(3, 4),
                )

        with self.subTest("non-positive dim"):
            with self.assertRaisesRegex(ValueError, "shape dimensions must be >= 1"):
                StreamSpec(
                    stream_id="telemetry",
                    schema_version=1,
                    schema=self.make_schema(),
                    shape=(2, 0),
                )

    def test_stream_spec_infers_default_shape_from_schema(self) -> None:
        multi_field = StreamSpec(
            stream_id="telemetry",
            schema_version=1,
            schema=self.make_schema(),
        )
        single_field = StreamSpec(
            stream_id="scalar",
            schema_version=1,
            schema=self.make_scalar_schema(),
        )

        self.assertEqual(multi_field.shape, (2, 1))
        self.assertEqual(single_field.shape, (1,))

    def test_default_connector_repr_is_configuration_only(self) -> None:
        connector = DefaultConnector(self.make_spec(), port=9101)

        self.assertEqual(connector.direction, "default")
        self.assertEqual(connector.endpoint_uri, "grpc://127.0.0.1:9101")
        self.assertEqual(
            repr(connector),
            "DefaultConnector(stream_id='telemetry', schema_version=1, "
            "direction='default', endpoint='grpc://127.0.0.1:9101')",
        )

    def test_receive_connector_keeps_only_latest_table(self) -> None:
        spec = self.make_spec(frames=2)
        connector = ReceiveConnector(spec, port=9103)

        connector._accept_table(spec.to_transport(self.make_table([1.0, 2.0])))
        connector._accept_table(spec.to_transport(self.make_table([3.0, 4.0])))

        table = connector.recv_table()
        self.assertIsNotNone(table)
        assert table is not None
        typed = spec.from_transport(table)
        self.assertEqual(typed.column("value").to_pylist(), [3.0, 4.0])
        self.assertIsNone(connector.recv_table())

    def test_receive_connector_rejects_invalid_transport_without_overwriting_latest(self) -> None:
        spec = self.make_spec(frames=2)
        connector = ReceiveConnector(spec, port=9104)
        valid = spec.to_transport(self.make_table([5.0, 6.0]))

        connector._accept_table(valid)

        with self.assertRaisesRegex(ValueError, "Row count mismatch"):
            connector._accept_table(spec.to_transport(self.make_table([7.0])))

        received = connector.recv_table()
        self.assertIsNotNone(received)
        assert received is not None
        typed = spec.from_transport(received)
        self.assertEqual(typed.column("value").to_pylist(), [5.0, 6.0])

    def test_receive_connector_can_convert_latest_transport_to_numpy(self) -> None:
        spec = self.make_spec(frames=2)
        connector = ReceiveConnector(spec, port=9105)
        connector._accept_table(spec.to_transport(self.make_table([4.0, 5.0])))

        arrays = connector.recv_numpy()

        self.assertIsNotNone(arrays)
        assert arrays is not None
        self.assertEqual(len(arrays), 2)
        self.assertEqual(arrays[0].dtype, np.float64)
        self.assertEqual(arrays[0].tolist(), [0.0, 1.0])
        self.assertEqual(arrays[1].tolist(), [4.0, 5.0])
        self.assertIsNone(connector.recv_numpy())

    def test_receive_connector_can_convert_latest_transport_to_numpy_batch(self) -> None:
        spec = self.make_spec(frames=2)
        connector = ReceiveConnector(spec, port=9106)
        connector._accept_table(spec.to_transport(self.make_table([4.0, 5.0])))

        batch = connector.recv_numpy_batch()

        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(batch.shape, (2, 2))
        np.testing.assert_array_equal(
            batch,
            np.array([[0.0, 1.0], [4.0, 5.0]], dtype=np.float64),
        )
        self.assertIsNone(connector.recv_numpy_batch())

    def test_send_connector_rejects_table_with_wrong_row_count(self) -> None:
        sender = SendConnector(self.make_spec(frames=2), port=9107)

        with self.assertRaisesRegex(ValueError, "Row count mismatch"):
            sender.send_table(self.make_table([1.0]))

    def test_defaulted_shape_allows_exactly_one_schema_send(self) -> None:
        sender = SendConnector(
            StreamSpec(
                stream_id="telemetry",
                schema_version=1,
                schema=self.make_schema(),
            ),
            port=9108,
        )

        sender.stream_spec.validate_table(self.make_table([1.0]))
        with self.assertRaisesRegex(ValueError, "Row count mismatch"):
            sender.stream_spec.validate_table(self.make_table([1.0, 2.0]))

    def test_send_connector_rejects_numpy_batch_with_wrong_shape(self) -> None:
        sender = SendConnector(self.make_spec(frames=2), port=9109)

        with self.assertRaisesRegex(ValueError, "NumPy batch shape mismatch"):
            sender.send_numpy(np.ones((2, 3), dtype=np.float64))

    def test_flight_send_and_receive_round_trip(self) -> None:
        spec = self.make_spec(frames=3)
        receiver = ReceiveConnector(spec, port=0)
        sender: SendConnector | None = None
        try:
            receiver.open()
            sender = SendConnector(spec, port=receiver.port)

            expected = self.make_table([10.0, 20.0, 30.0])
            sender.send_table(expected)

            received = self.wait_for_value(receiver.recv_table)

            self.assertIsNotNone(received)
            assert received is not None
            self.assertTrue(
                received.schema.equals(spec.transport_schema, check_metadata=True)
            )
            typed = spec.from_transport(received)
            self.assertTrue(typed.equals(expected))
        finally:
            if sender is not None:
                sender.close()
            receiver.close()

    def test_flight_recv_typed_numpy_casts_back(self) -> None:
        spec = self.make_spec(frames=2)
        receiver = ReceiveConnector(spec, port=0)
        sender: SendConnector | None = None
        try:
            receiver.open()
            sender = SendConnector(spec, port=receiver.port)

            sender.send_table(self.make_table([7.0, 8.0]))

            arrays = self.wait_for_value(receiver.recv_typed_numpy)

            self.assertIsNotNone(arrays)
            assert arrays is not None
            self.assertEqual(len(arrays), 2)
            self.assertEqual(arrays[0].dtype, np.dtype("datetime64[ns]"))
            self.assertEqual(arrays[1].tolist(), [7.0, 8.0])
        finally:
            if sender is not None:
                sender.close()
            receiver.close()

    def test_flight_recv_numpy_batch_returns_field_major_shape(self) -> None:
        spec = self.make_spec(frames=2)
        receiver = ReceiveConnector(spec, port=0)
        sender: SendConnector | None = None
        try:
            receiver.open()
            sender = SendConnector(spec, port=receiver.port)

            sender.send_table(self.make_table([1.5, 2.5]))

            batch = self.wait_for_value(receiver.recv_numpy_batch)

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.shape, (2, 2))
            np.testing.assert_array_equal(
                batch,
                np.array([[0.0, 1.0], [1.5, 2.5]], dtype=np.float64),
            )
        finally:
            if sender is not None:
                sender.close()
            receiver.close()

    def test_flight_send_numpy_and_receive_single_field_batch(self) -> None:
        spec = self.make_scalar_spec(frames=3)
        receiver = ReceiveConnector(spec, port=0)
        sender: SendConnector | None = None
        try:
            receiver.open()
            sender = SendConnector(spec, port=receiver.port)

            sender.send_numpy(np.array([1.5, 2.5, 3.5], dtype=np.float32))

            batch = self.wait_for_value(receiver.recv_numpy_batch)

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.shape, (3,))
            np.testing.assert_array_equal(
                batch,
                np.array([1.5, 2.5, 3.5], dtype=np.float64),
            )
        finally:
            if sender is not None:
                sender.close()
            receiver.close()

    def test_flight_descriptor_mismatch_fails_clearly(self) -> None:
        receiver = ReceiveConnector(
            self.make_spec(stream_id="expected", frames=2),
            port=0,
        )
        sender: SendConnector | None = None
        try:
            receiver.open()
            sender = SendConnector(
                self.make_spec(stream_id="other", frames=2),
                port=receiver.port,
            )

            with self.assertRaises(pa.ArrowException):
                sender.send_table(self.make_table([1.0, 2.0]))
        finally:
            if sender is not None:
                sender.close()
            receiver.close()


if __name__ == "__main__":
    unittest.main()
