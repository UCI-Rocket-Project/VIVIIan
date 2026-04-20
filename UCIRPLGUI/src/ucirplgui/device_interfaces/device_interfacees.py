from __future__ import annotations

import argparse
import binascii
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from ucirplgui import config
from viviian.connector_utils import ReceiveConnector, SendConnector, StreamSpec


LOGGER = logging.getLogger("ucirplgui.device_interfaces")

GSE_TELEMETRY_FORMAT = "<L???????????????fffffffffffffffff"
ECU_TELEMETRY_FORMAT = "<Lff????fffffffffffffffffffffffffffffff"
LOADCELL_TELEMETRY_FORMAT = "<Ll"
GSE_COMMAND_FORMAT = "<????????????"
ECU_COMMAND_FORMAT = "<????"

GSE_PACKET_LEN = 91
ECU_PACKET_LEN = 144
LOADCELL_PACKET_LEN = 8


def _pressure_from_voltage(channel_name: str, voltage: float) -> float:
    scaling, intercept = {
        "pressureGn2": (190.0, 11.9),
        "pressureVent": (190.0, 11.9),
        "pressureLox": (190.0, 11.9),
        "pressureLng": (190.0, 11.9),
        "pressureCopv": (964.0, 37.2),
        "pressureInjectorLox": (190.0, 11.9),
        "pressureInjectorLng": (190.0, 11.9),
        "pressureLoxInjTee": (190.0, 11.9),
        "pressureLoxMvas": (190.0, 11.9),
        "pressureOne": (190.0, 11.9),
        "pressureTwo": (190.0, 11.9),
        "pressureThree": (190.0, 11.9),
        "pressureFour": (190.0, 11.9),
        "pressureFive": (190.0, 11.9),
    }[channel_name]
    return max(0.0, (float(voltage) * scaling) + intercept)


def _build_send_connector(stream_id: str, port: int) -> SendConnector:
    return SendConnector(
        StreamSpec(
            stream_id=stream_id,
            schema=config.SCHEMAS[stream_id],
            shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
        ),
        port=port,
        host=config.DEFAULT_CONNECTOR_HOST,
    )


def _build_receive_connector(stream_id: str, port: int) -> ReceiveConnector:
    return ReceiveConnector(
        StreamSpec(
            stream_id=stream_id,
            schema=config.SCHEMAS[stream_id],
            shape=(config.ROWS_PER_FRAME, len(config.SCHEMAS[stream_id])),
        ),
        port=port,
        host=config.DEFAULT_CONNECTOR_HOST,
    )


def _read_exact(sock: socket.socket, total_bytes: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = total_bytes
    while remaining > 0:
        try:
            chunk = sock.recv(remaining)
        except socket.timeout:
            return None
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass(slots=True)
class BaseBoardInterface:
    board_name: str
    simulator_port: int
    telemetry_len: int
    send_connector: SendConnector
    command_connector: ReceiveConnector | None = None

    def run_forever(self) -> None:
        self.send_connector.open()
        if self.command_connector is not None:
            self.command_connector.open()
        while True:
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection(
                    (config.SIMULATOR_HOST, self.simulator_port),
                    timeout=2.0,
                )
                sock.settimeout(0.2)
                LOGGER.info("%s connected to simulator on %d", self.board_name, self.simulator_port)
                self._run_socket_loop(sock)
            except Exception as exc:
                LOGGER.warning("%s disconnected: %s", self.board_name, exc)
                time.sleep(0.5)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _run_socket_loop(self, sock: socket.socket) -> None:
        while True:
            self._maybe_send_command(sock)
            packet = _read_exact(sock, self.telemetry_len)
            if packet is None:
                return
            decoded = self._decode_packet(packet)
            if decoded is None:
                continue
            self.send_connector.send_numpy(decoded)

    def _maybe_send_command(self, sock: socket.socket) -> None:
        del sock

    def _decode_packet(self, packet: bytes) -> np.ndarray | None:
        raise NotImplementedError


class GSEDeviceInterface(BaseBoardInterface):
    def __init__(self) -> None:
        super().__init__(
            board_name="gse",
            simulator_port=config.SIMULATOR_GSE_PORT,
            telemetry_len=GSE_PACKET_LEN,
            send_connector=_build_send_connector(
                config.RAW_GSE_STREAM_ID,
                config.CONNECTOR_PORTS["raw_gse"],
            ),
            command_connector=_build_receive_connector(
                config.CMD_GSE_STREAM_ID,
                config.CONNECTOR_PORTS["cmd_gse"],
            ),
        )
        self._last_sent: tuple[int, ...] | None = None

    def _maybe_send_command(self, sock: socket.socket) -> None:
        if self.command_connector is None or not self.command_connector.has_batch:
            return
        values = tuple(int(round(v)) for v in self.command_connector.batch[0])
        if self._last_sent == values:
            return
        payload = struct.pack(GSE_COMMAND_FORMAT, *[bool(v) for v in values])
        packet = payload + struct.pack("<L", binascii.crc32(payload))
        sock.sendall(packet)
        self._last_sent = values

    def _decode_packet(self, packet: bytes) -> np.ndarray | None:
        if len(packet) != GSE_PACKET_LEN:
            return None
        payload, crc = packet[:-4], packet[-4:]
        if struct.unpack("<L", crc)[0] != binascii.crc32(payload):
            return None
        raw = struct.unpack(GSE_TELEMETRY_FORMAT, payload)
        row = np.array(
            [
                float(raw[0]),
                _pressure_from_voltage("pressureGn2", raw[29]),
                _pressure_from_voltage("pressureLoxInjTee", raw[30]),
                _pressure_from_voltage("pressureVent", raw[31]),
                _pressure_from_voltage("pressureLoxMvas", raw[32]),
                float(raw[27]),
                float(raw[28]),
                float(raw[1]),
                float(raw[2]),
                float(raw[6]),
                float(raw[7]),
                float(raw[8]),
                float(raw[9]),
                float(raw[10]),
                float(raw[11]),
                float(raw[12]),
                float(raw[13]),
                float(raw[14]),
                float(raw[15]),
            ],
            dtype=np.float64,
        )
        return row.reshape(1, -1)


class ECUDeviceInterface(BaseBoardInterface):
    def __init__(self) -> None:
        super().__init__(
            board_name="ecu",
            simulator_port=config.SIMULATOR_ECU_PORT,
            telemetry_len=ECU_PACKET_LEN,
            send_connector=_build_send_connector(
                config.RAW_ECU_STREAM_ID,
                config.CONNECTOR_PORTS["raw_ecu"],
            ),
            command_connector=_build_receive_connector(
                config.CMD_ECU_STREAM_ID,
                config.CONNECTOR_PORTS["cmd_ecu"],
            ),
        )
        self._last_sent: tuple[int, ...] | None = None

    def _maybe_send_command(self, sock: socket.socket) -> None:
        if self.command_connector is None or not self.command_connector.has_batch:
            return
        values = tuple(int(round(v)) for v in self.command_connector.batch[0])
        if self._last_sent == values:
            return
        payload = struct.pack(ECU_COMMAND_FORMAT, *[bool(v) for v in values])
        packet = payload + struct.pack("<L", binascii.crc32(payload))
        sock.sendall(packet)
        self._last_sent = values

    def _decode_packet(self, packet: bytes) -> np.ndarray | None:
        if len(packet) != ECU_PACKET_LEN:
            return None
        payload, crc = packet[:-4], packet[-4:]
        if struct.unpack("<L", crc)[0] != binascii.crc32(payload):
            return None
        raw = struct.unpack(ECU_TELEMETRY_FORMAT, payload)
        row = np.array(
            [
                float(raw[0]),
                _pressure_from_voltage("pressureCopv", raw[14]),
                _pressure_from_voltage("pressureLox", raw[15]),
                _pressure_from_voltage("pressureLng", raw[16]),
                _pressure_from_voltage("pressureInjectorLox", raw[17]),
                _pressure_from_voltage("pressureInjectorLng", raw[18]),
                float(raw[13]),
                float(raw[28]),
                float(raw[1]),
                float(raw[2]),
                float(raw[3]),
                float(raw[4]),
                float(raw[5]),
                float(raw[6]),
            ],
            dtype=np.float64,
        )
        return row.reshape(1, -1)


class EXTRECUDeviceInterface(BaseBoardInterface):
    def __init__(self) -> None:
        super().__init__(
            board_name="extr_ecu",
            simulator_port=config.SIMULATOR_EXTR_ECU_PORT,
            telemetry_len=ECU_PACKET_LEN,
            send_connector=_build_send_connector(
                config.RAW_EXTR_ECU_STREAM_ID,
                config.CONNECTOR_PORTS["raw_extr_ecu"],
            ),
            command_connector=None,
        )

    def _decode_packet(self, packet: bytes) -> np.ndarray | None:
        if len(packet) != ECU_PACKET_LEN:
            return None
        payload, crc = packet[:-4], packet[-4:]
        if struct.unpack("<L", crc)[0] != binascii.crc32(payload):
            return None
        raw = struct.unpack(ECU_TELEMETRY_FORMAT, payload)
        row = np.array(
            [
                float(raw[0]),
                _pressure_from_voltage("pressureOne", raw[14]),
                _pressure_from_voltage("pressureTwo", raw[15]),
                _pressure_from_voltage("pressureThree", raw[16]),
                _pressure_from_voltage("pressureFour", raw[17]),
                _pressure_from_voltage("pressureFive", raw[18]),
                float(raw[1]),
                float(raw[2]),
            ],
            dtype=np.float64,
        )
        return row.reshape(1, -1)


class LoadCellDeviceInterface(BaseBoardInterface):
    def __init__(self) -> None:
        super().__init__(
            board_name="loadcell",
            simulator_port=config.SIMULATOR_LOADCELL_PORT,
            telemetry_len=LOADCELL_PACKET_LEN,
            send_connector=_build_send_connector(
                config.RAW_LOADCELL_STREAM_ID,
                config.CONNECTOR_PORTS["raw_loadcell"],
            ),
            command_connector=None,
        )

    def _decode_packet(self, packet: bytes) -> np.ndarray | None:
        if len(packet) != LOADCELL_PACKET_LEN:
            return None
        raw = struct.unpack(LOADCELL_TELEMETRY_FORMAT, packet)
        row = np.array([float(raw[0]), float(raw[1])], dtype=np.float64)
        return row.reshape(1, -1)


INTERFACE_BUILDERS: dict[str, Callable[[], BaseBoardInterface]] = {
    "gse": GSEDeviceInterface,
    "ecu": ECUDeviceInterface,
    "extr_ecu": EXTRECUDeviceInterface,
    "loadcell": LoadCellDeviceInterface,
}


def run_device_interface(board: str) -> None:
    builder = INTERFACE_BUILDERS[board]
    builder().run_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UCIRPLGUI board device interface process")
    parser.add_argument(
        "--board",
        required=True,
        choices=tuple(INTERFACE_BUILDERS.keys()),
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    run_device_interface(args.board)


if __name__ == "__main__":
    main()
