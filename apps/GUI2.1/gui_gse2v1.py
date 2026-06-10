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
ABORT_BUTTON_ID = "abort"
GSE2V1_PCB_NAME = "gse2v1"
GSE2V1_CMD_HOST = "127.0.0.1"
GSE2V1_CMD_PORT = 8827


GSE2V1_COMMAND_BUTTONS: dict[str, dict[str, Any]] = {
    "sol_gn2_fill_1": {
        "display_name": "GN2 Fill 1",
        "command_field": "solenoidState1",
        "status_field": "solenoidInternalState1",
        "status_value": "solenoidCurrent1",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "sol_gn2_fill_2": {
        "display_name": "GN2 Fill 2",
        "command_field": "solenoidState2",
        "status_field": "solenoidInternalState2",
        "status_value": "solenoidCurrent2",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "sol_gn2_fill_3": {
        "display_name": "GN2 Fill 3",
        "command_field": "solenoidState3",
        "status_field": "solenoidInternalState3",
        "status_value": "solenoidCurrent3",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "sol_gn2_fill_4": {
        "display_name": "GN2 Fill 4",
        "command_field": "solenoidState7",
        "status_field": "solenoidInternalState7",
        "status_value": "solenoidCurrent7",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "sol_gn2_vent": {
        "display_name": "GN2 Vent",
        "command_field": "solenoidState0",
        "status_field": "solenoidInternalState0",
        "status_value": "solenoidCurrent0",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "copv_vent": {
        "display_name": "COPV Vent",
        "command_field": "solenoidState9",
        "status_field": "solenoidInternalState9",
        "status_value": "solenoidCurrent9",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "pv1": {
        "display_name": "PV1",
        "command_field": "solenoidState10",
        "status_field": "solenoidInternalState10",
        "status_value": "solenoidCurrent10",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "pv2": {
        "display_name": "PV2",
        "command_field": "solenoidState11",
        "status_field": "solenoidInternalState11",
        "status_value": "solenoidCurrent11",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "tank_vent": {
        "display_name": "Tank Vent",
        "command_field": "solenoidState8",
        "status_field": "solenoidInternalState8",
        "status_value": "solenoidCurrent8",
        "disabled_by": ABORT_BUTTON_ID,
    },
    "mvas_open": {
        "display_name": "Mvas Open",
        "command_field": ["solenoidState4", "solenoidState5"],
        "status_field": ["solenoidInternalState4", "solenoidInternalState5"],
        "status_value": ["solenoidCurrent4", "solenoidCurrent5"],
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": 1.0,
    },
    "open_mvas_lox": {
        "display_name": "Open Mvas Lox",
        "command_field": "solenoidState4",
        "status_field": "solenoidInternalState4",
        "status_value": "solenoidCurrent4",
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": 1.0,
    },
    "open_mvas_lng": {
        "display_name": "Open Mvas Lng",
        "command_field": "solenoidState5",
        "status_field": "solenoidInternalState5",
        "status_value": "solenoidCurrent5",
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": 1.0,
    },
    "mvas_close": {
        "display_name": "Mvas Close",
        "command_field": "solenoidState6",
        "status_field": "solenoidInternalState6",
        "status_value": "solenoidCurrent6",
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": 1.0,
    },
    "igniter_0": {
        "display_name": "Igniter 0",
        "command_field": "igniter0Fire",
        "status_field": "igniterInternalState0",
        "status_value": "igniter0Continuity",
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "igniter_1": {
        "display_name": "Igniter 1",
        "command_field": "igniter1Fire",
        "status_field": "igniterInternalState1",
        "status_value": "igniter1Continuity",
        "disabled_by": ABORT_BUTTON_ID,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "alarm": {
        "display_name": "Alarm",
        "command_field": "alarm",
        "status_field": "alarmInternalState",
        "status_value": "alarmCurrent",
        "enabled": True,
    },
    f"{ABORT_BUTTON_ID}": {
        "display_name": "Abort",
        "command_field": None,
        "enabled": True,
    },
    "all_off_t12_t13_t16": {
        "display_name": "All Off (T12, T13, T16)",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
    "purge_1_t17": {
        "display_name": "Purge #1 (T17)",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
    "purge_2_t18": {
        "display_name": "Purge #2 (T18)",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
    "t19": {
        "display_name": "T19",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
    "t20": {
        "display_name": "T20",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
    "final_t23": {
        "display_name": "Final (T23)",
        "command_field": None,
        "disabled_by": ABORT_BUTTON_ID,
    },
}

TABLE_BUTTONS: dict[str, dict[str, Any]] = {
    f"{ABORT_BUTTON_ID}": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": True,
            "copv_vent": True,
            "pv1": False,
            "pv2": False,
            "tank_vent": False,
            "mvas_open": False,
            "mvas_close": True,
            "igniter_0": False,
            "igniter_1": False,
            "alarm": True,
        }
    },
    "all_off_t12_t13_t16": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": False,
            "copv_vent": False,
            "pv1": False,
            "pv2": False,
            "tank_vent": False,
            "mvas_open": False,
            "mvas_close": False,
        }
    },
    "purge_1_t17": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": False,
            "copv_vent": False,
            "pv1": False,
            "pv2": False,
            "tank_vent": False,
            "mvas_open": True,
            "mvas_close": False,
        }
    },
    "purge_2_t18": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": False,
            "copv_vent": False,
            "pv1": False,
            "pv2": False,
            "tank_vent": True,
            "mvas_open": True,
            "mvas_close": False,
        }
    },
    "t19": {
        "table_states": {
            "sol_gn2_fill_1": True,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": False,
            "copv_vent": False,
            "pv1": False,
            "pv2": False,
            "tank_vent": False,
            "mvas_open": False,
            "mvas_close": False,
        }
    },
    "t20": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": False,
            "copv_vent": False,
            "pv1": False,
            "pv2": False,
            "mvas_open": False,
            "mvas_close": False,
        }
    },
    "final_t23": {
        "table_states": {
            "sol_gn2_fill_1": False,
            "sol_gn2_fill_2": False,
            "sol_gn2_fill_3": False,
            "sol_gn2_fill_4": False,
            "sol_gn2_vent": True,
            "copv_vent": False,
            "pv1": True,
            "pv2": True,
            "tank_vent": True,
            "mvas_open": False,
            "mvas_close": False,
        }
    },
}
