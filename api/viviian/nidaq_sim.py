import logging
import queue
import socket
import threading
import time

import nidaqmx
import numpy as np
import pyarrow as pa
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogMultiChannelReader
from questdb.ingress import Sender, IngressError

import sys
from pathlib import Path

from viviian import VIVIIan 

# Add shared_config to sys.path so we can import config_parser
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared_config.config_parser import load_toml_config


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nidaq_stream")

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


def nidaq() -> None:
    nidaq_cfg, db_cfg, stream_cfg, signal_cfgs, graph_cells = load_toml_config(str(ROOT_DIR / "gse2_0.toml"))
    
    nidaq_channels = [cfg.source_column for cfg in signal_cfgs.values()]
    num_channels = len(nidaq_channels)
    
    # Nidaq Stream configs
    STREAM_HOST = stream_cfg.host
    STREAM_PORT = stream_cfg.port
    GUI_TX_BUFFER_LEN = stream_cfg.raw_batch_points
    
    # Nidaq params
    NIDAQ_DEVICE = nidaq_cfg.device
    CHANNEL_SAMPLING_RATE = nidaq_cfg.channel_sampling_rate
    POLLING_FREQ = nidaq_cfg.polling_freq
    NIDAQ_BUFFER_DURATION_SEC = nidaq_cfg.buffer_duration_sec

    sampling_rate = CHANNEL_SAMPLING_RATE * num_channels
    buffer_size_samples = int(sampling_rate * NIDAQ_BUFFER_DURATION_SEC)

    logging.info(
        "Configuring NIDAQ: %d channels @ %d Hz (%d Hz total)",
        num_channels,
        CHANNEL_SAMPLING_RATE,
        sampling_rate,
    )
    noise_std = float(0.0008)
    base_hz = float(2.0)
    hz_step = float(0.6)
    phase_step = float(0.5)

    rng = np.random.default_rng(seed=42)
    phase = np.array([i * phase_step for i in range(num_channels)], dtype=np.float64)
    freqs = np.array([base_hz + i * hz_step for i in range(num_channels)], dtype=np.float64)
    
    with VIVIIan() as viv:
        logging.info("Acquisition started.")
        last_metrics = time.monotonic()
        stats = {
            "read_rows": 0,
            "drops": 0,
        }
        next_tick = time.monotonic()

        while True:
            # Simulate waiting for samples
            n = GUI_TX_BUFFER_LEN
            batch_dt_s = n / max(1.0, sampling_rate)
            next_tick += batch_dt_s
            sleep_s = next_tick - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_tick = time.monotonic()

            samples_available = n
            
            t = np.arange(n, dtype=np.float64) / max(1.0, sampling_rate)
            data_buffer = np.empty((num_channels, n), dtype=np.float64)
            for i in range(num_channels):
                ch_name = nidaq_channels[i].strip().lower()
                if "load" in ch_name:
                    # Slow, smoother load-cell-like motion with low-frequency drift.
                    wave = 0.015 * np.sin(2.0 * np.pi * 1.2 * t + phase[i])
                    drift = 0.004 * np.sin(2.0 * np.pi * 0.12 * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, noise_std * 0.6, size=n)
                    data_buffer[i, :] = wave + drift + noise
                elif "pts" in ch_name:
                    # Sharper, higher-frequency structure for pressure/PTS-like behavior.
                    wave = 0.006 * np.sin(2.0 * np.pi * 9.0 * t + phase[i])
                    harm = 0.005 * np.sin(2.0 * np.pi * 22.0 * t + 0.3 * phase[i])
                    saw_phase = ((1.8 * t + (phase[i] / (2.0 * np.pi))) % 1.0)
                    saw = (2.0 * saw_phase) - 1.0
                    burst = 0.003 * saw
                    noise = rng.normal(0.0, noise_std * 1.3, size=n)
                    data_buffer[i, :] = wave + harm + burst + noise
                else:
                    # Fallback synthetic profile.
                    wave = 0.01 * np.sin(2.0 * np.pi * freqs[i] * t + phase[i])
                    harm = 0.004 * np.sin(2.0 * np.pi * (freqs[i] * 0.27 + 0.8) * t + 0.5 * phase[i])
                    noise = rng.normal(0.0, noise_std, size=n)
                    data_buffer[i, :] = wave + harm + noise
            phase = (phase + (2.0 * np.pi * freqs * (n / max(1.0, sampling_rate)))) % (2.0 * np.pi)

            logger.info("acquire_loop: generated NI-DAQ (%d samples)", samples_available)
            stats["read_rows"] += samples_available
            timestamps = make_timestamps(samples_available, sampling_rate)
            logger.info("acquire_loop: generated samples")

            table = chunk_table(timestamps, data_buffer, nidaq_channels)

            viv.ingress_table(table)

            time.sleep(1 / POLLING_FREQ)


if __name__ == "__main__":
    while True:
        try:
            nidaq()
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
