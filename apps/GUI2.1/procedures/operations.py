"""
Reusable operations and adapters that let the engine drive valves.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Callable

import gui_gse2v1
from gui_elements import Button
from gui_gse2v1 import GSE2V1_COMMAND_BUTTONS, GseCommandClient, abort_is_inactive
from state_machine import (
    Action,
    ApplyTable,
    ControlContext,
    DEFAULT_LEAD_TIME_SECONDS,
    Dwell,
    Operation,
    PANIC_PRIORITY,
    Pulse,
    SetValve,
)

from . import table_states

# A table command moves several valves at once, so it gets longer to settle
# than a single-valve move does.
TABLE_LEAD_TIME_SECONDS = 1.5


# Adapters


def _button(button_id: str) -> Button | None:
    button = GSE2V1_COMMAND_BUTTONS.get(button_id, {}).get("button")
    return button if isinstance(button, Button) else None


class GseEffector:
    """Stages valve commands on the GSE2V1 buttons; one sent per control cycle."""

    def __init__(self, client: GseCommandClient) -> None:
        self.client = client
        self._dirty = False

    def stage_button(self, button_id: str, state: bool) -> None:
        button = _button(button_id)
        if button is None:
            print(f"[operations] no such button {button_id!r}")
            return
        button.set_state(bool(state))
        if button.momentary_seconds is not None:
            button.momentary_until = (
                time.monotonic() + button.momentary_seconds if state else None
            )
        gui_gse2v1._sync_button_status(button)
        gui_gse2v1._sync_button_to_client_row(self.client, button_id)
        self._dirty = True

    def stage_table(self, states: Mapping[str, bool]) -> None:
        gui_gse2v1._apply_table_state(self.client, dict(states))
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def flush(self) -> bool:
        sent = self.client.send()
        self._dirty = False
        return sent

    def abort_active(self) -> bool:
        return not abort_is_inactive()


class GseValveMap:
    """Valve identity, so the engine needn't know the GSE2V1 button config."""

    def button_ids(self) -> tuple[str, ...]:
        return tuple(GSE2V1_COMMAND_BUTTONS)

    def commanded(self, button_id: str) -> bool:
        button = _button(button_id)
        return bool(button.state) if button is not None else False

    def status_fields(self, button_id: str) -> tuple[str, ...]:
        config = GSE2V1_COMMAND_BUTTONS.get(button_id, {})
        return gui_gse2v1._field_names(config.get("status_field"))

    def display_name(self, button_id: str) -> str:
        return GSE2V1_COMMAND_BUTTONS.get(button_id, {}).get("display_name", button_id)


def attach_manual_listener(dispatcher) -> None:
    """
    Tell the dispatcher when the operator clicks a raw valve button.
    """
    for button_id, config in GSE2V1_COMMAND_BUTTONS.items():
        button = config.get("button")
        if not isinstance(button, Button):
            continue
        inner = button.on_click

        def wrapped(clicked: Button, _inner=inner, _id=button_id) -> None:
            if _inner is not None:
                _inner(clicked)
            if clicked.momentary_seconds is not None and not clicked.state:
                return  # momentary release, not a click
            dispatcher.note_manual_command(_id)

        button.on_click = wrapped


# Action factories


def open_valve(button_id: str, *, confirm: bool = False) -> SetValve:
    """Open a valve. Handles normally-open polarity for PV 2 and the tank vents."""
    return SetValve(button_id, _energized_for(button_id, want_open=True), confirm=confirm)


def close_valve(button_id: str, *, confirm: bool = False) -> SetValve:
    return SetValve(button_id, _energized_for(button_id, want_open=False), confirm=confirm)


def _energized_for(button_id: str, *, want_open: bool) -> bool:
    normally_open = button_id in ("pv2", "tank_vent")
    return (not want_open) if normally_open else want_open


def apply_table(name: str) -> ApplyTable:
    return ApplyTable(name, table_states.TABLES[name])


def pulse(button_id: str) -> Pulse:
    return Pulse(button_id)


def countdown(seconds: float, label: str) -> Dwell:
    return Dwell(seconds, label=label)


# Operation factories


def manual_gate(
    label: str,
    dest: str,
    *,
    description: str = "",
    lead_time_s: float = DEFAULT_LEAD_TIME_SECONDS,
) -> Operation:
    """A human step: the machine holds until the operator confirms it happened.

    Uses the procedure's own wording, so the state machine doubles as the
    checklist the test director is reading from.

    A gate moves no valves, but the destination check still runs, confirming
    the system is where the procedure claims it is before the next step is unlocked.
    """
    return Operation(
        actions=(),
        dest_state=dest,
        auto=False,
        name=label,
        description=description,
        lead_time_s=lead_time_s,
    )


def manual_operation(
    label: str,
    dest: str,
    actions: Sequence[Action],
    *,
    description: str = "",
    requires_captcha: bool = False,
    timeout_s: float | None = 30.0,
    lead_time_s: float = DEFAULT_LEAD_TIME_SECONDS,
    verify_dest: bool = True,
) -> Operation:
    """An operator-commanded valve move."""
    return Operation(
        actions=tuple(actions),
        dest_state=dest,
        auto=False,
        name=label,
        description=description,
        requires_captcha=requires_captcha,
        timeout_s=timeout_s,
        lead_time_s=lead_time_s,
        verify_dest=verify_dest,
    )


def auto_operation(
    label: str,
    dest: str,
    actions: Sequence[Action],
    guard: Callable[[ControlContext], bool],
    *,
    guard_text: str = "",
    priority: int = 0,
    description: str = "",
    timeout_s: float | None = 30.0,
    mutually_exclusive_with: Sequence[str] = (),
    lead_time_s: float = DEFAULT_LEAD_TIME_SECONDS,
    verify_dest: bool = True,
) -> Operation:
    """A sensor-driven transition. ``guard_text`` is shown live in the GUI."""
    return Operation(
        actions=tuple(actions),
        dest_state=dest,
        auto=True,
        name=label,
        guard=guard,
        guard_text=guard_text,
        priority=priority,
        description=description,
        timeout_s=timeout_s,
        mutually_exclusive_with=tuple(mutually_exclusive_with),
        lead_time_s=lead_time_s,
        verify_dest=verify_dest,
    )


def panic_operation(dest: str, *, label: str = "PANIC — abort configuration") -> Operation:
    """Per-state safe-out: the abort table, which opens the vents and closes MVAS.

    The alarm is staged separately because ``_apply_table_state`` skips it, so
    the ``alarm: True`` in the abort table never actually reaches the board.
    """
    return Operation(
        actions=(apply_table("abort"), SetValve("alarm", True)),
        dest_state=dest,
        auto=False,
        name=label,
        priority=PANIC_PRIORITY,
        overrides_abort=True,
        description="Vents open, MVAS closed, alarm on.",
        lead_time_s=TABLE_LEAD_TIME_SECONDS,
        verify_dest=False,
    )
