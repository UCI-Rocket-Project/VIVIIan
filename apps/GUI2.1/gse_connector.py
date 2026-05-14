"""
TCP client for the legacy GSE board (command frames out, telemetry in).

Telemetry path
    board TCP ──recv──▸ pythusa ring ──generic_connector──▸ backend Flight

Command path
    frontend ──generic_connector (do_put)──▸ CommandBuffer ──▸ board TCP sendall

The Flight command receiver runs in a **thread** of the process that owns the
board TCP socket so ``send_gse_command`` can use the same connection.

Run:  ``python apps/GUI2.1/gse_connector.py``
Env:  GSE_IP, GSE_PORT, GUI21_FLIGHT, GSE_CMD_FLIGHT_BIND
"""

from __future__ import annotations

import functools
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pythusa

_gui21 = Path(__file__).resolve().parent
if str(_gui21) not in sys.path:
    sys.path.insert(0, str(_gui21))

from legacy_conn import (  # noqa: E402
    GSE_DATA_LENGTH,
    GSE_FIELD_NAMES,
    gse_cmd_pack,
    gse_recv_unpack,
)
from generic_connector import generic_connector, run_generic_receiver  # noqa: E402

ROWS_PER_FRAME = 1000
NUM_SIGNALS = len(GSE_FIELD_NAMES)

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


class GSETcpBoard:
    """TCP interface to a GSE board: command packets out, telemetry in."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: Optional[float] = 5.0,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._connect_timeout = connect_timeout
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(self._connect_timeout)
            s.connect((self._host, self._port))
            s.settimeout(None)
        except BaseException:
            s.close()
            raise
        self._sock = s

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._sock = None

    def __enter__(self) -> GSETcpBoard:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _require_socket(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("not connected; call connect() or use a context manager")
        return self._sock

    def send_gse_command(
        self,
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
        sock = self._require_socket()
        with self._send_lock:
            sock.sendall(payload)

    def recv_gse_telemetry(self, *, timeout: Optional[float] = None) -> tuple[Sequence, int]:
        sock = self._require_socket()
        prev = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            raw = recv_exact(sock, GSE_DATA_LENGTH)
        finally:
            sock.settimeout(prev)
        return gse_recv_unpack(raw)


# ---------------------------------------------------------------------------
# Command buffer — duck-types as a stream_writer for StorageServer
# ---------------------------------------------------------------------------

class CommandBuffer:
    """Thread-safe single-slot buffer.

    ``StorageServer.do_put`` calls ``.write(data)`` from the Flight thread.
    The telemetry loop calls ``.pop()`` to grab the latest pending command.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Optional[np.ndarray] = None

    def write(self, data: np.ndarray) -> None:
        with self._lock:
            self._pending = data.copy()

    def pop(self) -> Optional[np.ndarray]:
        with self._lock:
            cmd = self._pending
            self._pending = None
            return cmd


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

def _gse_telemetry_tuple_to_signal_row(fields: Sequence) -> np.ndarray:
    return np.asarray(fields, dtype=np.float64)


# ---------------------------------------------------------------------------
# Combined task: TCP telemetry reader + Flight command receiver thread
# ---------------------------------------------------------------------------

def gse_tcp_and_cmd_receiver(
    *,
    telemetry_stream,
    host: str,
    port: int,
    cmd_grpc_bind: str,
) -> None:
    """Owns the board TCP socket.

    * Main loop reads telemetry and packs ring frames.
    * A daemon thread runs a generic Flight receiver (``StorageServer``) that
      deposits incoming command frames into a ``CommandBuffer``.
    * Between telemetry reads the loop polls the buffer and forwards any
      pending command to the board.
    """
    cmd_buf = CommandBuffer()

    cmd_thread = threading.Thread(
        target=run_generic_receiver,
        kwargs=dict(
            grpc_bind=cmd_grpc_bind,
            stream_writer=cmd_buf,
            rows_per_frame=CMD_ROWS_PER_FRAME,
            num_signals=NUM_CMD_SIGNALS,
        ),
        daemon=True,
    )
    cmd_thread.start()
    print(f"GSE command Flight receiver listening on {cmd_grpc_bind}")

    with GSETcpBoard(host, port) as gse:
        while True:
            frame = np.empty((ROWS_PER_FRAME, NUM_SIGNALS), dtype=np.float64)
            for i in range(ROWS_PER_FRAME):
                cmd = cmd_buf.pop()
                if cmd is not None:
                    bools = [bool(cmd[0, j]) for j in range(NUM_CMD_SIGNALS)]
                    gse.send_gse_command(*bools)

                fields, _crc = gse.recv_gse_telemetry()
                frame[i] = _gse_telemetry_tuple_to_signal_row(fields)
            telemetry_stream.write(frame)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    host = os.environ.get("GSE_IP", "127.0.0.1")
    port = int(os.environ.get("GSE_PORT", "10001"))
    backend_flight = os.environ.get("GUI21_FLIGHT", "grpc://localhost:8815")
    cmd_bind = os.environ.get("GSE_CMD_FLIGHT_BIND", "grpc://0.0.0.0:8825")

    tcp_fn = functools.partial(
        gse_tcp_and_cmd_receiver,
        host=host,
        port=port,
        cmd_grpc_bind=cmd_bind,
    )
    send_fn = functools.partial(
        generic_connector,
        field_names=list(GSE_FIELD_NAMES),
        flight_address=backend_flight,
    )

    with pythusa.Pipeline("gse_connector") as pipeline:
        pipeline.add_stream(
            "gse_telemetry",
            shape=(ROWS_PER_FRAME, NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_task(
            "gse_tcp_and_cmd",
            fn=tcp_fn,
            writes={"telemetry_stream": "gse_telemetry"},
        )
        pipeline.add_task(
            "flight_send_to_backend",
            fn=send_fn,
            reads={"stream": "gse_telemetry"},
        )
        pipeline.run()


if __name__ == "__main__":
    print(f"GSE connector — telemetry Flight to backend, command Flight receiver")
    main()
