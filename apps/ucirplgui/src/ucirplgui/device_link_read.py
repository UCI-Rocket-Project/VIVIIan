from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ucirplgui import config

_BOARDS = ("gse", "ecu", "extr_ecu", "loadcell")


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


def _parse_board_payload(data: dict[str, Any]) -> DeviceLinkBoardSnapshot:
    return DeviceLinkBoardSnapshot(
        board=str(data.get("board", "")),
        connected=bool(data.get("connected", False)),
        last_connect_epoch_s=_maybe_float(data.get("last_connect_epoch_s")),
        last_rx_epoch_s=_maybe_float(data.get("last_rx_epoch_s")),
        endpoint_host=str(data.get("endpoint_host", "")),
        endpoint_port=int(data.get("endpoint_port", 0)),
        last_error=(None if data.get("last_error") in (None, "") else str(data.get("last_error"))[:200]),
        snapshot_epoch_s=_maybe_float(data.get("snapshot_epoch_s")),
    )


def _maybe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # filter nan


def read_device_link_snapshots() -> dict[str, DeviceLinkBoardSnapshot]:
    """Read all board JSON files; missing boards are absent from the dict."""
    out: dict[str, DeviceLinkBoardSnapshot] = {}
    directory = config.DEVICE_LINK_DIR
    if not directory.is_dir():
        return out
    for board in _BOARDS:
        path = directory / f"{board}.json"
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                continue
            out[board] = _parse_board_payload(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return out


def staleness_severity(*, now_s: float, snap: DeviceLinkBoardSnapshot | None) -> str:
    """Return imgui-style severity label for strip coloring."""
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
    if epoch_s is None:
        return "—"
    delta = max(0.0, now_s - epoch_s)
    if delta < 1.0:
        return f"{int(delta * 1000)} ms"
    if delta < 60.0:
        return f"{delta:.1f} s"
    return f"{delta / 60.0:.1f} min"
