import binascii
import os
import socket
import struct
import threading
import time





import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

from generic_connector import CommandServer



# --- PACKET DEFINITIONS ---
GSE2V1_HEADER_ALIGN_BYTES = b'\xef\xbe\xad\xde'
GSE2V1_DATA_FORMAT = '<I I 18? 14f 4I'
GSE2V1_DATA_SIZE = struct.calcsize(GSE2V1_DATA_FORMAT)

GSE2V1_COMMAND_FORMAT = '<I 15? I'
GSE2V1_COMMAND_MAGIC = 0xDEADD00D
GSE2V1_COMMAND_SIZE = struct.calcsize(GSE2V1_COMMAND_FORMAT)

GSE2V1_ROWS_PER_FRAME = 1000
GSE2V1_BACKEND_FLIGHT = "grpc://127.0.0.1:8815"
GSE2V1_ECHO_FLIGHT = "grpc://127.0.0.1:8820"
GSE2V1_CMD_FLIGHT_BIND = "grpc://0.0.0.0:8827"

_TCP_SESSION_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionResetError,
    OSError,
)

# remember start with majic header and end with crc
GSE2V1_COMMAND_FIELD_NAMES = [
    "igniter0Fire",
    "igniter1Fire",
    "alarm",
    "solenoidState0",
    "solenoidState1",
    "solenoidState2",
    "solenoidState3",
    "solenoidState4",
    "solenoidState5",
    "solenoidState6",
    "solenoidState7",
    "solenoidState8",
    "solenoidState9",
    "solenoidState10",
    "solenoidState11",
]


GSE2V1_STATE_FIELD_NAMES = [
    "igniterArmed",
    "igniter0Continuity",
    "igniter1Continuity",
    "solenoidCurrent0",
    "solenoidCurrent1",
    "solenoidCurrent2",
    "solenoidCurrent3",
    "solenoidCurrent4",
    "solenoidCurrent5",
    "solenoidCurrent6",
    "solenoidCurrent7",
    "solenoidCurrent8",
    "solenoidCurrent9",
    "solenoidCurrent10",
    "solenoidCurrent11",
]

GSE2V1_FIELD_NAMES = [
    "magicHeader",
    "timestamp",
    "igniterArmed",
    "igniter0Continuity",
    "igniter1Continuity",
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
    "supplyVoltage0",
    "supplyVoltage1",
    "solenoidCurrent0",
    "solenoidCurrent1",
    "solenoidCurrent2",
    "solenoidCurrent3",
    "solenoidCurrent4",
    "solenoidCurrent5",
    "solenoidCurrent6",
    "solenoidCurrent7",
    "solenoidCurrent8",
    "solenoidCurrent9",
    "solenoidCurrent10",
    "solenoidCurrent11",
    "temperature0",
    "temperature1",
    "temperature2",
    "crc",
]

def decode_gse2v1_data(raw_bytes):
    try: 
        unpacked = struct.unpack(GSE2V1_DATA_FORMAT, raw_bytes[:-4])
        return unpacked
    except Exception as e:
        print(f"Error decoding GSE2V1 data: {e}")
        return None


def gse2v1_cmd_pack(
    igniter0_fire: bool,
    igniter1_fire: bool,
    alarm: bool,
    solenoid_states: list[bool],
) -> bytes:
    """Pack a GSE2V1 command frame: magic + 15 bools + CRC32 (see example_newGSE2_0.send_gse_command)."""
    payload = struct.pack(
        "<I 15?",
        GSE2V1_COMMAND_MAGIC,
        igniter0_fire,
        igniter1_fire,
        alarm,
        *solenoid_states,
    )
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack("<I", crc)


def gse2v1_cmd_pack_from_row(row: np.ndarray) -> bytes:
    """Build wire-format command bytes from one Flight row (columns = GSE2V1_COMMAND_FIELD_NAMES)."""
    n = len(GSE2V1_COMMAND_FIELD_NAMES)
    if row.shape[0] < n:
        raise ValueError(f"Expected at least {n} command fields, got row length {row.shape[0]}")
    bools = [bool(row[j]) for j in range(n)]
    return gse2v1_cmd_pack(bools[0], bools[1], bools[2], bools[3:])

def gse2v1_telemetry_to_flight_connector(
    rows_per_frame: int, 
    tcp_connection: socket.socket, 
    nbytes: int, 
    struct_format: str,
    command_field_names: list[str],
    telemetry_field_names: list[str],
    flight_address: str, #the address of the backend server that we are sending the data to
    echo_state_address: str #the adress of the frontend server we are sending the internal and current states to 
) -> None:
    try: 
        telemetry_schema = pa.schema([(name, pa.float64()) for name in telemetry_field_names])
        command_schema = pa.schema([(name, pa.float64()) for name in command_field_names])
        backend_client = flight.connect(flight_address)
        descriptor = flight.FlightDescriptor.for_path("gse2v1_telemetry")
        writer, _ = backend_client.do_put(descriptor, telemetry_schema)
        written_bytes = 0
        start_time = time.time()
        running_buffer = b"" #storing data in this buffer till we get full or maybe more than that 
        current_data_buffer = np.empty((rows_per_frame, len(telemetry_field_names)), dtype=np.float64)
        current_data_index = 0
        echo_client = flight.connect(echo_state_address)
        echo_descriptor = flight.FlightDescriptor.for_path("gse2v1_echo_state")
        echo_writer, _ = echo_client.do_put(echo_descriptor, command_schema)
        last_known_state = [0.0] * (len(command_field_names) + 1) #1 for the connected state
        while True:
            try: 
                #continue reading from the socket

                chunk = tcp_connection.recv(256)
                if not chunk:
                    raise ConnectionError("GSE2V1 TCP socket closed")
                running_buffer += chunk
                while len(running_buffer) >= GSE2V1_DATA_SIZE:
                    magic_idx = running_buffer.find(GSE2V1_HEADER_ALIGN_BYTES)
                    if magic_idx == -1:
                        running_buffer = running_buffer[-3:] if len(running_buffer) >= 3 else running_buffer
                        break
                    packet_bytes = running_buffer[magic_idx:magic_idx + GSE2V1_DATA_SIZE]
                    unpacked_data = decode_gse2v1_data(packet_bytes)
                    current_data_buffer[current_data_index] = unpacked_data
                    current_data_index += 1
                    running_buffer = running_buffer[magic_idx + GSE2V1_DATA_SIZE:]
                    #if we have a full buffer we write it to the flight and reset the index 
                    if current_data_index == rows_per_frame:
                        arrays = [
                            pa.array(current_data_buffer[:, i], type=pa.float64())
                            for i in range(len(telemetry_field_names))
                        ]
                        batch = pa.RecordBatch.from_arrays(arrays, schema=telemetry_field_names)
                        writer.write_batch(batch)
                        written_bytes += int(batch.nbytes)
                        #write the command to the echo state should be the last row of the current data buffer write, 
                        command_data = [float(1)]
                        for command_field_name in command_field_names:
                            command_data.append(float(current_data_buffer[-1, GSE2V1_STATE_FIELD_NAMES.index(command_field_name)]))

                        echo_writer.write_batch(command_data)
                        last_known_state = command_data
                        # reset the index and the buffer
                        current_data_index = 0
                        current_data_buffer = np.empty((rows_per_frame, len(telemetry_field_names)), dtype=np.float64)                    
            except _TCP_SESSION_ERRORS as e:
                print(f"Error in GSE2V1 telemetry to flight connector: {e}")
                last_known_state[0] = float(0)
                echo_writer.write_batch(last_known_state)
                raise e
            except Exception as e:
                print(f"Error in GSE2V1 telemetry to flight connector: {e}")
                last_known_state[0] = float(0)
                echo_writer.write_batch(last_known_state)
                raise e
    except Exception as e:
        print(f"Error in GSE2V1 telemetry to flight connector: {e}")
        raise


def gse2v1_make_command_server(
    location: str,
    tcp_connection: socket.socket,
    command_server: type[CommandServer] = CommandServer,
) -> CommandServer:
    """Build a Flight command server (call ``serve()`` on a background thread)."""

    def _converter(data: np.ndarray) -> bytes | None:
        if data.size == 0:
            return None
        return gse2v1_cmd_pack_from_row(data[-1])

    return command_server(location, tcp_connection, _converter)


def gse2v1_flight_to_command_converter(
    location: str,
    command_server: type[CommandServer],
    tcp_connection: socket.socket,
) -> None:
    """Flight command receiver: last row of each do_put batch → GSE2V1 command packet → TCP."""
    gse2v1_make_command_server(location, tcp_connection, command_server).serve()


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
    host = os.environ.get("GSE2V1_IP", "127.0.0.1")
    port = int(os.environ.get("GSE2V1_PORT", "10001"))
    backend_flight = os.environ.get("GSE2V1_BACKEND_FLIGHT", GSE2V1_BACKEND_FLIGHT)
    echo_flight = os.environ.get("GSE2V1_ECHO_FLIGHT", GSE2V1_ECHO_FLIGHT)
    cmd_bind = os.environ.get("GSE2V1_CMD_FLIGHT_BIND", GSE2V1_CMD_FLIGHT_BIND)
    reconnect_s = float(os.environ.get("GSE2V1_RECONNECT_S", "1.0"))

    while True:
        sock: socket.socket | None = None
        telemetry_thread: threading.Thread | None = None
        command_thread: threading.Thread | None = None
        command_server: CommandServer | None = None
        try:
            print(f"GSE2V1 connector: connecting to {host}:{port}")
            while True:
                try:
                    sock = socket.create_connection((host, port), timeout=5.0)
                    sock.settimeout(None)
                    break
                except (_TCP_SESSION_ERRORS, socket.timeout) as exc:
                    print(f"GSE2V1 TCP connect failed ({exc}); retrying in 1s")
                    time.sleep(1.0)

            print("GSE2V1 connector: TCP connected")

            telemetry_thread = threading.Thread(
                target=gse2v1_telemetry_to_flight_connector,
                kwargs={
                    "rows_per_frame": GSE2V1_ROWS_PER_FRAME,
                    "tcp_connection": sock,
                    "nbytes": GSE2V1_DATA_SIZE,
                    "struct_format": GSE2V1_DATA_FORMAT,
                    "command_field_names": GSE2V1_COMMAND_FIELD_NAMES,
                    "telemetry_field_names": GSE2V1_FIELD_NAMES,
                    "flight_address": backend_flight,
                    "echo_state_address": echo_flight,
                },
                daemon=True,
            )
            command_server = gse2v1_make_command_server(cmd_bind, sock)
            command_thread = threading.Thread(target=command_server.serve, daemon=True)
            telemetry_thread.start()
            command_thread.start()
            telemetry_thread.join()
        except KeyboardInterrupt:
            print("\nGSE2V1 connector — shutting down")
            break
        except Exception as exc:
            print(f"GSE2V1 connector session ended: {exc}")
        finally:
            _close_socket(sock)
            if command_server is not None:
                try:
                    command_server.shutdown()
                except Exception:
                    pass
            if telemetry_thread is not None and telemetry_thread.is_alive():
                telemetry_thread.join(timeout=1.0)
            if command_thread is not None and command_thread.is_alive():
                command_thread.join(timeout=1.0)

        print(f"GSE2V1 connector: disconnected, reconnecting in {reconnect_s:.0f}s...")
        time.sleep(reconnect_s)


if __name__ == "__main__":
    print("GSE2V1 connector — telemetry to backend, commands from frontend to TCP")
    main()

































