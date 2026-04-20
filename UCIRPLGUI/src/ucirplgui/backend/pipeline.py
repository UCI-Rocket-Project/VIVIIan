from __future__ import annotations

import csv
import logging
import math
from pathlib import Path
import time
from typing import Final

import numpy as np
from ucirplgui import config
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


LOGGER = logging.getLogger("ucirplgui.backend")
_SLEEP_S: Final[float] = 0.05
_FFT_WINDOW: Final[int] = 128


def _stream_spec(stream_id: str) -> StreamSpec:
    return StreamSpec(
        stream_id=stream_id,
        schema=config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
    )


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
        self.tx_fft = SendConnector(
            _stream_spec(config.FRONTEND_FFT_STREAM_ID),
            port=config.CONNECTOR_PORTS["frontend_fft"],
            host=config.DEFAULT_CONNECTOR_HOST,
        )

        self._fft_history: list[float] = []
        data_dir = Path("UCIRPLGUI") / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._storage_path = data_dir / "backend_storage.csv"
        self._ensure_storage_header()

    def _ensure_storage_header(self) -> None:
        if self._storage_path.exists():
            return
        with self._storage_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "ts_s",
                    "copv_psi",
                    "lox_psi",
                    "lng_psi",
                    "vent_psi",
                    "lox_mvas_psi",
                    "lox_inj_tee_psi",
                    "inj_lox_psi",
                    "inj_lng_psi",
                    "loadcell_lbf",
                ]
            )

    def _avg(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    def run_forever(self) -> None:
        for connector in (
            self.rx_gse,
            self.rx_ecu,
            self.rx_extr,
            self.rx_load,
            self.tx_tank,
            self.tx_line,
            self.tx_load,
            self.tx_fft,
        ):
            connector.open()

        while True:
            timestamp_s = time.time()
            gse_row = self.rx_gse.batch[0] if self.rx_gse.has_batch else None
            ecu_row = self.rx_ecu.batch[0] if self.rx_ecu.has_batch else None
            load_row = self.rx_load.batch[0] if self.rx_load.has_batch else None
            extr_row = self.rx_extr.batch[0] if self.rx_extr.has_batch else None

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

            self._fft_history.append(copv)
            if len(self._fft_history) > _FFT_WINDOW:
                self._fft_history = self._fft_history[-_FFT_WINDOW:]
            if len(self._fft_history) >= 8:
                centered = np.asarray(self._fft_history, dtype=np.float64)
                centered = centered - np.mean(centered)
                spectrum = np.fft.rfft(centered)
                fft_mag = float(np.max(np.abs(spectrum[1:]))) if len(spectrum) > 1 else 0.0
            else:
                fft_mag = 0.0
            if not math.isfinite(fft_mag):
                fft_mag = 0.0
            self.tx_fft.send_numpy(np.array([[timestamp_s, fft_mag]], dtype=np.float64))

            with self._storage_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        timestamp_s,
                        copv,
                        lox,
                        lng,
                        vent,
                        lox_mvas,
                        lox_inj_tee,
                        inj_lox,
                        inj_lng,
                        force,
                    ]
                )

            time.sleep(_SLEEP_S)


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