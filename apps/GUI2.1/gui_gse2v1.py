from __future__ import annotations

from typing import Any

from numpy._core.umath import NAN
import pyarrow as pa
import pyarrow.flight as flight

from gse21connector import GSE2V1_COMMAND_FIELD_NAMES, GSE2V1_NUM_COMMAND_SIGNALS
from gui_elements import BUTTON_STATUS_OFF_COLOR, BUTTON_STATUS_ON_COLOR, Button
from generic_connector import LatestServer

BUTTON_WIDTH = 260.0
IGNITER_MOMENTARY_SECONDS = 1.0
ABORT_BUTTON_ID = "abort"


class GseCommandClient:
    def __init__(self) -> None:
        self.row = [0.0] * GSE2V1_NUM_COMMAND_SIGNALS
        self.schema = pa.schema([(name, pa.float64()) for name in GSE2V1_COMMAND_FIELD_NAMES])
        self.descriptor = flight.FlightDescriptor.for_path("gse2v1_commands")
        self.writer = None

    def send(self) -> None:
        if self.writer is None:
            client = flight.connect("grpc://127.0.0.1:8827")
            self.writer, _ = client.do_put(self.descriptor, self.schema)
        batch = pa.RecordBatch.from_arrays(
            [pa.array([value], type=pa.float64()) for value in self.row],
            schema=self.schema,
        )
        self.writer.write_batch(batch)


def abort_is_inactive() -> bool:
    return not _button_state(ABORT_BUTTON_ID)


GSE2V1_COMMAND_BUTTONS: dict[str, dict[str, Any]] = {
    "sol_gn2_fill_1": {
        "display_name": "GN2 Fill 1",
        "command_field": "solenoidState0",
        "status_field": "solenoidInternalState0",
        "status_value": "solenoidCurrent0",

        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_2": {
        "display_name": "GN2 Fill 2",
        "command_field": "solenoidState1",
        "status_field": "solenoidInternalState1",
        "status_value": "solenoidCurrent1",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_3": {
        "display_name": "GN2 Fill 3",
        "command_field": "solenoidState2",
        "status_field": "solenoidInternalState2",
        "status_value": "solenoidCurrent2",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_4": {
        "display_name": "GN2 Fill 4",
        "command_field": "solenoidState3",
        "status_field": "solenoidInternalState3",
        "status_value": "solenoidCurrent3",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_vent": {
        "display_name": "GN2 Vent",
        "command_field": "solenoidState4",
        "status_field": "solenoidInternalState4",
        "status_value": "solenoidCurrent4",
        "enabled": abort_is_inactive,
    },
    "copv_vent": {
        "display_name": "COPV Vent",
        "command_field": "solenoidState5",
        "status_field": "solenoidInternalState5",
        "status_value": "solenoidCurrent5",
        "enabled": abort_is_inactive,
    },
    "pv1": {
        "display_name": "PV1",
        "command_field": "solenoidState6",
        "status_field": "solenoidInternalState6",
        "status_value": "solenoidCurrent6",
        "enabled": abort_is_inactive,
    },
    "pv2": {
        "display_name": "PV2",
        "command_field": "solenoidState7",
        "status_field": "solenoidInternalState7",
        "status_value": "solenoidCurrent7",
        "enabled": abort_is_inactive,
    },
    "tank_vent": {
        "display_name": "Tank Vent",
        "command_field": "solenoidState8",
        "status_field": "solenoidInternalState8",
        "status_value": "solenoidCurrent8",
        "enabled": abort_is_inactive,
    },
    "mvas_open": {
        "display_name": "Mvas Vent",
        "command_field": ["solenoidState9", "solenoidState10"],
        "status_field": ["solenoidInternalState9", "solenoidInternalState10"],
        "status_value": ["solenoidCurrent9", "solenoidCurrent10"],
        "enabled": abort_is_inactive,
    },
    "mvas_close": {
        "display_name": "Mvas Open",
        "command_field": "solenoidState11",
        "status_field": "solenoidInternalState11",
        "status_value": "solenoidCurrent11",
        "enabled": abort_is_inactive,
    },
    "mvas_close": {
        "display_name": "Mvas Close",
        "command_field": "solenoidState11",
        "status_field": "solenoidInternalState11",
        "status_value": "solenoidCurrent11",
        "enabled": abort_is_inactive,
    },
    "igniter_0": {
        "display_name": "Igniter 0",
        "command_field": "igniter0Fire",
        "status_field": "igniterInternalState0",
        "status_value": "igniter0Continuity",
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "igniter_1": {
        "display_name": "Igniter 1",
        "command_field": "igniter1Fire",
        "status_field": "igniterInternalState1",
        "status_value": "igniter1Continuity",
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "alarm": {
        "display_name": "Alarm",
        "command_field": "alarm",
        "status_field": "alarmInternalState",
        "status_value": "alarmCurrent",
        "enabled": True,
    },
    "abort": {
        "display_name": "Abort",
        "command_field": None,
        "enabled": True,
    },
}


def get_status_text(latest_server: LatestServer, status_value_field: str):
    def get_status_text_for_button(): 
        try:
            value = latest_server.latest[status_value_field]
        except KeyError:
            return "NAN"
        except Exception as e:
            return "NAN"
        if value is None: 
            return "NAN" # No data
        else: 
            return f"{value:.3f}"
    return get_status_text_for_button




def get_internal_status_value(latest_server: LatestServer, internal_status_field: str):
    def get_internal_status_value_for_button(): 
        try:
            value = latest_server.latest[internal_status_field]
        except KeyError:
            return NAN
        except Exception as e:
            return NAN
        if value is None: 
            return NAN # No data
        else:
            return value 
    return get_internal_status_value_for_button 



def _handle_button_click(
    client: GseCommandClient,
    button_id: str,
    button: Button,
) -> None:
    _sync_button_to_client_row(client, button_id)
    _sync_button_status(button)

    if button_id == ABORT_BUTTON_ID and button.state:
        _apply_abort(client)

    client.send()

ABORT_BUTTON_STATES = {
    "sol_gn2_fill_1": False,
    "sol_gn2_fill_2": False,
    "sol_gn2_fill_3": False,
    "sol_gn2_fill_4": False,
    "sol_gn2_vent": False,
    "tank_vent": True,
    "copv_vent": True,
    "pv1": False,
    "pv2": True,
    "vent": True,
    "mvas_open": False,
    "mvas_close": True,
    "igniter_0": False,
    "igniter_1": False,
    "alarm": True,
}



def _apply_abort(client: GseCommandClient) -> None:
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        if button_id == ABORT_BUTTON_ID or button_id == "alarm":
            continue
        button = config.get("button")
        if not isinstance(button, Button):
            continue
        if button_id in ABORT_BUTTON_STATES:
            button.set_state(ABORT_BUTTON_STATES[button_id])
        _sync_button_status(button)
        _sync_button_to_client_row(client, button_id)


def _sync_button_to_client_row(client: GseCommandClient, button_id: str) -> None:
    config = GSE2V1_COMMAND_BUTTONS[button_id]
    command_field = config.get("command_field")
    button = config.get("button")
    if command_field is None or not isinstance(button, Button):
        return
    index = GSE2V1_COMMAND_FIELD_NAMES.index(command_field)
    client.row[index] = 1.0 if button.state else 0.0

def _internal_status_to_button(button, internal_status_value: int | str):
    if internal_status_value is NAN:
        button.enabled = False
    else: 
        button.enabled = True
        button.state = internal_status_value > 0.0




def _sync_button_status(button: Button) -> None:
    button.set_status_color(BUTTON_STATUS_ON_COLOR if button.state else BUTTON_STATUS_OFF_COLOR)


def _button_state(button_id: str) -> bool:
    button = GSE2V1_COMMAND_BUTTONS.get(button_id, {}).get("button")
    return bool(button.state) if isinstance(button, Button) else False

def make_gse2v1_command_buttons(client: GseCommandClient, latest_server: LatestServer) -> tuple[Button, ...]:
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        button = Button(
            button_id,
            config["display_name"],
            width=BUTTON_WIDTH,
            toggle_on_click=config.get("momentary_seconds") is None,
            momentary_seconds=config.get("momentary_seconds"),
            status_color=BUTTON_STATUS_OFF_COLOR,
            enabled=config.get("enabled", True),
            status_text = get_status_text(latest_server, config.get("status_value")),
            internal_status_value = get_internal_status_value(latest_server, config.get("status_field")),
        )
        config["button"] = button

        def send(clicked_button: Button, clicked_id: str = button_id) -> None:
            _handle_button_click(client, clicked_id, clicked_button)

        button.on_click = send

    return tuple(config["button"] for config in GSE2V1_COMMAND_BUTTONS.values())



