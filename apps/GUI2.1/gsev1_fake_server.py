"""
Fake GSEV1 TCP server for local integration testing.

It streams packets matching GSEV1_DATA_FORMAT and accepts command packets matching
GSEV1_COMMAND_FORMAT. By default it tries to bind to 10.0.0.88:10001 so the
existing gsev1connector.py can connect without changes.

Run:
    python apps/GUI2.1/gsev1_fake_server.py

Environment overrides:
    FAKE_GSEV1_HOST=127.0.0.1
    FAKE_GSEV1_PORT=10001
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

from gsev1connector import (
    GSEV1_COMMAND_SIZE,
    GSEV1_DATA_SIZE,
)

_DATA_BODY_FORMAT = "<I 15? 17f"
_CMD_BODY_FORMAT = "<12?"
_DATA_BODY_SIZE = struct.calcsize(_DATA_BODY_FORMAT)
_CMD_BODY_SIZE = struct.calcsize(_CMD_BODY_FORMAT)

UPDATE_HZ = 30.0

_shutdown = False


def _request_shutdown(*_: object) -> None:
    global _shutdown
    print("\nfake GSEV1: stop requested")
    _shutdown = True


@dataclass
class _SimState:
    igniter0_fire: bool = False
    igniter1_fire: bool = False
    alarm: bool = False
    solenoid_states: list[bool] = field(default_factory=lambda: [False] * 9)


def _pack_telemetry(seq: int, state: _SimState) -> bytes:
    t = seq / UPDATE_HZ
    currents = [
        (1.8 + 0.25 * math.sin(t * 2 * math.pi * (0.5 + i * 0.11) + i))
        if state.solenoid_states[i]
        else (0.04 + 0.015 * math.sin(t * 2 * math.pi * (0.3 + i * 0.05) + i))
        for i in range(9)
    ]

    payload = struct.pack(
        _DATA_BODY_FORMAT,
        seq & 0xFFFFFFFF,
        True,                    # igniterArmed
        True,                    # igniter0Continuity
        True,                    # igniter1Continuity
        state.igniter0_fire,     # igniterInternalState0
        state.igniter1_fire,     # igniterInternalState1
        state.alarm,             # alarmInternalState
        *state.solenoid_states,  # solenoidInternalState0-8
        5.0,                     # supplyVoltage0
        5.0,                     # supplyVoltage1
        *currents,               # solenoidCurrent0-8
        295.0 + 3.0 * math.sin(t * 0.4),
        292.0 + 2.0 * math.sin(t * 0.35 + 1.0),
        700.0 + 15.0 * math.sin(t * 0.9),
        480.0 + 10.0 * math.sin(t * 0.6 + 3.0),
        35.0 + 2.0 * math.sin(t * 1.2 + 2.0),
        520.0 + 8.0 * math.sin(t * 0.7 + 1.0),
    )
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack("<I", crc)


def _drain_commands(sock: socket.socket, buf: bytearray, state: _SimState) -> bool:
    r, _, _ = select.select([sock], [], [], 0.0)
    if not r:
        return True

    chunk = sock.recv(8192)
    if not chunk:
        return False

    buf.extend(chunk)
    while len(buf) >= GSEV1_COMMAND_SIZE:
        pkt = bytes(buf[:GSEV1_COMMAND_SIZE])
        del buf[:GSEV1_COMMAND_SIZE]

        body = pkt[:_CMD_BODY_SIZE]
        crc_bytes = pkt[_CMD_BODY_SIZE:]
        expected_crc = struct.unpack("<I", crc_bytes)[0]
        actual_crc = binascii.crc32(body) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            print("fake GSEV1: dropped command with bad CRC")
            continue

        bools = struct.unpack(_CMD_BODY_FORMAT, body)
        state.igniter0_fire = bool(bools[0])
        state.igniter1_fire = bool(bools[1])
        state.alarm = bool(bools[2])
        state.solenoid_states = [bool(value) for value in bools[3:12]]
        print(
            "fake GSEV1 command:",
            f"igniter0={state.igniter0_fire}",
            f"igniter1={state.igniter1_fire}",
            f"alarm={state.alarm}",
            f"solenoids={state.solenoid_states}",
        )

    return True


def _serve_one_client(conn: socket.socket, addr: object, state: _SimState) -> None:
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buf = bytearray()
    seq = 0
    dt = 1.0 / UPDATE_HZ
    print(f"fake GSEV1: client connected {addr}")

    while not _shutdown:
        try:
            t0 = time.time()
            if not _drain_commands(conn, buf, state):
                print("fake GSEV1: client closed")
                return
            conn.sendall(_pack_telemetry(seq, state))
            seq = (seq + 1) & 0xFFFFFFFF
            sleep_s = dt - (time.time() - t0)
            if sleep_s > 0:
                time.sleep(sleep_s)
        except (BrokenPipeError, ConnectionResetError):
            print("fake GSEV1: peer disconnected")
            return
        except OSError as e:
            print(f"fake GSEV1: socket error: {e}")
            return


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    host = os.environ.get("FAKE_GSEV1_HOST", "127.0.0.1")
    port = int(os.environ.get("FAKE_GSEV1_PORT", "10001"))
    state = _SimState()

    if _DATA_BODY_SIZE + 4 != GSEV1_DATA_SIZE:
        raise RuntimeError(
            f"sim telemetry size mismatch: {_DATA_BODY_SIZE + 4} != {GSEV1_DATA_SIZE}"
        )

    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((host, port))
        ls.listen(1)
        ls.settimeout(1.0)
        print(
            f"fake GSEV1 listening on {host}:{port} "
            f"(telemetry {GSEV1_DATA_SIZE} B, cmd {GSEV1_COMMAND_SIZE} B, {UPDATE_HZ:.0f} Hz)"
        )
        while not _shutdown:
            try:
                conn, addr = ls.accept()
                try:
                    _serve_one_client(conn, addr, state)
                finally:
                    conn.close()
                    print("fake GSEV1: connection closed, waiting for next client")
            except (TimeoutError, socket.timeout):
                continue
            except KeyboardInterrupt:
                _request_shutdown()
                break
            except OSError as e:
                if _shutdown:
                    break
                print(f"fake GSEV1: accept error: {e}; retrying")
                time.sleep(0.25)
        print("fake GSEV1: listener exited")
    finally:
        try:
            ls.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
