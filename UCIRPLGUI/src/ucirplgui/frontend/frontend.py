from __future__ import annotations

import logging
import threading
import time

import numpy as np
from ucirplgui import config
from ucirplgui.components.dashboard import build_dashboard
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec
from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import MomentaryButton, ToggleButton


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


class CommandBridgeWriter:
    def __init__(
        self,
        *,
        slot_component_ids: tuple[str, ...],
        gse_sender: SendConnector,
        ecu_sender: SendConnector,
    ) -> None:
        self.shape = (len(slot_component_ids),)
        self.dtype = np.float64
        self._slot_index = {name: idx for idx, name in enumerate(slot_component_ids)}
        self._gse_sender = gse_sender
        self._ecu_sender = ecu_sender

    def write(self, snapshot: np.ndarray) -> bool:
        snapshot = np.asarray(snapshot, dtype=np.float64)
        gse_row = np.array(
            [[
                self._v(snapshot, "igniter_0"),
                self._v(snapshot, "igniter_1"),
                self._v(snapshot, "alarm"),
                self._v(snapshot, "gn2_fill"),
                self._v(snapshot, "gn2_vent"),
                self._v(snapshot, "gn2_disconnect"),
                self._v(snapshot, "mvas_fill"),
                self._v(snapshot, "mvas_vent"),
                self._v(snapshot, "mvas_open"),
                self._v(snapshot, "mvas_close"),
                self._v(snapshot, "lox_vent"),
                self._v(snapshot, "lng_vent"),
            ]],
            dtype=np.float64,
        )
        ecu_row = np.array(
            [[
                self._v(snapshot, "copv_vent"),
                self._v(snapshot, "pv1"),
                self._v(snapshot, "pv2"),
                self._v(snapshot, "vent"),
            ]],
            dtype=np.float64,
        )
        self._gse_sender.send_numpy(gse_row)
        self._ecu_sender.send_numpy(ecu_row)
        return True

    def _v(self, snapshot: np.ndarray, component_id: str) -> float:
        return float(snapshot[self._slot_index[component_id]])


def _build_control_components(frontend: Frontend) -> None:
    frontend.add(ToggleButton("igniter_0", "Igniter 1", "cmd.gse.igniter_0", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("igniter_1", "Igniter 2", "cmd.gse.igniter_1", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("alarm", "Alarm", "cmd.gse.alarm", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("gn2_fill", "GN2 Fill", "cmd.gse.gn2_fill", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("gn2_vent", "GN2 Vent", "cmd.gse.gn2_vent", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("gn2_disconnect", "GN2 Disconnect", "cmd.gse.gn2_disconnect", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("mvas_fill", "MVAS Fill", "cmd.gse.mvas_fill", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("mvas_vent", "MVAS Vent", "cmd.gse.mvas_vent", False, theme_name=config.THEME_NAME))
    frontend.add(MomentaryButton("mvas_open", "MVAS Open", "cmd.gse.mvas_open", 1.0, theme_name=config.THEME_NAME))
    frontend.add(MomentaryButton("mvas_close", "MVAS Close", "cmd.gse.mvas_close", 1.0, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("lox_vent", "LOX Vent", "cmd.gse.lox_vent", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("lng_vent", "LNG Vent", "cmd.gse.lng_vent", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("copv_vent", "COPV Vent", "cmd.ecu.copv_vent", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("pv1", "PV1", "cmd.ecu.pv1", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("pv2", "PV2", "cmd.ecu.pv2", False, theme_name=config.THEME_NAME))
    frontend.add(ToggleButton("vent", "Vent", "cmd.ecu.vent", False, theme_name=config.THEME_NAME))


def run_frontend() -> None:
    frontend = Frontend("ucirpl_frontend")
    frontend.add(build_dashboard())
    _build_control_components(frontend)
    frontend.compile()

    rx_tank = ReceiveConnector(_stream_spec(config.FRONTEND_TANK_PRESSURES_STREAM_ID), config.CONNECTOR_PORTS["frontend_tank_pressures"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_line = ReceiveConnector(_stream_spec(config.FRONTEND_LINE_PRESSURES_STREAM_ID), config.CONNECTOR_PORTS["frontend_line_pressures"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_load = ReceiveConnector(_stream_spec(config.FRONTEND_LOADCELL_STREAM_ID), config.CONNECTOR_PORTS["frontend_loadcell"], host=config.DEFAULT_CONNECTOR_HOST)
    rx_fft = ReceiveConnector(_stream_spec(config.FRONTEND_FFT_STREAM_ID), config.CONNECTOR_PORTS["frontend_fft"], host=config.DEFAULT_CONNECTOR_HOST)
    tx_cmd_gse = SendConnector(_stream_spec(config.CMD_GSE_STREAM_ID), config.CONNECTOR_PORTS["cmd_gse"], host=config.DEFAULT_CONNECTOR_HOST)
    tx_cmd_ecu = SendConnector(_stream_spec(config.CMD_ECU_STREAM_ID), config.CONNECTOR_PORTS["cmd_ecu"], host=config.DEFAULT_CONNECTOR_HOST)

    for connector in (rx_tank, rx_line, rx_load, rx_fft, tx_cmd_gse, tx_cmd_ecu):
        connector.open()

    readers = {name: ScalarSeriesReader() for name in frontend.required_reads}
    writer = CommandBridgeWriter(
        slot_component_ids=tuple(slot.component_id for slot in frontend.output_slots),
        gse_sender=tx_cmd_gse,
        ecu_sender=tx_cmd_ecu,
    )

    def feed_loop() -> None:
        while True:
            if rx_tank.has_batch:
                row = rx_tank.batch[0]
                readers["tank_copv"].prime(float(row[0]), float(row[1]))
                readers["tank_lox"].prime(float(row[0]), float(row[2]))
                readers["tank_lng"].prime(float(row[0]), float(row[3]))
            if rx_line.has_batch:
                row = rx_line.batch[0]
                readers["line_vent"].prime(float(row[0]), float(row[1]))
                readers["line_lox_mvas"].prime(float(row[0]), float(row[2]))
                readers["line_lox_inj_tee"].prime(float(row[0]), float(row[3]))
                readers["line_inj_lox"].prime(float(row[0]), float(row[4]))
                readers["line_inj_lng"].prime(float(row[0]), float(row[5]))
            if rx_load.has_batch:
                row = rx_load.batch[0]
                readers["load_force"].prime(float(row[0]), float(row[1]))
            if rx_fft.has_batch:
                row = rx_fft.batch[0]
                readers["fft_mag"].prime(float(row[0]), float(row[1]))
            time.sleep(0.02)

    threading.Thread(target=feed_loop, daemon=True).start()
    task = frontend.build_task(
        backend=GlfwBackend(width=1600, height=980, theme_name=config.THEME_NAME),
        window_title=config.WINDOW_TITLE,
        fill_backend_window=True,
    )
    task(output=writer, **readers)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info("Starting frontend runtime")
    run_frontend()


if __name__ == "__main__":
    main()