from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "GUI2.1"))

from plot_saved_data import (
    DATASETS, parse_columns, plot_dataset, plot_combined_datasets,
    read_dataset, signal_as_float,
)


class SavedDataTests(unittest.TestCase):
    def adc_table(self):
        columns = {
            "storageTimestamp": [1.0, 2.0, 3.0],
            "solenoidInternalState0": [0.0, 1.0, 0.0],
            "externalAdcValidBitmask": [255.0, 0.0, 255.0],
        }
        columns.update({f"pressure{i}": [12.0, 1e24, 24.0] for i in range(4)})
        columns.update({f"loadCellForce{i}": [30.0, 40.0, 50.0] for i in range(2, 6)})
        return pa.table(columns)

    def test_adc_fields_are_default_signals_and_explicitly_selectable(self):
        table = self.adc_table()
        defaults = parse_columns(None, None, table, "gse")
        self.assertEqual(len(defaults), 9)
        self.assertNotIn("externalAdcValidBitmask", defaults)
        self.assertEqual(parse_columns("gse.pressure0,loadCellForce5", None, table, "gse"),
                         ["pressure0", "loadCellForce5"])
        self.assertNotIn("pressure0", parse_columns(None, "pressure0", table, "gse"))

    def test_adc_mask_applies_per_channel_and_rejects_bad_masks(self):
        table = pa.table({
            "externalAdcValidBitmask": [1.0, 16.0, 255.0, None, float("nan"), 256.0, -1.0, 1.5],
            "pressure0": [5.0] * 8,
            "loadCellForce2": [6.0, 6.0, float("nan"), 6.0, 6.0, 6.0, 6.0, 6.0],
        })
        with redirect_stdout(StringIO()):
            np.testing.assert_array_equal(signal_as_float(table, "gse", "pressure0"),
                                          [5, np.nan, 5, np.nan, np.nan, np.nan, np.nan, np.nan])
            np.testing.assert_array_equal(signal_as_float(table, "gse", "loadCellForce2"),
                                          [np.nan, 6, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
            missing_mask = signal_as_float(pa.table({"pressure0": [5.0]}), "gse", "pressure0")
        self.assertTrue(np.isnan(missing_mask).all())

    def test_single_and_combined_plots_keep_analog_values_off_state_axis(self):
        gse = self.adc_table()
        nidaq = pa.table({"storageTimestamp": [1.0, 2.0, 3.0], "LNGTANK": [1.0, 2.0, 3.0]})
        for combined in (False, True):
            with self.subTest(combined=combined):
                def dataset_reader(session, name, x):
                    return gse if name == "gse" else nidaq
                with patch("plot_saved_data.read_dataset", side_effect=dataset_reader), \
                     patch("matplotlib.pyplot.show"), redirect_stdout(StringIO()):
                    kwargs = dict(session_dir=Path("example"), columns_arg=None, disabled_arg=None,
                                  x_name="storageTimestamp", max_points=0, output=None)
                    if combined:
                        plot_combined_datasets(dataset_names=["gse", "nidaq"], **kwargs)
                    else:
                        plot_dataset(dataset_name="gse", **kwargs)
                fig = plt.gcf()
                try:
                    fig.canvas.draw()
                    sensor_axis = next(a for a in fig.axes if a.get_ylabel().startswith("GSE sensor"))
                    state_axis = next(a for a in fig.axes if a.get_ylabel() == "GSE internal state")
                    self.assertEqual(len(sensor_axis.lines), 8)
                    self.assertEqual(len(state_axis.lines), 1)
                    self.assertGreater(sensor_axis.get_ylim()[1], 24)
                    self.assertEqual(state_axis.get_ylim(), (-0.05, 1.05))
                    line = next(l for l in sensor_axis.lines if l.get_label() == "gse.pressure0")
                    np.testing.assert_array_equal(line.get_ydata(), [12, np.nan, 24])
                    labels = [text.get_text() for text in fig.axes[0].get_legend().get_texts()]
                    self.assertIn("gse.pressure0", labels)
                    self.assertIn("gse.loadCellForce5", labels)
                finally:
                    plt.close(fig)

    def test_short_session_reads_all_parts_in_order(self):
        with TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            folder = session / DATASETS["gse"]
            folder.mkdir()
            for i in range(3):
                pq.write_table(pa.table({"timestamp": [i], "solenoidInternalState0": [float(i % 2)]}),
                               folder / f"part-{i}.parquet")
            with redirect_stdout(StringIO()):
                table = read_dataset(session, "gse", "storageTimestamp")
            self.assertEqual(table["timestamp"].to_pylist(), [0, 1, 2])

    def test_unfinished_file_does_not_hide_readable_parts(self):
        with TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            folder = session / DATASETS["gse"]
            folder.mkdir()
            pq.write_table(pa.table({"timestamp": [42]}), folder / "part-0.parquet")
            (folder / "part-1.parquet").write_bytes(b"PAR1")
            output = StringIO()
            with redirect_stdout(output):
                table = read_dataset(session, "gse", "storageTimestamp")
            self.assertEqual(table["timestamp"].to_pylist(), [42])
            self.assertIn("Opened 1/2", output.getvalue())
            self.assertIn("part-1.parquet", output.getvalue())


if __name__ == "__main__":
    unittest.main()
