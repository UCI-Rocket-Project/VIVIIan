"""
TCP stand-in for a real GSE board: listens on a port, accepts one client at a time,
streams valid 91-byte telemetry frames (``legacy_conn`` layout + CRC), and
optionally ingests ``gse_cmd_pack`` command frames from the client (13-byte body
+ 4-byte CRC) to echo solenoid / igniter bits into the telemetry bool fields.

Run (from repo root or this directory):

    python apps/GUI2.1/fake_gse_server.py

Environment (optional):

- ``FAKE_GSE_HOST`` — bind address (default ``0.0.0.0``).
- ``FAKE_GSE_PORT`` or ``GSE_PORT`` — listen port (default ``10001``).

Point ``gse_connector`` at ``127.0.0.1`` and the same port to consume the stream.
"""

from __future__ import annotations

import binascii
import math
import os
import select
import signal
import socket
import struct
import sys
import threading
import time
from pathlib import Path
_gui21 = Path(__file__).resolve().parent
if str(_gui21) not in sys.path:
    sys.path.insert(0, str(_gui21))

from legacy_conn import (  # noqa: E402
    GSE_CMD_BODY_FORMAT,
    GSE_DATA_LENGTH,
    GSE_RECV_FORMAT,
    gse_recv_unpack,
)

GSE_CMD_PACKET_LENGTH = struct.calcsize(GSE_CMD_BODY_FORMAT) + 4

# Ctrl+C queues SIGINT; we clear blocking ``accept()`` / tight send loops via this flag (Windows-safe).
_shutdown = threading.Event()


def _request_shutdown(*_args: object) -> None:
    print("\nfake GSE: stop requested")
    _shutdown.set()


def _pack_gse_telemetry(seq: int, cmd_bools: list[bool]) -> bytes:
    """Build one wire frame: ``GSE_RECV_FORMAT`` payload + little-endian CRC32."""
    packet_time = seq & 0xFFFFFFFF
    while len(cmd_bools) < 13:
        cmd_bools.append(False)
    # ``GSE_RECV_FORMAT``: ``L`` + 15 ``?`` + 17 ``f`` (33 values total).
    bools15 = list(cmd_bools[:13]) + [False, False]
    floats17 = tuple(10.0 * math.sin(0.002 * seq + i) + 0.5 * i for i in range(17))
    payload = struct.pack(GSE_RECV_FORMAT, packet_time, *bools15, *floats17)
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    #time.sleep(0.0001)
    return payload + struct.pack("<L", crc)


def _drain_commands(sock: socket.socket, buf: bytearray, cmd_bools: list[bool]) -> bool:
    """
    Non-blocking read: append to ``buf``, consume whole command packets, update
    ``cmd_bools`` from the last valid frame. Returns False if peer closed.
    """
    r, _, _ = select.select([sock], [], [], 0.0)
    if not r:
        return True
    chunk = sock.recv(8192)
    if not chunk:
        return False
    buf.extend(chunk)
    while len(buf) >= GSE_CMD_PACKET_LENGTH:
        pkt = bytes(buf[:GSE_CMD_PACKET_LENGTH])
        del buf[:GSE_CMD_PACKET_LENGTH]
        body, crc_le = pkt[:-4], pkt[-4:]
        if (binascii.crc32(body) & 0xFFFFFFFF) != struct.unpack("<L", crc_le)[0]:
            continue
        cmd_bools[:] = list(struct.unpack(GSE_CMD_BODY_FORMAT, body))
    return True


def _serve_one_client(conn: socket.socket, client_addr: object) -> None:
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buf = bytearray()
    cmd_bools = [False] * 13
    seq = 0
    print(f"fake GSE: client connected {client_addr}")
    while not _shutdown.is_set():
        try:
            if not _drain_commands(conn, buf, cmd_bools):
                print("fake GSE: client closed (read)")
                return
            frame = _pack_gse_telemetry(seq, cmd_bools)
            if len(frame) != GSE_DATA_LENGTH:
                raise RuntimeError(
                    f"internal bug: frame len {len(frame)} != {GSE_DATA_LENGTH}"
                )
            conn.sendall(frame)
            seq = (seq + 1) & 0xFFFFFFFF
        except (BrokenPipeError, ConnectionResetError):
            print("fake GSE: peer disconnected (write/reset)")
            return
        except OSError as e:
            print(f"fake GSE: socket error on client loop: {e}")
            return
        except Exception as e:
            print(f"fake GSE: unexpected error in telemetry loop ({e}); closing client handler")
            return


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)

    host = os.environ.get("FAKE_GSE_HOST", "0.0.0.0")
    port = int(os.environ.get("FAKE_GSE_PORT", os.environ.get("GSE_PORT", "10001")))
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((host, port))
        ls.listen(1)
        ls.settimeout(1.0)
        print(
            f"fake GSE listening on {host}:{port} (telemetry {GSE_DATA_LENGTH} B, cmd {GSE_CMD_PACKET_LENGTH} B)"
        )
        while not _shutdown.is_set():
            try:
                conn, addr = ls.accept()
                try:
                    try:
                        _serve_one_client(conn, addr)
                    except Exception as e:
                        print(f"fake GSE: error serving {addr}: {e}")
                finally:
                    conn.close()
                    print("fake GSE: connection closed, waiting for next client")
            except (TimeoutError, socket.timeout):
                continue
            except KeyboardInterrupt:
                _request_shutdown()
                break
            except OSError as e:
                if _shutdown.is_set():
                    break
                print(f"fake GSE: accept failed: {e}; retrying in 0.25s")
                time.sleep(0.25)
            except Exception as e:
                if _shutdown.is_set():
                    break
                print(f"fake GSE: listener loop error: {e}; retrying in 0.25s")
                time.sleep(0.25)
        print("fake GSE: listener exited")
    finally:
        try:
            ls.close()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        # Quick sanity: wire frame round-trips through ``gse_recv_unpack``.
        _t = _pack_gse_telemetry(42, [True, False] * 6 + [False])
        gse_recv_unpack(_t)
        main()
    except KeyboardInterrupt:
        _request_shutdown()
        print("\nfake GSE: stopped")
