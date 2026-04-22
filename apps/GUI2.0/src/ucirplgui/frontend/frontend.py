from __future__ import annotations

import logging
import threading
import time

import numpy as np
from ucirplgui import config
from ucirplgui.components.dashboard import DeviceLinkStore, build_dashboard
from ucirplgui.device_link_read import read_device_link_snapshots


_DEVLINK_CONNECTION_STREAMS = {
    "gse": "gse_connection",
    "ecu": "ecu_connection",
    "extr_ecu": "extr_ecu_connection",
    "loadcell": "load_cell_connection",
}


def _devlink_rx_latency_ms(board: str, boards: dict, now_s: float) -> float:
    """Milliseconds of RX freshness age from device-link snapshots."""
    snap = boards.get(board)
    if snap is None or not snap.connected or snap.last_rx_epoch_s is None:
        return 250.0
    return max(0.0, (now_s - snap.last_rx_epoch_s) * 1000.0)
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec
from viviian.frontend import Frontend, GlfwBackend


LOGGER = logging.getLogger("ucirplgui.frontend")


def _stream_spec(stream_id: str) -> StreamSpec:
    return StreamSpec(
        stream_id=stream_id,
        schema=config.SCHEMAS[stream_id],
        shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
    )


class ScalarSeriesReader:
    def __init__(self) -> None:
        self.shape = (2, 1)
        self.dtype = np.float64
        self._frame: np.ndarray | None = None

    def prime(self, timestamp_s: float, value: float) -> None:
        self._frame = np.array([[timestamp_s], [value]], dtype=np.float64)

    def read(self) -> np.ndarray | None:
        if self._frame is None:
            return None
        frame = self._frame
        self._frame = None
        return frame


def _prime_reader(
    readers: Mapping[str, ScalarSeriesReader],
    stream_name: str,
    timestamp_s: float,
    value: float,
) -> None:
    reader = readers.get(stream_name)
    if reader is not None:
        reader.prime(timestamp_s, value)


class CommandBridgeWriter:
    def __init__(self, *, gse_sender: SendConnector, ecu_sender: SendConnector) -> None:
        # Snapshot order is authored in UCIRPLDashboard._control_vector().
        self.shape = (16,)
        self.dtype = np.float64
        self._gse_sender = gse_sender
        self._ecu_sender = ecu_sender

    def write(self, snapshot: np.ndarray) -> bool:
        snapshot = np.asarray(snapshot, dtype=np.float64)
        gse_row = np.array(
            [[
                self._v(snapshot, 0),
                self._v(snapshot, 1),
                self._v(snapshot, 2),
                self._v(snapshot, 3),
                self._v(snapshot, 4),
                self._v(snapshot, 5),
                self._v(snapshot, 6),
                self._v(snapshot, 7),
                self._v(snapshot, 8),
                self._v(snapshot, 9),
                self._v(snapshot, 10),
                self._v(snapshot, 11),
            ]],
            dtype=np.float64,
        )
        ecu_row = np.array(
            [[
                self._v(snapshot, 12),
                self._v(snapshot, 13),
                self._v(snapshot, 14),
                self._v(snapshot, 15),
            ]],
            dtype=np.float64,
        )
        self._gse_sender.send_numpy(gse_row)
        self._ecu_sender.send_numpy(ecu_row)
        return True

    def _v(self, snapshot: np.ndarray, index: int) -> float:
        if index >= len(snapshot):
            return 0.0
        return float(snapshot[index])

def run_frontend() -> None:
    link_store = DeviceLinkStore()
    tx_cmd_gse = SendConnector(_stream_spec(config.CMD_GSE_STREAM_ID), config.CONNECTOR_PORTS["cmd_gse"], host=config.DEFAULT_CONNECTOR_HOST)
    tx_cmd_ecu = SendConnector(_stream_spec(config.CMD_ECU_STREAM_ID), config.CONNECTOR_PORTS["cmd_ecu"], host=config.DEFAULT_CONNECTOR_HOST)
    command_writer = CommandBridgeWriter(
        gse_sender=tx_cmd_gse,
        ecu_sender=tx_cmd_ecu,
    )
    frontend = Frontend("ucirpl_frontend")
    frontend.add(build_dashboard(command_writer=command_writer, link_store=link_store))
    frontend.compile()

    rx_tank = ReceiveConnector(_stream_spec(config.FRONTEND_TANK_PRESSURES_STREAM_ID), config.CONNECTOR_PORTS["frontend_tank_pressures"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_line = ReceiveConnector(_stream_spec(config.FRONTEND_LINE_PRESSURES_STREAM_ID), config.CONNECTOR_PORTS["frontend_line_pressures"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_load = ReceiveConnector(_stream_spec(config.FRONTEND_LOADCELL_STREAM_ID), config.CONNECTOR_PORTS["frontend_loadcell"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_fft = ReceiveConnector(_stream_spec(config.FRONTEND_FFT_STREAM_ID), config.CONNECTOR_PORTS["frontend_fft"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_scalars = ReceiveConnector(_stream_spec(config.FRONTEND_GSE_ECU_SCALARS_STREAM_ID), config.CONNECTOR_PORTS["frontend_gse_ecu_scalars"], host=config.DEFAULT_CONNECTOR_HOST)

    for connector in (rx_tank, rx_line, rx_load, rx_fft, rx_scalars, tx_cmd_gse, tx_cmd_ecu):
        connector.open()

    readers = {name: ScalarSeriesReader() for name in frontend.required_reads}

    def feed_loop() -> None:
        while True:
            boards = read_device_link_snapshots()
            link_store.update(boards)
            now = time.time()
            for board, stream_name in _DEVLINK_CONNECTION_STREAMS.items():
                _prime_reader(readers, stream_name, now, _devlink_rx_latency_ms(board, boards, now))
            if rx_tank.has_batch:
                row = rx_tank.batch[0]
                _prime_reader(readers, "tank_copv", float(row[0]), float(row[1]))
                _prime_reader(readers, "tank_lox", float(row[0]), float(row[2]))
                _prime_reader(readers, "tank_lng", float(row[0]), float(row[3]))
            if rx_line.has_batch:
                row = rx_line.batch[0]
                _prime_reader(readers, "line_vent", float(row[0]), float(row[1]))
                _prime_reader(readers, "line_lox_mvas", float(row[0]), float(row[2]))
                _prime_reader(readers, "line_lox_inj_tee", float(row[0]), float(row[3]))
                _prime_reader(readers, "line_inj_lox", float(row[0]), float(row[4]))
                _prime_reader(readers, "line_inj_lng", float(row[0]), float(row[5]))
            if rx_load.has_batch:
                row = rx_load.batch[0]
                _prime_reader(readers, "load_force", float(row[0]), float(row[1]))
            if rx_fft.has_batch:
                row = rx_fft.batch[0]
                _prime_reader(readers, "fft_mag", float(row[0]), float(row[1]))
            if rx_scalars.has_batch:
                row = rx_scalars.batch[0]
                _prime_reader(readers, "eng_tc_1", float(row[0]), float(row[1]))
                _prime_reader(readers, "eng_tc_2", float(row[0]), float(row[2]))
                _prime_reader(readers, "gn2_chamber_proxy", float(row[0]), float(row[3]))
                _prime_reader(readers, "copv_tc", float(row[0]), float(row[4]))
            time.sleep(config.FRONTEND_FEED_SLEEP_S)

    threading.Thread(target=feed_loop, daemon=True).start()
    task = frontend.build_task(
        backend=GlfwBackend(width=1600, height=980, theme_name=config.THEME_NAME),
        window_title=config.WINDOW_TITLE,
        fill_backend_window=True,
    )
    task(**readers)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info("Starting frontend runtime")
    run_frontend()


if __name__ == "__main__":
    main()
