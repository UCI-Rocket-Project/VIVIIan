
from __future__ import annotations

import binascii
import os
import socket
import struct
import threading
import time
from typing import Final, List, Optional, Sequence, Tuple

import numpy as np

from generic_connector import CommandServer, generic_tcp_to_flight_connector  # noqa: E402

GSE_DATA_LENGTH: Final[int] = 91  # payload + CRC
GSE_RECV_FORMAT: Final[str] = "<L???????????????fffffffffffffffff"
GSE_CMD_BODY_FORMAT: Final[str] = "<????????????"  # 12 x bool


def gse_recv_unpack(raw_91: bytes) -> Tuple[Sequence, int]:
    if len(raw_91) != GSE_DATA_LENGTH:
        raise ValueError(f"GSE frame must be {GSE_DATA_LENGTH} bytes, got {len(raw_91)}")
    payload, crc_le = raw_91[:-4], raw_91[-4:]
    crc_calc = binascii.crc32(payload) & 0xFFFFFFFF
    crc_wire = struct.unpack("<L", crc_le)[0]
    if crc_calc != crc_wire:
        raise ValueError("GSE CRC mismatch")
    return struct.unpack(GSE_RECV_FORMAT, payload), crc_wire

def gse_cmd_pack(
    igniter0: bool,
    igniter1: bool,
    alarm: bool,
    sol_gn2_fill: bool,
    sol_gn2_vent: bool,
    sol_gn2_disconnect: bool,
    sol_mvas_fill: bool,
    sol_mvas_vent: bool,
    sol_mvas_open: bool,
    sol_mvas_close: bool,
    sol_lox_vent: bool,
    sol_lng_vent: bool,
) -> bytes:
    body = struct.pack(
        GSE_CMD_BODY_FORMAT,
        igniter0,
        igniter1,
        alarm,
        sol_gn2_fill,
        sol_gn2_vent,
        sol_gn2_disconnect,
        sol_mvas_fill,
        sol_mvas_vent,
        sol_mvas_open,
        sol_mvas_close,
        sol_lox_vent,
        sol_lng_vent,
    )
    return body + struct.pack("<L", binascii.crc32(body) & 0xFFFFFFFF)

GSE_FIELD_NAMES: Final[List[str]] = [
    "packet_time",
    "igniterArmed",
    "igniterCurrent0",
    "igniterCurrent1",
    "igniterInternalState0",
    "igniterInternalState1",
    "alarmInternalState",
    "solenoidInternalStateGn2Fill",
    "solenoidInternalStateGn2Vent",
    "solenoidInternalStateGn2Disconnect",
    "solenoidInternalStateMvasFill",
    "solenoidInternalStateMvasVent",
    "solenoidInternalStateMvasOpen",
    "solenoidInternalStateMvasClose",
    "solenoidInternalStateLoxVent",
    "solenoidInternalStateLngVent",
    "supplyVoltage0",
    "supplyVoltage1",
    "solenoidCurrentGn2Fill",
    "solenoidCurrentGn2Vent",
    "solenoidCurrentGn2Disconnect",
    "solenoidCurrentMvasFill",
    "solenoidCurrentMvasVent",
    "solenoidCurrentMvasOpen",
    "solenoidCurrentMvasClose",
    "solenoidCurrentLoxVent",
    "solenoidCurrentLngVent",
    "temperatureEngine1",
    "temperatureEngine2",
    "pressureGn2",
    "pressureLoxInjTee",
    "pressureVent",
    "pressureLoxMvas",
]




GSE_CMD_FIELD_NAMES = [
    "igniter0",
    "igniter1",
    "alarm",
    "sol_gn2_fill",
    "sol_gn2_vent",
    "sol_gn2_disconnect",
    "sol_mvas_fill",
    "sol_mvas_vent",
    "sol_mvas_open",
    "sol_mvas_close",
    "sol_lox_vent",
    "sol_lng_vent",
]
NUM_CMD_SIGNALS = len(GSE_CMD_FIELD_NAMES)
CMD_ROWS_PER_FRAME = 1

NUM_SIGNALS: Final[int] = len(GSE_FIELD_NAMES)
ROWS_PER_FRAME: Final[int] = 1000  # must match backend.py GSE_ROWS_PER_FRAME


# ---------------------------------------------------------------------------
# TCP helpers (unchanged)
# ---------------------------------------------------------------------------

def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    """Read exactly *nbytes* from a blocking stream socket."""
    parts: list[bytes] = []
    remaining = nbytes
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(
                f"connection closed after {nbytes - remaining} bytes, expected {nbytes}"
            )
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def send_gse_command(
    sock: socket.socket,
    igniter0: bool,
    igniter1: bool,
    alarm: bool,
    sol_gn2_fill: bool,
    sol_gn2_vent: bool,
    sol_gn2_disconnect: bool,
    sol_mvas_fill: bool,
    sol_mvas_vent: bool,
    sol_mvas_open: bool,
    sol_mvas_close: bool,
    sol_lox_vent: bool,
    sol_lng_vent: bool,
) -> None:
    payload = gse_cmd_pack(
        igniter0, igniter1, alarm,
        sol_gn2_fill, sol_gn2_vent, sol_gn2_disconnect,
        sol_mvas_fill, sol_mvas_vent, sol_mvas_open, sol_mvas_close,
        sol_lox_vent, sol_lng_vent,
    )
    sock.sendall(payload)

def recv_gse_telemetry(sock: socket.socket, *, timeout: Optional[float] = None) -> Tuple[Sequence, int]:
    prev = sock.gettimeout()
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        raw = recv_exact(sock, GSE_DATA_LENGTH)
    finally:
        sock.settimeout(prev)
    return gse_recv_unpack(raw)


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

def _gse_telemetry_tuple_to_signal_row(fields: Sequence) -> np.ndarray:
    return np.asarray(fields, dtype=np.float64)


# ---------------------------------------------------------------------------
# Combined task: TCP telemetry reader + Flight command receiver thread
# --------------------------------------------------------------------------


def array_to_command_converter(array: np.ndarray) -> bytes:
    bools = [bool(array[0, j]) for j in range(NUM_CMD_SIGNALS)]
    return gse_cmd_pack(*bools)

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    host = os.environ.get("GSE_IP", "127.0.0.1")
    port = int(os.environ.get("GSE_PORT", "10001"))
    backend_flight = os.environ.get("GUI21_FLIGHT", "grpc://localhost:8815")
    cmd_bind = os.environ.get("GSE_CMD_FLIGHT_BIND", "grpc://0.0.0.0:8825")

    sock = socket.create_connection((host, port))
    command_server = CommandServer(cmd_bind, sock, array_to_command_converter)

    data_thread = threading.Thread(
        target=generic_tcp_to_flight_connector,
        args=(ROWS_PER_FRAME, sock, GSE_DATA_LENGTH, GSE_RECV_FORMAT, list(GSE_FIELD_NAMES), backend_flight),
        daemon=True,
    )
    command_thread = threading.Thread(target=command_server.serve, daemon=True)
    data_thread.start()
    command_thread.start()

    try:
        while data_thread.is_alive() or command_thread.is_alive():
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nGSE connector — shutting down")
    finally:
        try:
            command_server.shutdown()
        except Exception:
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


if __name__ == "__main__":
    print(f"GSE connector — telemetry Flight to backend, command Flight receiver")
    main()
