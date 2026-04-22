from __future__ import annotations

from collections import deque
import logging
import time
from typing import Final

import numpy as np
from ucirplgui import config
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec

from .fft_process import FftReconstructionProcess
# Raw telemetry database service is disabled for now.
# from .storage import RawTelemetryRecorder


LOGGER = logging.getLogger("ucirplgui.backend")
_SLEEP_S: Final[float] = config.DEFAULT_BATCH_SLEEP_S
_BITS_PER_MEGABIT: Final[float] = 1_000_000.0


def _stream_spec(stream_id: str) -> StreamSpec:
    return StreamSpec(
        stream_id=stream_id,
        schema=config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
    )


def _raw_stream_specs() -> dict[str, StreamSpec]:
    return {stream_id: _stream_spec(stream_id) for stream_id in config.RAW_STREAMS}


class BackendPipelineRuntime:
    def __init__(self) -> None:
        self.rx_gse = ReceiveConnector(
            _stream_spec(config.RAW_GSE_STREAM_ID),
            port=config.CONNECTOR_PORTS["raw_gse"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.rx_ecu = ReceiveConnector(
            _stream_spec(config.RAW_ECU_STREAM_ID),
            port=config.CONNECTOR_PORTS["raw_ecu"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.rx_extr = ReceiveConnector(
            _stream_spec(config.RAW_EXTR_ECU_STREAM_ID),
            port=config.CONNECTOR_PORTS["raw_extr_ecu"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.rx_load = ReceiveConnector(
            _stream_spec(config.RAW_LOADCELL_STREAM_ID),
            port=config.CONNECTOR_PORTS["raw_loadcell"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )

        self.tx_tank = SendConnector(
            _stream_spec(config.FRONTEND_TANK_PRESSURES_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_tank_pressures"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_line = SendConnector(
            _stream_spec(config.FRONTEND_LINE_PRESSURES_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_line_pressures"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_load = SendConnector(
            _stream_spec(config.FRONTEND_LOADCELL_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_loadcell"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_tank_fft = SendConnector(
            _stream_spec(config.FRONTEND_TANK_FFT_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_fft"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_line_fft = SendConnector(
            _stream_spec(config.FRONTEND_LINE_FFT_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_line_fft"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_load_fft = SendConnector(
            _stream_spec(config.FRONTEND_LOADCELL_FFT_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_loadcell_fft"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_scalars = SendConnector(
            _stream_spec(config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_gse_ecu_scalars"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )
        self.tx_backend_throughput = SendConnector(
            _stream_spec(config.FRONTEND_BACKEND_THROUGHPUT_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_backend_throughput"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )

        self._fft_process = FftReconstructionProcess(
            window_size=config.FFT_RECONSTRUCTION_WINDOW,
            retained_frequency_bins=config.FFT_RECONSTRUCTION_LOW_FREQUENCY_BINS,
            min_samples=config.FFT_RECONSTRUCTION_MIN_SAMPLES,
        )
        self._throughput_history_mbps: deque[tuple[float, float]] = deque()
        self._last_throughput_timestamp_s: float | None = None
        self._raw_receivers = {
            config.RAW_GSE_STREAM_ID: self.rx_gse,
            config.RAW_ECU_STREAM_ID: self.rx_ecu,
            config.RAW_EXTR_ECU_STREAM_ID: self.rx_extr,
            config.RAW_LOADCELL_STREAM_ID: self.rx_load,
        }
        self._latest_raw_batches = {
            stream_id: None for stream_id in self._raw_receivers
        }
        # Raw telemetry database service is disabled for now.
        # self._raw_storage = RawTelemetryRecorder(
        #     config.BACKEND_RAW_STORAGE_DIR,
        #     _raw_stream_specs(),
        #     rows_per_file=config.BACKEND_RAW_STORAGE_ROWS_PER_FILE,
        # )
        self._raw_storage = None
        self._opened = False
        self._closed = False

    def _avg(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    def open(self) -> None:
        if self._closed:
            raise RuntimeError("BackendPipelineRuntime is closed.")
        if self._opened:
            return
        for connector in (
            self.rx_gse,
            self.rx_ecu,
            self.rx_extr,
            self.rx_load,
            self.tx_tank,
            self.tx_line,
            self.tx_load,
            self.tx_tank_fft,
            self.tx_line_fft,
            self.tx_load_fft,
            self.tx_scalars,
            self.tx_backend_throughput,
        ):
            connector.open()
        self._opened = True

    def close(self) -> None:
        if self._closed:
            return

        close_error: Exception | None = None
        for connector in (
            self.tx_scalars,
            self.tx_load_fft,
            self.tx_line_fft,
            self.tx_tank_fft,
            self.tx_load,
            self.tx_line,
            self.tx_tank,
            self.tx_backend_throughput,
            self.rx_load,
            self.rx_extr,
            self.rx_ecu,
            self.rx_gse,
        ):
            try:
                connector.close()
            except Exception as exc:
                if close_error is None:
                    close_error = exc

        # Raw telemetry database service is disabled for now.
        # try:
        #     self._raw_storage.close()
        # except Exception as exc:
        #     if close_error is None:
        #         close_error = exc

        self._closed = True
        if close_error is not None:
            raise close_error

    def _poll_raw_streams(self) -> None:
        for stream_id, connector in self._raw_receivers.items():
            if not connector.has_batch:
                continue

            batch = np.asarray(connector.batch, dtype=np.float64).copy()
            self._latest_raw_batches[stream_id] = batch
            # Raw telemetry database service is disabled for now.
            # stored_batch = self._stored_raw_batches[stream_id]
            # if stored_batch is None or not np.array_equal(stored_batch, batch):
            #     self._raw_storage.store(stream_id, batch)
            #     self._stored_raw_batches[stream_id] = batch

    def _latest_row(self, stream_id: str) -> np.ndarray | None:
        batch = self._latest_raw_batches[stream_id]
        if batch is None:
            return None
        return batch[0]

    def _backend_throughput_mbps(
        self,
        *,
        timestamp_s: float,
        step_batches: tuple[np.ndarray, ...],
    ) -> float:
        step_bits = sum(batch.nbytes for batch in step_batches) * 8.0
        previous_timestamp_s = self._last_throughput_timestamp_s
        if previous_timestamp_s is None:
            interval_s = _SLEEP_S
        else:
            interval_s = max(timestamp_s - previous_timestamp_s, _SLEEP_S)
        self._last_throughput_timestamp_s = timestamp_s

        instantaneous_mbps = step_bits / (interval_s * _BITS_PER_MEGABIT)
        self._throughput_history_mbps.append((timestamp_s, instantaneous_mbps))

        cutoff_s = timestamp_s - float(config.BACKEND_THROUGHPUT_WINDOW_S)
        while self._throughput_history_mbps:
            sample_timestamp_s, _ = self._throughput_history_mbps[0]
            if sample_timestamp_s >= cutoff_s:
                break
            self._throughput_history_mbps.popleft()

        if not self._throughput_history_mbps:
            return 0.0
        return float(
            sum(value for _, value in self._throughput_history_mbps)
            / len(self._throughput_history_mbps)
        )

    def step(self) -> None:
        self.open()
        self._poll_raw_streams()

        timestamp_s = time.time()
        gse_row = self._latest_row(config.RAW_GSE_STREAM_ID)
        ecu_row = self._latest_row(config.RAW_ECU_STREAM_ID)
        load_row = self._latest_row(config.RAW_LOADCELL_STREAM_ID)
        extr_row = self._latest_row(config.RAW_EXTR_ECU_STREAM_ID)

        copv = float(ecu_row[1]) if ecu_row is not None else 0.0
        lox = float(ecu_row[2]) if ecu_row is not None else 0.0
        lng = float(ecu_row[3]) if ecu_row is not None else 0.0
        inj_lox = float(ecu_row[4]) if ecu_row is not None else 0.0
        inj_lng = float(ecu_row[5]) if ecu_row is not None else 0.0
        vent = float(gse_row[3]) if gse_row is not None else 0.0
        lox_mvas = float(gse_row[4]) if gse_row is not None else 0.0
        lox_inj_tee = float(gse_row[2]) if gse_row is not None else 0.0
        force = float(load_row[1]) if load_row is not None else 0.0

        # Use EXTR_ECU as secondary source to smooth line-pressure channels.
        if extr_row is not None:
            inj_lox = self._avg([inj_lox, float(extr_row[3])])
            inj_lng = self._avg([inj_lng, float(extr_row[4])])

        tank_out = np.array([[timestamp_s, copv, lox, lng]], dtype=np.float64)
        line_out = np.array(
            [[timestamp_s, vent, lox_mvas, lox_inj_tee, inj_lox, inj_lng]],
            dtype=np.float64,
        )
        load_out = np.array([[timestamp_s, force]], dtype=np.float64)

        self.tx_tank.send_numpy(tank_out)
        self.tx_line.send_numpy(line_out)
        self.tx_load.send_numpy(load_out)

        tank_fft_out = self._fft_process.frame(
            timestamp_s,
            (
                ("fft_tank_copv", copv),
                ("fft_tank_lox", lox),
                ("fft_tank_lng", lng),
            ),
        )
        line_fft_out = self._fft_process.frame(
            timestamp_s,
            (
                ("fft_line_vent", vent),
                ("fft_line_lox_mvas", lox_mvas),
                ("fft_line_lox_inj_tee", lox_inj_tee),
                ("fft_line_inj_lox", inj_lox),
                ("fft_line_inj_lng", inj_lng),
            ),
        )
        load_fft_out = self._fft_process.frame(
            timestamp_s,
            (("fft_load_force", force),),
        )
        self.tx_tank_fft.send_numpy(tank_fft_out)
        self.tx_line_fft.send_numpy(line_fft_out)
        self.tx_load_fft.send_numpy(load_fft_out)

        eng1 = float(gse_row[5]) if gse_row is not None else 0.0
        eng2 = float(gse_row[6]) if gse_row is not None else 0.0
        gn2 = float(gse_row[1]) if gse_row is not None else 0.0
        copv_tc = float(ecu_row[6]) if ecu_row is not None else 0.0
        scalars_out = np.array(
            [[timestamp_s, eng1, eng2, gn2, copv_tc]],
            dtype=np.float64,
        )
        self.tx_scalars.send_numpy(scalars_out)

        step_batches = tuple(
            batch
            for batch in (
                self._latest_raw_batches[config.RAW_GSE_STREAM_ID],
                self._latest_raw_batches[config.RAW_ECU_STREAM_ID],
                self._latest_raw_batches[config.RAW_EXTR_ECU_STREAM_ID],
                self._latest_raw_batches[config.RAW_LOADCELL_STREAM_ID],
                tank_out,
                line_out,
                load_out,
                tank_fft_out,
                line_fft_out,
                load_fft_out,
                scalars_out,
            )
            if batch is not None
        )
        backend_throughput_mbps = self._backend_throughput_mbps(
            timestamp_s=timestamp_s,
            step_batches=step_batches,
        )
        self.tx_backend_throughput.send_numpy(
            np.array([[timestamp_s, backend_throughput_mbps]], dtype=np.float64)
        )

    def run_forever(self) -> None:
        self.open()
        try:
            while True:
                self.step()
                time.sleep(_SLEEP_S)
        finally:
            self.close()

    def __enter__(self) -> BackendPipelineRuntime:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

def run_pipeline() -> None:
    runtime = BackendPipelineRuntime()
    runtime.run_forever()


def build_ucirplgui_pipeline() -> BackendPipelineRuntime:
    return BackendPipelineRuntime()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info("Starting backend pipeline runtime")
    run_pipeline()


if __name__ == "__main__":
    main()
