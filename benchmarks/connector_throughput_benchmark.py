from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

from _reporting import build_payload, emit_payload
from connector_utils import ReceiveConnector, SendConnector, StreamSpec


DEFAULT_ROWS = (512, 2048, 8192)
DEFAULT_COLUMNS = (4, 8, 16, 32)
DEFAULT_DURATION_S = 1.0
DEFAULT_WARMUP_S = 0.25
DEFAULT_DRAIN_S = 0.2
RETRY_SLEEP_S = 0.001

SEQUENCE_COLUMN = 0
SENT_AT_NS_COLUMN = 1


@dataclass(frozen=True)
class BenchmarkResult:
    rows: int
    columns: int
    payload_columns: int
    batch_bytes: int
    published_batches: int
    observed_batches: int
    published_batches_s: float
    observed_batches_s: float
    published_mb_s: float
    observed_mb_s: float
    overwrite_fraction: float
    latency_mean_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float


class InstrumentedReceiveConnector(ReceiveConnector):
    def __init__(
        self,
        stream_spec: StreamSpec,
        port: int,
        run_origin_ns: int,
        host: str = "127.0.0.1",
    ) -> None:
        super().__init__(stream_spec, port, host)
        self._run_origin_ns = run_origin_ns
        self._measurement_start_sequence = 0
        self.reset_measurement(0)

    def reset_measurement(self, start_sequence: int) -> None:
        self._measurement_start_sequence = int(start_sequence)
        self.observed_batches = 0
        self._latency_samples_ns: list[int] = []
        self._last_sequence = start_sequence - 1

    def snapshot_metrics(self) -> tuple[int, np.ndarray]:
        return self.observed_batches, np.asarray(self._latency_samples_ns, dtype=np.int64)

    def _reader_loop(self) -> None:
        while not self._closing:
            try:
                self._reader = self.do_get(
                    flight.Ticket(self.stream_spec.stream_id.encode("utf-8"))
                )
                while not self._closing:
                    chunk = self._reader.read_chunk()
                    record_batch = chunk.data
                    for index in range(self.stream_spec.column_count):
                        self.batch[:, index] = record_batch.column(index).to_numpy(
                            zero_copy_only=False
                        )
                    self.has_batch = True

                    sequence = int(self.batch[0, SEQUENCE_COLUMN])
                    if sequence >= self._measurement_start_sequence and sequence != self._last_sequence:
                        self._last_sequence = sequence
                        self.observed_batches += 1
                        sent_offset_ns = int(self.batch[0, SENT_AT_NS_COLUMN])
                        latency_ns = time.perf_counter_ns() - self._run_origin_ns - sent_offset_ns
                        self._latency_samples_ns.append(max(latency_ns, 0))
            except StopIteration:
                pass
            except pa.ArrowException:
                if self._closing:
                    return
            finally:
                self._reader = None

            if not self._closing:
                time.sleep(0.05)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VIVIIan connector throughput benchmark.")
    parser.add_argument(
        "--rows",
        default=",".join(str(value) for value in DEFAULT_ROWS),
        help="Comma-separated row counts to benchmark.",
    )
    parser.add_argument(
        "--columns",
        default=",".join(str(value) for value in DEFAULT_COLUMNS),
        help="Comma-separated total column counts to benchmark, including sequence and sent_at_ns.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="Measurement duration per shape in seconds.",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=DEFAULT_WARMUP_S,
        help="Warmup duration per shape in seconds.",
    )
    parser.add_argument(
        "--target-gbps",
        type=float,
        default=None,
        help="Optional producer pacing target in gigabits per second.",
    )
    parser.add_argument(
        "--label",
        help="Optional label stored in structured output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON summary to stdout instead of the text table.",
    )
    parser.add_argument(
        "--json-out",
        help="Write the JSON summary to this file path.",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help="Render heatmaps after the benchmark completes.",
    )
    parser.add_argument(
        "--graph-out",
        type=Path,
        default=None,
        help="Optional path to save the heatmaps.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Build graph output without opening a matplotlib window.",
    )
    return parser.parse_args()


def _parse_int_csv(text: str, *, name: str, minimum: int) -> tuple[int, ...]:
    values: list[int] = []
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            continue
        value = int(item)
        if value < minimum:
            raise SystemExit(f"{name} values must be >= {minimum}.")
        values.append(value)
    if not values:
        raise SystemExit(f"Provide at least one {name} value.")
    return tuple(values)


def _graph_requested(args: argparse.Namespace) -> bool:
    return args.graph or args.graph_out is not None


def _make_schema(total_columns: int) -> pa.Schema:
    fields = [
        pa.field("sequence", pa.float64()),
        pa.field("sent_at_ns", pa.float64()),
    ]
    for index in range(total_columns - 2):
        fields.append(pa.field(f"payload_{index}", pa.float64()))
    return pa.schema(fields)


def _prepare_batch(batch: np.ndarray, sequence: int, sent_offset_ns: int) -> None:
    batch[:, SEQUENCE_COLUMN].fill(float(sequence))
    batch[:, SENT_AT_NS_COLUMN].fill(float(sent_offset_ns))


def _latency_percentile_ms(samples_ns: np.ndarray, percentile: float) -> float:
    if samples_ns.size == 0:
        return 0.0
    return float(np.percentile(samples_ns / 1_000_000.0, percentile))


def _metric_matrix(
    results: list[BenchmarkResult],
    *,
    rows: tuple[int, ...],
    columns: tuple[int, ...],
    value_attr: str,
) -> np.ndarray:
    matrix = np.empty((len(rows), len(columns)), dtype=np.float64)
    for row_index, row_value in enumerate(rows):
        for col_index, col_value in enumerate(columns):
            for result in results:
                if result.rows == row_value and result.columns == col_value:
                    matrix[row_index, col_index] = getattr(result, value_attr)
                    break
            else:  # pragma: no cover - every shape should be present.
                raise RuntimeError("missing benchmark result for shape")
    return matrix


def _pace_send(
    *,
    target_gbps: float | None,
    batch_bytes: int,
    sent_batches: int,
    phase_start: float,
) -> None:
    if target_gbps is None:
        return
    target_bytes_per_second = (target_gbps * 1_000_000_000.0) / 8.0
    if target_bytes_per_second <= 0.0:
        return
    target_elapsed_s = ((sent_batches + 1) * batch_bytes) / target_bytes_per_second
    sleep_s = (phase_start + target_elapsed_s) - time.perf_counter()
    if sleep_s > 0.0:
        time.sleep(sleep_s)


def _run_shape(
    *,
    rows: int,
    columns: int,
    duration_s: float,
    warmup_s: float,
    target_gbps: float | None,
) -> BenchmarkResult:
    if columns < 2:
        raise SystemExit("column count must be >= 2 to hold sequence and sent_at_ns.")

    run_origin_ns = time.perf_counter_ns()
    batch = np.empty((rows, columns), dtype=np.float64)
    if columns > 2:
        payload_values = np.arange(columns - 2, dtype=np.float64)
        batch[:, 2:] = payload_values

    spec = StreamSpec(
        stream_id=f"connector_bench_{rows}x{columns}",
        schema=_make_schema(columns),
        shape=(rows, columns),
    )
    batch_bytes = rows * columns * np.dtype(np.float64).itemsize

    with SendConnector(spec, port=0) as sender, InstrumentedReceiveConnector(
        spec,
        port=sender.port,
        run_origin_ns=run_origin_ns,
    ) as receiver:
        next_sequence = 0
        warmup_start = time.perf_counter()
        warmup_deadline = warmup_start + warmup_s
        while time.perf_counter() < warmup_deadline:
            _prepare_batch(batch, next_sequence, time.perf_counter_ns() - run_origin_ns)
            sender.send_numpy(batch)
            _pace_send(
                target_gbps=target_gbps,
                batch_bytes=batch_bytes,
                sent_batches=next_sequence,
                phase_start=warmup_start,
            )
            next_sequence += 1

        measurement_start_sequence = next_sequence
        measurement_start_ns = time.perf_counter_ns()
        measurement_start = time.perf_counter()
        receiver.reset_measurement(measurement_start_sequence)

        measurement_deadline = measurement_start + duration_s
        while time.perf_counter() < measurement_deadline:
            _prepare_batch(batch, next_sequence, time.perf_counter_ns() - run_origin_ns)
            sender.send_numpy(batch)
            _pace_send(
                target_gbps=target_gbps,
                batch_bytes=batch_bytes,
                sent_batches=next_sequence - measurement_start_sequence,
                phase_start=measurement_start,
            )
            next_sequence += 1

        published_batches = next_sequence - measurement_start_sequence
        final_sequence = next_sequence - 1
        drain_deadline = time.perf_counter() + DEFAULT_DRAIN_S
        while time.perf_counter() < drain_deadline:
            if receiver.has_batch and int(receiver.batch[0, SEQUENCE_COLUMN]) >= final_sequence:
                break
            time.sleep(RETRY_SLEEP_S)

        measurement_elapsed_s = (time.perf_counter_ns() - measurement_start_ns) / 1_000_000_000.0
        observed_batches, latency_samples_ns = receiver.snapshot_metrics()

    published_batches_s = published_batches / max(measurement_elapsed_s, 1e-12)
    observed_batches_s = observed_batches / max(measurement_elapsed_s, 1e-12)
    published_mb_s = published_batches_s * batch_bytes / 1_000_000.0
    observed_mb_s = observed_batches_s * batch_bytes / 1_000_000.0
    overwrite_fraction = 0.0
    if published_batches > 0:
        overwrite_fraction = max(published_batches - observed_batches, 0) / published_batches

    latency_mean_ms = 0.0
    latency_max_ms = 0.0
    if latency_samples_ns.size:
        latency_mean_ms = float(latency_samples_ns.mean() / 1_000_000.0)
        latency_max_ms = float(latency_samples_ns.max() / 1_000_000.0)

    return BenchmarkResult(
        rows=rows,
        columns=columns,
        payload_columns=columns - 2,
        batch_bytes=batch_bytes,
        published_batches=published_batches,
        observed_batches=observed_batches,
        published_batches_s=published_batches_s,
        observed_batches_s=observed_batches_s,
        published_mb_s=published_mb_s,
        observed_mb_s=observed_mb_s,
        overwrite_fraction=overwrite_fraction,
        latency_mean_ms=latency_mean_ms,
        latency_p50_ms=_latency_percentile_ms(latency_samples_ns, 50),
        latency_p95_ms=_latency_percentile_ms(latency_samples_ns, 95),
        latency_p99_ms=_latency_percentile_ms(latency_samples_ns, 99),
        latency_max_ms=latency_max_ms,
    )


def _print_header(rows: tuple[int, ...], columns: tuple[int, ...], duration_s: float, warmup_s: float) -> None:
    print("VIVIIan Connector Throughput Benchmark")
    print(
        f"config rows={list(rows)} columns={list(columns)} duration={duration_s:.2f}s "
        f"warmup={warmup_s:.2f}s metadata_columns=2"
    )
    print(
        "rows  cols payload  batch_kb  pub/s      obs/s      pub_mb/s   obs_mb/s   overwrite  "
        "mean_ms   p95_ms    p99_ms    max_ms"
    )


def _print_result(result: BenchmarkResult) -> None:
    print(
        f"{result.rows:4d}"
        f"{result.columns:6d}"
        f"{result.payload_columns:8d}"
        f"{result.batch_bytes / 1024.0:10.1f}"
        f"{result.published_batches_s:11.1f}"
        f"{result.observed_batches_s:11.1f}"
        f"{result.published_mb_s:11.1f}"
        f"{result.observed_mb_s:11.1f}"
        f"{result.overwrite_fraction:11.3f}"
        f"{result.latency_mean_ms:10.3f}"
        f"{result.latency_p95_ms:10.3f}"
        f"{result.latency_p99_ms:10.3f}"
        f"{result.latency_max_ms:10.3f}"
    )


def _load_pyplot():
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:  # pragma: no cover - local benchmark env includes matplotlib.
        raise SystemExit(
            "Graphing requires matplotlib."
        ) from exc
    return plt


def _annotate_heatmap(ax, matrix: np.ndarray, *, fmt: str) -> None:
    finite = matrix[np.isfinite(matrix)]
    threshold = np.nanmean(finite) if finite.size else 0.0

    for row_index in range(matrix.shape[0]):
        for col_index in range(matrix.shape[1]):
            value = matrix[row_index, col_index]
            if not np.isfinite(value):
                continue
            color = "black" if value >= threshold else "white"
            ax.text(
                col_index,
                row_index,
                format(value, fmt),
                ha="center",
                va="center",
                color=color,
                fontsize=8,
            )


def _draw_heatmap(
    plt,
    ax,
    matrix: np.ndarray,
    *,
    title: str,
    xticklabels: list[str],
    yticklabels: list[str],
    colorbar_label: str,
    annotation_fmt: str,
    cmap: str,
) -> None:
    image = ax.imshow(matrix, aspect="auto", cmap=cmap)
    ax.set_title(title)
    ax.set_xlabel("Columns")
    ax.set_xticks(range(len(xticklabels)))
    ax.set_xticklabels(xticklabels)
    ax.set_yticks(range(len(yticklabels)))
    ax.set_yticklabels(yticklabels)
    _annotate_heatmap(ax, matrix, fmt=annotation_fmt)
    plt.colorbar(image, ax=ax, label=colorbar_label)


def _plot_heatmaps(
    results: list[BenchmarkResult],
    *,
    rows: tuple[int, ...],
    columns: tuple[int, ...],
    plot_out: Path | None,
    no_show: bool,
) -> None:
    plt = _load_pyplot()
    xticklabels = [str(value) for value in columns]
    yticklabels = [str(value) for value in rows]
    drop_percent_matrix = (
        _metric_matrix(results, rows=rows, columns=columns, value_attr="overwrite_fraction")
        * 100.0
    )

    figure, axes = plt.subplots(2, 3, figsize=(22, 10))
    _draw_heatmap(
        plt,
        axes[0, 0],
        _metric_matrix(results, rows=rows, columns=columns, value_attr="published_mb_s"),
        title="Published Throughput",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="MB/s",
        annotation_fmt=".1f",
        cmap="viridis",
    )
    _draw_heatmap(
        plt,
        axes[0, 1],
        _metric_matrix(results, rows=rows, columns=columns, value_attr="observed_mb_s"),
        title="Observed Throughput",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="MB/s",
        annotation_fmt=".1f",
        cmap="viridis",
    )
    _draw_heatmap(
        plt,
        axes[0, 2],
        drop_percent_matrix,
        title="Dropped Batches",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="%",
        annotation_fmt=".1f",
        cmap="magma",
    )
    _draw_heatmap(
        plt,
        axes[1, 0],
        _metric_matrix(results, rows=rows, columns=columns, value_attr="latency_mean_ms"),
        title="Mean Latency",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="ms",
        annotation_fmt=".3f",
        cmap="viridis_r",
    )
    _draw_heatmap(
        plt,
        axes[1, 1],
        _metric_matrix(results, rows=rows, columns=columns, value_attr="latency_p95_ms"),
        title="P95 Latency",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="ms",
        annotation_fmt=".3f",
        cmap="viridis_r",
    )
    _draw_heatmap(
        plt,
        axes[1, 2],
        _metric_matrix(results, rows=rows, columns=columns, value_attr="latency_p99_ms"),
        title="P99 Latency",
        xticklabels=xticklabels,
        yticklabels=yticklabels,
        colorbar_label="ms",
        annotation_fmt=".3f",
        cmap="viridis_r",
    )

    figure.suptitle("VIVIIan Connector Heatmaps", fontsize=14)
    figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))

    if plot_out is not None:
        plot_out.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(plot_out, dpi=180)

    if no_show:
        plt.close(figure)
    else:
        plt.show()


def _build_payload(
    *,
    rows: tuple[int, ...],
    columns: tuple[int, ...],
    duration_s: float,
    warmup_s: float,
    target_gbps: float | None,
    results: list[BenchmarkResult],
    label: str | None,
) -> dict[str, object]:
    best_observed = max(results, key=lambda item: item.observed_mb_s)
    lowest_p95 = min(results, key=lambda item: item.latency_p95_ms)
    return build_payload(
        benchmark="connector_throughput_benchmark",
        label=label,
        config={
            "rows": list(rows),
            "columns": list(columns),
            "duration_s": duration_s,
            "warmup_s": warmup_s,
            "target_gbps": target_gbps,
            "metadata_columns": 2,
        },
        results=results,
        summary={
            "best_observed_shape": [best_observed.rows, best_observed.columns],
            "best_observed_mb_s": best_observed.observed_mb_s,
            "lowest_p95_shape": [lowest_p95.rows, lowest_p95.columns],
            "lowest_p95_ms": lowest_p95.latency_p95_ms,
        },
        notes=(
            "First two columns are reserved for sequence and sent_at_ns benchmark metadata.",
            "sent_at_ns is stored as perf_counter_ns offset from run origin to stay precise in float64.",
            "Observed throughput counts only batches the receiver actually saw.",
            "Dropped-batch percentage heatmaps are derived from overwrite_fraction * 100.",
            "When target_gbps is set, the producer is paced by batch_bytes over wall-clock time.",
        ),
    )


def main() -> None:
    args = _parse_args()
    rows = _parse_int_csv(args.rows, name="rows", minimum=1)
    columns = _parse_int_csv(args.columns, name="columns", minimum=2)
    if args.duration <= 0.0:
        raise SystemExit("--duration must be > 0.")
    if args.warmup < 0.0:
        raise SystemExit("--warmup must be >= 0.")
    if args.target_gbps is not None and args.target_gbps <= 0.0:
        raise SystemExit("--target-gbps must be > 0 when provided.")
    if args.no_show and not _graph_requested(args):
        raise SystemExit("--no-show requires --graph or --graph-out.")

    results: list[BenchmarkResult] = []
    if not args.json:
        _print_header(rows, columns, args.duration, args.warmup)
    for row_count in rows:
        for column_count in columns:
            result = _run_shape(
                rows=row_count,
                columns=column_count,
                duration_s=args.duration,
                warmup_s=args.warmup,
                target_gbps=args.target_gbps,
            )
            results.append(result)
            if not args.json:
                _print_result(result)

    payload = _build_payload(
        rows=rows,
        columns=columns,
        duration_s=args.duration,
        warmup_s=args.warmup,
        target_gbps=args.target_gbps,
        results=results,
        label=args.label,
    )
    emit_payload(payload, json_stdout=args.json, json_out=args.json_out)

    if _graph_requested(args):
        _plot_heatmaps(
            results,
            rows=rows,
            columns=columns,
            plot_out=args.graph_out,
            no_show=args.no_show,
        )
        if not args.json and args.graph_out is not None:
            print(f"graph_out={args.graph_out}")
    if not args.json and args.json_out is not None:
        print(f"json_out={args.json_out}")


if __name__ == "__main__":
    main()
