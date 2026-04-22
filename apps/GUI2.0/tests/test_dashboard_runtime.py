from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from ucirplgui import config
from ucirplgui.components.dashboard import DeviceLinkStore, build_dashboard
from ucirplgui.device_link_read import (
    decode_device_link_batches,
    decode_device_link_row,
    encode_device_link_row,
    format_age_s,
    staleness_severity,
)
from ucirplgui.device_interfaces.device_interfaces import BaseBoardInterface
from ucirplgui.frontend.frontend import _devlink_rx_latency_ms


class DashboardRuntimeTests(unittest.TestCase):
    def test_scalars_stream_is_registered(self) -> None:
        self.assertIn(config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID, config.SCHEMAS)
        self.assertIn(config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID, config.FRONTEND_STREAMS)
        self.assertEqual(
            config.FRONTEND_GSE_ECU_SCALARS_COLUMNS,
            (
                "timestamp_s",
                "temperature_engine_1_c",
                "temperature_engine_2_c",
                "pressure_gn2_psi",
                "temperature_copv_c",
            ),
        )

    def test_backend_throughput_stream_is_registered(self) -> None:
        self.assertIn(config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID, config.SCHEMAS)
        self.assertIn(config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID, config.FRONTEND_STREAMS)
        self.assertEqual(
            config.FRONTEND_BACKEND_THROUGHPUT_COLUMNS,
            (
                "timestamp_s",
                "backend_throughput_mbps",
            ),
        )

    def test_device_link_encode_decode_roundtrip(self) -> None:
        row = encode_device_link_row(
            board="gse",
            connected=True,
            last_connect_epoch_s=100.0,
            last_rx_epoch_s=101.5,
            endpoint_host="127.0.0.1",
            endpoint_port=10002,
            last_error=None,
            snapshot_epoch_s=150.0,
        )
        snap = decode_device_link_row(row)
        assert snap is not None
        self.assertEqual(snap.board, "gse")
        self.assertTrue(snap.connected)
        self.assertEqual(snap.endpoint_port, 10002)
        self.assertEqual(snap.endpoint_host, "127.0.0.1")
        self.assertAlmostEqual(snap.last_connect_epoch_s or 0.0, 100.0)
        self.assertAlmostEqual(snap.last_rx_epoch_s or 0.0, 101.5)
        self.assertIsNone(snap.last_error)

    def test_device_link_decode_skips_bad_rows(self) -> None:
        bad = np.zeros((1, 3), dtype=np.float64)
        self.assertIsNone(decode_device_link_row(bad))

    def test_status_helpers(self) -> None:
        row = encode_device_link_row(
            board="loadcell",
            connected=True,
            last_connect_epoch_s=10.0,
            last_rx_epoch_s=10.0,
            endpoint_host="127.0.0.1",
            endpoint_port=10069,
            last_error=None,
            snapshot_epoch_s=10.0,
        )
        snap = decode_device_link_row(row)
        assert snap is not None
        self.assertEqual(staleness_severity(now_s=10.2, snap=snap), "ok")
        self.assertEqual(staleness_severity(now_s=11.2, snap=snap), "warn")
        self.assertEqual(staleness_severity(now_s=13.2, snap=snap), "crit")
        self.assertEqual(format_age_s(10.0, None), "—")
        self.assertEqual(format_age_s(10.3, 10.1), "200 ms")

    def test_device_link_publish_uses_configured_interval(self) -> None:
        interface = BaseBoardInterface(
            board_name="gse",
            simulator_port=10002,
            telemetry_len=8,
            send_connector=object(),
            command_connector=None,
        )
        mock_tx = MagicMock()
        with (
            patch.object(config, "DEVICE_LINK_PUBLISH_INTERVAL_S", 0.02),
            patch("ucirplgui.device_interfaces.device_interfaces.time.time", side_effect=(100.0, 100.01, 100.03)),
            patch(
                "ucirplgui.device_interfaces.device_interfaces._build_device_link_send_connector",
                return_value=mock_tx,
            ),
        ):
            interface._publish_link(
                connected=True,
                host="127.0.0.1",
                port=10002,
                last_connect=99.0,
                last_rx=100.0,
                last_error=None,
                force=False,
            )
            interface._publish_link(
                connected=True,
                host="127.0.0.1",
                port=10002,
                last_connect=99.0,
                last_rx=100.01,
                last_error=None,
                force=False,
            )
            interface._publish_link(
                connected=True,
                host="127.0.0.1",
                port=10002,
                last_connect=99.0,
                last_rx=100.03,
                last_error=None,
                force=False,
            )
        self.assertEqual(mock_tx.send_numpy.call_count, 2)

    def test_decode_device_link_batches_merges_sources(self) -> None:
        class _Rx:
            def __init__(self, row: object | None) -> None:
                self._row = row
                self.has_batch = row is not None
                self.batch = row

        gse_row = encode_device_link_row(
            board="gse",
            connected=True,
            last_connect_epoch_s=1.0,
            last_rx_epoch_s=2.0,
            endpoint_host="127.0.0.1",
            endpoint_port=1,
            last_error=None,
            snapshot_epoch_s=3.0,
        )
        merged = decode_device_link_batches(
            {
                "gse": _Rx(gse_row),
                "ecu": _Rx(None),
            }
        )
        self.assertIn("gse", merged)
        self.assertNotIn("ecu", merged)

    def test_connection_gauges_are_labeled_rx_age(self) -> None:
        dashboard = build_dashboard(command_writer=None, link_store=DeviceLinkStore())
        gauges = (
            dashboard.gse_connection_guage,
            dashboard.ecu_connection_guage,
            dashboard.extr_ecu_connection_guage,
            dashboard.load_cell_connection_guage,
        )
        for gauge in gauges:
            self.assertEqual(gauge.header_right, "RX Age")

    def test_backend_throughput_gauge_uses_mbps_units(self) -> None:
        dashboard = build_dashboard(command_writer=None, link_store=DeviceLinkStore())
        self.assertEqual(dashboard.backend_throughput_guage.stream_name, "backend_throughput_mbps")
        self.assertEqual(dashboard.backend_throughput_guage.unit_label, "Mbps")
        self.assertEqual(dashboard.backend_throughput_guage.header_right, "Mbps")

    def test_frontend_devlink_rx_latency_reports_age_ms(self) -> None:
        row = encode_device_link_row(
            board="gse",
            connected=True,
            last_connect_epoch_s=9.0,
            last_rx_epoch_s=10.1,
            endpoint_host="127.0.0.1",
            endpoint_port=10002,
            last_error=None,
            snapshot_epoch_s=10.2,
        )
        snap = decode_device_link_row(row)
        boards = {"gse": snap} if snap is not None else {}
        self.assertAlmostEqual(_devlink_rx_latency_ms("gse", boards, 10.3), 200.0)
        self.assertEqual(_devlink_rx_latency_ms("ecu", boards, 10.3), 250.0)


if __name__ == "__main__":
    unittest.main()
