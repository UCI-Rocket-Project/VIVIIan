from __future__ import annotations

import argparse
import binascii
import logging
import os
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


LOGGER = logging.getLogger("ucirpl_device_simulator")

# Rocket2-compatible packet formats (without CRC trailer).
GSE_TELEMETRY_FORMAT = "<L???????????????fffffffffffffffff"
ECU_TELEMETRY_FORMAT = "<Lff????fffffffffffffffffffffffffffffff"
LOADCELL_TELEMETRY_FORMAT = "<Ll"
GSE_COMMAND_FORMAT = "<????????????"
ECU_COMMAND_FORMAT = "<????"

GSE_TELEMETRY_LENGTH = 91
ECU_TELEMETRY_LENGTH = 144
LOADCELL_TELEMETRY_LENGTH = 8
GSE_COMMAND_LENGTH = 16
ECU_COMMAND_LENGTH = 8

# Same pressure calibrations used in rocket2 webservice (psi = scaling * volts + intercept).
PT_CALIBRATIONS = {
    "pressureGn2": (190.0, 11.9),
    "pressureVent": (190.0, 11.9),
    "pressureLox": (190.0, 11.9),
    "pressureLng": (190.0, 11.9),
    "pressureCopv": (964.0, 37.2),
    "pressureInjectorLox": (190.0, 11.9),
    "pressureInjectorLng": (190.0, 11.9),
    "pressureLoxInjTee": (190.0, 11.9),
    "pressureLoxMvas": (190.0, 11.9),
    "pressureOne": (190.0, 11.9),
    "pressureTwo": (190.0, 11.9),
    "pressureThree": (190.0, 11.9),
    "pressureFour": (190.0, 11.9),
    "pressureFive": (190.0, 11.9),
}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def pressure_to_voltage(channel_name: str, pressure_psi: float) -> float:
    scaling, intercept = PT_CALIBRATIONS[channel_name]
    return max(0.0, (pressure_psi - intercept) / scaling)


@dataclass(slots=True)
class SimulatorConfig:
    host: str = "127.0.0.1"
    gse_port: int = 10002
    ecu_port: int = 10004
    extr_ecu_port: int = 10006
    loadcell_port: int = 10069
    update_hz: float = 2000.0
    telemetry_hz: float = 1000.0
    seed: int = 42
    # When True, do not bind the local fake ECU port (device_ecu talks to hardware instead).
    skip_ecu_duplex: bool = False


@dataclass(slots=True)
class GseState:
    packet_time_ms: int = 0
    igniter0: bool = False
    igniter1: bool = False
    alarm: bool = False
    gn2_fill: bool = False
    gn2_vent: bool = False
    gn2_disconnect: bool = False
    mvas_fill: bool = False
    mvas_vent: bool = False
    mvas_open: bool = False
    mvas_close: bool = False
    lox_vent: bool = False
    lng_vent: bool = False
    gn2_pressure_psi: float = 180.0
    vent_pressure_psi: float = 30.0
    lox_inj_pressure_psi: float = 45.0
    lox_mvas_pressure_psi: float = 50.0
    temp_engine_1_c: float = 24.0
    temp_engine_2_c: float = 24.0
    supply_voltage_0_v: float = 28.0
    supply_voltage_1_v: float = 28.0


@dataclass(slots=True)
class EcuState:
    packet_time_ms: int = 0
    copv_vent: bool = False
    pv1: bool = False
    pv2: bool = False
    vent: bool = False
    packet_rssi: float = -42.0
    packet_loss: float = 0.0
    supply_voltage_v: float = 28.0
    battery_voltage_v: float = 25.0
    pressure_copv_psi: float = 2800.0
    pressure_lox_psi: float = 160.0
    pressure_lng_psi: float = 140.0
    pressure_inj_lox_psi: float = 20.0
    pressure_inj_lng_psi: float = 20.0
    temperature_copv_c: float = 25.0
    temperature_c: float = 24.0
    altitude_m: float = 0.0
    ecef_pos_x: float = 0.0
    ecef_pos_y: float = 0.0
    ecef_pos_z: float = 0.0
    ecef_pos_acc: float = 0.8
    ecef_vel_x: float = 0.0
    ecef_vel_y: float = 0.0
    ecef_vel_z: float = 0.0
    ecef_vel_acc: float = 0.6
    gyro_x: float = 0.0
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    accel_x: float = 0.0
    accel_y: float = 0.0
    accel_z: float = 9.8
    mag_x: float = 18.0
    mag_y: float = 3.0
    mag_z: float = -28.0


@dataclass(slots=True)
class ExtrEcuState:
    packet_time_ms: int = 0
    packet_rssi: float = -40.0
    packet_loss: float = 0.0
    pressure_one_psi: float = 80.0
    pressure_two_psi: float = 75.0
    pressure_three_psi: float = 70.0
    pressure_four_psi: float = 66.0
    pressure_five_psi: float = 62.0


@dataclass(slots=True)
class LoadCellState:
    packet_time_ms: int = 0
    total_force_lbf: float = 0.0


@dataclass(slots=True)
class DeviceModel:
    gse: GseState = field(default_factory=GseState)
    ecu: EcuState = field(default_factory=EcuState)
    extr_ecu: ExtrEcuState = field(default_factory=ExtrEcuState)
    loadcell: LoadCellState = field(default_factory=LoadCellState)

    def apply_gse_command(self, command_values: tuple[bool, ...]) -> None:
        (
            self.gse.igniter0,
            self.gse.igniter1,
            self.gse.alarm,
            self.gse.gn2_fill,
            self.gse.gn2_vent,
            self.gse.gn2_disconnect,
            self.gse.mvas_fill,
            self.gse.mvas_vent,
            self.gse.mvas_open,
            self.gse.mvas_close,
            self.gse.lox_vent,
            self.gse.lng_vent,
        ) = command_values

    def apply_ecu_command(self, command_values: tuple[bool, ...]) -> None:
        self.ecu.copv_vent, self.ecu.pv1, self.ecu.pv2, self.ecu.vent = command_values

    def advance(self, dt_s: float, now_ms: int, rng: random.Random) -> None:
        gse = self.gse
        ecu = self.ecu
        extr = self.extr_ecu
        loadcell = self.loadcell

        gse.packet_time_ms = now_ms
        ecu.packet_time_ms = now_ms
        extr.packet_time_ms = now_ms
        loadcell.packet_time_ms = now_ms

        # GSE dynamics.
        gse.gn2_pressure_psi += (70.0 if gse.gn2_fill else -25.0 if gse.gn2_vent else -4.0) * dt_s
        gse.gn2_pressure_psi = clamp(gse.gn2_pressure_psi, 0.0, 1200.0)
        gse.vent_pressure_psi += (-30.0 if gse.gn2_vent or gse.mvas_vent else 8.0) * dt_s
        gse.vent_pressure_psi = clamp(gse.vent_pressure_psi, 0.0, 400.0)
        gse.lox_inj_pressure_psi += (20.0 if gse.mvas_open else -10.0) * dt_s
        gse.lox_mvas_pressure_psi += (24.0 if gse.mvas_fill else -16.0 if gse.mvas_close else -2.5) * dt_s
        if gse.lox_vent:
            gse.lox_inj_pressure_psi -= 18.0 * dt_s
            gse.lox_mvas_pressure_psi -= 22.0 * dt_s
        gse.lox_inj_pressure_psi = clamp(gse.lox_inj_pressure_psi, 0.0, 600.0)
        gse.lox_mvas_pressure_psi = clamp(gse.lox_mvas_pressure_psi, 0.0, 600.0)
        gse.temp_engine_1_c = clamp(gse.temp_engine_1_c + rng.gauss(0.0, 0.08), -20.0, 250.0)
        gse.temp_engine_2_c = clamp(gse.temp_engine_2_c + rng.gauss(0.0, 0.08), -20.0, 250.0)
        gse.supply_voltage_0_v = clamp(28.0 + rng.gauss(0.0, 0.05), 26.0, 29.5)
        gse.supply_voltage_1_v = clamp(28.0 + rng.gauss(0.0, 0.05), 26.0, 29.5)

        # ECU dynamics.
        if ecu.copv_vent:
            ecu.pressure_copv_psi -= 110.0 * dt_s
        else:
            ecu.pressure_copv_psi -= 4.0 * dt_s
        ecu.pressure_copv_psi = clamp(ecu.pressure_copv_psi, 300.0, 4500.0)

        if ecu.pv1:
            ecu.pressure_lox_psi += 34.0 * dt_s
        else:
            ecu.pressure_lox_psi -= 8.0 * dt_s
        if ecu.pv2:
            ecu.pressure_lng_psi += 32.0 * dt_s
        else:
            ecu.pressure_lng_psi -= 7.5 * dt_s
        if ecu.vent:
            ecu.pressure_lox_psi -= 22.0 * dt_s
            ecu.pressure_lng_psi -= 22.0 * dt_s
            ecu.pressure_inj_lox_psi -= 14.0 * dt_s
            ecu.pressure_inj_lng_psi -= 14.0 * dt_s
        else:
            ecu.pressure_inj_lox_psi += (ecu.pressure_lox_psi - ecu.pressure_inj_lox_psi) * 0.18 * dt_s
            ecu.pressure_inj_lng_psi += (ecu.pressure_lng_psi - ecu.pressure_inj_lng_psi) * 0.18 * dt_s

        ecu.pressure_lox_psi = clamp(ecu.pressure_lox_psi, 0.0, 1200.0)
        ecu.pressure_lng_psi = clamp(ecu.pressure_lng_psi, 0.0, 1200.0)
        ecu.pressure_inj_lox_psi = clamp(ecu.pressure_inj_lox_psi, 0.0, 1000.0)
        ecu.pressure_inj_lng_psi = clamp(ecu.pressure_inj_lng_psi, 0.0, 1000.0)

        ecu.temperature_copv_c = clamp(ecu.temperature_copv_c + rng.gauss(0.0, 0.06), -30.0, 80.0)
        ecu.temperature_c = clamp(ecu.temperature_c + rng.gauss(0.0, 0.04), -40.0, 100.0)
        ecu.packet_rssi = clamp(-40.0 + rng.gauss(0.0, 1.8), -95.0, -10.0)
        ecu.packet_loss = clamp(0.03 + abs(rng.gauss(0.0, 0.01)), 0.0, 0.2)
        ecu.supply_voltage_v = clamp(27.8 + rng.gauss(0.0, 0.08), 25.0, 29.5)
        ecu.battery_voltage_v = clamp(24.8 + rng.gauss(0.0, 0.07), 20.0, 26.5)
        ecu.gyro_x += rng.gauss(0.0, 0.01)
        ecu.gyro_y += rng.gauss(0.0, 0.01)
        ecu.gyro_z += rng.gauss(0.0, 0.01)
        ecu.accel_x = rng.gauss(0.0, 0.05)
        ecu.accel_y = rng.gauss(0.0, 0.05)
        ecu.accel_z = 9.8 + rng.gauss(0.0, 0.04)

        # EXTR_ECU correlated with ECU pressures but independent jitter.
        extr.packet_rssi = clamp(-39.0 + rng.gauss(0.0, 2.0), -95.0, -10.0)
        extr.packet_loss = clamp(0.02 + abs(rng.gauss(0.0, 0.01)), 0.0, 0.2)
        extr.pressure_one_psi = clamp(ecu.pressure_lox_psi * 0.55 + rng.gauss(0.0, 1.2), 0.0, 1200.0)
        extr.pressure_two_psi = clamp(ecu.pressure_lng_psi * 0.58 + rng.gauss(0.0, 1.2), 0.0, 1200.0)
        extr.pressure_three_psi = clamp(ecu.pressure_inj_lox_psi * 0.75 + rng.gauss(0.0, 1.0), 0.0, 1200.0)
        extr.pressure_four_psi = clamp(ecu.pressure_inj_lng_psi * 0.77 + rng.gauss(0.0, 1.0), 0.0, 1200.0)
        extr.pressure_five_psi = clamp(gse.vent_pressure_psi * 0.82 + rng.gauss(0.0, 1.0), 0.0, 1200.0)

        # Load cell trends upward with injector pressure plus noise.
        thrust_target = (ecu.pressure_inj_lox_psi + ecu.pressure_inj_lng_psi) * 0.4
        loadcell.total_force_lbf += (thrust_target - loadcell.total_force_lbf) * 0.12 * dt_s
        loadcell.total_force_lbf = clamp(loadcell.total_force_lbf + rng.gauss(0.0, 0.8), 0.0, 20000.0)


class CommandCodec:
    @staticmethod
    def decode_gse(packet: bytes) -> tuple[bool, ...] | None:
        if len(packet) != GSE_COMMAND_LENGTH:
            return None
        payload, crc_part = packet[:-4], packet[-4:]
        expected_crc = struct.unpack("<L", crc_part)[0]
        if binascii.crc32(payload) != expected_crc:
            return None
        return struct.unpack(GSE_COMMAND_FORMAT, payload)

    @staticmethod
    def decode_ecu(packet: bytes) -> tuple[bool, ...] | None:
        if len(packet) != ECU_COMMAND_LENGTH:
            return None
        payload, crc_part = packet[:-4], packet[-4:]
        expected_crc = struct.unpack("<L", crc_part)[0]
        if binascii.crc32(payload) != expected_crc:
            return None
        return struct.unpack(ECU_COMMAND_FORMAT, payload)

    @staticmethod
    def encode_gse(command_values: tuple[bool, ...]) -> bytes:
        payload = struct.pack(GSE_COMMAND_FORMAT, *command_values)
        return payload + struct.pack("<L", binascii.crc32(payload))

    @staticmethod
    def encode_ecu(command_values: tuple[bool, ...]) -> bytes:
        payload = struct.pack(ECU_COMMAND_FORMAT, *command_values)
        return payload + struct.pack("<L", binascii.crc32(payload))


class TelemetryCodec:
    @staticmethod
    def build_gse_packet(model: DeviceModel) -> bytes:
        g = model.gse
        payload = struct.pack(
            GSE_TELEMETRY_FORMAT,
            g.packet_time_ms,
            g.igniter0,
            g.igniter1,
            g.igniter0,
            g.igniter0,
            g.igniter1,
            g.alarm,
            g.gn2_fill,
            g.gn2_vent,
            g.gn2_disconnect,
            g.mvas_fill,
            g.mvas_vent,
            g.mvas_open,
            g.mvas_close,
            g.lox_vent,
            g.lng_vent,
            g.supply_voltage_0_v,
            g.supply_voltage_1_v,
            float(g.gn2_fill) * 0.52,
            float(g.gn2_vent) * 0.52,
            float(g.gn2_disconnect) * 0.52,
            float(g.mvas_fill) * 0.52,
            float(g.mvas_vent) * 0.52,
            float(g.mvas_open) * 0.52,
            float(g.mvas_close) * 0.52,
            float(g.lox_vent) * 0.52,
            float(g.lng_vent) * 0.52,
            g.temp_engine_1_c,
            g.temp_engine_2_c,
            pressure_to_voltage("pressureGn2", g.gn2_pressure_psi),
            pressure_to_voltage("pressureLoxInjTee", g.lox_inj_pressure_psi),
            pressure_to_voltage("pressureVent", g.vent_pressure_psi),
            pressure_to_voltage("pressureLoxMvas", g.lox_mvas_pressure_psi),
        )
        return payload + struct.pack("<L", binascii.crc32(payload))

    @staticmethod
    def build_ecu_packet(model: DeviceModel) -> bytes:
        e = model.ecu
        payload = struct.pack(
            ECU_TELEMETRY_FORMAT,
            e.packet_time_ms,
            e.packet_rssi,
            e.packet_loss,
            e.copv_vent,
            e.pv1,
            e.pv2,
            e.vent,
            e.supply_voltage_v,
            e.battery_voltage_v,
            float(e.copv_vent) * 0.5,
            float(e.pv1) * 0.5,
            float(e.pv2) * 0.5,
            float(e.vent) * 0.5,
            e.temperature_copv_c,
            pressure_to_voltage("pressureCopv", e.pressure_copv_psi),
            pressure_to_voltage("pressureLox", e.pressure_lox_psi),
            pressure_to_voltage("pressureLng", e.pressure_lng_psi),
            pressure_to_voltage("pressureInjectorLox", e.pressure_inj_lox_psi),
            pressure_to_voltage("pressureInjectorLng", e.pressure_inj_lng_psi),
            e.gyro_x,
            e.gyro_y,
            e.gyro_z,
            e.accel_x,
            e.accel_y,
            e.accel_z,
            e.mag_x,
            e.mag_y,
            e.mag_z,
            e.temperature_c,
            e.altitude_m,
            e.ecef_pos_x,
            e.ecef_pos_y,
            e.ecef_pos_z,
            e.ecef_pos_acc,
            e.ecef_vel_x,
            e.ecef_vel_y,
            e.ecef_vel_z,
            e.ecef_vel_acc,
        )
        return payload + struct.pack("<L", binascii.crc32(payload))

    @staticmethod
    def build_extr_ecu_packet(model: DeviceModel) -> bytes:
        extr = model.extr_ecu
        payload = struct.pack(
            ECU_TELEMETRY_FORMAT,
            extr.packet_time_ms,
            extr.packet_rssi,
            extr.packet_loss,
            False,
            False,
            False,
            False,
            27.5,
            24.6,
            0.0,
            0.0,
            0.0,
            0.0,
            24.0,
            pressure_to_voltage("pressureOne", extr.pressure_one_psi),
            pressure_to_voltage("pressureTwo", extr.pressure_two_psi),
            pressure_to_voltage("pressureThree", extr.pressure_three_psi),
            pressure_to_voltage("pressureFour", extr.pressure_four_psi),
            pressure_to_voltage("pressureFive", extr.pressure_five_psi),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            9.8,
            0.0,
            0.0,
            0.0,
            24.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
        )
        return payload + struct.pack("<L", binascii.crc32(payload))

    @staticmethod
    def build_loadcell_packet(model: DeviceModel) -> bytes:
        load = model.loadcell
        return struct.pack(
            LOADCELL_TELEMETRY_FORMAT,
            load.packet_time_ms,
            int(round(load.total_force_lbf)),
        )


def recv_exact(sock: socket.socket, total_bytes: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = total_bytes
    while remaining > 0:
        data = sock.recv(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


class DeviceSimulatorService:
    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.model = DeviceModel()
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._start_monotonic = time.monotonic()
        self._rng = random.Random(config.seed)

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._start_monotonic) * 1000)

    def _advance_loop(self) -> None:
        period = 1.0 / self.config.update_hz
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            dt = max(0.0, now - (next_tick - period))
            with self._state_lock:
                self.model.advance(dt_s=dt, now_ms=self._now_ms(), rng=self._rng)
            next_tick += period
            time.sleep(max(0.0, next_tick - time.monotonic()))

    def _run_bidirectional_server(
        self,
        port: int,
        command_len: int,
        decoder: Callable[[bytes], tuple[bool, ...] | None],
        applier: Callable[[tuple[bool, ...]], None],
        packet_builder: Callable[[DeviceModel], bytes],
        name: str,
    ) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.config.host, port))
            server.listen()
            server.settimeout(0.5)
            LOGGER.info("%s duplex server listening on %s:%d", name, self.config.host, port)
            while not self._stop_event.is_set():
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                LOGGER.info("%s duplex client connected from %s", name, addr)
                threading.Thread(
                    target=self._duplex_client_loop,
                    args=(client, command_len, decoder, applier, packet_builder, name),
                    daemon=True,
                ).start()

    def _duplex_client_loop(
        self,
        client: socket.socket,
        command_len: int,
        decoder: Callable[[bytes], tuple[bool, ...] | None],
        applier: Callable[[tuple[bool, ...]], None],
        packet_builder: Callable[[DeviceModel], bytes],
        name: str,
    ) -> None:
        stop_reader = threading.Event()

        def read_commands_loop() -> None:
            while not self._stop_event.is_set() and not stop_reader.is_set():
                packet = recv_exact(client, command_len)
                if packet is None:
                    stop_reader.set()
                    return
                decoded = decoder(packet)
                if decoded is None:
                    LOGGER.warning("%s command packet dropped (bad length or CRC)", name)
                    continue
                with self._state_lock:
                    applier(decoded)

        period = 1.0 / self.config.telemetry_hz
        with client:
            reader = threading.Thread(target=read_commands_loop, daemon=True)
            reader.start()
            while not self._stop_event.is_set() and not stop_reader.is_set():
                try:
                    with self._state_lock:
                        packet = packet_builder(self.model)
                    client.sendall(packet)
                    time.sleep(period)
                except OSError:
                    break
            stop_reader.set()

    def _run_telemetry_server(
        self,
        port: int,
        packet_builder: Callable[[DeviceModel], bytes],
        name: str,
    ) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.config.host, port))
            server.listen()
            server.settimeout(0.5)
            LOGGER.info("%s telemetry server listening on %s:%d", name, self.config.host, port)
            while not self._stop_event.is_set():
                try:
                    client, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                LOGGER.info("%s telemetry client connected from %s", name, addr)
                threading.Thread(
                    target=self._telemetry_client_loop,
                    args=(client, packet_builder),
                    daemon=True,
                ).start()

    def _telemetry_client_loop(
        self,
        client: socket.socket,
        packet_builder: Callable[[DeviceModel], bytes],
    ) -> None:
        period = 1.0 / self.config.telemetry_hz
        with client:
            while not self._stop_event.is_set():
                try:
                    with self._state_lock:
                        packet = packet_builder(self.model)
                    client.sendall(packet)
                    time.sleep(period)
                except OSError:
                    return

    def start(self) -> None:
        workers: list[threading.Thread] = [
            threading.Thread(target=self._advance_loop, daemon=True),
            threading.Thread(
                target=self._run_bidirectional_server,
                args=(
                    self.config.gse_port,
                    GSE_COMMAND_LENGTH,
                    CommandCodec.decode_gse,
                    self.model.apply_gse_command,
                    TelemetryCodec.build_gse_packet,
                    "GSE",
                ),
                daemon=True,
            ),
        ]
        if self.config.skip_ecu_duplex:
            LOGGER.info(
                "ECU duplex server skipped (hardware ECU in use); not listening on %s:%d",
                self.config.host,
                self.config.ecu_port,
            )
        else:
            workers.append(
                threading.Thread(
                    target=self._run_bidirectional_server,
                    args=(
                        self.config.ecu_port,
                        ECU_COMMAND_LENGTH,
                        CommandCodec.decode_ecu,
                        self.model.apply_ecu_command,
                        TelemetryCodec.build_ecu_packet,
                        "ECU",
                    ),
                    daemon=True,
                )
            )
        workers.extend(
            [
                threading.Thread(
                    target=self._run_telemetry_server,
                    args=(self.config.extr_ecu_port, TelemetryCodec.build_extr_ecu_packet, "EXTR_ECU"),
                    daemon=True,
                ),
                threading.Thread(
                    target=self._run_telemetry_server,
                    args=(self.config.loadcell_port, TelemetryCodec.build_loadcell_packet, "LOAD_CELL"),
                    daemon=True,
                ),
            ]
        )
        self._threads.extend(workers)
        for worker in workers:
            worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        for worker in self._threads:
            worker.join(timeout=1.5)

    def run_forever(self) -> None:
        self.start()
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            LOGGER.info("Shutting down simulator service")
            self.stop()


def round_trip_command_decode() -> None:
    gse_values = (
        True,
        False,
        True,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
        False,
        True,
    )
    ecu_values = (True, False, True, False)
    assert CommandCodec.decode_gse(CommandCodec.encode_gse(gse_values)) == gse_values
    assert CommandCodec.decode_ecu(CommandCodec.encode_ecu(ecu_values)) == ecu_values


def validate_packet_lengths() -> None:
    model = DeviceModel()
    packet_gse = TelemetryCodec.build_gse_packet(model)
    packet_ecu = TelemetryCodec.build_ecu_packet(model)
    packet_extr = TelemetryCodec.build_extr_ecu_packet(model)
    packet_load = TelemetryCodec.build_loadcell_packet(model)

    assert len(packet_gse) == GSE_TELEMETRY_LENGTH
    assert len(packet_ecu) == ECU_TELEMETRY_LENGTH
    assert len(packet_extr) == ECU_TELEMETRY_LENGTH
    assert len(packet_load) == LOADCELL_TELEMETRY_LENGTH
    assert binascii.crc32(packet_gse[:-4]) == struct.unpack("<L", packet_gse[-4:])[0]
    assert binascii.crc32(packet_ecu[:-4]) == struct.unpack("<L", packet_ecu[-4:])[0]
    assert binascii.crc32(packet_extr[:-4]) == struct.unpack("<L", packet_extr[-4:])[0]


def run_self_checks() -> None:
    assert struct.calcsize(GSE_COMMAND_FORMAT) + 4 == GSE_COMMAND_LENGTH
    assert struct.calcsize(ECU_COMMAND_FORMAT) + 4 == ECU_COMMAND_LENGTH
    assert struct.calcsize(GSE_TELEMETRY_FORMAT) + 4 == GSE_TELEMETRY_LENGTH
    assert struct.calcsize(ECU_TELEMETRY_FORMAT) + 4 == ECU_TELEMETRY_LENGTH
    assert struct.calcsize(LOADCELL_TELEMETRY_FORMAT) == LOADCELL_TELEMETRY_LENGTH
    round_trip_command_decode()
    validate_packet_lengths()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UCIRPLGUI rocket device simulator service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gse-port", type=int, default=10002)
    parser.add_argument("--ecu-port", type=int, default=10004)
    parser.add_argument("--extr-ecu-port", type=int, default=10006)
    parser.add_argument("--loadcell-port", type=int, default=10069)
    parser.add_argument("--update-hz", type=float, default=2000.0)
    parser.add_argument("--telemetry-hz", type=float, default=1000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-self-checks", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    if not args.skip_self_checks:
        run_self_checks()
        LOGGER.info("Self-checks passed")

    skip_ecu = os.environ.get("UCIRPL_SKIP_SIMULATOR_ECU", "").strip().lower() in ("1", "true", "yes", "on")
    config = SimulatorConfig(
        host=args.host,
        gse_port=args.gse_port,
        ecu_port=args.ecu_port,
        extr_ecu_port=args.extr_ecu_port,
        loadcell_port=args.loadcell_port,
        update_hz=args.update_hz,
        telemetry_hz=args.telemetry_hz,
        seed=args.seed,
        skip_ecu_duplex=skip_ecu,
    )
    service = DeviceSimulatorService(config)
    service.run_forever()


if __name__ == "__main__":
    main()
