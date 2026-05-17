
from __future__ import annotations

import binascii
import os
import socket
import struct
import threading
import time
from typing import Callable, Final, List, Optional, Sequence, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

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

# Command-echo contract: the 12 commands plus a 0/1 connectivity flag the
# frontend uses to disable controls and sync button state on reconnect.
ECHO_FIELD_NAMES: Final[List[str]] = list(GSE_CMD_FIELD_NAMES) + ["connected"]
NUM_ECHO_SIGNALS: Final[int] = len(ECHO_FIELD_NAMES)
ECHO_ROWS_PER_FRAME: Final[int] = 1
ECHO_FLIGHT_DESCRIPTOR_PATH: Final[str] = "gse_command_echo"
DEFAULT_ECHO_INTERVAL_S: Final[float] = 0.1

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
# Command echo: forward latest commands + TCP connectivity to the frontend
# ---------------------------------------------------------------------------


class EchoState:
    """Thread-safe shared state holding the latest 12 commands and a connected
    flag, used by the echo Flight client to push status to the frontend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commands: List[bool] = [False] * NUM_CMD_SIGNALS
        self._connected: bool = False

    def update_commands(self, commands: Sequence[bool]) -> None:
        if len(commands) != NUM_CMD_SIGNALS:
            raise ValueError(
                f"expected {NUM_CMD_SIGNALS} command bools, got {len(commands)}"
            )
        with self._lock:
            self._commands = [bool(c) for c in commands]

    def set_connected(self, value: bool) -> None:
        with self._lock:
            self._connected = bool(value)

    def snapshot_row(self) -> np.ndarray:
        with self._lock:
            row = [1.0 if c else 0.0 for c in self._commands] + [
                1.0 if self._connected else 0.0
            ]
        return np.asarray([row], dtype=np.float64)


def make_array_to_command_converter(
    echo_state: EchoState,
) -> Callable[[np.ndarray], bytes]:
    """Wrap the bool→bytes converter so commands flow into the echo state."""

    def converter(array: np.ndarray) -> bytes:
        bools = [bool(array[0, j]) for j in range(NUM_CMD_SIGNALS)]
        echo_state.update_commands(bools)
        return gse_cmd_pack(*bools)

    return converter


class GseCommandServer(CommandServer):
    """CommandServer wrapper that flips ``echo_state.connected`` to False
    whenever a Flight do_put fails (which happens when the TCP sendall to the
    GSE board raises)."""

    def __init__(
        self,
        location: str,
        tcp_connection: socket.socket,
        converter: Callable[[np.ndarray], bytes],
        echo_state: EchoState,
    ) -> None:
        super().__init__(location, tcp_connection, converter)
        self._echo_state = echo_state

    def do_put(self, context, descriptor, reader, writer):  # type: ignore[override]
        try:
            return super().do_put(context, descriptor, reader, writer)
        except Exception:
            self._echo_state.set_connected(False)
            raise


def echo_to_flight_connector(
    echo_state: EchoState,
    flight_address: str,
    *,
    interval_s: float = DEFAULT_ECHO_INTERVAL_S,
) -> None:
    """Open a Flight client to the frontend's echo server and push (1, 13)
    rows at ``interval_s`` cadence.  Reconnects on any failure so the loop is
    resilient to the frontend coming up late or restarting."""

    schema = pa.schema([(name, pa.float64()) for name in ECHO_FIELD_NAMES])
    descriptor = flight.FlightDescriptor.for_path(ECHO_FLIGHT_DESCRIPTOR_PATH)
    while True:
        try:
            client = flight.connect(flight_address)
            writer, _ = client.do_put(descriptor, schema)
            try:
                while True:
                    row = echo_state.snapshot_row()  # shape (1, NUM_ECHO_SIGNALS)
                    arrays = [
                        pa.array(row[:, i], type=pa.float64())
                        for i in range(len(ECHO_FIELD_NAMES))
                    ]
                    batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                    writer.write_batch(batch)
                    time.sleep(interval_s)
            finally:
                try:
                    writer.close()
                except Exception:
                    pass
        except Exception as e:
            print(f"GSE echo connector error: {e}")
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    host = os.environ.get("GSE_IP", "127.0.0.1")
    port = int(os.environ.get("GSE_PORT", "10001"))
    backend_flight = os.environ.get("GUI21_FLIGHT", "grpc://localhost:8815")
    cmd_bind = os.environ.get("GSE_CMD_FLIGHT_BIND", "grpc://0.0.0.0:8825")
    echo_flight = os.environ.get("GSE_CMD_ECHO_FLIGHT", "grpc://localhost:8820")

    # Start the echo Flight client before touching TCP so the frontend gets a
    # connected=0 heartbeat even while we are still trying to dial the board.
    echo_state = EchoState()
    echo_thread = threading.Thread(
        target=echo_to_flight_connector,
        args=(echo_state, echo_flight),
        daemon=True,
    )
    echo_thread.start()

    print(f"GSE connector: connecting to {host}:{port}")
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=5.0)
            sock.settimeout(None)
            break
        except (ConnectionError, OSError, socket.timeout) as e:
            echo_state.set_connected(False)
            print(f"GSE TCP connect failed ({e}); retrying in 1s")
            time.sleep(1.0)
    echo_state.set_connected(True)
    print(f"GSE connector: TCP connected")

    converter = make_array_to_command_converter(echo_state)
    command_server = GseCommandServer(cmd_bind, sock, converter, echo_state)

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
        echo_state.set_connected(False)
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
