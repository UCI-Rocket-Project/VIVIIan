
#generic connector 
import pyarrow.flight as flight
import threading
import time
import numpy as np
import pyarrow as pa


class StorageServer(flight.FlightServerBase):
    """Flight receiver: each do_put stream is turned into NumPy frames on the pythusa ring."""

    def __init__(
        self,
        location: str,
        stream_writer,
        *,
        rows_per_frame: int,
        num_signals: int,
    ) -> None:
        super().__init__(location)
        self._stream = stream_writer
        self._write_lock = threading.Lock()
        self._rows_per_frame = rows_per_frame
        self._num_signals = num_signals

    def do_put(self, context, descriptor, reader, writer):
        print("Test stand started streaming (Flight do_put)...")
        start_time = time.time()
        received_bytes = 0
        for chunk in reader:
            record_batch = chunk.data
            arrays = [
                record_batch.column(i).to_numpy(zero_copy_only=False) for i in range(record_batch.num_columns)
            ]
            data = np.column_stack(arrays).astype(np.float64, copy=False)
            if data.shape != (self._rows_per_frame, self._num_signals):
                raise ValueError(
                    f"Expected frame shape {(self._rows_per_frame, self._num_signals)}, got {data.shape}"
                )
            with self._write_lock:
                self._stream.write(data)
            received_bytes += int(data.nbytes)
            if time.time() - start_time > 1.0:
                print(f"Flight ingest: {received_bytes / 1_000_000:.2f} MB in the last second (approx)")
                start_time = time.time()
                received_bytes = 0






def generic_connector(*, stream, field_names: list[str], flight_address: str) -> None:
    """Generic connector for any flight address."""
    while True:
        try:
            schema = pa.schema([(name, pa.float64()) for name in field_names])
            client = flight.connect(flight_address)
            descriptor = flight.FlightDescriptor.for_path("high_speed_test")
            writer, _ = client.do_put(descriptor, schema)
            written_bytes = 0
            start_time = time.time()
            while True:
                frame = stream.read()
                if frame is None:
                    continue
                arrays = [pa.array(frame[:, i], type=pa.float64()) for i in range(len(field_names))]
                batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                writer.write_batch(batch)
                written_bytes += int(frame.nbytes)
                if time.time() - start_time > 1.0:
                    print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
                    start_time = time.time()
                    written_bytes = 0
        except Exception as e:
            print(f"Error sending data: {e}")
            time.sleep(0.01)