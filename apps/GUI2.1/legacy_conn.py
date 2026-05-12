"""
Reference copy of GSE / ECU / EXTR_ECU / load-cell binary layouts as used by
rocket2-webservice-gui (webservice/server.py, webservice/helpers.py,
webservice/constants.py).

- GSE, ECU, EXTR_ECU: last 4 bytes of each received frame are little-endian
  CRC32 over the preceding bytes (same check as server).
- Load cell: 8-byte frame, no CRC in this codebase.

EXTR_ECU uses the same byte layout as ECU; only field names differ.
No command packet for EXTR_ECU or load cell is defined in this webservice.
"""

from __future__ import annotations

import binascii
import struct
from typing import Final, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Lengths (bytes) — must match server recv() size
# ---------------------------------------------------------------------------

GSE_DATA_LENGTH: Final[int] = 91  # payload + CRC
ECU_DATA_LENGTH: Final[int] = 144  # payload + CRC (same for EXTR_ECU)
LOAD_CELL_DATA_LENGTH: Final[int] = 8

# ---------------------------------------------------------------------------
# struct format strings (Python struct module, little-endian "<")
# ---------------------------------------------------------------------------

# Received: unpack on raw_data[:-4] for GSE/ECU/EXTR_ECU (strip CRC first).
GSE_RECV_FORMAT: Final[str] = "<L???????????????fffffffffffffffff"
ECU_RECV_FORMAT: Final[str] = "<Lff????fffffffffffffffffffffffffffffff"
EXTR_ECU_RECV_FORMAT: Final[str] = ECU_RECV_FORMAT  # identical binary layout

# Received: unpack on full buffer (no CRC).
LOAD_CELL_RECV_FORMAT: Final[str] = "<Ll"  # uint32 + int32 (C long, 4 bytes typical)

# Commands (webservice -> device): body + little-endian CRC32(body).
GSE_CMD_BODY_FORMAT: Final[str] = "<?????????????"  # 13 x bool
ECU_CMD_BODY_FORMAT: Final[str] = "<????"  # 4 x bool


def gse_cmd_pack(
    igniter0: bool,
    igniter1: bool,
    alarm: bool,
    sol_gn2_fill: bool,
    sol_gn2_vent: bool,
    sol_gn2_disconnect: bool,
    sol_mvas_fill: bool,
    sol_mvas_vent: bool,
    sol_mvas_open: bool,
    sol_mvas_close: bool,
    sol_lox_vent: bool,
    sol_lng_vent: bool,
) -> bytes:
    body = struct.pack(
        GSE_CMD_BODY_FORMAT,
        igniter0,
        igniter1,
        alarm,
        sol_gn2_fill,
        sol_gn2_vent,
        sol_gn2_disconnect,
        sol_mvas_fill,
        sol_mvas_vent,
        sol_mvas_open,
        sol_mvas_close,
        sol_lox_vent,
        sol_lng_vent,
    )
    return body + struct.pack("<L", binascii.crc32(body) & 0xFFFFFFFF)


def ecu_cmd_pack(
    sol_copv_vent: bool,
    sol_pv1: bool,
    sol_pv2: bool,
    sol_vent: bool,
) -> bytes:
    body = struct.pack(
        ECU_CMD_BODY_FORMAT,
        sol_copv_vent,
        sol_pv1,
        sol_pv2,
        sol_vent,
    )
    return body + struct.pack("<L", binascii.crc32(body) & 0xFFFFFFFF)


def gse_recv_unpack(raw_91: bytes) -> Tuple[Sequence, int]:
    if len(raw_91) != GSE_DATA_LENGTH:
        raise ValueError(f"GSE frame must be {GSE_DATA_LENGTH} bytes, got {len(raw_91)}")
    payload, crc_le = raw_91[:-4], raw_91[-4:]
    crc_calc = binascii.crc32(payload) & 0xFFFFFFFF
    crc_wire = struct.unpack("<L", crc_le)[0]
    if crc_calc != crc_wire:
        raise ValueError("GSE CRC mismatch")
    return struct.unpack(GSE_RECV_FORMAT, payload), crc_wire


def ecu_recv_unpack(raw_144: bytes) -> Tuple[Sequence, int]:
    if len(raw_144) != ECU_DATA_LENGTH:
        raise ValueError(f"ECU frame must be {ECU_DATA_LENGTH} bytes, got {len(raw_144)}")
    payload, crc_le = raw_144[:-4], raw_144[-4:]
    crc_calc = binascii.crc32(payload) & 0xFFFFFFFF
    crc_wire = struct.unpack("<L", crc_le)[0]
    if crc_calc != crc_wire:
        raise ValueError("ECU CRC mismatch")
    return struct.unpack(ECU_RECV_FORMAT, payload), crc_wire


def extr_ecu_recv_unpack(raw_144: bytes) -> Tuple[Sequence, int]:
    """Same bytes as ECU; use EXTR_ECU_FIELD_NAMES for semantics."""
    return ecu_recv_unpack(raw_144)


def load_cell_recv_unpack(raw_8: bytes) -> Tuple[int, int]:
    if len(raw_8) != LOAD_CELL_DATA_LENGTH:
        raise ValueError(
            f"Load cell frame must be {LOAD_CELL_DATA_LENGTH} bytes, got {len(raw_8)}"
        )
    return struct.unpack(LOAD_CELL_RECV_FORMAT, raw_8)


# ---------------------------------------------------------------------------
# Field names (same order as struct.unpack tuple)
# ---------------------------------------------------------------------------

GSE_FIELD_NAMES: Final[List[str]] = [
    "packet_time",
    "igniterArmed",
    "igniterCurrent0",
    "igniterCurrent1",
    "igniterInternalState0",
    "igniterInternalState1",
    "alarmInternalState",
    "solenoidInternalStateGn2Fill",
    "solenoidInternalStateGn2Vent",
    "solenoidInternalStateGn2Disconnect",
    "solenoidInternalStateMvasFill",
    "solenoidInternalStateMvasVent",
    "solenoidInternalStateMvasOpen",
    "solenoidInternalStateMvasClose",
    "solenoidInternalStateLoxVent",
    "solenoidInternalStateLngVent",
    "supplyVoltage0",
    "supplyVoltage1",
    "solenoidCurrentGn2Fill",
    "solenoidCurrentGn2Vent",
    "solenoidCurrentGn2Disconnect",
    "solenoidCurrentMvasFill",
    "solenoidCurrentMvasVent",
    "solenoidCurrentMvasOpen",
    "solenoidCurrentMvasClose",
    "solenoidCurrentLoxVent",
    "solenoidCurrentLngVent",
    "temperatureEngine1",
    "temperatureEngine2",
    "pressureGn2",
    "pressureLoxInjTee",
    "pressureVent",
    "pressureLoxMvas",
]

ECU_FIELD_NAMES: Final[List[str]] = [
    "packet_time",
    "packetRssi",
    "packetLoss",
    "solenoidInternalStateCopvVent",
    "solenoidInternalStatePv1",
    "solenoidInternalStatePv2",
    "solenoidInternalStateVent",
    "supplyVoltage",
    "batteryVoltage",
    "solenoidCurrentCopvVent",
    "solenoidCurrentPv1",
    "solenoidCurrentPv2",
    "solenoidCurrentVent",
    "temperatureCopv",
    "pressureCopv",
    "pressureLox",
    "pressureLng",
    "pressureInjectorLox",
    "pressureInjectorLng",
    "angularVelocityX",
    "angularVelocityY",
    "angularVelocityZ",
    "accelerationX",
    "accelerationY",
    "accelerationZ",
    "magneticFieldX",
    "magneticFieldY",
    "magneticFieldZ",
    "temperature",
    "altitude",
    "ecefPositionX",
    "ecefPositionY",
    "ecefPositionZ",
    "ecefPositionAccuracy",
    "ecefVelocityX",
    "ecefVelocityY",
    "ecefVelocityZ",
    "ecefVelocityAccuracy",
]

EXTR_ECU_FIELD_NAMES: Final[List[str]] = [
    "packet_time",
    "packetRssi",
    "packetLoss",
    "_solenoidInternalStateCopvVent",
    "_solenoidInternalStatePv1",
    "_solenoidInternalStatePv2",
    "_solenoidInternalStateVent",
    "_supplyVoltage",
    "_batteryVoltage",
    "_solenoidCurrentCopvVent",
    "_solenoidCurrentPv1",
    "_solenoidCurrentPv2",
    "_solenoidCurrentVent",
    "_temperatureCopv",
    "pressureOne",
    "pressureTwo",
    "pressureThree",
    "pressureFour",
    "pressureFive",
    "_angularVelocityX",
    "_angularVelocityY",
    "_angularVelocityZ",
    "_accelerationX",
    "_accelerationY",
    "_accelerationZ",
    "_magneticFieldX",
    "_magneticFieldY",
    "_magneticFieldZ",
    "_temperature",
    "_altitude",
    "_ecefPositionX",
    "_ecefPositionY",
    "_ecefPositionZ",
    "_ecefPositionAccuracy",
    "_ecefVelocityX",
    "_ecefVelocityY",
    "_ecefVelocityZ",
    "_ecefVelocityAccuracy",
]

LOAD_CELL_FIELD_NAMES: Final[List[str]] = ["packet_time", "total_force"]


def tuple_as_dict(names: Sequence[str], values: Sequence) -> dict:
    return dict(zip(names, values))