NIDAQ_DEVICE = "Dev1"          # NI Device ID
NIDAQ_CHANNELS = ["Load Cell", "PTS"] # Channels to poll for
CHANNEL_SAMPLING_RATE = 50000         # Hz
NIDAQ_BUFFER_DURATION_SEC = 20       # Internal NIDAQ buffer size (incase we can't clear)
GUI_TX_BUFFER_LEN = 200
POLLING_FREQ = 1               # Hz

QUESTDB_CONF = 'http::addr=localhost:9000;'
QUESTDB_TABLE = 'LOAD_CELL'
STREAM_QUEUE_MAX_BATCHES = 128