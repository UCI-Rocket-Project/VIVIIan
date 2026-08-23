"""Named valve configurations.

The tables already exist in ``gui_gse2v1.TABLE_BUTTONS`` and are what the
operator's table buttons apply. This module reads them rather than restating
them, so buttons and operations can never drift apart, and expands each one so
every valve is spelled out explicitly.

Valve polarity, from ``frontendv2.make_valve_states``: ``pv2`` and ``tank_vent``
are normally open, so solenoid ``False`` means the valve is OPEN. In procedure
terms:

    close LOX VV & LNG VV  ->  tank_vent = True
    close PV 2             ->  pv2       = True
    open  PV 1             ->  pv1       = True
    open  GN2 Fill 1       ->  sol_gn2_fill_1 = True
"""
from __future__ import annotations

from gui_gse2v1 import TABLE_BUTTONS
from state_machine import DONT_CARE, ValveState

# Steady-state valves. Momentary commands (mvas_open/mvas_close and the
# per-tank MVAS opens) are excluded on purpose: they pulse for a second and
# release.
VALVE_IDS: tuple[str, ...] = (
    "sol_gn2_fill_1",
    "sol_gn2_fill_2",
    "sol_gn2_fill_3",
    "sol_gn2_fill_4",
    "sol_gn2_vent",
    "copv_vent",
    "pv1",
    "pv2",
    "tank_vent",
)

# MVAS is the Main Valve Actuation System: the pneumatic actuators that turn the
# LOX and LNG main ball valves feeding the injector. The actuators are
# double-acting, so they hold the last commanded position rather than failing
# closed mid-burn, which is why open and close are separate momentary commands
# instead of one toggle.
MOMENTARY_IDS: tuple[str, ...] = (
    "mvas_open",
    "mvas_close",
    "open_mvas_lox",
    "open_mvas_lng",
)

# Human-readable names, for the procedure text in the GUI.
DISPLAY_NAMES: dict[str, str] = {
    "sol_gn2_fill_1": "GN2 Fill 1",
    "sol_gn2_fill_2": "GN2 Fill 2",
    "sol_gn2_fill_3": "GN2 Fill 3",
    "sol_gn2_fill_4": "GN2 Fill 4",
    "sol_gn2_vent": "GN2 VV",
    "copv_vent": "COPV VV",
    "pv1": "PV 1",
    "pv2": "PV 2",
    "tank_vent": "LOX VV & LNG VV",
    "mvas_open": "MVAS",
}


def expand(table_name: str) -> dict[str, bool]:
    """A named table from ``TABLE_BUTTONS`` with every valve spelled out."""
    try:
        states = TABLE_BUTTONS[table_name]["table_states"]
    except KeyError:
        raise KeyError(f"unknown valve table {table_name!r}") from None
    return {valve_id: bool(states.get(valve_id, False)) for valve_id in VALVE_IDS}


def valves(
    base: dict[str, ValveState] | None = None,
    **overrides: ValveState,
) -> dict[str, ValveState]:
    """A configuration derived from ALL OFF (or *base*) with named changes.

    ``valves(sol_gn2_fill_1=True)`` reads the way the procedure does, and
    ``valves(pv1=True, sol_gn2_vent=DONT_CARE)`` says the procedure pins PV 1
    and says nothing about the GN2 vent.
    """
    config: dict[str, ValveState] = dict(ALL_OFF if base is None else base)
    for valve_id, state in overrides.items():
        if valve_id not in VALVE_IDS:
            raise KeyError(f"{valve_id!r} is not a steady-state valve")
        config[valve_id] = state if state is DONT_CARE else bool(state)
    return config


def unchecked(base: dict[str, ValveState], *valve_ids: str) -> dict[str, ValveState]:
    """*base* with the named valves demoted to ``DONT_CARE``."""
    config = dict(base)
    for valve_id in valve_ids:
        if valve_id not in VALVE_IDS:
            raise KeyError(f"{valve_id!r} is not a steady-state valve")
        config[valve_id] = DONT_CARE
    return config


def describe(config: dict[str, ValveState]) -> str:
    """'GN2 Fill 1, PV 1' — the valves this configuration holds open.

    An unchecked valve is shown with a trailing '?', since we do not know.
    """
    parts: list[str] = []
    for valve_id, state in config.items():
        display_name = DISPLAY_NAMES.get(valve_id, valve_id)
        if state is DONT_CARE:
            parts.append(f"{display_name}?")
            continue
        # tank_vent and pv2 are normally open. False is the open position.
        if state != (valve_id in ("pv2", "tank_vent")):
            parts.append(display_name)
    return ", ".join(parts) if parts else "all closed"


# --- The tables the operator already has -----------------------------------

# Procedure Tables 12/13/16. Every solenoid de-energised, which leaves the tank
# vents and PV 2 open, since those two are normally open.
ALL_OFF: dict[str, bool] = expand("all_off_t12_t13_t16")

# Procedure Table 9, and what panic applies. It differs from ALL OFF by opening
# the GN2 and COPV vents, closing MVAS and sounding the alarm; the tank vents
# are open either way. docs/state-machine.md calls ALL OFF "effectively the safe
# state", but these are not the same table @TODO ask prop if this is ok
ABORT: dict[str, bool] = expand("abort")

PURGE_1: dict[str, bool] = expand("purge_1_t17")
PURGE_2: dict[str, bool] = expand("purge_2_t18")
GN2_FILL_1_OPEN: dict[str, bool] = expand("t19")
FINAL: dict[str, bool] = expand("final_t23")

TABLES: dict[str, dict[str, bool]] = {
    "all_off": ALL_OFF,
    "abort": ABORT,
    "purge_1": PURGE_1,
    "purge_2": PURGE_2,
    "gn2_fill_1_open": GN2_FILL_1_OPEN,
    "final": FINAL,
}
