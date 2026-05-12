"""
TCP client for the legacy GSE board link (command frames out, telemetry in),
plus a pythusa pipeline that buffers telemetry into ring frames and forwards
them to ``backend.py`` over Arrow Flight the same way board1 did (float64
``sensor_*`` batches on ``high_speed_test``).

Board TCP: ``socket(AF_INET, SOCK_STREAM)``, ``connect()``, ``sendall()`` on
``gse_cmd_pack(...)``, ``recv()`` for 91-byte telemetry. See
``legacy_connection.md`` and ``legacy_conn.py``.

Run the pipeline: ``python apps/GUI2.1/gse_connector.py`` (set ``GSE_IP``,
``GSE_PORT``; optional ``GUI21_FLIGHT``, default ``grpc://localhost:8815``).
"""

from __future__ import annotations

import functools
import os
import socket
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import pyarrow as pa
import pythusa
from pyarrow import flight

# Same-directory import when launched as ``python apps/GUI2.1/gse_connector.py``
# from repo root (``legacy_conn`` is not a top-level package).
_gui21 = Path(__file__).resolve().parent
if str(_gui21) not in sys.path:
    sys.path.insert(0, str(_gui21))

from legacy_conn import (  # noqa: E402
    GSE_DATA_LENGTH,
    GSE_FIELD_NAMES,
    gse_cmd_pack,
    gse_recv_unpack,
    tuple_as_dict,
)

ROWS_PER_FRAME = 1000
NUM_SIGNALS = len(GSE_FIELD_NAMES)


def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    """Read exactly ``nbytes`` from a blocking stream socket."""
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
    """
    TCP interface to a GSE board: send CRC-protected command packets and parse
    fixed-size telemetry using ``legacy_conn`` helpers.
    """

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
        """Pack a GSE command with CRC (``legacy_conn.gse_cmd_pack``) and ``sendall``."""
        payload = gse_cmd_pack(
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
        sock = self._require_socket()
        sock.sendall(payload)

    def recv_gse_telemetry(self, *, timeout: Optional[float] = None) -> tuple[Sequence, int]:
        """
        Block until one full GSE frame (``GSE_DATA_LENGTH`` bytes), verify CRC,
        return ``(struct_tuple, crc_wire)`` from ``gse_recv_unpack``.
        """
        sock = self._require_socket()
        prev = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            raw = recv_exact(sock, GSE_DATA_LENGTH)
        finally:
            sock.settimeout(prev)
        return gse_recv_unpack(raw)

    def recv_gse_telemetry_dict(self, *, timeout: Optional[float] = None) -> Mapping[str, object]:
        """Same as ``recv_gse_telemetry`` but field names from ``legacy_conn.GSE_FIELD_NAMES``."""
        fields, _crc = self.recv_gse_telemetry(timeout=timeout)
        return tuple_as_dict(GSE_FIELD_NAMES, fields)


def _gse_telemetry_tuple_to_signal_row(fields: Sequence) -> np.ndarray:
    """Cast the full GSE decode tuple (bools → 0.0/1.0, ints/floats as-is) to float64."""
    return np.asarray(fields, dtype=np.float64)


def gse_tcp_fill_ring_stream(
    *,
    stream,
    host: str,
    port: int,
) -> None:
    """Top-level for spawn: read GSE over TCP, pack ``ROWS_PER_FRAME`` rows of float64 for the ring."""
    with GSETcpBoard(host, port) as gse:
        while True:
            frame = np.empty((ROWS_PER_FRAME, NUM_SIGNALS), dtype=np.float64)
            for i in range(ROWS_PER_FRAME):
                fields, _crc = gse.recv_gse_telemetry()
                frame[i] = _gse_telemetry_tuple_to_signal_row(fields)
            stream.write(frame)


def gse_flight_send_to_backend(*, stream, flight_address: str) -> None:
    """Same Flight ``do_put`` path as board1: float64 ``sensor_*`` columns, ``high_speed_test`` descriptor."""
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in GSE_FIELD_NAMES])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                frame = stream.read()
                if frame is None:
                    continue
                arrays = [pa.array(frame[:, i], type=pa.float64()) for i in range(NUM_SIGNALS)]
                batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                writer.write_batch(batch)
                written_bytes += int(frame.nbytes)
                if time.time() - start_time > 1.0:
                    print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                    start_time = time.time()
                    written_bytes = 0
        except Exception as e:
            print(f"Error sending data: {e}")
            time.sleep(1)


def main() -> None:
    host = os.environ.get("GSE_IP", "127.0.0.1")
    port = int(os.environ.get("GSE_PORT", "10001"))
    flight_address = os.environ.get("GUI21_FLIGHT", "grpc://localhost:8815")
    fill_fn = functools.partial(gse_tcp_fill_ring_stream, host=host, port=port)
    send_fn = functools.partial(gse_flight_send_to_backend, flight_address=flight_address)

    with pythusa.Pipeline("gse_connector") as pipeline:
        pipeline.add_stream(
            "gse_telemetry",
            shape=(ROWS_PER_FRAME, NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_task(
            "gse_tcp_read",
            fn=fill_fn,
            writes={"stream": "gse_telemetry"},
        )
        pipeline.add_task(
            "flight_send",
            fn=send_fn,
            reads={"stream": "gse_telemetry"},
        )
        pipeline.run()


if __name__ == "__main__":
    main()
