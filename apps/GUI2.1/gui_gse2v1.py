from __future__ import annotations

import socket
import time
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
GSE2V1_CMD_HOST = "127.0.0.1"
GSE2V1_CMD_PORT = 8827
GSE2V1_CMD_RETRY_SECONDS = 1.0
GSE2V1_CMD_CONNECT_TIMEOUT_SECONDS = 0.02
GSE2V1_ECHO_STALE_SECONDS = 2.0
GSE2V1_ECHO_GRACE_SECONDS = 0.5


_COMMAND_INDEX = {name: index for index, name in enumerate(GSE2V1_COMMAND_FIELD_NAMES)}


class GseCommandClient:
    def __init__(self) -> None:
        self.row = [0.0] * GSE2V1_NUM_COMMAND_SIGNALS
        self.schema = pa.schema([(name, pa.float64()) for name in GSE2V1_COMMAND_FIELD_NAMES])
        self.descriptor = flight.FlightDescriptor.for_path("gse2v1_commands")
        self.writer = None
        self._next_connect_attempt_at = 0.0
        self._last_echo_generation = -1
        self._last_echo_connected: bool | None = None
        self._ignore_echo_until = 0.0
        self._pending_row: list[float] | None = None
        self._pending_until = 0.0

    def _mark_sent(self) -> None:
        now = time.monotonic()
        self._ignore_echo_until = now + GSE2V1_ECHO_GRACE_SECONDS
        self._pending_row = list(self.row)
        self._pending_until = now + GSE2V1_ECHO_STALE_SECONDS

    def _echo_matches_pending(self, echo: dict) -> bool:
        """Whether the board has acknowledged every field of the pending row.

        Once it has, the board is authoritative again and the echo resumes
        driving the buttons.
        """
        for name, index in _COMMAND_INDEX.items():
            commanded = self._pending_row[index] > 0.5
            if commanded != (float(echo.get(name, 0.0)) > 0.5):
                return False
        return True

    def awaiting_echo(self, echo: dict, command_fields: tuple[str, ...]) -> bool:
        """
        Whether the board has yet to acknowledge what we last commanded.
        """
        if self._pending_row is None or not command_fields:
            return False
        if time.monotonic() > self._pending_until or self._echo_matches_pending(echo):
            self._pending_row = None
            return False
        for field in command_fields:
            index = _COMMAND_INDEX.get(field)
            if index is None:
                continue
            commanded = self._pending_row[index] > 0.5
            acknowledged = float(echo.get(field, 0.0)) > 0.5
            if commanded != acknowledged:
                return True
        return False

    def send(self) -> bool:
        batch = pa.RecordBatch.from_arrays(
            [pa.array([value], type=pa.float64()) for value in self.row],
            schema=self.schema,
        )

        had_writer = self.writer is not None
        if self._write_batch(batch):
            self._mark_sent()
            return True

        if not had_writer:
            return False

        self.writer = None
        self._next_connect_attempt_at = 0.0
        if self._write_batch(batch):
            self._mark_sent()
            return True

        return False

    def _write_batch(self, batch: pa.RecordBatch) -> bool:
        now = time.monotonic()
        if self.writer is None:
            if now < self._next_connect_attempt_at:
                return False
            if not self._is_command_server_listening():
                print(f"[GSE2V1 CMD] connector unavailable on {GSE2V1_CMD_HOST}:{GSE2V1_CMD_PORT}")
                self._next_connect_attempt_at = now + GSE2V1_CMD_RETRY_SECONDS
                return False
        try:
            if self.writer is None:
                client = flight.connect(f"grpc://{GSE2V1_CMD_HOST}:{GSE2V1_CMD_PORT}")
                self.writer, _ = client.do_put(self.descriptor, self.schema)
            self.writer.write_batch(batch)
        except Exception as e:
            print(f"[GSE2V1 CMD] send failed: {type(e).__name__}")
            self.writer = None
            self._next_connect_attempt_at = now + GSE2V1_CMD_RETRY_SECONDS
            return False
        return True

    def _is_command_server_listening(self) -> bool:
        try:
            with socket.create_connection(
                (GSE2V1_CMD_HOST, GSE2V1_CMD_PORT),
                timeout=GSE2V1_CMD_CONNECT_TIMEOUT_SECONDS,
            ):
                return True
        except OSError:
            return False


def abort_is_inactive() -> bool:
    return not _button_state(ABORT_BUTTON_ID)


def _sync_command_fields_to_client_row(
    client: GseCommandClient,
    button_id: str,
    command_fields: tuple[str, ...],
) -> None:
    button = GSE2V1_COMMAND_BUTTONS[button_id].get("button")
    if not isinstance(button, Button):
        return
    value = 1.0 if button.state else 0.0
    for command_field in command_fields:
        index = GSE2V1_COMMAND_FIELD_NAMES.index(command_field)
        client.row[index] = value




def default_sync_button_to_client_row(client: GseCommandClient, button_id: str) -> None: 
    command_fields = GSE2V1_COMMAND_BUTTONS[button_id].get("command_field")
    if not command_fields:
        return
    button = GSE2V1_COMMAND_BUTTONS[button_id].get("button")
    if not isinstance(button, Button):
        return
    value = 1.0 if button.state else 0.0
    for command_field in command_fields:
        index = GSE2V1_COMMAND_FIELD_NAMES.index(command_field)
        client.row[index] = value * button.scale


def _sync_sol_gn2_fill_1_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "sol_gn2_fill_1", ("solenoidState1",))


def _sync_sol_gn2_fill_2_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "sol_gn2_fill_2", ("solenoidState2",))


def _sync_sol_gn2_fill_3_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "sol_gn2_fill_3", ("solenoidState3",))


def _sync_sol_gn2_fill_4_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "sol_gn2_fill_4", ("solenoidState7",))


def _sync_sol_gn2_vent_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "sol_gn2_vent", ("solenoidState0",))


def _sync_copv_vent_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "copv_vent", ("solenoidState9",))


def _sync_pv1_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "pv1", ("solenoidState10",))


def _sync_pv2_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "pv2", ("solenoidState11",))


def _sync_tank_vent_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "tank_vent", ("solenoidState8",))


def _sync_mvas_open_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(
        client,
        "mvas_open",
        ("solenoidState4", "solenoidState5"),
    )

def _sync_open_mvas_lox_to_client_row(client: GseCommandClient) -> None: 
    _sync_command_fields_to_client_row(client, "open_mvas_lox", ("solenoidState4",))

def _sync_open_mvas_lng_to_client_row(client: GseCommandClient) -> None: 
    _sync_command_fields_to_client_row(client, "open_mvas_lng", ("solenoidState5",))


def _sync_mvas_close_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "mvas_close", ("solenoidState6",))


def _sync_igniter_0_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "igniter_0", ("igniter0Fire",))


def _sync_igniter_1_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "igniter_1", ("igniter1Fire",))


def _sync_alarm_to_client_row(client: GseCommandClient) -> None:
    _sync_command_fields_to_client_row(client, "alarm", ("alarm",))


def _sync_noop_to_client_row(client: GseCommandClient) -> None:
    return

GSE2V1_COMMAND_BUTTONS: dict[str, dict[str, Any]] = {
    "sol_gn2_fill_1": {
        "display_name": "GN2 Fill 1",
        "command_field": "solenoidState1",
        "status_field": "solenoidInternalState1",
        "status_value": "solenoidCurrent1",
        "sync_button_to_client_row": _sync_sol_gn2_fill_1_to_client_row,

        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_2": {
        "display_name": "GN2 Fill 2",
        "command_field": "solenoidState2",
        "status_field": "solenoidInternalState2",
        "status_value": "solenoidCurrent2",
        "sync_button_to_client_row": _sync_sol_gn2_fill_2_to_client_row,
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_3": {
        "display_name": "GN2 Fill 3",
        "command_field": "solenoidState3",
        "status_field": "solenoidInternalState3",
        "status_value": "solenoidCurrent3",
        "sync_button_to_client_row": _sync_sol_gn2_fill_3_to_client_row,
        "enabled": abort_is_inactive,
    },
    "sol_gn2_fill_4": {
        "display_name": "GN2 Fill 4",
        "command_field": "solenoidState7",
        "status_field": "solenoidInternalState7",
        "status_value": "solenoidCurrent7",
        "sync_button_to_client_row": _sync_sol_gn2_fill_4_to_client_row,
        "enabled": abort_is_inactive,
    },
    "sol_gn2_vent": {
        "display_name": "GN2 Vent",
        "command_field": "solenoidState0",
        "status_field": "solenoidInternalState0",
        "status_value": "solenoidCurrent0",
        "sync_button_to_client_row": _sync_sol_gn2_vent_to_client_row,
        "enabled": abort_is_inactive,
    },
    "copv_vent": {
        "display_name": "COPV Vent",
        "command_field": "solenoidState9",
        "status_field": "solenoidInternalState9",
        "status_value": "solenoidCurrent9",
        "sync_button_to_client_row": _sync_copv_vent_to_client_row,
        "enabled": abort_is_inactive,
    },
    "pv1": {
        "display_name": "PV1",
        "command_field": "solenoidState10",
        "status_field": "solenoidInternalState10",
        "status_value": "solenoidCurrent10",
        "sync_button_to_client_row": _sync_pv1_to_client_row,
        "enabled": abort_is_inactive,
    },
    "pv2": {
        "display_name": "PV2",
        "command_field": "solenoidState11",
        "status_field": "solenoidInternalState11",
        "status_value": "solenoidCurrent11",
        "sync_button_to_client_row": _sync_pv2_to_client_row,
        "enabled": abort_is_inactive,
    },
    "tank_vent": {
        "display_name": "Tank Vent",
        "command_field": "solenoidState8",
        "status_field": "solenoidInternalState8",
        "status_value": "solenoidCurrent8",
        "sync_button_to_client_row": _sync_tank_vent_to_client_row,
        "enabled": abort_is_inactive,
    },
    "mvas_open": {
        "display_name": "Mvas Open",
        "command_field": ["solenoidState4", "solenoidState5"],
        "status_field": ["solenoidInternalState4", "solenoidInternalState5"],
        "status_value": ["solenoidCurrent4", "solenoidCurrent5"],
        "sync_button_to_client_row": _sync_mvas_open_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": 1.0,
    },
    "open_mvas_lox": {
        "display_name": "Open Mvas Lox",
        "command_field": "solenoidState4",
        "status_field": "solenoidInternalState4",
        "status_value": "solenoidCurrent4",
        "sync_button_to_client_row": _sync_open_mvas_lox_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": 1.0,
    },
    "open_mvas_lng": {
        "display_name": "Open Mvas Lng",
        "command_field": "solenoidState5",
        "status_field": "solenoidInternalState5",
        "status_value": "solenoidCurrent5",
        "sync_button_to_client_row": _sync_open_mvas_lng_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": 1.0,
    },
    "mvas_close": {
        "display_name": "Mvas Close",
        "command_field": "solenoidState6",
        "status_field": "solenoidInternalState6",
        "status_value": "solenoidCurrent6",
        "sync_button_to_client_row": _sync_mvas_close_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": 1.0,
    },
    "igniter_0": {
        "display_name": "Igniter 0",
        "command_field": "igniter0Fire",
        "status_field": "igniterInternalState0",
        "status_value": "igniter0Continuity",
        "sync_button_to_client_row": _sync_igniter_0_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "igniter_1": {
        "display_name": "Igniter 1",
        "command_field": "igniter1Fire",
        "status_field": "igniterInternalState1",
        "status_value": "igniter1Continuity",
        "sync_button_to_client_row": _sync_igniter_1_to_client_row,
        "enabled": abort_is_inactive,
        "momentary_seconds": IGNITER_MOMENTARY_SECONDS,
    },
    "alarm": {
        "display_name": "Alarm",
        "command_field": "alarm",
        "status_field": "alarmInternalState",
        "status_value": "alarmCurrent",
        "sync_button_to_client_row": _sync_alarm_to_client_row,
        "enabled": True,
    },
    f"{ABORT_BUTTON_ID}": {
        "display_name": "Abort",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": True,
    },
    "all_off_t12_t13_t16": {
        "display_name": "All Off (T12, T13, T16)",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
    },
    "purge_1_t17": {
        "display_name": "Purge #1 (T17)",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
    },
    "purge_2_t18": {
        "display_name": "Purge #2 (T18)",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
    },
    "t19": {
        "display_name": "T19",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
    },
    "t20": {
        "display_name": "T20",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
    },
    "final_t23": {
        "display_name": "Final (T23)",
        "command_field": None,
        "sync_button_to_client_row": _sync_noop_to_client_row,
        "enabled": abort_is_inactive,
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

def _field_names(field_name: str | list[str] | None) -> tuple[str, ...]:
    if field_name is None:
        return ()
    if isinstance(field_name, str):
        return (field_name,)
    return tuple(field_name)


def get_status_text(latest_server: LatestServer, status_value_field: str | list[str] | None):
    def get_status_text_for_button(): 
        status_value_fields = _field_names(status_value_field)
        if not status_value_fields:
            return "NAN"
        values = []
        for field in status_value_fields:
            try:
                value = latest_server.latest[field]
            except KeyError:
                return "NAN"
            except Exception as e:
                return "NAN"
            if value is None:
                return "NAN" # No data
            values.append(value)
        if len(values) == 1:
            return f"{values[0]:.3f}"
        return "/".join(f"{value:.3f}" for value in values)
    return get_status_text_for_button




def get_internal_status_value(latest_server: LatestServer, internal_status_field: str | list[str] | None):
    def get_internal_status_value_for_button(): 
        internal_status_fields = _field_names(internal_status_field)
        if not internal_status_fields:
            return NAN
        values = []
        for field in internal_status_fields:
            try:
                value = latest_server.latest[field]
            except KeyError:
                return NAN
            except Exception as e:
                return NAN
            if value is None:
                return NAN # No data
            values.append(value)
        if len(values) == 1:
            return values[0]
        return all(value > 0.0 for value in values)

    return get_internal_status_value_for_button 

def _handle_button_click(
    client: GseCommandClient,
    button_id: str,
    button: Button,
) -> None:
    _sync_button_to_client_row(client, button_id)
    _sync_button_status(button)

    if button_id in TABLE_BUTTONS.keys() and button.state:
        _apply_table_state(client, TABLE_BUTTONS[button_id]["table_states"])

    client.send()

def _apply_table_state(client: GseCommandClient, table: dict[str, bool]) -> None:
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        if button_id in TABLE_BUTTONS.keys() or button_id == "alarm":
            continue
        button = config.get("button")
        if not isinstance(button, Button):
            continue
        button.set_state(table.get(button_id, False))
        if button_id in table:
            table_state = table[button_id]
            button.set_state(table_state)
            if button.momentary_seconds is not None:
                button.momentary_until = (
                    time.monotonic() + button.momentary_seconds
                    if table_state
                    else None
                )
        _sync_button_status(button)
        _sync_button_to_client_row(client, button_id)


def _sync_button_to_client_row(client: GseCommandClient, button_id: str) -> None:
    config = GSE2V1_COMMAND_BUTTONS[button_id]
    sync_button_to_client_row = config.get("sync_button_to_client_row")
    if not callable(sync_button_to_client_row):
        return
    sync_button_to_client_row(client)


def sync_gse2v1_command_buttons_from_echo(
    client: GseCommandClient,
    echo_server: LatestServer,
) -> None:
    latest = echo_server.latest
    connected = (
        latest is not None
        and echo_server.is_fresh(GSE2V1_ECHO_STALE_SECONDS)
        and float(latest.get("connected", 0.0)) > 0.5
    )

    if (
        connected == client._last_echo_connected
        and echo_server.latest_generation == client._last_echo_generation
    ):
        return

    client._last_echo_connected = connected
    client._last_echo_generation = echo_server.latest_generation
    if connected:
        client._next_connect_attempt_at = 0.0

    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        button = config.get("button")
        if not isinstance(button, Button):
            continue

        button.set_enabled(config.get("enabled", True) if connected else False)
        if button_id in TABLE_BUTTONS.keys():
            continue

        if connected and (
            button.momentary_until is not None
            or time.monotonic() < client._ignore_echo_until
        ):
            continue

        command_fields = _field_names(config.get("command_field"))
        if connected and client.awaiting_echo(latest, command_fields):
            continue  # the board has not acknowledged our command yet
        state = (
            all(float(latest.get(field, 0.0)) > 0.5 for field in command_fields)
            if connected and command_fields
            else False
        )
        button.set_state(state)
        button.momentary_until = None
        _sync_button_status(button)
        _sync_button_to_client_row(client, button_id)

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
