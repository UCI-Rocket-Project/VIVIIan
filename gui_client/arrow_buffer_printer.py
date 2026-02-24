import time

from arrow_stream_reader import start_reader_thread
from stream_buffer import SharedRingBuffer


def main() -> None:
    shared = SharedRingBuffer(max_batches=1024)
    start_reader_thread(shared)

    while True:
        batches = shared.snapshot()
        if not batches:
            print("waiting for data...")
            time.sleep(0.2)
            continue

        columns, rows = batches[-1]
        latest_row = rows[-1] if rows else None
        print(f"batches={len(batches)} latest_batch_rows={len(rows)} columns={columns} latest_row={latest_row}")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
