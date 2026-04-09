from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pyarrow as pa

from deviceinterface import DeviceInterface
import deviceinterface.deviceinterface as deviceinterface_module


class FakeStreamServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent_tables: list[pa.Table] = []
        self.closed = False

    def send_table(self, table: pa.Table) -> None:
        self.sent_tables.append(table)

    def close(self) -> None:
        self.closed = True


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

    def build_device_interface(self, **kwargs) -> DeviceInterface:
        return DeviceInterface(self.make_schema(), **kwargs)

    def test_package_exports_device_interface_class(self) -> None:
        self.assertIs(DeviceInterface, deviceinterface_module.DeviceInterface)

    @mock.patch("deviceinterface_utils.deviceinterface.ArrowBatchStreamServer", FakeStreamServer)
    def test_tx_table_splits_batches_by_row_cap(self) -> None:
        device_interface = self.build_device_interface(max_rows=2)
        device_interface.ingress_table(self.make_table([1.0, 2.0, 3.0]))
        device_interface.ingress_table(self.make_table([4.0, 5.0]))

        device_interface._tx_table()

        self.assertEqual([table.num_rows for table in device_interface.stream_server.sent_tables], [2, 2, 1])
        self.assertEqual(device_interface.stats["sent_batches"], 3)
        self.assertEqual(device_interface.stats["sent_rows"], 5)
        self.assertEqual(device_interface.stats["queued_rows"], 0)
        self.assertEqual(device_interface.table_list, [])

    @mock.patch("deviceinterface_utils.deviceinterface.ArrowBatchStreamServer", FakeStreamServer)
    def test_ingress_rejects_schema_mismatch_without_queueing(self) -> None:
        device_interface = self.build_device_interface()

        device_interface.ingress_table(self.make_bad_table())

        self.assertEqual(device_interface.stats["queued_rows"], 0)
        self.assertEqual(device_interface.table_list, [])
        self.assertEqual(device_interface.stream_server.sent_tables, [])

    @mock.patch("deviceinterface_utils.deviceinterface.ArrowBatchStreamServer", FakeStreamServer)
    @mock.patch("deviceinterface_utils.deviceinterface.threading.Thread", FakeThread)
    def test_exit_flushes_remaining_rows_and_closes_stream_server(self) -> None:
        device_interface = self.build_device_interface(max_rows=4)

        with device_interface as active_device_interface:
            active_device_interface.ingress_table(self.make_table([1.0, 2.0, 3.0]))

        self.assertTrue(device_interface.stop_event.is_set())
        self.assertIsNotNone(device_interface._sender_thread)
        self.assertTrue(device_interface._sender_thread.started)
        self.assertEqual(device_interface._sender_thread.join_timeout, 2.0)
        self.assertEqual([table.num_rows for table in device_interface.stream_server.sent_tables], [3])
        self.assertTrue(device_interface.stream_server.closed)


if __name__ == "__main__":
    unittest.main()
