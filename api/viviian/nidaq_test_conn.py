import nidaqmx
import numpy as np
import pyarrow as pa
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogMultiChannelReader
import time

with nidaqmx.Task() as task:
    physical_channel = f"Dev1/ai0"
    print(task.devices)
    task.ai_channels.add_ai_voltage_chan(
        physical_channel=physical_channel,
        name_to_assign_to_channel="Test",
        terminal_config=TerminalConfiguration.DIFF,
        min_val=-5,
        max_val=5,
    )
    task.timing.cfg_samp_clk_timing(
        rate=10000,
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=100000,
    )
    reader = AnalogMultiChannelReader(task.in_stream)

    task.start()

    while True:
        samples_available = task.in_stream.avail_samp_per_chan
        data_buffer = np.empty((1, samples_available), dtype=np.float64)
        reader.read_many_sample(
            data=data_buffer,
            number_of_samples_per_channel=samples_available,
            timeout=10.0,
        )
        print("SUC")
        time.sleep(1)