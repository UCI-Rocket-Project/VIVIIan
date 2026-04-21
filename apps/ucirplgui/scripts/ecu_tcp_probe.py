#!/usr/bin/env python3
"""
Minimal ECU TCP client matching apps/rocket2_webservice_gui/webservice/server.py
(start_system_listening for ECU: AF_INET SOCK_STREAM .connect, then recv ECU_DATA_LENGTH).

Use this to compare behaviour with UCIRPLGUI device_ecu (e.g. errno 65 vs shell tools).

Examples:
  ECU_IP=10.0.2.1 ECU_PORT=10001 python apps/ucirplgui/scripts/ecu_tcp_probe.py
  python apps/ucirplgui/scripts/ecu_tcp_probe.py --host 10.0.2.1 --port 10001
"""
from __future__ import annotations

import argparse
import binascii
import errno
import os
import socket
import struct
import sys
import time

# Same as apps/rocket2_webservice_gui/webservice/server.py __main__ ECU listener args.
ECU_DATA_LENGTH = 144
ECU_PAYLOAD_FORMAT = "<Lff????fffffffffffffffffffffffffffffff"


def _recv_exact(sock: socket.socket, total: int) -> bytes:
    chunks: list[bytes] = []
    remaining = total
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("peer closed before full packet")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def connect_like_rocket2(host: str, port: int, timeout_s: float | None) -> socket.socket:
    """Mirror server.py: socket(AF_INET, SOCK_STREAM) then connect."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if timeout_s is not None:
        sock.settimeout(timeout_s)
    sock.connect((host, port))
    return sock


def describe_oserror(exc: OSError) -> str:
    err = exc.errno
    name = errno.errorcode.get(err, "") if err is not None else ""
    return f"errno={exc.errno} ({name}) {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="ECU TCP probe (rocket2 server.py style).")
    parser.add_argument("--host", default=os.environ.get("ECU_IP", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ECU_PORT", "10004")))
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket timeout in seconds for connect and first recv (default: 5).",
    )
    parser.add_argument(
        "--packets",
        type=int,
        default=1,
        help="After connect, read this many full ECU frames (default: 1). Use 0 to connect only.",
    )
    parser.add_argument(
        "--also-create-connection",
        action="store_true",
        help="Also try socket.create_connection (used by UCIRPLGUI device_interfaces).",
    )
    args = parser.parse_args()

    print("--- probe identity ---", flush=True)
    print(f"python: {sys.executable}", flush=True)
    print(f"version: {sys.version.splitlines()[0]}", flush=True)
    print(f"target: {args.host!r}:{args.port}", flush=True)
    print(flush=True)

    print("--- getaddrinfo(AF_INET) ---", flush=True)
    try:
        infos = socket.getaddrinfo(
            args.host,
            args.port,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        for info in infos:
            print(info, flush=True)
    except OSError as exc:
        print(f"getaddrinfo failed: {describe_oserror(exc)}", flush=True)
        return 1
    print(flush=True)

    # 1) Exact rocket2 pattern
    print("--- connect: socket(AF_INET, SOCK_STREAM).connect() [rocket2 server.py] ---", flush=True)
    t0 = time.monotonic()
    try:
        sock = connect_like_rocket2(args.host, args.port, args.timeout)
    except OSError as exc:
        dt_ms = (time.monotonic() - t0) * 1000.0
        print(f"FAILED after {dt_ms:.1f} ms: {describe_oserror(exc)}", flush=True)
        print(
            "\nNote: errno 65 (EHOSTUNREACH) here means the kernel had no route to that "
            "host at connect time — same meaning as in UCIRPLGUI logs, not a CRC/packet issue.",
            flush=True,
        )
        return 2

    dt_ms = (time.monotonic() - t0) * 1000.0
    print(f"OK connected in {dt_ms:.1f} ms (fd={sock.fileno()})", flush=True)

    try:
        if args.packets <= 0:
            print("(skip recv --packets 0)", flush=True)
        else:
            print(
                f"--- recv {args.packets} x {ECU_DATA_LENGTH} bytes (blocking, timeout={args.timeout}) ---",
                flush=True,
            )
            for i in range(args.packets):
                raw = _recv_exact(sock, ECU_DATA_LENGTH)
                crc_wire = struct.unpack("<L", raw[-4:])[0]
                crc_calc = binascii.crc32(raw[:-4])
                ok = crc_wire == crc_calc
                payload = raw[:-4]
                fields = struct.unpack(ECU_PAYLOAD_FORMAT, payload)
                print(
                    f"packet {i + 1}: len={len(raw)} crc_ok={ok} "
                    f"packet_time_ms={fields[0]} crc_wire={crc_wire} crc_calc={crc_calc}",
                    flush=True,
                )
    finally:
        sock.close()
        print("socket closed.", flush=True)

    if args.also_create_connection:
        print(flush=True)
        print("--- connect: socket.create_connection() [UCIRPLGUI device_interfaces] ---", flush=True)
        t1 = time.monotonic()
        try:
            sock2 = socket.create_connection((args.host, args.port), timeout=args.timeout)
        except OSError as exc:
            dt_ms = (time.monotonic() - t1) * 1000.0
            print(f"FAILED after {dt_ms:.1f} ms: {describe_oserror(exc)}", flush=True)
            return 3
        dt_ms = (time.monotonic() - t1) * 1000.0
        print(f"OK connected in {dt_ms:.1f} ms (fd={sock2.fileno()})", flush=True)
        sock2.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
