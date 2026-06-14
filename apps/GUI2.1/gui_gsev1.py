from __future__ import annotations

import socket
import time
from typing import Any

from numpy._core.umath import NAN
import pyarrow as pa
import pyarrow.flight as flight

from gui_elements import BUTTON_STATUS_OFF_COLOR, BUTTON_STATUS_ON_COLOR, Button
from generic_connector import LatestServer, RocketPCBCommandClient


IGNITER_MOMENTARY_SECONDS = 1.0
GSEV1_PCB_NAME = "gsev1"
GSEV1_CMD_HOST = "127.0.0.1"
GSEV1_CMD_PORT = 8828


GSEV1_COMMAND_BUTTONS: dict[str, dict[str, Any]] = {
    "s0": {
        "display_name": "Solenoid 0",
        "command_field": "solenoidState0",
        "status_field": "solenoidInternalState0",
        "status_value": "solenoidCurrent0",
    },
    "s1": {
        "display_name": "Solenoid 1",
        "command_field": "solenoidState1",
        "status_field": "solenoidInternalState1",
        "status_value": "solenoidCurrent1",
    },
}

