from __future__ import annotations

import json
import time
from typing import Any

from ucirplgui import config


def write_device_link_snapshot(
    *,
    board: str,
    connected: bool,
    last_connect_epoch_s: float | None,
    last_rx_epoch_s: float | None,
    endpoint_host: str,
    endpoint_port: int,
    last_error: str | None,
) -> None:
    """Atomic JSON write for the frontend feed_loop (no backend hop)."""
    payload: dict[str, Any] = {
        "board": board,
        "connected": connected,
        "last_connect_epoch_s": last_connect_epoch_s,
        "last_rx_epoch_s": last_rx_epoch_s,
        "endpoint_host": endpoint_host,
        "endpoint_port": int(endpoint_port),
        "last_error": last_error,
        "snapshot_epoch_s": time.time(),
    }
    directory = config.DEVICE_LINK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{board}.json"
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)
