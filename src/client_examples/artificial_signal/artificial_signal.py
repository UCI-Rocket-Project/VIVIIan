import logging
import time

import numpy as np
import pyarrow as pa

import tomllib

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

from viviian.deviceinterface import DeviceInterface

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("noise_stream")

def make_timestamps(samples_available: int, sampling_rate: float) -> np.ndarray:
    period_ns = (1.0 / sampling_rate) * 1_000_000_000
    duration_ns = (samples_available - 1) * period_ns
    relative_ns = (np.arange(samples_available, dtype=np.float64) * period_ns) - duration_ns
    current_time_ns = time.time_ns()
    timestamps_ns = relative_ns.astype(np.int64) + current_time_ns
    return timestamps_ns.astype("datetime64[ns]")


def chunk_table(timestamps: np.ndarray, data_buffer: np.ndarray, nidaq_channels: list[str]) -> pa.Table:
    cols = {"timestamps": pa.array(timestamps)}
    for i, channel_name in enumerate(nidaq_channels):
        cols[channel_name] = pa.array(data_buffer[i], type=pa.float64())
    return pa.table(cols)

def create_math_function(expression_str):
    x = sp.symbols('x')
    
    transformations = (standard_transformations + (implicit_multiplication_application,))
    expr = parse_expr(expression_str, transformations=transformations)
    
    return sp.lambdify(x, expr)

def gen_signals() -> None:
    with open("./example_config.toml", "rb") as f:
        cfg = tomllib.load(f)

    measurement_cfg = cfg['measurement_settings']
    channels = cfg['port']
    num_channels = len(channels)

    SAMPLING_RATE = measurement_cfg['sampling_rate_hz']
    POLLING_FREQ = measurement_cfg['software_polling_hz']

    logging.info(
        "Configuring noise sampler: %d channels @ %d Hz",
        num_channels,
        SAMPLING_RATE 
    )

    schema = pa.schema([
        pa.field("timestamps", pa.time64('ns'))
    ])
    for channel in channels:
        schema = schema.append(pa.field(channel['name'], pa.float64()))
    
    with DeviceInterface(schema) as device_interface:
        for channel in channels:
            try:
                channel['exec_py_func'] = create_math_function(channel['signal'])
            except sp.SympifyError as e:
                logging.error(f"Failed to parse: {channel['signal']}. Using constant function instead.")
                channel['exec_py_func'] = lambda _: 1

        last_time = time.monotonic()

        while True:
            logger.debug("acquire_loop: begin")

            samples_available = int(SAMPLING_RATE / POLLING_FREQ)
            if samples_available <= 0:
                samples_available = 1
        
            logger.debug("acquire_loop: generating %d artificial samples", samples_available)

            sample_times = last_time + np.arange(samples_available) / SAMPLING_RATE
            data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)

            for i, channel in enumerate(channels):
                data_buffer[i] = channel['exec_py_func'](sample_times)

            timestamps = make_timestamps(samples_available, SAMPLING_RATE)
            logger.debug("acquire_loop: read samples")

            table = chunk_table(timestamps, data_buffer, [channel['name'] for channel in channels])

            device_interface.ingress_table(table)

            last_time += samples_available / SAMPLING_RATE

            current_time = time.monotonic()
            sleep_time = last_time - current_time
            if (sleep_time > 0):
                time.sleep(sleep_time)
            else:
                logger.info(f"Loop overrun by {-sleep_time:.4f} seconds")


if __name__ == "__main__":
    while True:
        try:
            gen_signals()
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
