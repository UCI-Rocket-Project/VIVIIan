import time
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
import pythusa
import pyarrow as pa
import pyarrow.flight as flight
import numpy as np
from generic_connector import generic_stream_connector
from functools import partial



# --- Constants for NIDAQ Backend ---
NIDAQ_FLIGHT_BIND = "grpc://127.0.0.1:8825"
#NIDAQ_FLIGHT_BIND = "grpc://10.0.0.3:8825"
NIDAQ_ROWS_PER_FRAME = 1000

# --- Packet DEFINITIONS ---
NIDAQ_FIELD_NAME_MAP = {
    "ai2": "LNGPOT",# "PT4",
    "ai10": "LOXPOT",# "PT5",
}


NIDAQ_FIELDS = tuple(NIDAQ_FIELD_NAME_MAP.keys())
NIDAQ_FIELD_NAMES = tuple(NIDAQ_FIELD_NAME_MAP.values())

# --- Constants for NIDAQ Connector ---
DEVICE = "Dev1"
NUM_FIELDS = len(NIDAQ_FIELD_NAMES)
RATE = 250000 // NUM_FIELDS
NIDAQ_NUM_SIGNALS = NUM_FIELDS
NIDAQ_AVERAGE_OVER = 1000



def read_nidaq_data(*, outstream) -> None:
    with nidaqmx.Task() as task:
        # Add all fields first.
        for field_key, field_label in NIDAQ_FIELD_NAME_MAP.items():
            task.ai_channels.add_ai_voltage_chan(
                physical_channel=f"{DEVICE}/{field_key}",
                name_to_assign_to_channel=field_label,
                terminal_config=TerminalConfiguration.RSE,
                min_val=-10,
                max_val=10,
            )

        # Then configure timing once
        task.timing.cfg_samp_clk_timing(
            rate=RATE,
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=NIDAQ_ROWS_PER_FRAME * 20,
        )

        task.start()
        while True:
            data = task.read(number_of_samples_per_channel=NIDAQ_ROWS_PER_FRAME)

            # Shape: (NUM_FIELDS, CHUNK) -> (CHUNK, NUM_FIELDS)
            samples_by_time = np.asarray(data, dtype=np.float64).T
            outstream.write(samples_by_time)







def main(): 
    with pythusa.Pipeline("nidaq_gse") as pipeline:

        nidaq_flight_fn = partial(
            generic_stream_connector,
            field_names=list(NIDAQ_FIELD_NAMES),
            flight_address=NIDAQ_FLIGHT_BIND,
        )
        pipeline.add_stream("nidaq_data", shape=(NIDAQ_ROWS_PER_FRAME, NUM_FIELDS), dtype=np.float64)
        pipeline.add_task("nidaq_task", fn=read_nidaq_data, writes={"outstream": "nidaq_data"})
        pipeline.add_task("nidaq_flight_server", fn=nidaq_flight_fn, reads={"stream": "nidaq_data"})
        pipeline.run()


if __name__ == "__main__":
    main()
