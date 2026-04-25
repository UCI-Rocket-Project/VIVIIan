from __future__ import annotations

import functools
import time

import numpy as np
import pyarrow as pa
import pythusa
from pyarrow import flight

NUM_SIGNALS = 8
ROWS_PER_FRAME = 1000
PREGEN_ROWS = 100_000  # multiple of ROWS_PER_FRAME so each write is a full frame


def make_random_numpy(num_rows, num_columns, random_seed=None):
    rng = np.random.default_rng(random_seed)

    # Shared time axis
    t = np.linspace(0, 1, num_rows, endpoint=False)

    signals = np.zeros((num_rows, num_columns), dtype=np.float64)

    for col in range(num_columns):
        # Random sine parameters
        sin_amp = rng.uniform(0.3, 1.5)
        sin_freq = rng.uniform(1, 10)
        sin_phase = rng.uniform(0, 2 * np.pi)

        # Random square parameters
        square_amp = rng.uniform(0.3, 1.5)
        square_freq = rng.uniform(1, 10)
        square_phase = rng.uniform(0, 2 * np.pi)

        # Build waves
        sine_wave = sin_amp * np.sin(2 * np.pi * sin_freq * t + sin_phase)
        square_wave = square_amp * np.sign(np.sin(2 * np.pi * square_freq * t + square_phase))

        # Random mixing weights
        w1 = rng.uniform(0.2, 0.8)
        w2 = 1.0 - w1

        signal = w1 * sine_wave + w2 * square_wave

        # Optional small noise
        noise = rng.normal(0, 0.05, size=num_rows)
        signal = signal + noise

        signals[:, col] = signal

    return signals


def board1_generate_data(
    *,
    stream,
    pregen_signals: np.ndarray,
    batch_size: int,
    pregen_num_rows: int,
) -> None:
    """Top-level so the callable stays picklable for multiprocessing spawn."""
    current_index = 0
    while True:
        stream.write(pregen_signals[current_index : current_index + batch_size])
        current_index += batch_size
        if current_index >= pregen_num_rows:
            current_index = 0


def board1_send_data(*, stream, flight_address: str) -> None:
    """Top-level; converts ring frames (NumPy) to Arrow batches for Flight."""
    schema = pa.schema([(f"sensor_{i}", pa.float64()) for i in range(NUM_SIGNALS)])
    client = flight.connect(flight_address)
    descriptor = flight.FlightDescriptor.for_path("high_speed_test")
    writer, _ = client.do_put(descriptor, schema)
    written_bytes = 0
    start_time = time.time()
    while True:
        frame = stream.read()
        if frame is None:
            continue
        arrays = [pa.array(frame[:, i], type=pa.float64()) for i in range(NUM_SIGNALS)]
        batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
        writer.write_batch(batch)
        written_bytes += int(frame.nbytes)
        if time.time() - start_time > 1.0:
            print(f"Written {written_bytes / 1_000_000} MB in the last second (approx)")
            start_time = time.time()
            written_bytes = 0


def main() -> None:
    if PREGEN_ROWS % ROWS_PER_FRAME != 0:
        raise ValueError("PREGEN_ROWS must be a multiple of ROWS_PER_FRAME")

    pregen = make_random_numpy(PREGEN_ROWS, NUM_SIGNALS)
    gen_fn = functools.partial(
        board1_generate_data,
        pregen_signals=pregen,
        batch_size=ROWS_PER_FRAME,
        pregen_num_rows=PREGEN_ROWS,
    )
    send_fn = functools.partial(board1_send_data, flight_address="grpc://localhost:8815")

    with pythusa.Pipeline("board1") as pipeline:
        pipeline.add_stream(
            "generated_data",
            shape=(ROWS_PER_FRAME, NUM_SIGNALS),
            dtype=np.float64,
            cache_align=True,
            frames=64,
        )
        pipeline.add_task(
            "generate_data",
            fn=gen_fn,
            writes={"stream": "generated_data"},
        )
        pipeline.add_task(
            "send_data",
            fn=send_fn,
            reads={"stream": "generated_data"},
        )
        pipeline.run()


if __name__ == "__main__":
    main()
