from __future__ import annotations

from typing import Any

import pyarrow as pa
import pyarrow.flight as flight

from gse21connector import GSE2V1_COMMAND_FIELD_NAMES, GSE2V1_NUM_COMMAND_SIGNALS
from gui_elements import BUTTON_STATUS_OFF_COLOR, BUTTON_STATUS_ON_COLOR, Button


BUTTON_WIDTH = 260.0
IGNITER_MOMENTARY_SECONDS = 1.0
ABORT_BUTTON_ID = "abort"
VENT_BUTTON_IDS = (
    "sol_gn2_vent",
    "copv_vent",
    "vent",
    "lng_vent",
    "lox_vent",
)


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
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_2": {
        "display_name": "GN2 Fill 2",
        "command_field": "solenoidState1",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_3": {
        "display_name": "GN2 Fill 3",
        "command_field": "solenoidState2",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_4": {
        "display_name": "GN2 Fill 4",
        "command_field": "solenoidState3",
        "enabled": abort_is_inactive,
    },
    "sol_gn2_vent": {
        "display_name": "GN2 Vent",
        "command_field": "solenoidState4",
        "enabled": abort_is_inactive,
    },
    "copv_vent": {
        "display_name": "COPV Vent",
        "command_field": "solenoidState5",
        "enabled": abort_is_inactive,
    },
    "pv1": {
        "display_name": "PV1",
        "command_field": "solenoidState6",
        "enabled": abort_is_inactive,
    },
    "pv2": {
        "display_name": "PV2",
        "command_field": "solenoidState7",
        "enabled": abort_is_inactive,
    },
    "vent": {
        "display_name": "Vent",
        "command_field": "solenoidState8",
        "enabled": abort_is_inactive,
    },
    "lng_vent": {
        "display_name": "LNG Vent",
        "command_field": "solenoidState9",
        "enabled": abort_is_inactive,
    },
    "lox_vent": {
        "display_name": "LOX Vent",
        "command_field": "solenoidState10",
        "enabled": abort_is_inactive,
    },
    "igniter_0": {
        "display_name": "Igniter 0",
        "command_field": "igniter0Fire",
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "igniter_1": {
        "display_name": "Igniter 1",
        "command_field": "igniter1Fire",
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "alarm": {
        "display_name": "Alarm",
        "command_field": "alarm",
        "enabled": True,
    },
    "abort": {
        "display_name": "Abort",
        "command_field": None,
        "enabled": True,
    },
}


def make_gse2v1_command_buttons(client: GseCommandClient) -> tuple[Button, ...]:
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        button = Button(
            button_id,
            config["display_name"],
            width=BUTTON_WIDTH,
            toggle_on_click=config.get("momentary_seconds") is None,
            momentary_seconds=config.get("momentary_seconds"),
            status_color=BUTTON_STATUS_OFF_COLOR,
            enabled=config.get("enabled", True),
        )
        config["button"] = button

        def send(clicked_button: Button, clicked_id: str = button_id) -> None:
            _handle_button_click(client, clicked_id, clicked_button)

        button.on_click = send

    return tuple(config["button"] for config in GSE2V1_COMMAND_BUTTONS.values())


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


def _apply_abort(client: GseCommandClient) -> None:
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        if button_id == ABORT_BUTTON_ID or button_id == "alarm":
            continue
        button = config.get("button")
        if not isinstance(button, Button):
            continue
        button.set_state(button_id in VENT_BUTTON_IDS)
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


def _sync_button_status(button: Button) -> None:
    button.set_status_color(BUTTON_STATUS_ON_COLOR if button.state else BUTTON_STATUS_OFF_COLOR)


def _button_state(button_id: str) -> bool:
    button = GSE2V1_COMMAND_BUTTONS.get(button_id, {}).get("button")
    return bool(button.state) if isinstance(button, Button) else False
