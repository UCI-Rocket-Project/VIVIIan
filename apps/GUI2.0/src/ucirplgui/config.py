from __future__ import annotations

import pyarrow as pa

WINDOW_TITLE = "UCIRPLGUI"

THEME_NAME = "tau_ceti"

ROWS_PER_FRAME = 1

DEFAULT_CONNECTOR_HOST = "127.0.0.1"

DEFAULT_SAMPLE_RATE_HZ = 60.0
DEFAULT_ROWS_PER_BATCH = 64
DEFAULT_BATCH_SLEEP_S = 0.01
FRONTEND_FEED_SLEEP_S = 0.005
DEVICE_LINK_PUBLISH_INTERVAL_S = 0.02
BACKEND_DATA_DIR = "UCIRPLGUI/data"
BACKEND_RAW_STORAGE_DIR = f"{BACKEND_DATA_DIR}/raw"
BACKEND_RAW_STORAGE_ROWS_PER_FILE = 16384
BACKEND_THROUGHPUT_WINDOW_S = 1.0

SIMULATOR_HOST = "127.0.0.1"
SIMULATOR_GSE_PORT = 10002
SIMULATOR_ECU_PORT = 10004
SIMULATOR_EXTR_ECU_PORT = 10006
SIMULATOR_LOADCELL_PORT = 10069

CONNECTOR_PORTS = {
    # Device -> backend raw telemetry.
    "raw_gse": 7101,
    "raw_ecu": 7102,
    "raw_extr_ecu": 7103,
    "raw_loadcell": 7104,
    # Frontend -> device command streams.
    "cmd_gse": 7201,
    "cmd_ecu": 7202,
    "cmd_extr_ecu": 7203,
    "cmd_loadcell": 7204,
    # Backend -> frontend processed streams.
    "frontend_tank_pressures": 7301,
    "frontend_line_pressures": 7302,
    "frontend_loadcell": 7303,
    "frontend_fft": 7304,
    "frontend_gse_ecu_scalars": 7305,
    "frontend_backend_throughput": 7306,
    # Device interfaces -> frontend link status (one Flight port per board).
    "device_link_gse": 7401,
    "device_link_ecu": 7402,
    "device_link_extr_ecu": 7403,
    "device_link_loadcell": 7404,
}

DEVICE_LINK_STATUS_STREAM_ID = "ucirpl.device_link"
DEVICE_LINK_STATUS_COLUMNS = (
    "board_id",
    "connected_flag",
    "last_connect_epoch_s",
    "last_rx_epoch_s",
    "endpoint_host_ipv4",
    "endpoint_port",
    "snapshot_epoch_s",
    "last_error_code",
)
DEVICE_LINK_BOARDS = ("gse", "ecu", "extr_ecu", "loadcell")


def device_link_status_stream_id(board: str) -> str:
    if board not in DEVICE_LINK_BOARDS:
        raise ValueError(f"unknown device link board: {board!r}")
    return f"{DEVICE_LINK_STATUS_STREAM_ID}.{board}"


def device_link_status_port(board: str) -> int:
    return CONNECTOR_PORTS[f"device_link_{board}"]


RAW_GSE_STREAM_ID = "ucirpl.raw.gse"
RAW_ECU_STREAM_ID = "ucirpl.raw.ecu"
RAW_EXTR_ECU_STREAM_ID = "ucirpl.raw.extr_ecu"
RAW_LOADCELL_STREAM_ID = "ucirpl.raw.loadcell"

CMD_GSE_STREAM_ID = "ucirpl.cmd.gse"
CMD_ECU_STREAM_ID = "ucirpl.cmd.ecu"
CMD_EXTR_ECU_STREAM_ID = "ucirpl.cmd.extr_ecu"
CMD_LOADCELL_STREAM_ID = "ucirpl.cmd.loadcell"

FRONTEND_TANK_PRESSURES_STREAM_ID = "ucirpl.frontend.tank_pressures"
FRONTEND_LINE_PRESSURES_STREAM_ID = "ucirpl.frontend.line_pressures"
FRONTEND_LOADCELL_STREAM_ID = "ucirpl.frontend.loadcell"
FRONTEND_FFT_STREAM_ID = "ucirpl.frontend.fft"
FRONTEND_GSE_ECU_SCALARS_STREAM_ID = "ucirpl.frontend.gse_ecu_scalars"
FRONTEND_BACKEND_THROUGHPUT_STREAM_ID = "ucirpl.frontend.backend_throughput"

GSE_RAW_COLUMNS = (
    "packet_time_ms",
    "pressure_gn2_psi",
    "pressure_lox_inj_tee_psi",
    "pressure_vent_psi",
    "pressure_lox_mvas_psi",
    "temperature_engine_1_c",
    "temperature_engine_2_c",
    "igniter_0",
    "igniter_1",
    "alarm",
    "gn2_fill",
    "gn2_vent",
    "gn2_disconnect",
    "mvas_fill",
    "mvas_vent",
    "mvas_open",
    "mvas_close",
    "lox_vent",
    "lng_vent",
)

ECU_RAW_COLUMNS = (
    "packet_time_ms",
    "pressure_copv_psi",
    "pressure_lox_psi",
    "pressure_lng_psi",
    "pressure_inj_lox_psi",
    "pressure_inj_lng_psi",
    "temperature_copv_c",
    "temperature_c",
    "packet_rssi",
    "packet_loss",
    "copv_vent",
    "pv1",
    "pv2",
    "vent",
)

EXTR_ECU_RAW_COLUMNS = (
    "packet_time_ms",
    "pressure_one_psi",
    "pressure_two_psi",
    "pressure_three_psi",
    "pressure_four_psi",
    "pressure_five_psi",
    "packet_rssi",
    "packet_loss",
)

LOADCELL_RAW_COLUMNS = (
    "packet_time_ms",
    "total_force_lbf",
)

CMD_GSE_COLUMNS = (
    "igniter_0",
    "igniter_1",
    "alarm",
    "gn2_fill",
    "gn2_vent",
    "gn2_disconnect",
    "mvas_fill",
    "mvas_vent",
    "mvas_open",
    "mvas_close",
    "lox_vent",
    "lng_vent",
)

CMD_ECU_COLUMNS = (
    "copv_vent",
    "pv1",
    "pv2",
    "vent",
)

CMD_EXTR_ECU_COLUMNS = ("noop",)
CMD_LOADCELL_COLUMNS = ("noop",)

FRONTEND_TANK_PRESSURES_COLUMNS = (
    "timestamp_s",
    "pressure_copv_psi",
    "pressure_lox_psi",
    "pressure_lng_psi",
)

FRONTEND_LINE_PRESSURES_COLUMNS = (
    "timestamp_s",
    "pressure_vent_psi",
    "pressure_lox_mvas_psi",
    "pressure_lox_inj_tee_psi",
    "pressure_inj_lox_psi",
    "pressure_inj_lng_psi",
)

FRONTEND_LOADCELL_COLUMNS = (
    "timestamp_s",
    "total_force_lbf",
)

FRONTEND_FFT_COLUMNS = (
    "timestamp_s",
    "pressure_fft_mag",
)

# Extra scalars for Rocket-style GSE/ECU dials (engine TCs, GN2 proxy, COPV TC).
FRONTEND_GSE_ECU_SCALARS_COLUMNS = (
    "timestamp_s",
    "temperature_engine_1_c",
    "temperature_engine_2_c",
    "pressure_gn2_psi",
    "temperature_copv_c",
)

FRONTEND_BACKEND_THROUGHPUT_COLUMNS = (
    "timestamp_s",
    "backend_throughput_mbps",
)


def make_schema(columns: tuple[str, ...]) -> pa.Schema:
    return pa.schema([(name, pa.float64()) for name in columns])


SCHEMAS = {
    RAW_GSE_STREAM_ID: make_schema(GSE_RAW_COLUMNS),
    RAW_ECU_STREAM_ID: make_schema(ECU_RAW_COLUMNS),
    RAW_EXTR_ECU_STREAM_ID: make_schema(EXTR_ECU_RAW_COLUMNS),
    RAW_LOADCELL_STREAM_ID: make_schema(LOADCELL_RAW_COLUMNS),
    CMD_GSE_STREAM_ID: make_schema(CMD_GSE_COLUMNS),
    CMD_ECU_STREAM_ID: make_schema(CMD_ECU_COLUMNS),
    CMD_EXTR_ECU_STREAM_ID: make_schema(CMD_EXTR_ECU_COLUMNS),
    CMD_LOADCELL_STREAM_ID: make_schema(CMD_LOADCELL_COLUMNS),
    FRONTEND_TANK_PRESSURES_STREAM_ID: make_schema(FRONTEND_TANK_PRESSURES_COLUMNS),
    FRONTEND_LINE_PRESSURES_STREAM_ID: make_schema(FRONTEND_LINE_PRESSURES_COLUMNS),
    FRONTEND_LOADCELL_STREAM_ID: make_schema(FRONTEND_LOADCELL_COLUMNS),
    FRONTEND_FFT_STREAM_ID: make_schema(FRONTEND_FFT_COLUMNS),
    FRONTEND_GSE_ECU_SCALARS_STREAM_ID: make_schema(FRONTEND_GSE_ECU_SCALARS_COLUMNS),
    FRONTEND_BACKEND_THROUGHPUT_STREAM_ID: make_schema(
        FRONTEND_BACKEND_THROUGHPUT_COLUMNS
    ),
}
SCHEMAS.update(
    {device_link_status_stream_id(board): make_schema(DEVICE_LINK_STATUS_COLUMNS) for board in DEVICE_LINK_BOARDS}
)


RAW_STREAMS = (
    RAW_GSE_STREAM_ID,
    RAW_ECU_STREAM_ID,
    RAW_EXTR_ECU_STREAM_ID,
    RAW_LOADCELL_STREAM_ID,
)

FRONTEND_STREAMS = (
    FRONTEND_TANK_PRESSURES_STREAM_ID,
    FRONTEND_LINE_PRESSURES_STREAM_ID,
    FRONTEND_LOADCELL_STREAM_ID,
    FRONTEND_FFT_STREAM_ID,
    FRONTEND_GSE_ECU_SCALARS_STREAM_ID,
    FRONTEND_BACKEND_THROUGHPUT_STREAM_ID,
)
