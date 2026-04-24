from __future__ import annotations

from collections import deque
import logging
import time
from typing import Final

import numpy as np
from ucirplgui import config
from viviian import VIVIIan
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec

from .fft_process import FftReconstructionProcess


LOGGER = logging.getLogger("ucirplgui.backend")
_SLEEP_S: Final[float] = config.DEFAULT_BATCH_SLEEP_S
_BITS_PER_MEGABIT: Final[float] = 1_000_000.0

_GSE_STREAM = "gse_stream"
_ECU_STREAM = "ecu_stream"
_EXTR_ECU_STREAM = "extr_ecu_stream"
_LOADCELL_RAW_STREAM = "loadcell_raw_stream"
_TANK_PRESSURES_STREAM = "tank_pressures_stream"
_LINE_PRESSURES_STREAM = "line_pressures_stream"
_LOADCELL_STREAM = "loadcell_stream"
_TANK_FFT_STREAM = "tank_fft_stream"
_LINE_FFT_STREAM = "line_fft_stream"
_LOADCELL_FFT_STREAM = "loadcell_fft_stream"
_SCALARS_STREAM = "gse_ecu_scalars_stream"
_BACKEND_THROUGHPUT_STREAM = "backend_throughput_stream"


def _drain_reader(reader, cached: np.ndarray | None) -> np.ndarray | None:
    latest = cached
    while True:
        batch = reader.read()
        if batch is None:
            return latest
        if np.isnan(batch).all():
            continue
        latest = np.asarray(batch, dtype=np.float64).copy()


def _prepare_reader(*readers) -> None:
    for reader in readers:
        reader.set_blocking(False)


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _latest_row(batch: np.ndarray | None) -> np.ndarray | None:
    if batch is None:
        return None
    return batch[0]


def _compute_pressure_batches(
    *,
    timestamp_s: float,
    gse_batch: np.ndarray | None,
    ecu_batch: np.ndarray | None,
    extr_batch: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    gse_row = _latest_row(gse_batch)
    ecu_row = _latest_row(ecu_batch)
    extr_row = _latest_row(extr_batch)

    copv = float(ecu_row[1]) if ecu_row is not None else 0.0
    lox = float(ecu_row[2]) if ecu_row is not None else 0.0
    lng = float(ecu_row[3]) if ecu_row is not None else 0.0
    inj_lox = float(ecu_row[4]) if ecu_row is not None else 0.0
    inj_lng = float(ecu_row[5]) if ecu_row is not None else 0.0
    vent = float(gse_row[3]) if gse_row is not None else 0.0
    lox_mvas = float(gse_row[4]) if gse_row is not None else 0.0
    lox_inj_tee = float(gse_row[2]) if gse_row is not None else 0.0

    if extr_row is not None:
        inj_lox = _avg([inj_lox, float(extr_row[3])])
        inj_lng = _avg([inj_lng, float(extr_row[4])])

    tank_out = np.array([[timestamp_s, copv, lox, lng]], dtype=np.float64)
    line_out = np.array(
        [[timestamp_s, vent, lox_mvas, lox_inj_tee, inj_lox, inj_lng]],
        dtype=np.float64,
    )
    return tank_out, line_out


def _compute_loadcell_batch(
    *,
    timestamp_s: float,
    loadcell_batch: np.ndarray | None,
) -> np.ndarray:
    load_row = _latest_row(loadcell_batch)
    force = float(load_row[1]) if load_row is not None else 0.0
    return np.array([[timestamp_s, force]], dtype=np.float64)


def _compute_scalars_batch(
    *,
    timestamp_s: float,
    gse_batch: np.ndarray | None,
    ecu_batch: np.ndarray | None,
) -> np.ndarray:
    gse_row = _latest_row(gse_batch)
    ecu_row = _latest_row(ecu_batch)

    eng1 = float(gse_row[5]) if gse_row is not None else 0.0
    eng2 = float(gse_row[6]) if gse_row is not None else 0.0
    gn2 = float(gse_row[1]) if gse_row is not None else 0.0
    copv_tc = float(ecu_row[6]) if ecu_row is not None else 0.0
    return np.array([[timestamp_s, eng1, eng2, gn2, copv_tc]], dtype=np.float64)


class ThroughputTracker:
    def __init__(self) -> None:
        self._history_mbps: deque[tuple[float, float]] = deque()
        self._last_timestamp_s: float | None = None

    def update(
        self,
        *,
        timestamp_s: float,
        batches: tuple[np.ndarray, ...],
    ) -> float:
        step_bits = sum(batch.nbytes for batch in batches) * 8.0
        previous_timestamp_s = self._last_timestamp_s
        if previous_timestamp_s is None:
            interval_s = _SLEEP_S
        else:
            interval_s = max(timestamp_s - previous_timestamp_s, _SLEEP_S)
        self._last_timestamp_s = timestamp_s

        instantaneous_mbps = step_bits / (interval_s * _BITS_PER_MEGABIT)
        self._history_mbps.append((timestamp_s, instantaneous_mbps))

        cutoff_s = timestamp_s - float(config.BACKEND_THROUGHPUT_WINDOW_S)
        while self._history_mbps:
            sample_timestamp_s, _ = self._history_mbps[0]
            if sample_timestamp_s >= cutoff_s:
                break
            self._history_mbps.popleft()

        if not self._history_mbps:
            return 0.0
        return float(
            sum(value for _, value in self._history_mbps) / len(self._history_mbps)
        )


def make_gse_connector(gse_stream) -> None:
    with ReceiveConnector(
        StreamSpec(
            stream_id=config.RAW_GSE_STREAM_ID,
            schema=config.SCHEMAS[config.RAW_GSE_STREAM_ID],
            shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[config.RAW_GSE_STREAM_ID])),
            stream=gse_stream,
        ),
        port=config.CONNECTOR_PORTS["raw_gse"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ):
        while True:
            pass


def make_ecu_connector(ecu_stream) -> None:
    with ReceiveConnector(
        StreamSpec(
            stream_id=config.RAW_ECU_STREAM_ID,
            schema=config.SCHEMAS[config.RAW_ECU_STREAM_ID],
            shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[config.RAW_ECU_STREAM_ID])),
            stream=ecu_stream,
        ),
        port=config.CONNECTOR_PORTS["raw_ecu"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ):
        while True:
            pass


def make_extr_ecu_connector(extr_ecu_stream) -> None:
    with ReceiveConnector(
        StreamSpec(
            stream_id=config.RAW_EXTR_ECU_STREAM_ID,
            schema=config.SCHEMAS[config.RAW_EXTR_ECU_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.RAW_EXTR_ECU_STREAM_ID]),
            ),
            stream=extr_ecu_stream,
        ),
        port=config.CONNECTOR_PORTS["raw_extr_ecu"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ):
        while True:
            pass


def make_loadcell_connector(loadcell_raw_stream) -> None:
    with ReceiveConnector(
        StreamSpec(
            stream_id=config.RAW_LOADCELL_STREAM_ID,
            schema=config.SCHEMAS[config.RAW_LOADCELL_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.RAW_LOADCELL_STREAM_ID]),
            ),
            stream=loadcell_raw_stream,
        ),
        port=config.CONNECTOR_PORTS["raw_loadcell"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ):
        while True:
            pass


def pressure_domain_task(
    gse_stream,
    ecu_stream,
    extr_ecu_stream,
    tank_pressures_stream,
    line_pressures_stream,
) -> None:
    _prepare_reader(gse_stream, ecu_stream, extr_ecu_stream)
    gse_batch: np.ndarray | None = None
    ecu_batch: np.ndarray | None = None
    extr_batch: np.ndarray | None = None

    while True:
        gse_batch = _drain_reader(gse_stream, gse_batch)
        ecu_batch = _drain_reader(ecu_stream, ecu_batch)
        extr_batch = _drain_reader(extr_ecu_stream, extr_batch)
        timestamp_s = time.time()
        tank_out, line_out = _compute_pressure_batches(
            timestamp_s=timestamp_s,
            gse_batch=gse_batch,
            ecu_batch=ecu_batch,
            extr_batch=extr_batch,
        )
        tank_pressures_stream.write(tank_out)
        line_pressures_stream.write(line_out)


def loadcell_domain_task(loadcell_raw_stream, loadcell_stream) -> None:
    _prepare_reader(loadcell_raw_stream)
    loadcell_batch: np.ndarray | None = None

    while True:
        loadcell_batch = _drain_reader(loadcell_raw_stream, loadcell_batch)
        loadcell_stream.write(
            _compute_loadcell_batch(
                timestamp_s=time.time(),
                loadcell_batch=loadcell_batch,
            )
        )


def fft_domain_task(
    tank_pressures_stream,
    line_pressures_stream,
    loadcell_stream,
    tank_fft_stream,
    line_fft_stream,
    loadcell_fft_stream,
) -> None:
    _prepare_reader(tank_pressures_stream, line_pressures_stream, loadcell_stream)
    fft_process = FftReconstructionProcess(
        window_size=config.FFT_RECONSTRUCTION_WINDOW,
        retained_frequency_bins=config.FFT_RECONSTRUCTION_LOW_FREQUENCY_BINS,
        min_samples=config.FFT_RECONSTRUCTION_MIN_SAMPLES,
    )
    tank_batch: np.ndarray | None = None
    line_batch: np.ndarray | None = None
    load_batch: np.ndarray | None = None

    while True:
        tank_batch = _drain_reader(tank_pressures_stream, tank_batch)
        line_batch = _drain_reader(line_pressures_stream, line_batch)
        load_batch = _drain_reader(loadcell_stream, load_batch)

        tank_row = _latest_row(tank_batch)
        line_row = _latest_row(line_batch)
        load_row = _latest_row(load_batch)
        timestamp_s = time.time()

        tank_fft_out = fft_process.frame(
            timestamp_s,
            (
                ("fft_tank_copv", float(tank_row[1]) if tank_row is not None else 0.0),
                ("fft_tank_lox", float(tank_row[2]) if tank_row is not None else 0.0),
                ("fft_tank_lng", float(tank_row[3]) if tank_row is not None else 0.0),
            ),
        )
        line_fft_out = fft_process.frame(
            timestamp_s,
            (
                ("fft_line_vent", float(line_row[1]) if line_row is not None else 0.0),
                (
                    "fft_line_lox_mvas",
                    float(line_row[2]) if line_row is not None else 0.0,
                ),
                (
                    "fft_line_lox_inj_tee",
                    float(line_row[3]) if line_row is not None else 0.0,
                ),
                (
                    "fft_line_inj_lox",
                    float(line_row[4]) if line_row is not None else 0.0,
                ),
                (
                    "fft_line_inj_lng",
                    float(line_row[5]) if line_row is not None else 0.0,
                ),
            ),
        )
        load_fft_out = fft_process.frame(
            timestamp_s,
            (("fft_load_force", float(load_row[1]) if load_row is not None else 0.0),),
        )

        tank_fft_stream.write(tank_fft_out)
        line_fft_stream.write(line_fft_out)
        loadcell_fft_stream.write(load_fft_out)


def scalar_metrics_task(
    gse_stream,
    ecu_stream,
    extr_ecu_stream,
    loadcell_raw_stream,
    tank_pressures_stream,
    line_pressures_stream,
    loadcell_stream,
    tank_fft_stream,
    line_fft_stream,
    loadcell_fft_stream,
    gse_ecu_scalars_stream,
    backend_throughput_stream,
) -> None:
    _prepare_reader(
        gse_stream,
        ecu_stream,
        extr_ecu_stream,
        loadcell_raw_stream,
        tank_pressures_stream,
        line_pressures_stream,
        loadcell_stream,
        tank_fft_stream,
        line_fft_stream,
        loadcell_fft_stream,
    )
    throughput_tracker = ThroughputTracker()
    gse_batch: np.ndarray | None = None
    ecu_batch: np.ndarray | None = None
    extr_batch: np.ndarray | None = None
    loadcell_raw_batch: np.ndarray | None = None
    tank_batch: np.ndarray | None = None
    line_batch: np.ndarray | None = None
    load_batch: np.ndarray | None = None
    tank_fft_batch: np.ndarray | None = None
    line_fft_batch: np.ndarray | None = None
    load_fft_batch: np.ndarray | None = None

    while True:
        gse_batch = _drain_reader(gse_stream, gse_batch)
        ecu_batch = _drain_reader(ecu_stream, ecu_batch)
        extr_batch = _drain_reader(extr_ecu_stream, extr_batch)
        loadcell_raw_batch = _drain_reader(loadcell_raw_stream, loadcell_raw_batch)
        tank_batch = _drain_reader(tank_pressures_stream, tank_batch)
        line_batch = _drain_reader(line_pressures_stream, line_batch)
        load_batch = _drain_reader(loadcell_stream, load_batch)
        tank_fft_batch = _drain_reader(tank_fft_stream, tank_fft_batch)
        line_fft_batch = _drain_reader(line_fft_stream, line_fft_batch)
        load_fft_batch = _drain_reader(loadcell_fft_stream, load_fft_batch)

        timestamp_s = time.time()
        scalars_out = _compute_scalars_batch(
            timestamp_s=timestamp_s,
            gse_batch=gse_batch,
            ecu_batch=ecu_batch,
        )
        gse_ecu_scalars_stream.write(scalars_out)

        step_batches = tuple(
            batch
            for batch in (
                gse_batch,
                ecu_batch,
                extr_batch,
                loadcell_raw_batch,
                tank_batch,
                line_batch,
                load_batch,
                tank_fft_batch,
                line_fft_batch,
                load_fft_batch,
                scalars_out,
            )
            if batch is not None
        )
        throughput_out = np.array(
            [[
                timestamp_s,
                throughput_tracker.update(
                    timestamp_s=timestamp_s,
                    batches=step_batches,
                ),
            ]],
            dtype=np.float64,
        )
        backend_throughput_stream.write(throughput_out)


def make_tank_pressures_connector(tank_pressures_stream) -> None:
    _prepare_reader(tank_pressures_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_TANK_PRESSURES_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_TANK_PRESSURES_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_TANK_PRESSURES_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_tank_pressures"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(tank_pressures_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_line_pressures_connector(line_pressures_stream) -> None:
    _prepare_reader(line_pressures_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_LINE_PRESSURES_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_LINE_PRESSURES_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_LINE_PRESSURES_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_line_pressures"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(line_pressures_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_loadcell_output_connector(loadcell_stream) -> None:
    _prepare_reader(loadcell_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_LOADCELL_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_LOADCELL_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_LOADCELL_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_loadcell"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(loadcell_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_tank_fft_connector(tank_fft_stream) -> None:
    _prepare_reader(tank_fft_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_TANK_FFT_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_TANK_FFT_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_TANK_FFT_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_fft"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(tank_fft_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_line_fft_connector(line_fft_stream) -> None:
    _prepare_reader(line_fft_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_LINE_FFT_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_LINE_FFT_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_LINE_FFT_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_line_fft"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(line_fft_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_loadcell_fft_connector(loadcell_fft_stream) -> None:
    _prepare_reader(loadcell_fft_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_LOADCELL_FFT_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_LOADCELL_FFT_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_LOADCELL_FFT_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_loadcell_fft"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(loadcell_fft_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_scalars_connector(gse_ecu_scalars_stream) -> None:
    _prepare_reader(gse_ecu_scalars_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_gse_ecu_scalars"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(gse_ecu_scalars_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def make_backend_throughput_connector(backend_throughput_stream) -> None:
    _prepare_reader(backend_throughput_stream)
    with SendConnector(
        StreamSpec(
            stream_id=config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID,
            schema=config.SCHEMAS[config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID],
            shape=(
                config.ROWS_PER_FRAME,
                len(config.SCHEMAS[config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID]),
            ),
        ),
        port=config.CONNECTOR_PORTS["frontend_backend_throughput"],
        host=config.DEFAULT_CONNECTOR_HOST,
    ) as connector:
        while True:
            batch = _drain_reader(backend_throughput_stream, None)
            if batch is not None:
                connector.send_numpy(batch)


def _configure_backend_pipeline(VIVII: VIVIIan) -> None:
    VIVII.add_stream(
        _GSE_STREAM,
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[config.RAW_GSE_STREAM_ID])),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _ECU_STREAM,
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[config.RAW_ECU_STREAM_ID])),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _EXTR_ECU_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.RAW_EXTR_ECU_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _LOADCELL_RAW_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.RAW_LOADCELL_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _TANK_PRESSURES_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_TANK_PRESSURES_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _LINE_PRESSURES_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_LINE_PRESSURES_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _LOADCELL_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_LOADCELL_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _TANK_FFT_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_TANK_FFT_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _LINE_FFT_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_LINE_FFT_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _LOADCELL_FFT_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_LOADCELL_FFT_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _SCALARS_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID]),
        ),
        dtype=np.float64,
    )
    VIVII.add_stream(
        _BACKEND_THROUGHPUT_STREAM,
        shape=(
            config.ROWS_PER_FRAME,
            len(config.SCHEMAS[config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID]),
        ),
        dtype=np.float64,
    )

    VIVII.add_task("gse_task", fn=make_gse_connector, writes={"gse_stream": _GSE_STREAM})
    VIVII.add_task("ecu_task", fn=make_ecu_connector, writes={"ecu_stream": _ECU_STREAM})
    VIVII.add_task(
        "extr_ecu_task",
        fn=make_extr_ecu_connector,
        writes={"extr_ecu_stream": _EXTR_ECU_STREAM},
    )
    VIVII.add_task(
        "loadcell_raw_task",
        fn=make_loadcell_connector,
        writes={"loadcell_raw_stream": _LOADCELL_RAW_STREAM},
    )

    VIVII.add_task(
        "pressure_domain_task",
        fn=pressure_domain_task,
        reads={
            "gse_stream": _GSE_STREAM,
            "ecu_stream": _ECU_STREAM,
            "extr_ecu_stream": _EXTR_ECU_STREAM,
        },
        writes={
            "tank_pressures_stream": _TANK_PRESSURES_STREAM,
            "line_pressures_stream": _LINE_PRESSURES_STREAM,
        },
    )
    VIVII.add_task(
        "loadcell_domain_task",
        fn=loadcell_domain_task,
        reads={"loadcell_raw_stream": _LOADCELL_RAW_STREAM},
        writes={"loadcell_stream": _LOADCELL_STREAM},
    )
    VIVII.add_task(
        "fft_domain_task",
        fn=fft_domain_task,
        reads={
            "tank_pressures_stream": _TANK_PRESSURES_STREAM,
            "line_pressures_stream": _LINE_PRESSURES_STREAM,
            "loadcell_stream": _LOADCELL_STREAM,
        },
        writes={
            "tank_fft_stream": _TANK_FFT_STREAM,
            "line_fft_stream": _LINE_FFT_STREAM,
            "loadcell_fft_stream": _LOADCELL_FFT_STREAM,
        },
    )
    VIVII.add_task(
        "scalar_metrics_task",
        fn=scalar_metrics_task,
        reads={
            "gse_stream": _GSE_STREAM,
            "ecu_stream": _ECU_STREAM,
            "extr_ecu_stream": _EXTR_ECU_STREAM,
            "loadcell_raw_stream": _LOADCELL_RAW_STREAM,
            "tank_pressures_stream": _TANK_PRESSURES_STREAM,
            "line_pressures_stream": _LINE_PRESSURES_STREAM,
            "loadcell_stream": _LOADCELL_STREAM,
            "tank_fft_stream": _TANK_FFT_STREAM,
            "line_fft_stream": _LINE_FFT_STREAM,
            "loadcell_fft_stream": _LOADCELL_FFT_STREAM,
        },
        writes={
            "gse_ecu_scalars_stream": _SCALARS_STREAM,
            "backend_throughput_stream": _BACKEND_THROUGHPUT_STREAM,
        },
    )

    VIVII.add_task(
        "tank_pressures_connector_task",
        fn=make_tank_pressures_connector,
        reads={"tank_pressures_stream": _TANK_PRESSURES_STREAM},
    )
    VIVII.add_task(
        "line_pressures_connector_task",
        fn=make_line_pressures_connector,
        reads={"line_pressures_stream": _LINE_PRESSURES_STREAM},
    )
    VIVII.add_task(
        "loadcell_connector_task",
        fn=make_loadcell_output_connector,
        reads={"loadcell_stream": _LOADCELL_STREAM},
    )
    VIVII.add_task(
        "tank_fft_connector_task",
        fn=make_tank_fft_connector,
        reads={"tank_fft_stream": _TANK_FFT_STREAM},
    )
    VIVII.add_task(
        "line_fft_connector_task",
        fn=make_line_fft_connector,
        reads={"line_fft_stream": _LINE_FFT_STREAM},
    )
    VIVII.add_task(
        "loadcell_fft_connector_task",
        fn=make_loadcell_fft_connector,
        reads={"loadcell_fft_stream": _LOADCELL_FFT_STREAM},
    )
    VIVII.add_task(
        "scalars_connector_task",
        fn=make_scalars_connector,
        reads={"gse_ecu_scalars_stream": _SCALARS_STREAM},
    )
    VIVII.add_task(
        "backend_throughput_connector_task",
        fn=make_backend_throughput_connector,
        reads={"backend_throughput_stream": _BACKEND_THROUGHPUT_STREAM},
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info("Starting backend pipeline task graph")
    with VIVIIan("backend") as VIVII:
        _configure_backend_pipeline(VIVII)
        VIVII.run()


if __name__ == "__main__":
    main()
