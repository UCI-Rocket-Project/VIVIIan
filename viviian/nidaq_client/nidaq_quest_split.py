import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogMultiChannelReader
import numpy as np
import pandas as pd
import time
import logging
from questdb.ingress import Sender, IngressError
from config import *

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def stream_nidaq_to_questdb():
    num_channels = len(NIDAQ_CHANNELS)
    sampling_rate = CHANNEL_SAMPLING_RATE * num_channels
    buffer_size_samples = int(sampling_rate * BUFFER_DURATION_SEC)

    logging.info(f"Configuring NIDAQ: {num_channels} channels @ {CHANNEL_SAMPLING_RATE} Hz ({sampling_rate} Hz total)")
    
    try:
        with nidaqmx.Task() as task:
            for i, channel_name in enumerate(NIDAQ_CHANNELS):
                physical_channel = f"{NIDAQ_DEVICE}/ai{i}"
                task.ai_channels.add_ai_voltage_chan(
                    physical_channel=physical_channel,
                    name_to_assign_to_channel=channel_name,
                    terminal_config=TerminalConfiguration.DIFF,
                    min_val=-0.02,
                    max_val=0.02
                )

            # Setup Timing
            task.timing.cfg_samp_clk_timing(
                rate=sampling_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buffer_size_samples
            )

            # Setup Reader
            reader = AnalogMultiChannelReader(task.in_stream)
            
            # 3. Initialize QuestDB Sender
            logging.info(f"Connecting to QuestDB at {QUESTDB_CONF}...")
            with Sender.from_conf(QUESTDB_CONF) as sender:
                
                logging.info("Starting Acquisition...")
                task.start()

                while True:
                    # Check buffer
                    samples_available = task.in_stream.avail_samp_per_chan
                    
                    if samples_available > 0:
                        # Allocate buffer
                        data_buffer = np.empty((num_channels, samples_available), dtype=np.float64)

                        # Read from Device
                        reader.read_many_sample(
                            data=data_buffer, 
                            number_of_samples_per_channel=samples_available,
                            timeout=10.0 
                        )
                        
                        # --- Timestamp Calculation ---
                        period_ns = (1 / sampling_rate) * 1_000_000_000
                        duration_ns = (samples_available - 1) * period_ns
                        
                        relative_ns = (np.arange(samples_available, dtype=np.float64) * period_ns) - duration_ns
                        
                        current_time_ns = time.time_ns()
                        timestamps_ns = relative_ns.astype(np.int64) + current_time_ns
                        timestamps = timestamps_ns.astype('datetime64[ns]')
                        
                        # --- DataFrame Construction ---
                        data_dict = dict(zip(NIDAQ_CHANNELS, data_buffer))
                        data_dict['timestamps'] = timestamps
                        
                        df = pd.DataFrame(data_dict)

                        # Reorder columns
                        cols = ['timestamps'] + NIDAQ_CHANNELS
                        df = df[cols]
                        
                        # --- 4. Send to QuestDB ---
                        try:
                            sender.dataframe(df, table_name=QUESTDB_TABLE, at='timestamps')
                            sender.flush()
                            pass
                        except IngressError as e:
                            logging.error(f"QuestDB Ingress Error: {e}")

                    # Sleep to prevent tight loop
                    time.sleep(1 / POLLING_FREQ)

    except nidaqmx.DaqError as e:
        logging.error(f"NIDAQ Error: {e}")
    except KeyboardInterrupt:
        logging.info("\nStopping acquisition (User Interrupt).")
        exit()
    except Exception as e:
        logging.error(f"General Error: {e}")

if __name__ == "__main__":
    while True:
        try:
            stream_nidaq_to_questdb()
        except KeyboardInterrupt:
            logging.info("\nStopping acquisition (User Interrupt).")
            exit()
        except Exception as e:
            print(f"HIGH LEVEL ERROR: {e}")
            time.sleep(1)
