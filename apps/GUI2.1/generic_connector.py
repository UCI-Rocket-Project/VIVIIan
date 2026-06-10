
#generic connector 
import pyarrow.flight as flight
import socket
import struct
import threading
import time
from typing import Callable

import numpy as np
import pyarrow as pa

from __future__ import annotations

import socket
import time
from typing import Any

from numpy._core.umath import NAN
import pyarrow as pa
import pyarrow.flight as flight

from gui_elements import BUTTON_STATUS_OFF_COLOR, BUTTON_STATUS_ON_COLOR, Button



class PrintServer(flight.FlightServerBase):
    """ Flight server that prints the data to the console and keeps a copy in the latest dictionary variable """
    def __init__(self, address: str, name: str, fields: tuple[str, ...]) -> None:
        super().__init__(address)
        self.name = name
        self.fields = fields
        self.latest: dict[str, float] | None = None

    def do_put(self, context, descriptor, reader, writer):
        for chunk in reader:
            batch = chunk.data
            data = np.column_stack(
                [
                    batch.column(i).to_numpy(zero_copy_only=False)
                    for i in range(batch.num_columns)
                ]
            )
            for row in data:
                self.latest = {
                    field: value
                    for field, value in zip(self.fields, row, strict=False)
                }
                print(self.name, self.latest)


class LatestServer(flight.FlightServerBase):
    """ Flight server that keeps a copy in the latest dictionary variable """
    def __init__(self, address: str, name: str, fields: tuple[str, ...]) -> None:
        super().__init__(address)
        self.name = name
        self.fields = fields
        self.latest: dict[str, float] | None = None
        self.latest_update_time: float | None = None
        self.latest_generation = 0

    def do_put(self, context, descriptor, reader, writer):
        for chunk in reader:
            batch = chunk.data
            data = np.column_stack(
                [
                    batch.column(i).to_numpy(zero_copy_only=False)
                    for i in range(batch.num_columns)
                ]
            )
            for row in data:
                self.latest = {
                    field: value
                    for field, value in zip(self.fields, row, strict=False)
                }
                self.latest_update_time = time.monotonic()
                self.latest_generation += 1

    def latest_age(self) -> float | None:
        if self.latest_update_time is None:
            return None
        return time.monotonic() - self.latest_update_time

    def is_fresh(self, timeout_seconds: float) -> bool:
        age = self.latest_age()
        return age is not None and age <= timeout_seconds

class StorageServer(flight.FlightServerBase):
    """Flight receiver: each do_put stream is turned into NumPy frames on the pythusa ring."""

    def __init__(
        self,
        location: str,
        stream_writer,
        *,
        rows_per_frame: int,
        num_signals: int,
    ) -> None:
        super().__init__(location)
        self._stream = stream_writer
        self._write_lock = threading.Lock()
        self._rows_per_frame = rows_per_frame
        self._num_signals = num_signals
        self._last_storage_time: float | None = None

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        start_time = time.time()
        received_bytes = 0
        for chunk in reader:
            record_batch = chunk.data
            arrays = [
                record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)
            ]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            if data.shape != (self._rows_per_frame, self._num_signals):
                raise ValueError(
                    f"Expected frame shape {(self._rows_per_frame, self._num_signals)}, got {data.shape}"
                )
            data = self._add_storage_timestamps(data)
            with self._write_lock:
                self._stream.write(data)
            received_bytes += int(data.nbytes)
            if time.time() - start_time > 1.0:
                print(f"Flight ingest: {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
                start_time = time.time()
                received_bytes = 0

    def _add_storage_timestamps(self, data: np.ndarray) -> np.ndarray:
        now = time.time()
        rows = data.shape[0]
        if self._last_storage_time is None or rows <= 1:
            timestamps = np.full(rows, now, dtype=np.float64)
        else:
            timestamps = np.linspace(self._last_storage_time, now, rows + 1, dtype=np.float64)[1:]
        self._last_storage_time = now
        return np.column_stack((data, timestamps))



class CommandServer (flight.FlightServerBase):
    """Flight server that takes in the flight data and then sends over tcp"""
    def __init__(
        self,
        location: str,
        tcp_connection: socket.socket,
        flight_tcp_data_converter: Callable[[np.ndarray], bytes],
    ) -> None:
        super().__init__(location)
        self._tcp_connection = tcp_connection
        self._flight_tcp_data_converter = flight_tcp_data_converter

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        for chunk in reader:
            record_batch = chunk.data
            arrays = [
                record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)
            ]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            flight_data = self._flight_tcp_data_converter(data)
            if flight_data is None:
                continue
            self._tcp_connection.sendall(flight_data)




def run_generic_receiver(
    *,
    grpc_bind: str,
    stream_writer,
    rows_per_frame: int,
    num_signals: int,
) -> None:
    """Start a StorageServer on *grpc_bind* and block on serve().

    *stream_writer* is anything with a ``.write(np.ndarray)`` method —
    a pythusa stream, a CommandBuffer, etc.
    """
    server = StorageServer(
        grpc_bind,
        stream_writer,
        rows_per_frame=rows_per_frame,
        num_signals=num_signals,
    )
    server.serve()






def generic_tcp_to_flight_connector(rows_per_frame: int, tcp_connection: socket.socket, nbytes: int, struct_format: str, field_names: list[str], flight_address: str) -> None:
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in field_names])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                try:
                    data_batch = np.empty((rows_per_frame, len(field_names)), dtype=np.float64)
                    for i in range(rows_per_frame):
                        parts: list[bytes] = []
                        remaining = nbytes
                        while remaining > 0:
                            chunk = tcp_connection.recv(remaining)
                            if not chunk:
                                raise ConnectionError("socket closed")
                            parts.append(chunk)
                            remaining -= len(chunk)
                        recv_bytes = b"".join(parts)
                        unpacked_data = struct.unpack(struct_format, recv_bytes[:-4]) #get rid of the crc check 
                        data_batch[i] = unpacked_data
                    arrays = [pa.array(data_batch[:, i], type=pa.float64()) for i in range(len(field_names))]
                    batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                    writer.write_batch(batch)
                    written_bytes += int(batch.nbytes)
                    if time.time() - start_time > 1.0:
                        print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                        start_time = time.time()
                        written_bytes = 0
                except Exception as e:
                    print(f"Error sending data: {e}")
                    time.sleep(0.01)
        except Exception as e:
            print(f"Error connecting to Flight: {e}")
            time.sleep(0.01)

def generic_flight_to_tcp_connector(flight_address: str, tcp_connection: socket.socket, nbytes: int, struct_format: str, field_names: list[str]) -> None:
    """Flight server: receives do_put batches and forwards each row as struct-packed bytes over TCP.

    Inverse of generic_tcp_to_flight_connector — binds at *flight_address*, waits for a
    do_put stream, unpacks each row from float64 back to the original wire format defined
    by *struct_format*, and sends the raw bytes to *tcp_connection*.
    """
    assert struct.calcsize(struct_format) == nbytes, (
        f"struct_format '{struct_format}' calcsize {struct.calcsize(struct_format)} != nbytes {nbytes}"
    )

    def _converter(data: np.ndarray) -> bytes:
        out = bytearray()
        for i in range(data.shape[0]):
            out += struct.pack(struct_format, *data[i])
        return bytes(out)

    server = CommandServer(flight_address, tcp_connection, _converter)
    server.serve()











def generic_stream_connector(*, stream, field_names: list[str], flight_address: str) -> None:
    """Generic connector for any flight address."""
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in field_names])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                frame = stream.read()
                if frame is None:
                    time.sleep(0.001)
                    continue
                arrays = [pa.array(frame[:, i], type=pa.float64()) for i in range(len(field_names))]
                batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                writer.write_batch(batch)
                written_bytes += int(frame.nbytes)
                if time.time() - start_time > 1.0:
                    print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                    start_time = time.time()
                    written_bytes = 0
        except Exception as e:
            print(f"Error sending data: {e}")
            time.sleep(0.01)








CMD_RETRY_SECONDS = 1.0
CMD_CONNECT_TIMEOUT_SECONDS = 0.02
ECHO_STALE_SECONDS = 2.0
ECHO_GRACE_SECONDS = 0.5
BUTTON_WIDTH = 260.0


class RocketPCBCommandClient:
    def __init__(
        self,
        *,
        pcb_name: str,
        cmd_field_names: tuple[str, ...],
        cmd_host: str,
        cmd_port: int,
        button_configs: dict[str, dict[str, Any]],
        table_button_configs: dict[str, dict[str, Any]] | None = None,
        latest_server: LatestServer,
        retry_seconds: float = CMD_RETRY_SECONDS,
        connect_timeout_seconds: float = CMD_CONNECT_TIMEOUT_SECONDS,
        echo_grace_seconds: float = ECHO_GRACE_SECONDS,
        echo_stale_seconds: float = ECHO_STALE_SECONDS,
        button_width: float = BUTTON_WIDTH,
    ) -> None:
        self.pcb_name = pcb_name
        self.cmd_field_names = tuple(cmd_field_names)
        self.cmd_host = cmd_host
        self.cmd_port = cmd_port
        self.button_configs = button_configs 
        self.table_button_configs = table_button_configs or {}
        self.latest_server = latest_server
        self.retry_seconds = retry_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.echo_grace_seconds = echo_grace_seconds
        self.echo_stale_seconds = echo_stale_seconds
        self.button_width = button_width
        self.buttons: dict[str, Button] = {}
        self.row = [0.0] * len(self.cmd_field_names)
        self.schema = pa.schema([(name, pa.float64()) for name in self.cmd_field_names])
        self.descriptor = flight.FlightDescriptor.for_path(f"{pcb_name}_commands")
        self.writer = None
        self._next_connect_attempt_at = 0.0
        self._last_echo_generation = -1
        self._last_echo_connected: bool | None = None
        self._ignore_echo_until = 0.0

    def command_field_index(self, command_field: str) -> int:
        return self.cmd_field_names.index(command_field)

    def button_state(self, button_id: str) -> bool:
        button = self.buttons.get(button_id)
        return bool(button.state) if isinstance(button, Button) else False

    def make_command_buttons(self) -> tuple[Button, ...]:
        for button_id, config in self.button_configs.items():
            button = Button(
                button_id,
                config["display_name"],
                width=self.button_width,
                toggle_on_click=config.get("momentary_seconds") is None,
                momentary_seconds=config.get("momentary_seconds"),
                status_color=BUTTON_STATUS_OFF_COLOR,
                enabled=self.make_enabled_rule(config),
                status_text=self.make_status_text_getter(config.get("status_value")),
                internal_status_value=self.make_internal_status_value_getter(
                    config.get("status_field")
                ),
            )
            self.buttons[button_id] = button

            def send(clicked_button: Button, clicked_id: str = button_id) -> None:
                self.handle_button_click(clicked_id, clicked_button)

            button.on_click = send

        return tuple(self.buttons[button_id] for button_id in self.button_configs)

    def make_enabled_rule(self, config: dict[str, Any]) -> Any:
        disabled_by = config.get("disabled_by")
        if disabled_by is not None:
            return lambda disabled_by=disabled_by: not self.button_state(disabled_by)
        return config.get("enabled", True)

    def handle_button_click(self, button_id: str, button: Button) -> None:
        self.sync_button_to_row(button_id)
        self.sync_button_status(button)

        if button_id in self.table_button_configs and button.state:
            self.apply_table_state(self.table_button_configs[button_id]["table_states"])

        self.send()

    def apply_table_state(self, table: dict[str, bool]) -> None:
        for button_id, config in self.button_configs.items():
            if button_id in self.table_button_configs or button_id == "alarm":
                continue
            button = self.buttons.get(button_id)
            if not isinstance(button, Button):
                continue
            button.set_state(table.get(button_id, False))
            if button_id in table:
                table_state = table[button_id]
                button.set_state(table_state)
                if button.momentary_seconds is not None:
                    button.momentary_until = (
                        time.monotonic() + button.momentary_seconds
                        if table_state
                        else None
                    )
            self.sync_button_status(button)
            self.sync_button_to_row(button_id)

    def sync_button_to_row(self, button_id: str) -> None:
        config = self.button_configs[button_id]
        command_fields = self._field_names(config.get("command_field"))
        if command_fields:
            self.sync_command_fields_to_row(button_id, command_fields)

    def sync_command_fields_to_row(
        self,
        button_id: str,
        command_fields: tuple[str, ...],
    ) -> None:
        button = self.buttons.get(button_id)
        if not isinstance(button, Button):
            return
        value = 1.0 if button.state else 0.0
        for command_field in command_fields:
            index = self.command_field_index(command_field)
            self.row[index] = value

    def sync_buttons_from_echo(self, echo_server: LatestServer) -> None:
        latest = echo_server.latest
        connected = (
            latest is not None
            and echo_server.is_fresh(self.echo_stale_seconds)
            and float(latest.get("connected", 0.0)) > 0.5
        )

        if (
            connected == self._last_echo_connected
            and echo_server.latest_generation == self._last_echo_generation
        ):
            return

        self._last_echo_connected = connected
        self._last_echo_generation = echo_server.latest_generation
        if connected:
            self._next_connect_attempt_at = 0.0

        for button_id, config in self.button_configs.items():
            button = self.buttons.get(button_id)
            if not isinstance(button, Button):
                continue

            button.set_enabled(self.make_enabled_rule(config) if connected else False)
            if button_id in self.table_button_configs:
                continue

            if connected and (
                button.momentary_until is not None
                or time.monotonic() < self._ignore_echo_until
            ):
                continue

            command_fields = self._field_names(config.get("command_field"))
            state = (
                all(float(latest.get(field, 0.0)) > 0.5 for field in command_fields)
                if connected and command_fields
                else False
            )
            button.set_state(state)
            button.momentary_until = None
            self.sync_button_status(button)
            self.sync_button_to_row(button_id)

    def make_status_text_getter(self, status_value_field: str | list[str] | None):
        def get_status_text_for_button():
            status_value_fields = self._field_names(status_value_field)
            if not status_value_fields:
                return "NAN"
            values = []
            for field in status_value_fields:
                try:
                    value = self.latest_server.latest[field]
                except KeyError:
                    return "NAN"
                except Exception:
                    return "NAN"
                if value is None:
                    return "NAN"
                values.append(value)
            if len(values) == 1:
                return f"{values[0]:.3f}"
            return "/".join(f"{value:.3f}" for value in values)

        return get_status_text_for_button

    def make_internal_status_value_getter(
        self,
        internal_status_field: str | list[str] | None,
    ):
        def get_internal_status_value_for_button():
            internal_status_fields = self._field_names(internal_status_field)
            if not internal_status_fields:
                return NAN
            values = []
            for field in internal_status_fields:
                try:
                    value = self.latest_server.latest[field]
                except KeyError:
                    return NAN
                except Exception:
                    return NAN
                if value is None:
                    return NAN
                values.append(value)
            if len(values) == 1:
                return values[0]
            return all(value > 0.0 for value in values)

        return get_internal_status_value_for_button

    @staticmethod
    def sync_button_status(button: Button) -> None:
        button.set_status_color(BUTTON_STATUS_ON_COLOR if button.state else BUTTON_STATUS_OFF_COLOR)

    @staticmethod
    def _field_names(field_name: str | list[str] | None) -> tuple[str, ...]:
        if field_name is None:
            return ()
        if isinstance(field_name, str):
            return (field_name,)
        return tuple(field_name)

    def send(self) -> bool:
        batch = pa.RecordBatch.from_arrays(
            [pa.array([value], type=pa.float64()) for value in self.row],
            schema=self.schema,
        )

        had_writer = self.writer is not None
        if self._write_batch(batch):
            self._ignore_echo_until = time.monotonic() + self.echo_grace_seconds
            return True

        if not had_writer:
            return False

        self.writer = None
        self._next_connect_attempt_at = 0.0
        if self._write_batch(batch):
            self._ignore_echo_until = time.monotonic() + self.echo_grace_seconds
            return True

        return False

    def _write_batch(self, batch: pa.RecordBatch) -> bool:
        now = time.monotonic()
        if self.writer is None:
            if now < self._next_connect_attempt_at:
                return False
            if not self._is_command_server_listening():
                print(f"[{self.pcb_name} CMD] connector unavailable on {self.cmd_host}:{self.cmd_port}")
                self._next_connect_attempt_at = now + self.retry_seconds
                return False
        try:
            if self.writer is None:
                client = flight.connect(f"grpc://{self.cmd_host}:{self.cmd_port}")
                self.writer, _ = client.do_put(self.descriptor, self.schema)
            self.writer.write_batch(batch)
        except Exception as e:
            print(f"[{self.pcb_name} CMD] send failed: {type(e).__name__}")
            self.writer = None
            self._next_connect_attempt_at = now + self.retry_seconds
            return False
        return True

    def _is_command_server_listening(self) -> bool:
        try:
            with socket.create_connection(
                (self.cmd_host, self.cmd_port),
                timeout=self.connect_timeout_seconds,
            ):
                return True
        except OSError:
            return False
