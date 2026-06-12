import binascii
import socket
import struct
import threading
import time
from collections.abc import Sequence

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

from generic_connector import CommandServer


# --- Constants for LOADCELL Connector ---
#LOADCELL_IP = "127.0.0.1"
LOADCELL_IP = "10.0.0.217"
LOADCELL_PORT = 10001
LOADCELL_RECONNECT_S = 0.5

# --- Constants for LOADCELL Backend ---
LOADCELL_FLIGHT_BIND = "grpc://127.0.0.1:8815"
LOADCELL_ROWS_PER_FRAME = 1

# --- Constants for LOADCELL Frontend ---
LOADCELL_ECHO_FLIGHT = "grpc://127.0.0.1:8820"
LOADCELL_CMD_FLIGHT_BIND = "grpc://0.0.0.0:8827"


# --- PACKET DEFINITIONS ---
LOADCELL_HEADER_ALIGN_BYTES = b"\xef\xbe\xad\xde"

LOADCELL_DATA_FORMAT = "<I I 18? 14f 4I"
LOADCELL_DATA_SIZE = struct.calcsize(LOADCELL_DATA_FORMAT)

LOADCELL_COMMAND_FORMAT = "<I 15? I"
LOADCELL_COMMAND_MAGIC = 0xDEADD00D
LOADCELL_COMMAND_SIZE = struct.calcsize(LOADCELL_COMMAND_FORMAT)


_TCP_SESSION_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionResetError,
    OSError,
    TimeoutError,
    socket.timeout,
)


LOADCELL_STATE_FIELD_NAME_MAP = {
    "igniterArmed": "igniterArmed",
    "igniter0Continuity": "igniter0Continuity",
    "igniter1Continuity": "igniter1Continuity",
    "solenoidCurrent0": "solenoidCurrent0",
    "solenoidCurrent1": "solenoidCurrent1",
    "solenoidCurrent2": "solenoidCurrent2",
    "solenoidCurrent3": "solenoidCurrent3",
    "solenoidCurrent4": "solenoidCurrent4",
    "solenoidCurrent5": "solenoidCurrent5",
    "solenoidCurrent6": "solenoidCurrent6",
    "solenoidCurrent7": "solenoidCurrent7",
    "solenoidCurrent8": "solenoidCurrent8",
    "solenoidCurrent9": "solenoidCurrent9",
    "solenoidCurrent10": "solenoidCurrent10",
    "solenoidCurrent11": "solenoidCurrent11",
}

LOADCELL_STATE_FIELDS = tuple(LOADCELL_STATE_FIELD_NAME_MAP.keys())
LOADCELL_STATE_FIELD_NAMES = tuple(LOADCELL_STATE_FIELD_NAME_MAP.values())


LOADCELL_FIELD_NAME_MAP = {
    "magicHeader": "magicHeader",
    "timestamp": "timestamp",
    "igniterArmed": "igniterArmed",
    "igniter0Continuity": "igniter0Continuity",
    "igniter1Continuity": "igniter1Continuity",
    "igniterInternalState0": "igniterInternalState0",
    "igniterInternalState1": "igniterInternalState1",
    "alarmInternalState": "alarmInternalState",
    "solenoidInternalState0": "solenoidInternalState0",
    "solenoidInternalState1": "solenoidInternalState1",
    "solenoidInternalState2": "solenoidInternalState2",
    "solenoidInternalState3": "solenoidInternalState3",
    "solenoidInternalState4": "solenoidInternalState4",
    "solenoidInternalState5": "solenoidInternalState5",
    "solenoidInternalState6": "solenoidInternalState6",
    "solenoidInternalState7": "solenoidInternalState7",
    "solenoidInternalState8": "solenoidInternalState8",
    "solenoidInternalState9": "solenoidInternalState9",
    "solenoidInternalState10": "solenoidInternalState10",
    "solenoidInternalState11": "solenoidInternalState11",
    "supplyVoltage0": "supplyVoltage0",
    "supplyVoltage1": "supplyVoltage1",
    "solenoidCurrent0": "solenoidCurrent0",
    "solenoidCurrent1": "solenoidCurrent1",
    "solenoidCurrent2": "solenoidCurrent2",
    "solenoidCurrent3": "solenoidCurrent3",
    "solenoidCurrent4": "solenoidCurrent4",
    "solenoidCurrent5": "solenoidCurrent5",
    "solenoidCurrent6": "solenoidCurrent6",
    "solenoidCurrent7": "solenoidCurrent7",
    "solenoidCurrent8": "solenoidCurrent8",
    "solenoidCurrent9": "solenoidCurrent9",
    "solenoidCurrent10": "solenoidCurrent10",
    "solenoidCurrent11": "solenoidCurrent11",
    "temperature0": "temperature0",
    "temperature1": "temperature1",
    "temperature2": "temperature2",
    "crc": "crc",
}

LOADCELL_FIELDS = tuple(LOADCELL_FIELD_NAME_MAP.keys())
LOADCELL_FIELD_NAMES = tuple(LOADCELL_FIELD_NAME_MAP.values())
LOADCELL_NUM_SIGNALS = len(LOADCELL_FIELD_NAMES)
LOADCELL_FIELD_INDEX = {field: i for i, field in enumerate(LOADCELL_FIELDS)}

LOADCELL_COMMAND_ECHO_FIELDS = (
    "igniterInternalState0",
    "igniterInternalState1",
    "alarmInternalState",
    "solenoidInternalState0",
    "solenoidInternalState1",
    "solenoidInternalState2",
    "solenoidInternalState3",
    "solenoidInternalState4",
    "solenoidInternalState5",
    "solenoidInternalState6",
    "solenoidInternalState7",
    "solenoidInternalState8",
    "solenoidInternalState9",
    "solenoidInternalState10",
    "solenoidInternalState11",
)

LOADCELL_COMMAND_ECHO_FIELD_INDICES = tuple(
    LOADCELL_FIELD_INDEX[field] for field in LOADCELL_COMMAND_ECHO_FIELDS
)

LOADCELL_ECHO_FIELD_NAMES = ("connected", *LOADCELL_COMMAND_FIELD_NAMES)
LOADCELL_NUM_ECHO_SIGNALS = len(LOADCELL_ECHO_FIELD_NAMES)


if len(LOADCELL_COMMAND_ECHO_FIELD_INDICES) != LOADCELL_NUM_COMMAND_SIGNALS:
    raise ValueError("LOADCELL command echo field count must match command field count")


def decode_LOADCELL_data(raw_bytes: bytes) -> tuple | None:
    try:
        return struct.unpack(LOADCELL_DATA_FORMAT, raw_bytes)
    except struct.error as e:
        print(f"Error decoding LOADCELL data: {e}")
        return None


def _single_row_batch(row: Sequence[float] | np.ndarray, schema: pa.Schema) -> pa.RecordBatch:
    arrays = [pa.array([value], type=pa.float64()) for value in row]
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


class _ReconnectFlightWriter:
    def __init__(
        self,
        *,
        name: str,
        address: str,
        path: str,
        schema: pa.Schema,
        reconnect_s: float = 1.0,
    ) -> None:
        self._name = name
        self._address = address
        self._descriptor = flight.FlightDescriptor.for_path(path)
        self._schema = schema
        self._reconnect_s = reconnect_s
        self._next_connect_at = 0.0
        self._client = None
        self._writer = None

    def close(self) -> None:
        writer = self._writer
        client = self._client
        self._writer = None
        self._client = None

        if writer is None:
            return

        try:
            writer.close()  # send end-of-stream before releasing the channel
        except Exception:
            pass

        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _mark_disconnected(self) -> None:
        self.close()
        self._next_connect_at = time.time() + self._reconnect_s

    def _connect_if_needed(self) -> bool:
        if self._writer is not None:
            return True

        now = time.time()
        if now < self._next_connect_at:
            return False

        try:
            self._client = flight.connect(self._address)
            self._writer, _ = self._client.do_put(self._descriptor, self._schema)
            print(f"{self._name}: connected to {self._address}")
            return True

        except Exception as e:
            print(f"{self._name}: connect failed ({e}); retrying in {self._reconnect_s:.1f}s")
            self._mark_disconnected()
            return False

    def write_batch(self, batch: pa.RecordBatch) -> bool:
        if not self._connect_if_needed():
            return False

        assert self._writer is not None
        try:
            self._writer.write_batch(batch)
            return True

        except Exception as e:
            print(f"{self._name}: write failed ({e}); retrying in {self._reconnect_s:.1f}s")
            self._mark_disconnected()
            return False

    def write_row(self, row: Sequence[float] | np.ndarray) -> bool:
        batch = _single_row_batch(row, self._schema)
        return self.write_batch(batch)


class _TcpSocketProxy:
    """Thread-safe holder for the current board TCP socket used by commands."""

    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    def set_socket(self, sock: socket.socket) -> None:
        with self._lock:
            self._socket = sock

    def clear_socket(self, sock: socket.socket | None = None) -> None:
        with self._lock:
            if sock is None or self._socket is sock:
                self._socket = None

    def sendall(self, data: bytes) -> None:
        with self._lock:
            sock = self._socket

        if sock is None:
            raise ConnectionError("LOADCELL TCP command socket is not connected")

        try:
            sock.sendall(data)
        except _TCP_SESSION_ERRORS:
            self.clear_socket(sock)
            raise


def read_LOADCELL_packets(sock: socket.socket):
    """
    Read raw TCP bytes, align to magic header, and yield decoded packets.

    If the TCP socket dies, this raises an exception.
    The outer reconnect loop owns reconnecting.
    """

    running_buffer = b""

    while True:
        chunk = sock.recv(256)

        if not chunk:
            raise ConnectionError("LOADCELL TCP socket closed by board")

        running_buffer += chunk

        while len(running_buffer) >= LOADCELL_DATA_SIZE:
            magic_idx = running_buffer.find(LOADCELL_HEADER_ALIGN_BYTES)

            if magic_idx == -1:
                running_buffer = running_buffer[-3:]
                break

            if len(running_buffer) - magic_idx < LOADCELL_DATA_SIZE:
                running_buffer = running_buffer[magic_idx:]
                break

            packet_bytes = running_buffer[magic_idx : magic_idx + LOADCELL_DATA_SIZE]
            running_buffer = running_buffer[magic_idx + LOADCELL_DATA_SIZE :]

            decoded = decode_LOADCELL_data(packet_bytes)
            if decoded is not None:
                yield decoded


def make_telemetry_writer() -> _ReconnectFlightWriter:
    telemetry_schema = pa.schema([(name, pa.float64()) for name in LOADCELL_FIELD_NAMES])

    return _ReconnectFlightWriter(
        name="LOADCELL telemetry Flight writer",
        address=LOADCELL_FLIGHT_BIND,
        path="LOADCELL_telemetry",
        schema=telemetry_schema,
    )


def make_echo_writer() -> _ReconnectFlightWriter:
    echo_schema = pa.schema([(name, pa.float64()) for name in LOADCELL_ECHO_FIELD_NAMES])

    return _ReconnectFlightWriter(
        name="LOADCELL echo Flight writer",
        address=LOADCELL_ECHO_FLIGHT,
        path="LOADCELL_echo_state",
        schema=echo_schema,
    )


def make_echo_row(telemetry_row: Sequence[float] | np.ndarray, connected: bool) -> list[float]:
    echo_row = [1.0 if connected else 0.0]

    echo_row.extend(
        float(telemetry_row[index])
        for index in LOADCELL_COMMAND_ECHO_FIELD_INDICES
    )

    return echo_row


def run_LOADCELL_telemetry_session(sock: socket.socket) -> None:
    """
    Runs one TCP session.

    When the socket dies, this function exits by raising.
    main() catches that, closes the socket, waits briefly, and reconnects.
    """

    telemetry_writer = make_telemetry_writer()
    echo_writer = make_echo_writer()

    last_echo_row = [0.0] * LOADCELL_NUM_ECHO_SIGNALS
    _prev_state_key: tuple | None = None

    try:
        for decoded_packet in read_LOADCELL_packets(sock):
            telemetry_row = np.asarray(decoded_packet, dtype=np.float64)

            state_key = tuple(bool(telemetry_row[i] > 0.5) for i in LOADCELL_COMMAND_ECHO_FIELD_INDICES)
            if state_key != _prev_state_key:
                state_str = ", ".join(
                    f"{name}={v}" for name, v in zip(LOADCELL_COMMAND_ECHO_FIELDS, state_key)
                )
                print(f"[LOADCELL STATE] {state_str}")
                _prev_state_key = state_key

            telemetry_writer.write_row(telemetry_row)

            last_echo_row = make_echo_row(telemetry_row, connected=True)
            echo_writer.write_row(last_echo_row)

    finally:
        echo_writer.write_row([0.0] * LOADCELL_NUM_ECHO_SIGNALS)

        telemetry_writer.close()
        echo_writer.close()


def LOADCELL_cmd_pack(
    igniter0_fire: bool,
    igniter1_fire: bool,
    alarm: bool,
    solenoid_states: list[bool],
) -> bytes:
    payload = struct.pack(
        "<I 15?",
        LOADCELL_COMMAND_MAGIC,
        igniter0_fire,
        igniter1_fire,
        alarm,
        *solenoid_states,
    )

    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack("<I", crc)


def LOADCELL_cmd_pack_from_row(row: np.ndarray) -> bytes:
    n = LOADCELL_NUM_COMMAND_SIGNALS

    if row.shape[0] < n:
        raise ValueError(f"Expected at least {n} command fields, got row length {row.shape[0]}")

    bools = [bool(row[j]) for j in range(n)]

    return LOADCELL_cmd_pack(
        bools[0],
        bools[1],
        bools[2],
        bools[3:],
    )


def LOADCELL_make_command_server(
    location: str,
    tcp_connection: socket.socket | _TcpSocketProxy,
    command_server: type[CommandServer] = CommandServer,
) -> CommandServer:
    def _converter(data: np.ndarray) -> bytes | None:
        if data.size == 0:
            return None

        row = data[-1]
        cmd_str = ", ".join(
            f"{name}={bool(row[i] > 0.5)}" for i, name in enumerate(LOADCELL_COMMAND_FIELD_NAMES)
        )
        print(f"[LOADCELL CMD] {cmd_str}")
        return LOADCELL_cmd_pack_from_row(row)

    return command_server(location, tcp_connection, _converter)  # type: ignore[arg-type]


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


def _shutdown_command_server(command_server: CommandServer, command_thread: threading.Thread) -> None:
    try:
        command_server.shutdown(deadline=time.time() + 1.0)
    except Exception:
        pass
    command_thread.join(timeout=1.0)


def main() -> None:
    command_socket = _TcpSocketProxy()

    command_server = LOADCELL_make_command_server(
        LOADCELL_CMD_FLIGHT_BIND,
        command_socket,
    )

    command_thread = threading.Thread(
        target=command_server.serve,
        daemon=True,
    )

    command_thread.start()

    try:
        while True:
            sock: socket.socket | None = None

            try:
                print(f"LOADCELL connector: connecting to {LOADCELL_IP}:{LOADCELL_PORT}")

                sock = socket.create_connection(
                    (LOADCELL_IP, LOADCELL_PORT),
                    timeout=5.0,
                )

                sock.settimeout(0.4)
                command_socket.set_socket(sock)

                print("LOADCELL connector: TCP connected")

                run_LOADCELL_telemetry_session(sock)

            except KeyboardInterrupt:
                raise

            except _TCP_SESSION_ERRORS as e:
                print(f"LOADCELL connector: TCP session ended: {e}")

            except Exception as e:
                print(f"LOADCELL connector: unexpected session error: {e}")

            finally:
                command_socket.clear_socket(sock)
                _close_socket(sock)

            print(f"LOADCELL connector: reconnecting in {LOADCELL_RECONNECT_S:.1f}s")
            time.sleep(LOADCELL_RECONNECT_S)

    except KeyboardInterrupt:
        print("\nLOADCELL connector: shutting down")

    finally:
        command_socket.clear_socket()
        _shutdown_command_server(command_server, command_thread)


if __name__ == "__main__":
    print("LOADCELL connector — telemetry to backend, commands from frontend to TCP")
    main()
