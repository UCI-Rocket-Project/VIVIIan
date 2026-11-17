from __future__ import annotations

import binascii
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "GUI2.1"))

from gse21connector import (
    GSE2V1_DATA_SIZE,
    GSE2V1_EXTERNAL_ADC_FIELDS,
    GSE2V1_FIELD_NAMES,
    decode_gse2v1_data,
    gse2v1_field_valid,
    make_echo_row,
    read_gse2v1_packets,
)
from gse21_fake_server import _pack_telemetry, _SimState
from gui_elements import NidaqGraph, draw_table


class ExternalAdcTests(unittest.TestCase):
    def test_wire_order_and_fragmented_packets(self):
        # Build independently of the connector format to check firmware offsets.
        body = (
            struct.pack("<II18?14f3I", 0xDEADBEEF, 42, *([True] * 18),
                        *([2.5] * 14), 300, 310, 295)
            + struct.pack("<B8f", 0xA5, *range(8))
        )
        packet = body + struct.pack("<I", binascii.crc32(body) & 0xFFFFFFFF)
        self.assertEqual(len(packet), GSE2V1_DATA_SIZE)
        self.assertEqual(len(packet), 131)
        sock = MagicMock()
        sock.recv.side_effect = [b"noise" + packet[:100], packet[100:]]
        decoded = next(read_gse2v1_packets(sock))
        latest = dict(zip(GSE2V1_FIELD_NAMES, decoded, strict=True))
        self.assertEqual(latest["externalAdcValidBitmask"], 0xA5)
        self.assertEqual(latest["temperature2"], 295)
        for i, field in enumerate(GSE2V1_EXTERNAL_ADC_FIELDS):
            self.assertEqual(latest[field], i)
        self.assertEqual(make_echo_row(decoded, True), [1.0] * 16)

    def test_each_bit_controls_only_its_channel(self):
        latest = dict.fromkeys(GSE2V1_EXTERNAL_ADC_FIELDS, 123.0)
        for channel in range(8):
            latest["externalAdcValidBitmask"] = float(1 << channel)
            self.assertEqual(
                [gse2v1_field_valid(latest, field) for field in GSE2V1_EXTERNAL_ADC_FIELDS],
                [i == channel for i in range(8)],
            )

    def test_missing_or_nonfinite_values_are_invalid(self):
        for mask in (None, float("nan"), float("inf"), -1, 256, 1.5):
            self.assertFalse(gse2v1_field_valid(
                {"externalAdcValidBitmask": mask, "pressure0": 12.0}, "pressure0",
            ))
        self.assertFalse(gse2v1_field_valid({"pressure0": 12.0}, "pressure0"))
        for value in (None, float("nan"), float("inf")):
            self.assertFalse(gse2v1_field_valid(
                {"externalAdcValidBitmask": 255, "pressure0": value}, "pressure0",
            ))
        self.assertTrue(gse2v1_field_valid({}, "temperature0"))

    def test_fake_server_packet_matches_decoder(self):
        packet = _pack_telemetry(1, _SimState(external_adc_valid_bitmask=0x81))
        self.assertEqual(len(packet), GSE2V1_DATA_SIZE)
        latest = dict(zip(GSE2V1_FIELD_NAMES, decode_gse2v1_data(packet), strict=True))
        self.assertEqual(latest["externalAdcValidBitmask"], 0x81)
        self.assertEqual(latest["pressure0"], 100.0)
        self.assertEqual(latest["loadCellForce5"], 0.0)
        self.assertEqual(latest["crc"], binascii.crc32(packet[:-4]) & 0xFFFFFFFF)

    def test_graph_masks_before_scaling_and_recovers(self):
        server = SimpleNamespace(latest={"externalAdcValidBitmask": 1, "pressure0": 10.0})
        graph = NidaqGraph(server, field_names={"pressure0": (2, 3)},
                           field_validity=gse2v1_field_valid)
        graph.update()
        self.assertEqual(graph.history[-1], [23.0])
        server.latest = {**server.latest, "externalAdcValidBitmask": 0}
        graph.update()
        self.assertTrue(math.isnan(graph.history[-1][0]))
        server.latest = {**server.latest, "externalAdcValidBitmask": 1}
        graph.update()
        self.assertEqual(graph.history[-1], [23.0])

    def test_invalid_graph_has_finite_axes_and_visible_label(self):
        server = SimpleNamespace(latest={"externalAdcValidBitmask": 0, "pressure0": 99.0})
        graph = NidaqGraph(server, field_names={"pressure0": (1, 0)},
                           field_validity=gse2v1_field_valid)
        graph.update()
        server.latest = dict(server.latest)
        graph.update()
        ui = MagicMock()
        ui.button.return_value = False
        ui.get_content_region_avail.return_value = (1200, 800)
        with patch("gui_elements.implot") as plot:
            plot.begin_plot.return_value = True
            graph.draw(ui)
            limits = plot.set_next_axes_limits.call_args.args
            self.assertTrue(all(math.isfinite(v) for v in limits[:4]))
            self.assertTrue(np.isnan(plot.plot_line.call_args.args[2]).all())
        self.assertIn("pressure0: INVALID", [call.args[1] for call in ui.text_colored.call_args_list])

    def test_table_marks_masked_finite_value_invalid(self):
        server = SimpleNamespace(name="gse", fields=("pressure0", "temperature0"),
                                 latest={"externalAdcValidBitmask": 0,
                                         "pressure0": 100.0, "temperature0": 300})
        ui = MagicMock()
        draw_table(ui, server, field_validity=gse2v1_field_valid)
        self.assertEqual(ui.text_colored.call_args.args[1], "INVALID")
        ui.text_unformatted.assert_any_call("300")


if __name__ == "__main__":
    unittest.main()
