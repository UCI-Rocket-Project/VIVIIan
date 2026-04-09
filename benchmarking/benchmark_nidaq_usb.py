#!/usr/bin/env python3
import argparse
import signal
import sys
import time

import numpy as np

try:
    import nidaqmx
    from nidaqmx.stream_readers import AnalogMultiChannelReader
except ImportError as exc:
    print(f"Missing dependency: {exc}. Install with: pip install nidaqmx numpy")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure sustained NI-DAQ USB read throughput."
    )
    parser.add_argument(
        "--channels",
        required=True,
        help='DAQmx physical channels, e.g. "Dev1/ai0" or "Dev1/ai0:3".',
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=100000.0,
        help="Sample rate in samples/second per channel (default: 100000).",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=10000,
        help="Samples per channel to read per call (default: 10000).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="Benchmark duration in seconds (default: 300 = 5 min).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop_requested = False

    def _handle_sigint(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_sigint)

    with nidaqmx.Task() as task:
        task.ai_channels.add_ai_voltage_chan(args.channels)
        num_channels = len(task.ai_channels)
        task.timing.cfg_samp_clk_timing(
            rate=args.rate,
            sample_mode=nidaqmx.constants.AcquisitionType.CONTINUOUS,
            samps_per_chan=args.block_size,
        )

        reader = AnalogMultiChannelReader(task.in_stream)
        block = np.empty((num_channels, args.block_size), dtype=np.float64)

        print(
            f"Starting NI-DAQ benchmark: channels={args.channels}, "
            f"num_channels={num_channels}, rate={args.rate:.0f} S/s/ch, "
            f"block_size={args.block_size}, duration={args.duration:.1f}s"
        )
        task.start()

        total_samples_per_channel = 0
        reads = 0
        t0 = time.perf_counter()
        next_report = t0 + 1.0
        end_time = t0 + args.duration

        while not stop_requested and time.perf_counter() < end_time:
            reader.read_many_sample(
                data=block,
                number_of_samples_per_channel=args.block_size,
                timeout=10.0,
            )
            total_samples_per_channel += args.block_size
            reads += 1

            now = time.perf_counter()
            if now >= next_report:
                elapsed = now - t0
                total_samples = total_samples_per_channel * num_channels
                sps = total_samples / elapsed
                mib_s = (total_samples * 8) / elapsed / (1024 * 1024)
                print(
                    f"[{elapsed:7.1f}s] reads={reads:8d} "
                    f"throughput={sps:,.0f} samples/s total ({mib_s:,.2f} MiB/s)"
                )
                next_report = now + 1.0

        elapsed = time.perf_counter() - t0
        total_samples = total_samples_per_channel * num_channels
        sps = total_samples / elapsed if elapsed > 0 else 0.0
        mib_s = (total_samples * 8) / elapsed / (1024 * 1024) if elapsed > 0 else 0.0

    print("\n=== NI-DAQ USB Throughput Summary ===")
    print(f"Elapsed:                    {elapsed:.2f} s")
    print(f"Channels:                   {num_channels}")
    print(f"Total samples read:         {total_samples:,}")
    print(f"Avg throughput (samples/s): {sps:,.0f}")
    print(f"Avg throughput (MiB/s):     {mib_s:,.2f}")
    print("(MiB/s assumes float64 payload size of 8 bytes per sample.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
