from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ucirplgui import config
from ucirplgui.components.dashboard import DeviceLinkStore, build_dashboard
from ucirplgui.device_link_publish import write_device_link_snapshot
from ucirplgui.device_link_read import format_age_s, read_device_link_snapshots, staleness_severity
from ucirplgui.device_interfaces.device_interfaces import BaseBoardInterface
from ucirplgui.frontend.frontend import _devlink_rx_latency_ms


class DashboardRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="ucirpl_device_link_test_"))
        self._original_dir = config.DEVICE_LINK_DIR
        config.DEVICE_LINK_DIR = self._tmpdir

    def tearDown(self) -> None:
        config.DEVICE_LINK_DIR = self._original_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

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

    def test_device_link_roundtrip_json(self) -> None:
        write_device_link_snapshot(
            board="gse",
            connected=True,
            last_connect_epoch_s=100.0,
            last_rx_epoch_s=101.5,
            endpoint_host="127.0.0.1",
            endpoint_port=10002,
            last_error=None,
        )
        snapshots = read_device_link_snapshots()
        self.assertIn("gse", snapshots)
        gse = snapshots["gse"]
        self.assertTrue(gse.connected)
        self.assertEqual(gse.endpoint_port, 10002)
        self.assertEqual(gse.endpoint_host, "127.0.0.1")
        self.assertAlmostEqual(gse.last_connect_epoch_s or 0.0, 100.0)
        self.assertAlmostEqual(gse.last_rx_epoch_s or 0.0, 101.5)

    def test_device_link_parser_tolerates_bad_json(self) -> None:
        bad_file = self._tmpdir / "ecu.json"
        bad_file.write_text("{not-json", encoding="utf-8")
        snapshots = read_device_link_snapshots()
        self.assertEqual(snapshots, {})

    def test_status_helpers(self) -> None:
        write_device_link_snapshot(
            board="loadcell",
            connected=True,
            last_connect_epoch_s=10.0,
            last_rx_epoch_s=10.0,
            endpoint_host="127.0.0.1",
            endpoint_port=10069,
            last_error=None,
        )
        snap = read_device_link_snapshots()["loadcell"]
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
        with (
            patch.object(config, "DEVICE_LINK_PUBLISH_INTERVAL_S", 0.02),
            patch("ucirplgui.device_interfaces.device_interfaces.time.time", side_effect=(100.0, 100.01, 100.03)),
            patch("ucirplgui.device_interfaces.device_interfaces.write_device_link_snapshot") as write_snapshot,
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
        self.assertEqual(write_snapshot.call_count, 2)

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

    def test_frontend_devlink_rx_latency_reports_age_ms(self) -> None:
        write_device_link_snapshot(
            board="gse",
            connected=True,
            last_connect_epoch_s=9.0,
            last_rx_epoch_s=10.1,
            endpoint_host="127.0.0.1",
            endpoint_port=10002,
            last_error=None,
        )
        boards = read_device_link_snapshots()
        self.assertAlmostEqual(_devlink_rx_latency_ms("gse", boards, 10.3), 200.0)
        self.assertEqual(_devlink_rx_latency_ms("ecu", boards, 10.3), 250.0)


if __name__ == "__main__":
    unittest.main()
