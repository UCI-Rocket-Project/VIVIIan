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
    
    with VIVIIan() as viv:
        with nidaqmx.Task() as task:
            for i, channel_name in enumerate(nidaq_channels):
                physical_channel = f"{NIDAQ_DEVICE}/ai{i}"
                task.ai_channels.add_ai_voltage_chan(
                    physical_channel=physical_channel,
                    name_to_assign_to_channel=channel_name,
                    terminal_config=TerminalConfiguration.DIFF,
                    min_val=-5,
                    max_val=5,
                )

            task.timing.cfg_samp_clk_timing(
                rate=sampling_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buffer_size_samples,
            )
            reader = AnalogMultiChannelReader(task.in_stream)

            task.start()
            logging.info("Acquisition started.")
            last_metrics = time.monotonic()
            stats = {
                "read_rows": 0,
                "drops": 0,
            }

            while True:
                logger.debug("acquire_loop: begin")
                samples_available = task.in_stream.avail_samp_per_chan
                logger.debug("acquire_loop: samples_available=%d", samples_available)
                if samples_available <= 0:
                    logger.debug("acquire_loop: 0 samples available after %.5fs", 1 / POLLING_FREQ)
                    time.sleep(1 / POLLING_FREQ)
                    continue

                data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)
                logger.debug("acquire_loop: reading NI-DAQ (%d samples available)", samples_available)
                reader.read_many_sample(
                    data=data_buffer,
                    number_of_samples_per_channel=samples_available,
                    timeout=10.0,
                )
                stats["read_rows"] += samples_available
                timestamps = make_timestamps(samples_available, sampling_rate)
                logger.debug("acquire_loop: read samples")

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
