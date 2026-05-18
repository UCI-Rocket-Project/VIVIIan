
from __future__ import annotations

import binascii
import os
import socket
import struct
import threading
import time
from typing import Any, Callable, Final, List, Optional, Sequence, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight


_TCP_SESSION_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionResetError,
    OSError,
)

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

SOLENOID_CURRENT_ECHO_NAMES: Final[List[str]] = [
    "currentGn2Fill",
    "currentGn2Vent",
    "currentGn2Disconnect",
    "currentMvasFill",
    "currentMvasVent",
    "currentMvasOpen",
    "currentMvasClose",
    "currentLoxVent",
    "currentLngVent",
]
NUM_SOLENOID_CURRENTS: Final[int] = len(SOLENOID_CURRENT_ECHO_NAMES)

_SOLENOID_CURRENT_TELEMETRY_INDICES: Final[tuple[int, ...]] = tuple(
    GSE_FIELD_NAMES.index(name)
    for name in [
        "solenoidCurrentGn2Fill",
        "solenoidCurrentGn2Vent",
        "solenoidCurrentGn2Disconnect",
        "solenoidCurrentMvasFill",
        "solenoidCurrentMvasVent",
        "solenoidCurrentMvasOpen",
        "solenoidCurrentMvasClose",
        "solenoidCurrentLoxVent",
        "solenoidCurrentLngVent",
    ]
)

_FIRST_SOL_CMD_INDEX: Final[int] = GSE_CMD_FIELD_NAMES.index("sol_gn2_fill")
CMD_TO_CURRENT_SLOT: Final[dict[int, int]] = {
    _FIRST_SOL_CMD_INDEX + i: i for i in range(NUM_SOLENOID_CURRENTS)
}
CURRENT_ECHO_OFFSET: Final[int] = NUM_CMD_SIGNALS

# Command-echo contract: 12 command bools + 9 solenoid currents + connected flag.
ECHO_FIELD_NAMES: Final[List[str]] = (
    list(GSE_CMD_FIELD_NAMES) + list(SOLENOID_CURRENT_ECHO_NAMES) + ["connected"]
)
NUM_ECHO_SIGNALS: Final[int] = len(ECHO_FIELD_NAMES)
ECHO_ROWS_PER_FRAME: Final[int] = 1
ECHO_FLIGHT_DESCRIPTOR_PATH: Final[str] = "gse_command_echo"
CMD_FLIGHT_DESCRIPTOR_PATH: Final[str] = "gse_commands"
DEFAULT_ECHO_INTERVAL_S: Final[float] = 0.1
DEFAULT_CMD_FLIGHT: Final[str] = "grpc://127.0.0.1:8825"

GN2_FILL_CMD_INDEX: Final[int] = GSE_CMD_FIELD_NAMES.index("sol_gn2_fill")
MVAS_OPEN_CMD_INDEX: Final[int] = GSE_CMD_FIELD_NAMES.index("sol_mvas_open")
CONNECTED_ECHO_INDEX: Final[int] = NUM_CMD_SIGNALS + NUM_SOLENOID_CURRENTS

# Slots in the frontend ``ui_state`` vector (writable control order).
UI_SLOT_GN2_FILL: Final[int] = 0
UI_SLOT_MVAS_OPEN: Final[int] = 1

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
    """Thread-safe shared state holding the latest 12 commands, 9 solenoid
    currents, and a connected flag, pushed to the frontend via Flight echo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commands: List[bool] = [False] * NUM_CMD_SIGNALS
        self._currents: List[float] = [0.0] * NUM_SOLENOID_CURRENTS
        self._connected: bool = False

    def update_commands(self, commands: Sequence[bool]) -> None:
        if len(commands) != NUM_CMD_SIGNALS:
            raise ValueError(
                f"expected {NUM_CMD_SIGNALS} command bools, got {len(commands)}"
            )
        with self._lock:
            self._commands = [bool(c) for c in commands]

    def update_currents(self, currents: Sequence[float]) -> None:
        with self._lock:
            self._currents = [float(c) for c in currents[:NUM_SOLENOID_CURRENTS]]

    def set_connected(self, value: bool) -> None:
        with self._lock:
            self._connected = bool(value)

    def sync_from_telemetry(self, telemetry_fields: Sequence) -> None:
        """Seed commands and currents from a device telemetry frame so the
        echo reflects the device's actual state on reconnect."""
        with self._lock:
            self._commands = [
                bool(telemetry_fields[i + 1]) for i in range(NUM_CMD_SIGNALS)
            ]
            self._currents = [
                float(telemetry_fields[idx])
                for idx in _SOLENOID_CURRENT_TELEMETRY_INDICES
            ]

    def snapshot_row(self) -> np.ndarray:
        with self._lock:
            row = (
                [1.0 if c else 0.0 for c in self._commands]
                + list(self._currents)
                + [1.0 if self._connected else 0.0]
            )
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


class GseCommandServer(flight.FlightServerBase):
    """Flight command receiver bound once for the process lifetime.

  ``set_tcp_socket`` swaps the live GSE TCP connection across reconnects so we
    do not restart ``serve()`` (which would block on port reuse / shutdown).
    """

    def __init__(
        self,
        location: str,
        echo_state: EchoState,
        converter: Callable[[np.ndarray], bytes],
    ) -> None:
        super().__init__(location)
        self._echo_state = echo_state
        self._converter = converter
        self._lock = threading.Lock()
        self._tcp_connection: socket.socket | None = None

    def set_tcp_socket(self, sock: socket.socket | None) -> None:
        with self._lock:
            self._tcp_connection = sock

    def do_put(self, context, descriptor, reader, writer):  # type: ignore[override]
        try:
            for chunk in reader:
                record_batch = chunk.data
                arrays = [
                    record_batch.column(i).to_numpy(zero_copy_only=False)
                    for i in range(record_batch.num_columns)
                ]
                data = np.column_stack(arrays).astype(np.float64, copy=False)
                flight_data = self._converter(data)
                if flight_data is None:
                    continue
                with self._lock:
                    sock = self._tcp_connection
                if sock is None:
                    raise ConnectionError("GSE TCP socket not connected")
                try:
                    sock.sendall(flight_data)
                except _TCP_SESSION_ERRORS as exc:
                    self._echo_state.set_connected(False)
                    raise ConnectionError(f"GSE command send failed: {exc}") from exc
        except Exception:
            self._echo_state.set_connected(False)
            raise


def gse_telemetry_to_flight_connector(
    rows_per_frame: int,
    tcp_connection: socket.socket,
    nbytes: int,
    struct_format: str,
    field_names: list[str],
    flight_address: str,
    echo_state: EchoState,
) -> None:
    """Read GSE telemetry over TCP and forward to Flight.

    Returns when the TCP link is lost (e.g. fake GSE stopped) so ``main()`` can
    reconnect instead of spinning on a dead socket.
    """
    writer: Any = None
    try:
        schema = pa.schema([(name, pa.float64()) for name in field_names])
        client = flight.connect(flight_address)
        descriptor = flight.FlightDescriptor.for_path("high_speed_test")
        writer, _ = client.do_put(descriptor, schema)
        written_bytes = 0
        start_time = time.time()
        while True:
            data_batch = np.empty((rows_per_frame, len(field_names)), dtype=np.float64)
            for i in range(rows_per_frame):
                parts: list[bytes] = []
                remaining = nbytes
                while remaining > 0:
                    chunk = tcp_connection.recv(remaining)
                    if not chunk:
                        raise ConnectionError("GSE telemetry socket closed")
                    parts.append(chunk)
                    remaining -= len(chunk)
                recv_bytes = b"".join(parts)
                unpacked_data = struct.unpack(struct_format, recv_bytes[:-4])
                data_batch[i] = unpacked_data
            echo_state.update_currents([
                float(data_batch[-1, idx])
                for idx in _SOLENOID_CURRENT_TELEMETRY_INDICES
            ])
            arrays = [
                pa.array(data_batch[:, col], type=pa.float64())
                for col in range(len(field_names))
            ]
            batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
            writer.write_batch(batch)
            written_bytes += int(batch.nbytes)
            if time.time() - start_time > 1.0:
                print(
                    f"GSE telemetry Flight: {written_bytes / 1_000_000:.2f} MB/s (approx)"
                )
                start_time = time.time()
                written_bytes = 0
    except _TCP_SESSION_ERRORS as exc:
        print(f"GSE telemetry link lost: {exc}")
    except Exception as exc:
        print(f"GSE telemetry unexpected error: {exc}")
    finally:
        echo_state.set_connected(False)
        # Intentionally do not call writer.close() here; it can block when the
        # backend Flight peer is gone and would stall data_thread.join().


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
# UI state → GSE command Flight client (used by the frontend pipeline task)
# ---------------------------------------------------------------------------


def ui_snapshot_to_command_row(
    snapshot: np.ndarray,
    *,
    baseline: Sequence[bool] | None = None,
) -> np.ndarray:
    """Map the frontend ``ui_state`` vector to a (1, NUM_CMD_SIGNALS) row."""
    base = (
        [bool(b) for b in baseline]
        if baseline is not None
        else [False] * NUM_CMD_SIGNALS
    )
    if len(base) != NUM_CMD_SIGNALS:
        base = [False] * NUM_CMD_SIGNALS
    row = [1.0 if b else 0.0 for b in base]
    flat = np.asarray(snapshot, dtype=np.float64).reshape(-1)
    if flat.size > UI_SLOT_GN2_FILL:
        row[GN2_FILL_CMD_INDEX] = 1.0 if flat[UI_SLOT_GN2_FILL] > 0.5 else 0.0
    if flat.size > UI_SLOT_MVAS_OPEN:
        row[MVAS_OPEN_CMD_INDEX] = 1.0 if flat[UI_SLOT_MVAS_OPEN] > 0.5 else 0.0
    return np.asarray([row], dtype=np.float64)


class GseCommandFlightClient:
    """Persistent Flight ``do_put`` client to the connector CommandServer."""

    def __init__(self, flight_address: str = DEFAULT_CMD_FLIGHT) -> None:
        self._flight_address = flight_address
        self._schema = pa.schema([(name, pa.float64()) for name in GSE_CMD_FIELD_NAMES])
        self._descriptor = flight.FlightDescriptor.for_path(CMD_FLIGHT_DESCRIPTOR_PATH)
        self._client: Any = None
        self._writer: Any = None

    def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._client = None

    def send_row(self, row: np.ndarray) -> None:
        batch_row = np.asarray(row, dtype=np.float64).reshape(1, NUM_CMD_SIGNALS)
        if batch_row.shape != (1, NUM_CMD_SIGNALS):
            raise ValueError(
                f"expected command row shape (1, {NUM_CMD_SIGNALS}), got {batch_row.shape}"
            )
        if self._writer is None:
            self._client = flight.connect(self._flight_address)
            self._writer, _ = self._client.do_put(self._descriptor, self._schema)
        arrays = [
            pa.array(batch_row[:, i], type=pa.float64()) for i in range(NUM_CMD_SIGNALS)
        ]
        batch = pa.RecordBatch.from_arrays(arrays, schema=self._schema)
        self._writer.write_batch(batch)

    def send_row_safe(self, row: np.ndarray) -> bool:
        try:
            self.send_row(row)
            return True
        except Exception as e:
            print(f"GSE command Flight client error: {e}")
            self.close()
            return False


def forward_ui_state_to_gse_commands(
    *,
    ui_state: Any,
    command_echo: Any,
    cmd_flight: str = DEFAULT_CMD_FLIGHT,
    poll_sleep_s: float = 0.02,
) -> None:
    """Pipeline task: read ``frontend_ui_state``, send edge-triggered commands
    to ``gse_connector`` via Flight when the echo stream reports connected."""

    flight_client = GseCommandFlightClient(cmd_flight)
    last_snapshot: np.ndarray | None = None

    try:
        while True:
            latest_ui: np.ndarray | None = None
            while True:
                frame = ui_state.read()
                if frame is None:
                    break
                latest_ui = np.asarray(frame, dtype=np.float64).reshape(-1)

            connected = False
            baseline: list[bool] | None = None
            echo_row: np.ndarray | None = None
            while True:
                frame = command_echo.read()
                if frame is None:
                    break
                echo_row = np.asarray(frame, dtype=np.float64).reshape(-1)
            if echo_row is not None and echo_row.size > CONNECTED_ECHO_INDEX:
                connected = bool(echo_row[CONNECTED_ECHO_INDEX] > 0.5)
                baseline = [
                    bool(echo_row[i] > 0.5) for i in range(NUM_CMD_SIGNALS)
                ]

            if latest_ui is None:
                time.sleep(poll_sleep_s)
                continue
            if not connected:
                last_snapshot = None
                time.sleep(poll_sleep_s)
                continue
            if last_snapshot is not None and np.array_equal(latest_ui, last_snapshot):
                time.sleep(poll_sleep_s)
                continue

            cmd_row = ui_snapshot_to_command_row(latest_ui, baseline=baseline)
            if flight_client.send_row_safe(cmd_row):
                last_snapshot = latest_ui.copy()
            time.sleep(poll_sleep_s)
    finally:
        flight_client.close()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def main() -> None:
    host = os.environ.get("GSE_IP", "127.0.0.1")
    port = int(os.environ.get("GSE_PORT", "10001"))
    backend_flight = os.environ.get("GUI21_FLIGHT", "grpc://localhost:8815")
    cmd_bind = os.environ.get("GSE_CMD_FLIGHT_BIND", "grpc://0.0.0.0:8825")
    echo_flight = os.environ.get("GSE_CMD_ECHO_FLIGHT", "grpc://localhost:8820")
    reconnect_s = float(os.environ.get("GSE_RECONNECT_S", "1.0"))

    # Echo Flight client runs for the process lifetime (survives TCP reconnects).
    echo_state = EchoState()
    echo_thread = threading.Thread(
        target=echo_to_flight_connector,
        args=(echo_state, echo_flight),
        daemon=True,
    )
    echo_thread.start()

    converter = make_array_to_command_converter(echo_state)
    command_server = GseCommandServer(cmd_bind, echo_state, converter)
    command_thread = threading.Thread(target=command_server.serve, daemon=True)
    command_thread.start()

    try:
        while True:
            sock: socket.socket | None = None
            data_thread: threading.Thread | None = None
            try:
                print(f"GSE connector: connecting to {host}:{port}")
                while True:
                    try:
                        sock = socket.create_connection((host, port), timeout=5.0)
                        sock.settimeout(None)
                        break
                    except (ConnectionError, OSError, socket.timeout) as exc:
                        echo_state.set_connected(False)
                        command_server.set_tcp_socket(None)
                        print(f"GSE TCP connect failed ({exc}); retrying in 1s")
                        time.sleep(1.0)

                command_server.set_tcp_socket(sock)

                first_fields, _ = recv_gse_telemetry(sock, timeout=5.0)
                echo_state.sync_from_telemetry(first_fields)
                echo_state.set_connected(True)
                print("GSE connector: TCP connected (synced from device state)")

                data_thread = threading.Thread(
                    target=gse_telemetry_to_flight_connector,
                    args=(
                        ROWS_PER_FRAME,
                        sock,
                        GSE_DATA_LENGTH,
                        GSE_RECV_FORMAT,
                        list(GSE_FIELD_NAMES),
                        backend_flight,
                    ),
                    kwargs={"echo_state": echo_state},
                    daemon=True,
                )
                data_thread.start()
                data_thread.join()
            except KeyboardInterrupt:
                raise
            finally:
                echo_state.set_connected(False)
                command_server.set_tcp_socket(None)
                _close_socket(sock)
                if data_thread is not None and data_thread.is_alive():
                    data_thread.join(timeout=1.0)

            print(f"GSE connector: disconnected, reconnecting in {reconnect_s:.0f}s...")
            time.sleep(reconnect_s)
    except KeyboardInterrupt:
        print("\nGSE connector — shutting down")
    finally:
        try:
            command_server.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    print(f"GSE connector — telemetry Flight to backend, command Flight receiver")
    main()
