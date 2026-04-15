from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import time

import numpy as np
import pyarrow as pa

from _reporting import build_payload, emit_payload
from datastorage_utils import ParquetDatabase


DEFAULT_ROWS = 2048
DEFAULT_COLUMNS = 8
DEFAULT_DURATION_S = 5.0
DEFAULT_ROWS_PER_FILE_MULTIPLIER = 1024
DEFAULT_COMPRESSION = "snappy"


@dataclass(frozen=True)
class BenchmarkResult:
    rows: int
    columns: int
    batches_written: int
    rows_written: int
    bytes_written: int
    file_count: int
    elapsed_write_s: float
    elapsed_read_s: float
    write_batches_s: float
    write_rows_s: float
    write_mb_s: float
    read_rows: int
    read_mb: float
    read_rows_s: float
    read_mb_s: float
    start_timestamp_ns: int
    end_timestamp_ns: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark parquet-backed datastorage by hammering a target folder."
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder to write benchmark data into.",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_ROWS,
        help="Rows per stored batch.",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=DEFAULT_COLUMNS,
        help="Columns per stored batch.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="Write duration in seconds.",
    )
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=None,
        help="Rows per parquet file. Defaults to rows * 1024.",
    )
    parser.add_argument(
        "--compression",
        default=DEFAULT_COMPRESSION,
        help="Parquet compression codec.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep any existing folder contents and leave benchmark output in place.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout.",
    )
    parser.add_argument(
        "--json-out",
        help="Write JSON summary to this file.",
    )
    parser.add_argument(
        "--label",
        help="Optional label stored in structured output.",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help="Render a matplotlib summary figure.",
    )
    parser.add_argument(
        "--graph-out",
        type=Path,
        default=None,
        help="Optional path to save the matplotlib summary figure.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Build graph output without opening a matplotlib window.",
    )
    return parser.parse_args()


def _make_schema(columns: int) -> pa.Schema:
    fields = [pa.field(f"value_{index}", pa.float64()) for index in range(columns)]
    return pa.schema(fields)


def _make_batch(rows: int, columns: int) -> np.ndarray:
    batch = np.empty((rows, columns), dtype=np.float64)
    payload = np.arange(rows * columns, dtype=np.float64).reshape((rows, columns))
    batch[:] = payload
    return batch


def _existing_file_count(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for path in folder.iterdir() if path.is_file() and path.suffix == ".parquet")


def _run_benchmark(
    *,
    folder: Path,
    rows: int,
    columns: int,
    duration_s: float,
    rows_per_file: int | None,
    compression: str,
) -> BenchmarkResult:
    schema = _make_schema(columns)
    shape = (rows, columns)
    if rows_per_file is None:
        rows_per_file = rows * DEFAULT_ROWS_PER_FILE_MULTIPLIER

    batch = _make_batch(rows, columns)
    batch_bytes = int(batch.nbytes)

    with ParquetDatabase(
        folder,
        schema,
        shape,
        rows_per_file=rows_per_file,
        compression=compression,
    ) as db:
        phase_start = time.perf_counter()
        deadline = phase_start + duration_s
        batches_written = 0
        while time.perf_counter() < deadline:
            db.store(batch)
            batches_written += 1
        elapsed_write_s = time.perf_counter() - phase_start

        start_timestamp_ns = (
            int(db.manifest[0]["start_database_timestamp_ns"])
            if db.manifest
            else int(db._timestamp_buffer[0])
        )
        current_end_timestamp_ns = (
            int(db.manifest[-1]["end_database_timestamp_ns"])
            if db.manifest
            else int(db._timestamp_buffer[db._buffer_size - 1])
        )

        read_start = time.perf_counter()
        table = db.retrieve(
            start_ns=start_timestamp_ns,
            end_ns=current_end_timestamp_ns + 1,
        )
        elapsed_read_s = time.perf_counter() - read_start

        rows_written = batches_written * rows
        bytes_written = batches_written * batch_bytes
        read_rows = table.num_rows
        file_count = len(db.manifest)

    return BenchmarkResult(
        rows=rows,
        columns=columns,
        batches_written=batches_written,
        rows_written=rows_written,
        bytes_written=bytes_written,
        file_count=file_count,
        elapsed_write_s=elapsed_write_s,
        elapsed_read_s=elapsed_read_s,
        write_batches_s=batches_written / elapsed_write_s if elapsed_write_s else 0.0,
        write_rows_s=rows_written / elapsed_write_s if elapsed_write_s else 0.0,
        write_mb_s=(bytes_written / 1_000_000.0) / elapsed_write_s if elapsed_write_s else 0.0,
        read_rows=read_rows,
        read_mb=table.nbytes / 1_000_000.0,
        read_rows_s=read_rows / elapsed_read_s if elapsed_read_s else 0.0,
        read_mb_s=(table.nbytes / 1_000_000.0) / elapsed_read_s if elapsed_read_s else 0.0,
        start_timestamp_ns=start_timestamp_ns,
        end_timestamp_ns=current_end_timestamp_ns,
    )


def _print_summary(result: BenchmarkResult, folder: Path) -> None:
    print(f"folder: {folder}")
    print(f"shape: {result.rows} x {result.columns}")
    print(f"files: {result.file_count}")
    print(
        f"write: {result.batches_written} batches, {result.rows_written} rows, "
        f"{result.bytes_written / 1_000_000.0:.1f} MB in {result.elapsed_write_s:.3f}s"
    )
    print(
        f"write speed: {result.write_batches_s:.1f} batches/s, "
        f"{result.write_rows_s:.1f} rows/s, {result.write_mb_s:.1f} MB/s"
    )
    print(
        f"read: {result.read_rows} rows, {result.read_mb:.1f} MB "
        f"in {result.elapsed_read_s:.3f}s"
    )
    print(
        f"read speed: {result.read_rows_s:.1f} rows/s, {result.read_mb_s:.1f} MB/s"
    )


def _graph_requested(args: argparse.Namespace) -> bool:
    return args.graph or args.graph_out is not None


def _render_graph(
    result: BenchmarkResult,
    *,
    folder: Path,
    compression: str,
    rows_per_file: int,
    out_path: Path | None,
    show: bool,
) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))

    speed_labels = ["Write MB/s", "Read MB/s"]
    speed_values = [result.write_mb_s, result.read_mb_s]
    axes[0].bar(speed_labels, speed_values, color=["#2a9d8f", "#264653"])
    axes[0].set_title("Storage Throughput")
    axes[0].set_ylabel("MB/s")
    for index, value in enumerate(speed_values):
        axes[0].text(index, value, f"{value:.1f}", ha="center", va="bottom")

    row_labels = ["Write rows/s", "Read rows/s"]
    row_values = [result.write_rows_s, result.read_rows_s]
    axes[1].bar(row_labels, row_values, color=["#e76f51", "#f4a261"])
    axes[1].set_title("Row Rates")
    axes[1].set_ylabel("rows/s")
    for index, value in enumerate(row_values):
        axes[1].text(index, value, f"{value:.0f}", ha="center", va="bottom")

    axes[2].axis("off")
    axes[2].set_title("Run Summary")
    summary_text = "\n".join(
        [
            f"folder: {folder}",
            f"shape: {result.rows} x {result.columns}",
            f"compression: {compression}",
            f"rows_per_file: {rows_per_file}",
            f"files: {result.file_count}",
            f"write: {result.batches_written} batches",
            f"written: {result.bytes_written / 1_000_000.0:.1f} MB",
            f"read: {result.read_mb:.1f} MB",
            f"write time: {result.elapsed_write_s:.3f}s",
            f"read time: {result.elapsed_read_s:.3f}s",
        ]
    )
    axes[2].text(0.0, 1.0, summary_text, va="top", ha="left", family="monospace")

    fig.tight_layout()
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


def main() -> None:
    args = _parse_args()
    if args.rows < 1:
        raise SystemExit("--rows must be >= 1.")
    if args.columns < 1:
        raise SystemExit("--columns must be >= 1.")
    if args.duration <= 0.0:
        raise SystemExit("--duration must be > 0.")

    folder = args.folder.resolve()
    if folder.exists() and not args.keep:
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows_per_file = args.rows_per_file or args.rows * DEFAULT_ROWS_PER_FILE_MULTIPLIER

    existing_file_count = _existing_file_count(folder)
    result = _run_benchmark(
        folder=folder,
        rows=args.rows,
        columns=args.columns,
        duration_s=args.duration,
        rows_per_file=args.rows_per_file,
        compression=args.compression,
    )

    payload = build_payload(
        benchmark="datastorage_benchmark",
        config={
            "folder": folder,
            "rows": args.rows,
            "columns": args.columns,
            "duration_s": args.duration,
            "rows_per_file": rows_per_file,
            "compression": args.compression,
            "keep": args.keep,
        },
        results=result,
        label=args.label,
        notes=(
            "The benchmark hammers store(...) for the requested duration and then measures retrieve(...) across the full written time range.",
            "The database adds database_timestamp_ns internally; the benchmark does not reserve payload columns for metadata.",
            "Unless --keep is used, the target folder is cleared before the run.",
        ),
        summary={
            "existing_parquet_files_before_run": existing_file_count,
            "final_parquet_files": result.file_count,
        },
    )
    emit_payload(payload, json_stdout=args.json, json_out=args.json_out)
    if _graph_requested(args):
        _render_graph(
            result,
            folder=folder,
            compression=args.compression,
            rows_per_file=rows_per_file,
            out_path=args.graph_out,
            show=not args.no_show,
        )
    if not args.json:
        _print_summary(result, folder)


if __name__ == "__main__":
    main()
