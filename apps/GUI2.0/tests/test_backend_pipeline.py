from __future__ import annotations

import unittest

import numpy as np

from ucirplgui import config
from ucirplgui.backend.pipeline import (
    ThroughputTracker,
    _BACKEND_THROUGHPUT_STREAM,
    _ECU_STREAM,
    _EXTR_ECU_STREAM,
    _GSE_STREAM,
    _LINE_FFT_STREAM,
    _LINE_PRESSURES_STREAM,
    _LOADCELL_FFT_STREAM,
    _LOADCELL_RAW_STREAM,
    _LOADCELL_STREAM,
    _SCALARS_STREAM,
    _TANK_FFT_STREAM,
    _TANK_PRESSURES_STREAM,
    _compute_loadcell_batch,
    _compute_pressure_batches,
    _compute_scalars_batch,
    _configure_backend_pipeline,
    _drain_reader,
)
from viviian import VIVIIan


def _batch(stream_id: str, values: dict[int, float]) -> np.ndarray:
    batch = np.zeros(
        (config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
        dtype=np.float64,
    )
    for index, value in values.items():
        batch[0, index] = float(value)
    return batch


class _FakeReader:
    def __init__(self, *frames: np.ndarray | None) -> None:
        self._frames = list(frames)

    def read(self) -> np.ndarray | None:
        if not self._frames:
            return None
        return self._frames.pop(0)


class BackendPipelineTests(unittest.TestCase):
    def test_compute_pressure_batches_uses_extr_ecu_average(self) -> None:
        tank_out, line_out = _compute_pressure_batches(
            timestamp_s=123.0,
            gse_batch=_batch(
                config.RAW_GSE_STREAM_ID,
                {2: 22.0, 3: 33.0, 4: 44.0},
            ),
            ecu_batch=_batch(
                config.RAW_ECU_STREAM_ID,
                {1: 100.0, 2: 200.0, 3: 300.0, 4: 400.0, 5: 500.0},
            ),
            extr_batch=_batch(
                config.RAW_EXTR_ECU_STREAM_ID,
                {3: 900.0, 4: 1000.0},
            ),
        )

        np.testing.assert_allclose(tank_out, np.array([[123.0, 100.0, 200.0, 300.0]]))
        np.testing.assert_allclose(
            line_out,
            np.array([[123.0, 33.0, 44.0, 22.0, 650.0, 750.0]]),
        )

    def test_compute_loadcell_batch_preserves_force(self) -> None:
        loadcell_out = _compute_loadcell_batch(
            timestamp_s=456.0,
            loadcell_batch=_batch(config.RAW_LOADCELL_STREAM_ID, {1: 701.0}),
        )

        np.testing.assert_allclose(loadcell_out, np.array([[456.0, 701.0]]))

    def test_compute_scalars_batch_maps_expected_channels(self) -> None:
        scalars_out = _compute_scalars_batch(
            timestamp_s=789.0,
            gse_batch=_batch(
                config.RAW_GSE_STREAM_ID,
                {1: 11.0, 5: 55.0, 6: 66.0},
            ),
            ecu_batch=_batch(
                config.RAW_ECU_STREAM_ID,
                {6: 106.0},
            ),
        )

        np.testing.assert_allclose(
            scalars_out,
            np.array([[789.0, 55.0, 66.0, 11.0, 106.0]]),
        )

    def test_drain_reader_ignores_disconnect_nan_batches(self) -> None:
        cached = _batch(config.RAW_GSE_STREAM_ID, {1: 10.0})
        nan_frame = np.full_like(cached, np.nan)
        latest = _batch(config.RAW_GSE_STREAM_ID, {1: 12.0})

        drained = _drain_reader(_FakeReader(nan_frame, latest, None), cached)

        np.testing.assert_allclose(drained, latest)

    def test_throughput_tracker_returns_positive_rate(self) -> None:
        tracker = ThroughputTracker()
        first = tracker.update(
            timestamp_s=1.0,
            batches=(np.ones((1, 2), dtype=np.float64),),
        )
        second = tracker.update(
            timestamp_s=1.1,
            batches=(np.ones((1, 4), dtype=np.float64),),
        )

        self.assertGreater(first, 0.0)
        self.assertGreater(second, 0.0)

    def test_configure_backend_pipeline_registers_expected_graph(self) -> None:
        with VIVIIan("backend") as pipe:
            _configure_backend_pipeline(pipe)

            self.assertEqual(
                set(pipe._streams),
                {
                    _GSE_STREAM,
                    _ECU_STREAM,
                    _EXTR_ECU_STREAM,
                    _LOADCELL_RAW_STREAM,
                    _TANK_PRESSURES_STREAM,
                    _LINE_PRESSURES_STREAM,
                    _LOADCELL_STREAM,
                    _TANK_FFT_STREAM,
                    _LINE_FFT_STREAM,
                    _LOADCELL_FFT_STREAM,
                    _SCALARS_STREAM,
                    _BACKEND_THROUGHPUT_STREAM,
                },
            )
            self.assertEqual(
                set(pipe._tasks),
                {
                    "receive_connectors_task",
                    "compute_domain_task",
                    "fft_domain_task",
                    "send_connectors_task",
                },
            )


if __name__ == "__main__":
    unittest.main()
