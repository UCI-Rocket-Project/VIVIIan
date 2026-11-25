from contextlib import ExitStack
import threading
import time
import pyarrow as pa
import pyarrow.parquet as pq

from generic_connector import LatestServer

FRONTEND_TEST_NAME_SERVER = "grpc://0.0.0.0:1629"


class _SharedTestNameServer(LatestServer):
    """Publish received names to the other storage processes as well."""

    def __init__(self, address, name, fields, shared_state):
        self._shared_state = shared_state
        super().__init__(address, name, fields)

    @property
    def latest(self):
        return self._shared_state.latest

    @latest.setter
    def latest(self, value):
        self._shared_state.latest = value


def backend_run_test_name_flight_server(shared_state=None) -> LatestServer:
    server_args = (
        FRONTEND_TEST_NAME_SERVER,
        "Test Name",
        ("test_name",),
    )
    server = (
        LatestServer(*server_args)
        if shared_state is None
        else _SharedTestNameServer(*server_args, shared_state=shared_state)
    )
    threading.Thread(
        target=server.serve,
        name="test-name-flight-server",
        daemon=True,
    ).start()
    return server

def write_from_stream(
    stream,
    storage_base_dir,
    instrument_name,
    column_names,
    column_types,
    file_size: int = 1024 * 1024 * 10,
    rollover_seconds: float | None = None,
    *,
    test_name_state=None,
    start_test_name_server: bool = True,
):
    """
    Reads data from a stream and writes it to rolling Parquet files.
    A new file is created in the specified path once the current file exceeds `file_size`
    or has been open longer than `rollover_seconds`.

    One storage worker hosts the test-name server in a background thread.
    Other workers can poll the same multiprocessing Namespace by passing
    `test_name_state` and setting `start_test_name_server=False`.
    """
    
    # 1. Ensure the destination folder exists (assuming 'path' is a pathlib.Path object)
    path = storage_base_dir
    path.mkdir(parents=True, exist_ok=True)
    
    # 2. Define the blueprint for the Parquet files (Fixed typo here)
    schema = pa.schema([(name, column_types[i]) for i, name in enumerate(column_names)])

    if not start_test_name_server and test_name_state is None:
        raise ValueError("test_name_state is required when not starting a server")

    with ExitStack() as stack:
        if start_test_name_server:
            test_name_server = backend_run_test_name_flight_server(test_name_state)
            stack.callback(test_name_server.shutdown)
        else:
            test_name_server = test_name_state
        _write_test_named_files(
            stream, test_name_server, path, instrument_name, column_names,
            schema, file_size, rollover_seconds,
        )


def _write_test_named_files(
    stream, test_name_server, path, instrument_name, column_names,
    schema, file_size, rollover_seconds,
):
    print(f"{instrument_name}: listening for test names", flush=True)
    last_test_name = None
    
    # Outer loop: Handles creating a brand new file
    while True: 
        latest = test_name_server.latest
        if latest is None or not latest.get("test_name"):
            latest = {"test_name": "NO_NAME"}
        test_name = latest["test_name"]
        if test_name != last_test_name:
            test_path = path / test_name / instrument_name
            test_path.mkdir(parents=True, exist_ok=True)
            print(f"{instrument_name}: test name = {test_name}", flush=True)

        last_test_name = test_name

        file_path = test_path / f"part-{time.time_ns()}.parquet"
        bytes_written = 0
        rollover_at = (
            time.monotonic() + rollover_seconds
            if rollover_seconds is not None
            else None
        )
        try:
        # Open the writer for the new file
            with pq.ParquetWriter(file_path, schema) as writer:
                
                # Inner loop: Keeps writing to the CURRENT file until it gets too big
                while bytes_written < file_size:
                    if rollover_at is not None and time.monotonic() >= rollover_at:
                        break

                    latest = test_name_server.latest
                    test_name = (latest or {}).get("test_name") or "NO_NAME"
                    if test_name != last_test_name:
                        break
                    
                    frame = stream.read() # Expected to return a list/tuple of numpy arrays or 2D array
                    
                    if frame is None:
                        # Note: If 'None' means the stream is permanently finished, change 
                        # this to 'return' so the function ends. 
                        # If it just means "no data right now", we wait a tiny bit so 
                        # the CPU doesn't spin at 100% usage doing nothing.
                        time.sleep(0.01) 
                        continue
                    
                    # Convert the NumPy data into a PyArrow Table
                    columns = [frame[:, i] for i in range(frame.shape[1])]
                    table = pa.Table.from_arrays(columns, names=column_names)
                    
                    # Append the chunk to the open file
                    writer.write_table(table)
                    
                    # Add the size of this PyArrow table to our tracker
                    bytes_written += table.nbytes
        except Exception as e:
            print(f"Error writing to Parquet file: {e}")
            time.sleep(0.01)
            continue
                
        # Once bytes_written >= file_size, the 'while' loop breaks.
        # The 'with' block ends, automatically closing and finalizing the Parquet file.
        # The outer loop restarts, generating a new timestamped file_path!
