from __future__ import annotations

import argparse
from pathlib import Path


DATA_ROOT = Path(__file__).resolve().parent / "data"
NIDAQ_FOLDER = "nidaq_raw_telemetry_data"
SKIP_COLUMNS = {"storageTimestamp"}


def find_latest_session(data_root: Path) -> Path:
    sessions = [
        path
        for path in data_root.iterdir()
        if path.is_dir() and any((path / NIDAQ_FOLDER).glob("*.parquet"))
    ]
    if not sessions:
        raise FileNotFoundError(f"No NIDAQ data sessions found in {data_root}")
    return max(sessions, key=lambda path: path.stat().st_mtime)


def parse_numbers(value: str | None) -> set[int]:
    if not value:
        return set()
    numbers: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            numbers.add(int(item))
    return numbers


def read_window(session_dir: Path, from_end: int, to_end: int):
    import pyarrow as pa
    import pyarrow.parquet as pq

    dataset_dir = session_dir / NIDAQ_FOLDER
    files = sorted(dataset_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found in {dataset_dir}")

    tables = []
    total_files = len(files)
    for index, file in enumerate(files, start=1):
        if index < total_files - from_end or index > total_files - to_end:
            continue
        print(f"\rOpening parquet files: {index}/{total_files}", end="", flush=True)
        tables.append(pq.read_table(file))
    print()

    if not tables:
        raise ValueError(
            f"No files selected. total={total_files}, from_end={from_end}, to_end={to_end}"
        )
    return pa.concat_tables(tables, promote_options="default")


def numeric_columns(table) -> list[str]:
    import pyarrow as pa

    columns = []
    for field in table.schema:
        if field.name in SKIP_COLUMNS:
            continue
        if pa.types.is_integer(field.type) or pa.types.is_floating(field.type):
            columns.append(field.name)
    return columns


def column_as_float(table, name: str):
    import numpy as np

    return np.asarray(table[name].combine_chunks().to_numpy(zero_copy_only=False), dtype=float)


def average_blocks(values, block_size: int):
    import numpy as np

    if block_size <= 1 or values.size == 0:
        return values

    full_count = values.size // block_size
    full_size = full_count * block_size
    chunks = []
    if full_size:
        chunks.append(np.nanmean(values[:full_size].reshape(full_count, block_size), axis=1))
    if full_size < values.size:
        chunks.append(np.asarray([np.nanmean(values[full_size:])], dtype=float))
    return np.concatenate(chunks) if chunks else np.asarray([], dtype=float)


def abs_scale_0_to_1(values):
    import numpy as np

    values = np.abs(values)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return values
    max_value = float(np.max(finite))
    if max_value == 0.0:
        return values
    return values / max_value


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick index-based NIDAQ parquet debug plot.")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--session", type=Path, default=None)
    parser.add_argument("--disable", "--hide", default=None, help="Comma-separated trace numbers to hide.")
    parser.add_argument("--avg", type=int, default=200, help="Samples averaged into each plotted point.")
    parser.add_argument("--from-end", type=int, default=200, help="Start this many files before the end.")
    parser.add_argument("--to-end", type=int, default=50, help="Stop this many files before the end.")
    parser.add_argument("--output", type=Path, default=None, help="PNG path. If omitted, opens a plot window.")
    args = parser.parse_args()

    session_dir = args.session if args.session is not None else find_latest_session(args.data_root)
    if not session_dir.is_absolute():
        session_dir = args.data_root / session_dir

    table = read_window(session_dir, args.from_end, args.to_end)
    columns = numeric_columns(table)
    disabled = parse_numbers(args.disable)
    selected = [(number, column) for number, column in enumerate(columns) if number not in disabled]
    if not selected:
        raise ValueError("No traces selected to plot")

    ys = {
        number: abs_scale_0_to_1(average_blocks(column_as_float(table, column), args.avg))
        for number, column in selected
    }
    point_count = min(values.size for values in ys.values())
    x = list(range(point_count))

    import matplotlib.pyplot as plt

    fig, axis = plt.subplots(figsize=(14, 7))
    for number in ys:
        axis.plot(x, ys[number][:point_count], linewidth=1.0, label=str(number))

    axis.set_xlabel(f"{args.avg}-sample average point")
    axis.set_ylabel("abs scaled 0..1")
    axis.set_ylim(-0.02, 1.02)
    axis.grid(True, alpha=0.3)
    axis.legend(loc="upper left", fontsize="small", ncols=4, title="trace")
    fig.suptitle(f"NIDAQ debug plot: {session_dir.name}")
    fig.tight_layout()

    print(f"Plotted {len(selected)} traces from {table.num_rows} rows as {point_count} points.")
    if disabled:
        print(f"Hidden traces: {', '.join(str(number) for number in sorted(disabled))}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.output, dpi=150)
        print(f"Wrote {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
