from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pyarrow as pa

from viviian.connector_utils import StreamSpec
from viviian.deviceinterface import DeviceInterface
import viviian.deviceinterface.deviceinterface as deviceinterface_module


class FakeSendConnector:
    def __init__(self, spec, port, host="127.0.0.1"):
        self.spec = spec
        self.port = port
        self.host = host
        self.sent_batches: list[np.ndarray] = []
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def send_numpy(self, batch: np.ndarray) -> None:
        self.sent_batches.append(batch.copy())

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *a):
        self.close()


class FakeThread:
    def __init__(self, target, daemon: bool) -> None:
        self.target = target
        self.daemon = daemon
        self.started = False
        self.join_timeout: float | None = None

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout


class DeviceInterfaceTests(unittest.TestCase):
    def make_schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("timestamps", pa.time64("ns")),
                pa.field("signal", pa.float64()),
            ]
        )

    def make_table(self, values: list[float]) -> pa.Table:
        timestamps = np.arange(len(values), dtype=np.int64).astype("datetime64[ns]")
        return pa.table(
            {
                "timestamps": pa.array(timestamps),
                "signal": pa.array(values, type=pa.float64()),
            }
        )

    def make_bad_table(self) -> pa.Table:
        timestamps = np.arange(2, dtype=np.int64).astype("datetime64[ns]")
        return pa.table(
            {
                "timestamps": pa.array(timestamps),
                "signal": pa.array(["bad", "data"]),
            }
        )

    def build_device_interface(self, **kwargs) -> tuple[DeviceInterface, FakeSendConnector]:
        fake_sender = FakeSendConnector(spec=None, port=0)
        di = DeviceInterface(self.make_schema(), sender=fake_sender, **kwargs)
        return di, fake_sender

    def test_package_exports_device_interface_class(self) -> None:
        self.assertIs(DeviceInterface, deviceinterface_module.DeviceInterface)

    def test_tx_table_splits_batches_by_row_cap(self) -> None:
        device_interface, fake_sender = self.build_device_interface(max_rows=2)
        device_interface.ingress_table(self.make_table([1.0, 2.0, 3.0]))
        device_interface.ingress_table(self.make_table([4.0, 5.0]))

        device_interface._tx_table()

        self.assertEqual(len(fake_sender.sent_batches), 3)
        self.assertTrue(all(b.shape == (2, 2) for b in fake_sender.sent_batches))
        self.assertEqual(device_interface.stats["sent_batches"], 3)
        self.assertEqual(device_interface.stats["sent_rows"], 5)
        self.assertEqual(device_interface.stats["queued_rows"], 0)
        self.assertEqual(device_interface.table_list, [])

    def test_ingress_rejects_schema_mismatch_without_queueing(self) -> None:
        device_interface, fake_sender = self.build_device_interface()

        device_interface.ingress_table(self.make_bad_table())

        self.assertEqual(device_interface.stats["queued_rows"], 0)
        self.assertEqual(device_interface.table_list, [])
        self.assertEqual(fake_sender.sent_batches, [])

    @mock.patch("viviian.deviceinterface.deviceinterface.threading.Thread", FakeThread)
    def test_exit_flushes_remaining_rows_and_closes_stream_server(self) -> None:
        device_interface, fake_sender = self.build_device_interface(max_rows=4)

        with device_interface as active_device_interface:
            active_device_interface.ingress_table(self.make_table([1.0, 2.0, 3.0]))

        self.assertTrue(device_interface.stop_event.is_set())
        self.assertIsNotNone(device_interface._sender_thread)
        self.assertTrue(device_interface._sender_thread.started)
        self.assertEqual(device_interface._sender_thread.join_timeout, 2.0)
        self.assertEqual(len(fake_sender.sent_batches), 1)
        self.assertTrue(fake_sender.closed)

    @mock.patch("viviian.deviceinterface.deviceinterface.SendConnector")
    def test_publish_endpoint_arguments_are_forwarded_to_stream_server(self, MockSendConnector) -> None:
        mock_sender = mock.MagicMock()
        MockSendConnector.return_value = mock_sender

        DeviceInterface(self.make_schema(), publish_host="0.0.0.0", publish_port=9001)

        MockSendConnector.assert_called_once()
        args, kwargs = MockSendConnector.call_args
        self.assertIsInstance(args[0], StreamSpec)
        self.assertEqual(args[1], 9001)
        self.assertEqual(kwargs.get("host"), "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
