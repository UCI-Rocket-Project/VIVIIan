import time
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
import pythusa
import pyarrow as pa
import pyarrow.flight as flight
import numpy as np
from generic_connector import generic_stream_connector
from functools import partial



DEVICE = "Dev1"
CHANNEL = f"{DEVICE}/ai0"
NUM_CHANNELS = 16
RATE = 250000 // NUM_CHANNELS
NIDAQ_ROWS_PER_FRAME = 1000
NIDAQ_NUM_SIGNALS = NUM_CHANNELS
NIDAQ_AVERAGE_OVER = 1000
NIDAQ_FLIGHT_BIND = "grpc://0.0.0.0:8825"
NIDAQ_FLIGHT_CONNECT = "grpc://127.0.0.1:8825"




NIDAQ_CHANNEL_NAMES = {
    "ai0": "ai0",
    "ai1": "ai1",
    "ai2": "ai2",
    "ai3": "ai3",
    "ai4": "ai4",
    "ai5": "ai5",
    "ai6": "ai6",
    "ai7": "ai7",
    "ai8": "ai8",
    "ai9": "ai9",
    "ai10": "ai10",
    "ai11": "ai11",
    "ai12": "ai12",
    "ai13": "ai13",
    "ai14": "ai14",
    "ai15": "ai15",
}


NIDAQ_CHANNELS = tuple(NIDAQ_CHANNEL_NAMES.keys())
NIDAQ_FIELD_NAMES = tuple(NIDAQ_CHANNEL_NAMES.values())






def read_nidaq_data(*, outstream) -> None:
    with nidaqmx.Task() as task:
        # Add all channels first
        for channel_key, channel_label in NIDAQ_CHANNEL_NAMES.items():
            task.ai_channels.add_ai_voltage_chan(
                physical_channel=f"{DEVICE}/{channel_key}",
                name_to_assign_to_channel=channel_label,
                terminal_config=TerminalConfiguration.RSE,
                min_val=-10,
                max_val=10,
            )

        # Then configure timing once
        task.timing.cfg_samp_clk_timing(
            rate=RATE,
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=NIDAQ_ROWS_PER_FRAME,
        )

        task.start()
        while True:
            data = task.read(number_of_samples_per_channel=NIDAQ_ROWS_PER_FRAME)

            # Shape: (NUM_CHANNELS, CHUNK) -> (CHUNK, NUM_CHANNELS)
            samples_by_time = np.asarray(data, dtype=np.float64).T
            outstream.write(samples_by_time)







def main(): 
    with pythusa.Pipeline("nidaq_gse") as pipeline:

        nidaq_flight_fn = partial(
            generic_stream_connector,
            field_names=list(NIDAQ_FIELD_NAMES),
            flight_address=NIDAQ_FLIGHT_CONNECT,
        )
        pipeline.add_stream("nidaq_data", shape=(NIDAQ_ROWS_PER_FRAME, NUM_CHANNELS), dtype=np.float64)
        pipeline.add_task("nidaq_task", fn=read_nidaq_data, writes={"outstream": "nidaq_data"})
        pipeline.add_task("nidaq_flight_server", fn=nidaq_flight_fn, reads={"stream": "nidaq_data"})
        pipeline.run()


if __name__ == "__main__":
    main()