from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from ucirplgui import config
import ucirplgui.backend.pipeline as pipeline_module
from viviian.datastorage_utils import ParquetDatabase


def _make_raw_batch(stream_id: str, values: dict[int, float]) -> np.ndarray:
    batch = np.zeros(
        (config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
        dtype=np.float64,
    )
    for index, value in values.items():
        batch[0, index] = float(value)
    return batch


def _read_raw_table(
    root: Path,
    stream_id: str,
    rows_per_file: int,
) -> tuple[np.ndarray, list[str]]:
    database = ParquetDatabase(
        root / stream_id,
        config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
        rows_per_file=rows_per_file,
    )
    try:
        table = database.retrieve()
    finally:
        database.close()
    columns = [
        field.name
        for field in table.schema
        if field.name != "database_timestamp_ns"
    ]
    if columns and table.num_rows:
        data = np.column_stack([table.column(name).to_numpy() for name in columns])
    else:
        data = np.empty((0, len(columns)), dtype=np.float64)
    return data, columns


class _FakeReceiveConnector:
    instances: dict[str, _FakeReceiveConnector] = {}

    def __init__(self, stream_spec, port: int, host: str) -> None:
        del port, host
        self.stream_spec = stream_spec
        self.batch = np.empty(stream_spec.shape, dtype=np.float64)
        self.has_batch = False
        self.opened = False
        self.closed = False
        type(self).instances[stream_spec.stream_id] = self

    @classmethod
    def reset(cls) -> None:
        cls.instances = {}

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def enqueue(self, batch: np.ndarray) -> None:
        normalized = self.stream_spec.normalize_batch(batch)
        np.copyto(self.batch, normalized)
        self.has_batch = True


class _FakeSendConnector:
    instances: dict[str, _FakeSendConnector] = {}

    def __init__(self, stream_spec, port: int, host: str) -> None:
        del port, host
        self.stream_spec = stream_spec
        self.sent_batches: list[np.ndarray] = []
        self.opened = False
        self.closed = False
        type(self).instances[stream_spec.stream_id] = self

    @classmethod
    def reset(cls) -> None:
        cls.instances = {}

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def send_numpy(self, batch: np.ndarray) -> None:
        self.sent_batches.append(np.asarray(batch, dtype=np.float64).copy())


@unittest.skip("Raw telemetry database service is disabled in GUI2.0 for now.")
class BackendStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.raw_root = Path(self.temp_dir.name) / "raw"
        _FakeReceiveConnector.reset()
        _FakeSendConnector.reset()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_runtime(
        self,
        *,
        rows_per_file: int = 4,
    ) -> pipeline_module.BackendPipelineRuntime:
        patches = (
            patch.object(pipeline_module, "ReceiveConnector", _FakeReceiveConnector),
            patch.object(pipeline_module, "SendConnector", _FakeSendConnector),
            patch.object(config, "BACKEND_RAW_STORAGE_DIR", str(self.raw_root)),
            patch.object(config, "BACKEND_RAW_STORAGE_ROWS_PER_FILE", rows_per_file),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return pipeline_module.BackendPipelineRuntime()

    def test_runtime_persists_each_new_raw_batch_once(self) -> None:
        runtime = self._build_runtime(rows_per_file=4)
        self.addCleanup(runtime.close)

        for stream_id in config.RAW_STREAMS:
            self.assertTrue((self.raw_root / stream_id / "metadata.json").exists())

        _FakeReceiveConnector.instances[config.RAW_GSE_STREAM_ID].enqueue(
            _make_raw_batch(
                config.RAW_GSE_STREAM_ID,
                {1: 11.0, 2: 22.0, 3: 33.0, 4: 44.0, 5: 55.0, 6: 66.0},
            )
        )
        _FakeReceiveConnector.instances[config.RAW_ECU_STREAM_ID].enqueue(
            _make_raw_batch(
                config.RAW_ECU_STREAM_ID,
                {1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, 5: 105.0, 6: 106.0},
            )
        )
        _FakeReceiveConnector.instances[config.RAW_LOADCELL_STREAM_ID].enqueue(
            _make_raw_batch(config.RAW_LOADCELL_STREAM_ID, {1: 501.0})
        )

        runtime.step()
        runtime.step()
        runtime.close()

        gse_data, _ = _read_raw_table(self.raw_root, config.RAW_GSE_STREAM_ID, 4)
        ecu_data, _ = _read_raw_table(self.raw_root, config.RAW_ECU_STREAM_ID, 4)
        load_data, _ = _read_raw_table(self.raw_root, config.RAW_LOADCELL_STREAM_ID, 4)
        extr_data, _ = _read_raw_table(self.raw_root, config.RAW_EXTR_ECU_STREAM_ID, 4)

        self.assertEqual(gse_data.shape[0], 1)
        self.assertEqual(ecu_data.shape[0], 1)
        self.assertEqual(load_data.shape[0], 1)
        self.assertEqual(extr_data.shape[0], 0)
        self.assertEqual(gse_data[0, 3], 33.0)
        self.assertEqual(ecu_data[0, 1], 101.0)
        self.assertEqual(load_data[0, 1], 501.0)

    def test_runtime_stores_latest_visible_batch_per_step_and_reuses_cached_rows(self) -> None:
        runtime = self._build_runtime(rows_per_file=8)
        self.addCleanup(runtime.close)

        gse_rx = _FakeReceiveConnector.instances[config.RAW_GSE_STREAM_ID]
        ecu_rx = _FakeReceiveConnector.instances[config.RAW_ECU_STREAM_ID]
        extr_rx = _FakeReceiveConnector.instances[config.RAW_EXTR_ECU_STREAM_ID]
        load_rx = _FakeReceiveConnector.instances[config.RAW_LOADCELL_STREAM_ID]

        gse_rx.enqueue(
            _make_raw_batch(
                config.RAW_GSE_STREAM_ID,
                {1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0, 6: 60.0},
            )
        )
        ecu_rx.enqueue(
            _make_raw_batch(
                config.RAW_ECU_STREAM_ID,
                {1: 100.0, 2: 200.0, 3: 300.0, 4: 400.0, 5: 500.0, 6: 600.0},
            )
        )
        extr_rx.enqueue(
            _make_raw_batch(
                config.RAW_EXTR_ECU_STREAM_ID,
                {3: 900.0, 4: 1000.0},
            )
        )
        load_rx.enqueue(_make_raw_batch(config.RAW_LOADCELL_STREAM_ID, {1: 700.0}))
        runtime.step()

        gse_rx.enqueue(
            _make_raw_batch(
                config.RAW_GSE_STREAM_ID,
                {1: 11.0, 2: 21.0, 3: 31.0, 4: 41.0, 5: 51.0, 6: 61.0},
            )
        )
        gse_rx.enqueue(
            _make_raw_batch(
                config.RAW_GSE_STREAM_ID,
                {1: 12.0, 2: 22.0, 3: 32.0, 4: 42.0, 5: 52.0, 6: 62.0},
            )
        )
        load_rx.enqueue(_make_raw_batch(config.RAW_LOADCELL_STREAM_ID, {1: 701.0}))
        runtime.step()
        runtime.close()

        gse_data, _ = _read_raw_table(self.raw_root, config.RAW_GSE_STREAM_ID, 8)
        load_data, _ = _read_raw_table(self.raw_root, config.RAW_LOADCELL_STREAM_ID, 8)

        self.assertEqual(gse_data.shape[0], 2)
        self.assertEqual(load_data.shape[0], 2)
        np.testing.assert_array_equal(gse_data[:, 1], np.array([10.0, 12.0]))
        np.testing.assert_array_equal(load_data[:, 1], np.array([700.0, 701.0]))

        tank_batches = _FakeSendConnector.instances[
            config.FRONTEND_TANK_PRESSURES_STREAM_ID
        ].sent_batches
        line_batches = _FakeSendConnector.instances[
            config.FRONTEND_LINE_PRESSURES_STREAM_ID
        ].sent_batches
        load_batches = _FakeSendConnector.instances[
            config.FRONTEND_LOADCELL_STREAM_ID
        ].sent_batches
        tank_fft_batches = _FakeSendConnector.instances[
            config.FRONTEND_TANK_FFT_STREAM_ID
        ].sent_batches
        line_fft_batches = _FakeSendConnector.instances[
            config.FRONTEND_LINE_FFT_STREAM_ID
        ].sent_batches
        load_fft_batches = _FakeSendConnector.instances[
            config.FRONTEND_LOADCELL_FFT_STREAM_ID
        ].sent_batches
        throughput_batches = _FakeSendConnector.instances[
            config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID
        ].sent_batches

        self.assertEqual(len(tank_batches), 2)
        self.assertEqual(len(tank_fft_batches), 2)
        self.assertEqual(len(line_fft_batches), 2)
        self.assertEqual(len(load_fft_batches), 2)
        self.assertEqual(len(throughput_batches), 2)
        np.testing.assert_array_equal(
            tank_batches[-1][0, 1:],
            np.array([100.0, 200.0, 300.0]),
        )
        np.testing.assert_array_equal(
            line_batches[-1][0, 1:],
            np.array([32.0, 42.0, 22.0, 650.0, 750.0]),
        )
        np.testing.assert_array_equal(load_batches[-1][0, 1:], np.array([701.0]))
        self.assertEqual(tank_fft_batches[-1].shape, (1, 4))
        self.assertEqual(line_fft_batches[-1].shape, (1, 6))
        self.assertEqual(load_fft_batches[-1].shape, (1, 2))
        np.testing.assert_allclose(
            tank_fft_batches[-1][0, 1:],
            np.array([100.0, 200.0, 300.0]),
        )
        np.testing.assert_allclose(
            line_fft_batches[-1][0, 1:],
            np.array([31.0, 41.0, 21.0, 650.0, 750.0]),
        )
        np.testing.assert_allclose(
            load_fft_batches[-1][0, 1:],
            np.array([700.5]),
        )
        self.assertGreater(float(throughput_batches[-1][0, 1]), 0.0)


if __name__ == "__main__":
    unittest.main()
