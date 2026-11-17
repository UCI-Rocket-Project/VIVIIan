"""Plot exported CSVs: first column is x, every remaining column is a signal.

Example: python plot_csv.py exported_gse.csv exported_nidaq.csv
Requires only NumPy and Matplotlib; no project modules or Parquet files.
"""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+", help="One or more exported CSV files.")
    parser.add_argument("--output", type=Path, help="Save a plot instead of opening a window.")
    args = parser.parse_args()

    fig, axis = plt.subplots(figsize=(14, 7))
    x_labels = []
    for path in args.files:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), [])
            if len(header) < 2:
                parser.error(f"{path}: expected an x column and at least one signal")
            data = np.loadtxt(handle, delimiter=",", ndmin=2)
        if data.size == 0 or data.shape[1] != len(header):
            parser.error(f"{path}: empty data or column count does not match the header")
        for index, label in enumerate(header[1:], start=1):
            axis.plot(data[:, 0], data[:, index], linewidth=1, label=label)
        if header[0] not in x_labels:
            x_labels.append(header[0])

    axis.set_xlabel(" / ".join(x_labels))
    axis.set_ylabel("value")
    axis.set_title(", ".join(path.name for path in args.files))
    axis.grid(True, alpha=0.3)
    axis.legend(fontsize="small", ncols=2)
    fig.tight_layout()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.output, dpi=150)
    else:
        plt.show()


if __name__ == "__main__":
    main()
