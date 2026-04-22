from __future__ import annotations

import socket
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ucirplgui import config


@dataclass(frozen=True, slots=True)
class DeviceLinkBoardSnapshot:
    board: str
    connected: bool
    last_connect_epoch_s: float | None
    last_rx_epoch_s: float | None
    endpoint_host: str
    endpoint_port: int
    last_error: str | None
    snapshot_epoch_s: float | None


class _DeviceLinkBatchSource(Protocol):
    has_batch: bool
    batch: np.ndarray


def _maybe_float(value: float) -> float | None:
    if value != value or np.isnan(value):  # noqa: PLR0124 - NaN check
        return None
    return float(value)


def _pack_ipv4(host: str) -> float:
    try:
        packed = struct.unpack("!I", socket.inet_aton(host.strip()))[0]
    except OSError:
        return 0.0
    return float(packed)


def _unpack_ipv4(value: float) -> str:
    if value != value or np.isnan(value):  # noqa: PLR0124
        return ""
    as_u32 = int(round(float(value))) & 0xFFFFFFFF
    return socket.inet_ntoa(struct.pack("!I", as_u32))


def _encode_last_error_code(*, connected: bool, last_error: str | None) -> float:
    if not connected:
        return 1.0
    if last_error not in (None, ""):
        return 1.0
    return 0.0


def _decode_last_error_code(code: float) -> str | None:
    if code != code or np.isnan(code) or float(code) == 0.0:  # noqa: PLR0124
        return None
    return "disconnected"


def encode_device_link_row(
    *,
    board: str,
    connected: bool,
    last_connect_epoch_s: float | None,
    last_rx_epoch_s: float | None,
    endpoint_host: str,
    endpoint_port: int,
    last_error: str | None,
    snapshot_epoch_s: float,
) -> np.ndarray:
    """One ``ROWS_PER_FRAME`` row for ``DEVICE_LINK_STATUS_COLUMNS`` (float64)."""
    board_index = float(config.DEVICE_LINK_BOARDS.index(board))
    row = np.array(
        [
            board_index,
            1.0 if connected else 0.0,
            np.nan if last_connect_epoch_s is None else float(last_connect_epoch_s),
            np.nan if last_rx_epoch_s is None else float(last_rx_epoch_s),
            _pack_ipv4(endpoint_host),
            float(endpoint_port),
            float(snapshot_epoch_s),
            _encode_last_error_code(connected=connected, last_error=last_error),
        ],
        dtype=np.float64,
    )
    return row.reshape(config.ROWS_PER_FRAME, -1)


def decode_device_link_row(batch: np.ndarray) -> DeviceLinkBoardSnapshot | None:
    """Decode a single-row batch; returns ``None`` if the row is unusable."""
    arr = np.asarray(batch, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] != len(config.DEVICE_LINK_STATUS_COLUMNS):
        return None
    flat = arr[0]
    board_index = int(round(float(flat[0])))
    if board_index < 0 or board_index >= len(config.DEVICE_LINK_BOARDS):
        return None
    board = config.DEVICE_LINK_BOARDS[board_index]
    connected = bool(int(round(float(flat[1]))) != 0)
    return DeviceLinkBoardSnapshot(
        board=board,
        connected=connected,
        last_connect_epoch_s=_maybe_float(float(flat[2])),
        last_rx_epoch_s=_maybe_float(float(flat[3])),
        endpoint_host=_unpack_ipv4(float(flat[4])),
        endpoint_port=int(round(float(flat[5]))),
        last_error=_decode_last_error_code(float(flat[7])),
        snapshot_epoch_s=_maybe_float(float(flat[6])),
    )


def decode_device_link_batches(sources: Mapping[str, _DeviceLinkBatchSource]) -> dict[str, DeviceLinkBoardSnapshot]:
    """Merge per-board Flight batches into the snapshot dict used by the dashboard."""
    out: dict[str, DeviceLinkBoardSnapshot] = {}
    for _key, src in sources.items():
        if not src.has_batch:
            continue
        snap = decode_device_link_row(src.batch)
        if snap is not None:
            out[snap.board] = snap
    return out


def staleness_severity(*, now_s: float, snap: DeviceLinkBoardSnapshot | None) -> str:
    """Return severity from RX freshness age, not network RTT."""
    if snap is None:
        return "warn"
    if not snap.connected:
        return "info"
    if snap.last_rx_epoch_s is None:
        return "warn"
    age = now_s - snap.last_rx_epoch_s
    if age > 2.0:
        return "crit"
    if age > 0.75:
        return "warn"
    return "ok"


def format_age_s(now_s: float, epoch_s: float | None) -> str:
    """Format elapsed age since the last RX/connect event."""
    if epoch_s is None:
        return "—"
    delta = max(0.0, now_s - epoch_s)
    if delta < 1.0:
        return f"{int(delta * 1000)} ms"
    if delta < 60.0:
        return f"{delta:.1f} s"
    return f"{delta / 60.0:.1f} min"
