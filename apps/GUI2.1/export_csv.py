"""Export the same selected, converted signals as plot_saved_data.py.

Example: python export_csv.py --from-end 100 --to-end 0 --output exported.csv
With --dataset both, writes exported_gse.csv and exported_nidaq.csv.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as csv

from plot_saved_data import (
    DATA_ROOT,
    DATASETS,
    DEFAULT_X_COLUMN,
    downsample,
    find_latest_common_session,
    find_latest_session,
    make_x,
    parse_columns,
    read_dataset,
    signal_as_float,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--session", type=Path, help="Session folder, relative to data-root or absolute. Default: newest session.")
    parser.add_argument("--dataset", choices=[*sorted(DATASETS), "both"], default="both")
    parser.add_argument("--columns", help="Comma-separated signals, optionally prefixed with gse. or nidaq.")
    parser.add_argument("--disable", "--exclude", dest="disabled", help="Comma-separated signals to exclude.")
    parser.add_argument("--x", default=DEFAULT_X_COLUMN, help="X column or 'index'. Default: storageTimestamp.")
    parser.add_argument("--from-end", "--file-begin-offset", type=int, default=100, help="Start this many files before the last sorted Parquet file, inclusive. Default: 100.")
    parser.add_argument("--to-end", "--file-end-offset", type=int, default=0, help="Stop this many files before the last sorted Parquet file, inclusive. Default: 0.")
    parser.add_argument("--max-points", type=int, default=0, help="Keep every Nth sample to export at most this many rows per dataset. Default: 0 (all samples).")
    parser.add_argument("--output", type=Path, default=Path("exported.csv"), help="CSV path. For both datasets, adds _gse / _nidaq to the filename. Default: exported.csv.")
    args = parser.parse_args()
    if args.from_end < 0 or args.to_end < 0 or args.from_end < args.to_end:
        parser.error("file bounds must satisfy --from-end >= --to-end >= 0")
    if args.max_points < 0:
        parser.error("--max-points must be nonnegative")

    data_root = args.data_root.resolve()
    dataset_names = sorted(DATASETS) if args.dataset == "both" else [args.dataset]
    if args.session is not None:
        session_dir = args.session if args.session.is_absolute() else data_root / args.session
    elif args.dataset == "both":
        session_dir = find_latest_common_session(data_root, dataset_names)
    else:
        session_dir = find_latest_session(data_root, args.dataset)

    exported = 0
    for dataset_name in dataset_names:
        table = read_dataset(session_dir, dataset_name, args.x, args.from_end, args.to_end)
        columns = parse_columns(args.columns, args.disabled, table, dataset_name)
        if not columns:
            continue
        x, x_label = make_x(table, args.x)
        if x.size == 0:
            raise ValueError(f"No samples to export for {dataset_name}")
        ys = {f"{dataset_name}.{column}": signal_as_float(table, dataset_name, column) for column in columns}
        x, ys = downsample(x, ys, args.max_points)

        output = args.output
        if args.dataset == "both":
            output = output.with_name(f"{output.stem}_{dataset_name}.csv")
        output.parent.mkdir(parents=True, exist_ok=True)
        csv.write_csv(pa.table({x_label: x, **ys}), output)
        print(f"Wrote {output}: {x.size} rows, {len(ys)} signals (converted as in plot_saved_data.py).")
        exported += 1

    if not exported:
        raise ValueError("No numeric signals selected to export")


if __name__ == "__main__":
    main()
