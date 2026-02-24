#!/usr/bin/env python3
import argparse
import signal
import time

import numpy as np
import pandas as pd
from questdb.ingress import IngressError, Sender


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure QuestDB ingestion throughput using questdb.ingress Sender."
    )
    parser.add_argument(
        "--protocol",
        choices=("http", "tcp"),
        default="http",
        help="Transport protocol for Sender.from_conf (default: http).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB host.")
    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="QuestDB port (default 9000 for http, use 9009 for tcp).",
    )
    parser.add_argument(
        "--conf",
        default="",
        help="Optional full questdb ingress conf string; overrides protocol/host/port.",
    )
    parser.add_argument(
        "--table",
        default="daq_bench",
        help="Table name to write into (auto-created if enabled).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per send call (default: 5000).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="Benchmark duration in seconds (default: 300 = 5 min).",
    )
    return parser.parse_args()


def build_conf(args: argparse.Namespace) -> str:
    if args.conf:
        return args.conf
    return f"{args.protocol}::addr={args.host}:{args.port};"


def build_batch_df(start_idx: int, batch_size: int, start_ns: int) -> pd.DataFrame:
    idx = np.arange(start_idx, start_idx + batch_size, dtype=np.int64)
    values = (idx % 1000).astype(np.float64) * 0.001
    ts_ns = start_ns + (np.arange(batch_size, dtype=np.int64) * 1_000_000)  # +1 ms
    return pd.DataFrame(
        {
            "ts": ts_ns.astype("datetime64[ns]"),
            "source": "usb",
            "idx": idx,
            "val": values,
        }
    )


def main() -> int:
    args = parse_args()
    conf = build_conf(args)
    stop_requested = False

    def _handle_sigint(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_sigint)

    print(
        f"Starting QuestDB ingress benchmark: conf={conf}, table={args.table}, "
        f"batch_size={args.batch_size}, duration={args.duration:.1f}s"
    )

    rows_sent = 0
    sends = 0
    row_idx = 0
    t0 = time.perf_counter()
    next_report = t0 + 1.0
    end_time = t0 + args.duration
    base_ns = time.time_ns()

    try:
        with Sender.from_conf(conf) as sender:
            while not stop_requested and time.perf_counter() < end_time:
                df = build_batch_df(
                    row_idx,
                    args.batch_size,
                    base_ns + row_idx * 1_000_000,
                )
                sender.dataframe(
                    df,
                    table_name=args.table,
                    symbols=["source"],
                    at="ts",
                )
                sender.flush()

                sends += 1
                rows_sent += args.batch_size
                row_idx += args.batch_size

                now = time.perf_counter()
                if now >= next_report:
                    elapsed = now - t0
                    rps = rows_sent / elapsed
                    bps = sends / elapsed
                    print(
                        f"[{elapsed:7.1f}s] sends={sends:8d} rows/s={rps:,.0f} "
                        f"batches/s={bps:,.1f}"
                    )
                    next_report = now + 1.0
    except (IngressError, ConnectionError, OSError, RuntimeError, ValueError) as exc:
        print(f"Connection error during benchmark: {exc}")
        return 1

    elapsed = time.perf_counter() - t0
    rps = rows_sent / elapsed if elapsed > 0 else 0.0
    bps = sends / elapsed if elapsed > 0 else 0.0

    print("\n=== QuestDB Ingress Throughput Summary ===")
    print(f"Conf:               {conf}")
    print(f"Elapsed:            {elapsed:.2f} s")
    print(f"Rows sent:          {rows_sent:,}")
    print(f"Send calls:         {sends:,}")
    print(f"Avg rows/s:         {rps:,.0f}")
    print(f"Avg batches/s:      {bps:,.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
