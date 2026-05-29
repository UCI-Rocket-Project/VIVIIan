"""
Fake GSE2V1 TCP server: listens on a port, streams GSE2V1_DATA_FORMAT telemetry
frames at 30 Hz, and ingests GSE2V1_COMMAND_FORMAT command packets to update state.

Run:
    python apps/GUI2.1/gse21_fake_server.py

Environment (optional):
    FAKE_GSE21_HOST — bind address (default 0.0.0.0)
    FAKE_GSE21_PORT — listen port (default 10001)

Point gse21connector.py at 127.0.0.1 and the same port to consume the stream.
"""
from __future__ import annotations

import binascii
import math
import os
import select
import signal
import socket
import struct
import time
from dataclasses import dataclass, field

from gse21connector import (
    GSE2V1_COMMAND_MAGIC,
    GSE2V1_COMMAND_SIZE,
    GSE2V1_DATA_SIZE,
    GSE2V1_HEADER_ALIGN_BYTES,
)

_MAGIC_HEADER = struct.unpack('<I', GSE2V1_HEADER_ALIGN_BYTES)[0]
_DATA_BODY_FORMAT = '<I I 18? 14f 3I'  # GSE2V1_DATA_FORMAT without the trailing CRC uint32
_CMD_BODY_FORMAT = '<I 15?'             # GSE2V1_COMMAND_FORMAT without trailing CRC

DATA_SIZE = GSE2V1_DATA_SIZE
CMD_SIZE = GSE2V1_COMMAND_SIZE
UPDATE_HZ = 30.0

_shutdown = False


def _request_shutdown(*_: object) -> None:
    global _shutdown
    print("\nfake GSE2V1: stop requested")
    _shutdown = True


@dataclass
class _SimState:
    igniter0_fire: bool = False
    igniter1_fire: bool = False
    alarm: bool = False
    solenoid_states: list = field(default_factory=lambda: [False] * 12)


def _pack_telemetry(seq: int, state: _SimState) -> bytes:
    t = seq / UPDATE_HZ
    currents = [
        (2.0 + 0.3 * math.sin(t * 2 * math.pi * (0.5 + i * 0.13) + i))
        if state.solenoid_states[i]
        else (0.05 + 0.02 * math.sin(t * 2 * math.pi * (0.3 + i * 0.07) + i + 1.0))
        for i in range(12)
    ]
    payload = struct.pack(
        _DATA_BODY_FORMAT,
        _MAGIC_HEADER,           # magicHeader
        seq & 0xFFFFFFFF,        # timestamp
        False,                   # igniterArmed
        True,                    # igniter0Continuity
        True,                    # igniter1Continuity
        state.igniter0_fire,     # igniterInternalState0
        state.igniter1_fire,     # igniterInternalState1
        state.alarm,             # alarmInternalState
        *state.solenoid_states,  # solenoidInternalState0-11
        5.0,                     # supplyVoltage0
        5.0,                     # supplyVoltage1
        *currents,               # solenoidCurrent0-11
        300,                     # temperature0
        310,                     # temperature1
        295,                     # temperature2
    )
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack('<I', crc)


def _drain_commands(sock: socket.socket, buf: bytearray, state: _SimState) -> bool:
    """Non-blocking read: consume whole command packets and update state. Returns False on close."""
    r, _, _ = select.select([sock], [], [], 0.0)
    if not r:
        return True
    chunk = sock.recv(8192)
    if not chunk:
        return False
    buf.extend(chunk)
    cmd_body_size = struct.calcsize(_CMD_BODY_FORMAT)
    while len(buf) >= CMD_SIZE:
        pkt = bytes(buf[:CMD_SIZE])
        del buf[:CMD_SIZE]
        body = pkt[:cmd_body_size]
        crc_bytes = pkt[cmd_body_size:]
        if (binascii.crc32(body) & 0xFFFFFFFF) != struct.unpack('<I', crc_bytes)[0]:
            continue
        magic, *bools = struct.unpack(_CMD_BODY_FORMAT, body)
        if magic != GSE2V1_COMMAND_MAGIC:
            continue
        state.igniter0_fire = bool(bools[0])
        state.igniter1_fire = bool(bools[1])
        state.alarm = bool(bools[2])
        state.solenoid_states = [bool(b) for b in bools[3:15]]
    return True


def _serve_one_client(conn: socket.socket, addr: object, state: _SimState) -> None:
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buf = bytearray()
    seq = 0
    dt = 1.0 / UPDATE_HZ
    print(f"fake GSE2V1: client connected {addr}")
    while not _shutdown:
        try:
            t0 = time.time()
            if not _drain_commands(conn, buf, state):
                print("fake GSE2V1: client closed")
                return
            conn.sendall(_pack_telemetry(seq, state))
            seq = (seq + 1) & 0xFFFFFFFF
            elapsed = time.time() - t0
            sleep_s = dt - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)
        except (BrokenPipeError, ConnectionResetError):
            print("fake GSE2V1: peer disconnected")
            return
        except OSError as e:
            print(f"fake GSE2V1: socket error: {e}")
            return


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    host = os.environ.get("FAKE_GSE21_HOST", "0.0.0.0")
    port = int(os.environ.get("FAKE_GSE21_PORT", "10001"))
    state = _SimState()
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((host, port))
        ls.listen(1)
        ls.settimeout(1.0)
        print(
            f"fake GSE2V1 listening on {host}:{port} "
            f"(telemetry {DATA_SIZE} B, cmd {CMD_SIZE} B, {UPDATE_HZ:.0f} Hz)"
        )
        while not _shutdown:
            try:
                conn, addr = ls.accept()
                try:
                    _serve_one_client(conn, addr, state)
                except Exception as e:
                    print(f"fake GSE2V1: error serving {addr}: {e}")
                finally:
                    conn.close()
                    print("fake GSE2V1: connection closed, waiting for next client")
            except (TimeoutError, socket.timeout):
                continue
            except KeyboardInterrupt:
                _request_shutdown()
                break
            except OSError as e:
                if _shutdown:
                    break
                print(f"fake GSE2V1: accept error: {e}; retrying")
                time.sleep(0.25)
        print("fake GSE2V1: listener exited")
    finally:
        try:
            ls.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
