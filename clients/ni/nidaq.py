import logging
import time

import nidaqmx
import numpy as np
import pyarrow as pa
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogMultiChannelReader

import tomllib

from viviian import VIVIIan 

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
    with open("./example_config.toml", "rb") as f:
        cfg = tomllib.load(f)

    nidaq_cfg = cfg['measurement_settings']
    nidaq_channels = cfg['port']
    num_channels = len(nidaq_channels)

    NIDAQ_DEVICE = nidaq_cfg['device']
    SAMPLING_RATE = nidaq_cfg['sampling_rate_hz']
    POLLING_FREQ = nidaq_cfg['software_polling_hz']
    NIDAQ_BUFFER_DURATION_SEC = nidaq_cfg['nidaq_buffer_len_sec']

    channel_sampling_rate = SAMPLING_RATE / num_channels

    buffer_size_samples = int(SAMPLING_RATE * NIDAQ_BUFFER_DURATION_SEC)

    logging.info(
        "Configuring NIDAQ: %d channels @ %d Hz",
        num_channels,
        channel_sampling_rate
    )

    schema = pa.schema([
        pa.field("timestamps", pa.time64('ns'))
    ])
    for channel in nidaq_channels:
        schema = schema.append(pa.field(channel['name'], pa.float64()))
    
    with VIVIIan(schema) as viv:
        with nidaqmx.Task() as task:
            for channel in nidaq_channels:
                physical_channel = f"{NIDAQ_DEVICE}/{channel['channel']}"

                match channel['sample_type']:
                    case "DIFF":
                        terminal_config = TerminalConfiguration.DIFF
                    case "RSE":
                        terminal_config = TerminalConfiguration.RSE
                    case "NRSE":
                        terminal_config = TerminalConfiguration.NRSE
                    case _:
                        logging.error(f"Invalid channel type selected {channel['sample_type']}")

                task.ai_channels.add_ai_voltage_chan(
                    physical_channel=physical_channel,
                    name_to_assign_to_channel=channel['name'],
                    terminal_config=terminal_config,
                    min_val=channel['min_val'],
                    max_val=channel['max_val'],
                )

            task.timing.cfg_samp_clk_timing(
                rate=channel_sampling_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buffer_size_samples,
            )
            reader = AnalogMultiChannelReader(task.in_stream)

            task.start()
            logging.info("Acquisition started.")
            last_time = time.monotonic()
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
                else:
                    data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)
                    logger.debug("acquire_loop: reading NI-DAQ (%d samples available)", samples_available)
                    reader.read_many_sample(
                        data=data_buffer,
                        number_of_samples_per_channel=samples_available,
                        timeout=10.0,
                    )
                    stats["read_rows"] += samples_available
                    timestamps = make_timestamps(samples_available, channel_sampling_rate)
                    logger.debug("acquire_loop: read samples")

                    table = chunk_table(timestamps, data_buffer, [channel['name'] for channel in nidaq_channels])

                    viv.ingress_table(table)

                current_time = time.monotonic()
                sleep_time = (last_time + (1 / POLLING_FREQ)) - current_time
                if (sleep_time > 0):
                    time.sleep(sleep_time)
                else:
                    logger.info(f"Loop overrun by {sleep_time} seconds")
                last_time = time.monotonic()


if __name__ == "__main__":

    nidaq()
    while True:
        try:
            nidaq()
        except KeyboardInterrupt:
            logging.info("Stopping (user interrupt).")
            raise
        except Exception as e:
            logging.error("High-level error: %s", e)
            time.sleep(1)
