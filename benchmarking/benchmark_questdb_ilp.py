#!/usr/bin/env python3
import argparse
import signal
import socket
import time
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure QuestDB ingestion throughput using ILP over HTTP or TCP."
    )
    parser.add_argument(
        "--protocol",
        choices=("http", "tcp"),
        default="http",
        help="Transport protocol (default: http).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB host.")
    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="QuestDB port (default 9000 for http, use 9009 for tcp).",
    )
    parser.add_argument(
        "--table",
        default="daq_bench",
        help="Table name to write into (auto-created by ILP if enabled).",
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


def build_batch(table: str, start_idx: int, batch_size: int, start_ns: int) -> bytes:
    # Static tags + integer index + float value + explicit nanosecond timestamp.
    lines = []
    ts = start_ns
    for i in range(batch_size):
        idx = start_idx + i
        value = (idx % 1000) * 0.001
        lines.append(f"{table},source=usb idx={idx}i,val={value:.6f} {ts}")
        ts += 1_000_000  # +1 ms
    return ("\n".join(lines) + "\n").encode("ascii")


def main() -> int:
    args = parse_args()
    stop_requested = False

    def _handle_sigint(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_sigint)

    print(
        f"Starting QuestDB benchmark: protocol={args.protocol}, host={args.host}, "
        f"port={args.port}, table={args.table}, batch_size={args.batch_size}, "
        f"duration={args.duration:.1f}s"
    )

    rows_sent = 0
    bytes_sent = 0
    sends = 0
    row_idx = 0
    t0 = time.perf_counter()
    next_report = t0 + 1.0
    end_time = t0 + args.duration
    base_ns = time.time_ns()
    url = f"http://{args.host}:{args.port}/write?precision=n"
    sock = None

    if args.protocol == "tcp":
        try:
            sock = socket.create_connection((args.host, args.port), timeout=5.0)
            sock.settimeout(10.0)
        except OSError as exc:
            print(f"Failed to connect to QuestDB ILP TCP at {args.host}:{args.port}: {exc}")
            return 1

    try:
        while not stop_requested and time.perf_counter() < end_time:
            payload = build_batch(
                args.table, row_idx, args.batch_size, base_ns + row_idx * 1_000_000
            )
            if args.protocol == "tcp":
                sock.sendall(payload)
            else:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    method="POST",
                    headers={"Content-Type": "text/plain"},
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    if resp.status not in (200, 204):
                        raise RuntimeError(f"Unexpected HTTP status {resp.status}")
            sends += 1
            rows_sent += args.batch_size
            bytes_sent += len(payload)
            row_idx += args.batch_size

            now = time.perf_counter()
            if now >= next_report:
                elapsed = now - t0
                rps = rows_sent / elapsed
                mib_s = bytes_sent / elapsed / (1024 * 1024)
                print(
                    f"[{elapsed:7.1f}s] sends={sends:8d} rows/s={rps:,.0f} "
                    f"net={mib_s:,.2f} MiB/s"
                )
                next_report = now + 1.0
    except (BrokenPipeError, ConnectionError, OSError, urllib.error.URLError, RuntimeError) as exc:
        print(f"Connection error during benchmark: {exc}")
        return 1
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    elapsed = time.perf_counter() - t0
    rps = rows_sent / elapsed if elapsed > 0 else 0.0
    mib_s = bytes_sent / elapsed / (1024 * 1024) if elapsed > 0 else 0.0

    print("\n=== QuestDB ILP Throughput Summary ===")
    print(f"Protocol:           {args.protocol}")
    print(f"Elapsed:            {elapsed:.2f} s")
    print(f"Rows sent:          {rows_sent:,}")
    print(f"Payload bytes sent: {bytes_sent:,}")
    print(f"Avg rows/s:         {rps:,.0f}")
    print(f"Avg net MiB/s:      {mib_s:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
