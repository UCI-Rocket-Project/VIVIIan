from __future__ import annotations

import argparse
from pathlib import Path

from gse21connector import GSE2V1_EXTERNAL_ADC_BITS

DATA_ROOT = Path(__file__).resolve().parent / "data"
DATASETS = {
    "gse": "gse2v1_raw_telemetry_data",
    "nidaq": "nidaq_raw_telemetry_data",
}
DEFAULT_X_COLUMN = "storageTimestamp"
SKIP_DEFAULT_COLUMNS = {"magicHeader", "timestamp", "storageTimestamp", "crc"}

# Manual saved-column renames, grouped by dataset.
# Each dictionary is applied simultaneously, only if ALL its source fields exist.
# Use the resulting names in scales, styles, --columns, and --disable.
# These rules change the loaded table only; the Parquet files stay unchanged.
FIELD_RENAMES = {
    "gse": [
        {"pressure3": "LOX Tank", "LOX Tank": "pressure2"},
    ],
    "nidaq": [],
}


def renamed_field_names(column_names: list[str], dataset_name: str) -> list[str]:
    names = list(column_names)
    for renames in FIELD_RENAMES.get(dataset_name, []):
        if not all(source in names for source in renames):
            continue
        names = [renames.get(name, name) for name in names]
        if len(names) != len(set(names)):
            raise ValueError(f"FIELD_RENAMES creates duplicate column names for {dataset_name}: {names}")
    return names


def is_default_signal(dataset_name: str, column: str) -> bool:
    if dataset_name == "gse":
        return "InternalState" in column or column in GSE2V1_EXTERNAL_ADC_BITS
    if dataset_name == "nidaq":
        return column not in SKIP_DEFAULT_COLUMNS
    return column not in SKIP_DEFAULT_COLUMNS


def find_latest_session(data_root: Path, dataset_name: str) -> Path:
    dataset_folder = DATASETS[dataset_name]
    sessions = [
        path
        for path in data_root.iterdir()
        if path.is_dir() and any((path / dataset_folder).glob("*.parquet"))
    ]
    if not sessions:
        raise FileNotFoundError(f"No {dataset_name} data sessions found in {data_root}")
    return max(sessions, key=lambda path: path.stat().st_mtime)


def find_latest_common_session(data_root: Path, dataset_names: list[str]) -> Path:
    sessions = [
        path
        for path in data_root.iterdir()
        if path.is_dir()
        and all(any((path / DATASETS[dataset_name]).glob("*.parquet")) for dataset_name in dataset_names)
    ]
    if not sessions:
        names = ", ".join(dataset_names)
        raise FileNotFoundError(f"No data sessions containing all requested datasets: {names}")
    return max(sessions, key=lambda path: path.stat().st_mtime)

# Each calibration is (scale, offset): plotted = recorded * scale + offset.
NIDAQ_SCALES = {
    # "ai0": (402.45048,0), # this is wrong
    # "ai1": (1,0),
    # "LNGTANK": (402.45048,-0.471844),
    # "LOXTANK": (402.45048,-0.471844),
    
    # "VENT": (402.45048,0),
    # "COPV": (1255.98144,0),#(24471.303,5.4077),
    # # "ai7": (1,0),
    # "LOXING": (402.45048,0),
    # "LNGING": (402.45048,0),
    # # "ai10": (1,0),
    # "LOXPOT": (402.45048,0),
    # "LNGPOT": (402.45048,0),
    # "PT10": (1,0),
    # "Thrust": (1,0),
    "COPV": (2284.4816,-45.4223),#(24471.303,5.4077),
    # "ai7": (1,0),
    "LOXING": (402.45048,0),
    "LNGING": (402.45048,0),
    # "ai10": (1,0),
    "LOXPOT": (402.45048,0),
    "LNGPOT": (402.45048,0),
    # "PT10": (1,0),
}

GSE_SCALES = {
    "Tank Vent": (402.94591, 1.87778),
    "LNG Tank": (402.94591, 1.87778),
    "LOX Tank": (402.94591, 1.87778),
    "pressure2": (402.94591, 1.87778),
}

# Extra y-axes share the main plot's height and time axis.
# Add signals to a group to share its range, or add another group for a new axis.
# Signal names use FIELD_RENAMES results; dataset prefixes are optional.
# Calibration still comes from NIDAQ_SCALES / GSE_SCALES above.
ALT_Y_AXES = {
    "COPV": {
        "signals": ["nidaq.COPV"],
        "limits": (0, 5000),
        "label": "COPV pressure (psi)",
    },
}

SIGNAL_COLORS = {
    "LNG Tank": "tab:blue",
    "Tank Vent": "tab:orange",
    "COPV": "tab:green",
    "LOXING": "tab:red",
    "LNGING": "tab:purple",
    "LOX Tank": "tab:brown",
    "LNGPOT": "tab:pink",
    "LOXPOT": "tab:gray",
}
SIGNAL_LINESTYLES = {
    "Tank Vent": "--",
    "COPV": "--",
    "LOXING": "-.",
    "LNGING": "-.",
    "LNG Tank": "-",
    "LOX Tank": "-",
    "LNGPOT": ":",
    "LOXPOT": ":",
}
ROLLING_AVERAGE_SAMPLES = {
    "COPV": 0,
    "LNGING": 0, 
    "LOXING": 0,
    "LOXTANK": 0, 
    "LNGTANK": 0
}


def signal_format_value(
    mapping: dict[str, str], dataset_name: str, column: str
) -> str | None:
    return mapping.get(f"{dataset_name}.{column}", mapping.get(column))


def plot_style(dataset_name: str, column: str) -> dict[str, str]:
    style = {}
    color = signal_format_value(SIGNAL_COLORS, dataset_name, column)
    linestyle = signal_format_value(SIGNAL_LINESTYLES, dataset_name, column)
    if color is not None:
        style["color"] = color
    if linestyle is not None:
        style["linestyle"] = linestyle
    return style


def format_pressure_axis(axis) -> None:
    from matplotlib.ticker import MultipleLocator

    axis.set_ylabel("pressure (psi)")
    axis.yaxis.set_major_locator(MultipleLocator(100))
    axis.yaxis.set_minor_locator(MultipleLocator(25))
    axis.xaxis.set_major_locator(MultipleLocator(1))
    axis.grid(which="major", axis="y", linewidth=0.8, alpha=0.45)
    axis.grid(which="minor", axis="y", linewidth=0.35, alpha=0.3)
    axis.grid(which="major", axis="x", linewidth=0.35, alpha=0.3)


def columns_to_read(column_names: list[str], dataset_name: str, x_name: str) -> list[str]:
    if dataset_name != "nidaq":
        return list(column_names)

    x_columns = {DEFAULT_X_COLUMN, "timestamp"}
    if x_name != "index":
        x_columns.add(x_name)

    return [
        column
        for column, renamed in zip(column_names, renamed_field_names(column_names, dataset_name))
        if renamed in NIDAQ_SCALES or renamed in x_columns
    ]


def read_dataset(session_dir: Path, dataset_name: str, x_name: str) -> pa.Table:
    import pyarrow as pa
    import pyarrow.parquet as pq

    dataset_dir = session_dir / DATASETS[dataset_name]
    files = sorted(dataset_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found in {dataset_dir}")

    tables = []
    skipped: list[tuple[Path, str]] = []
    total_files = len(files)
    FILE_BEGIN_OFFSET = 70
    FILE_END_OFFSET = 50
    for index, file in enumerate(files, start=1):
        if index < total_files - FILE_BEGIN_OFFSET or index > total_files - FILE_END_OFFSET:
            continue
        print(f"\rOpening {dataset_name} parquet files: {index}/{len(files)}", end="", flush=True)
        try:
            column_names = pq.read_schema(file).names
            selected_columns = columns_to_read(column_names, dataset_name, x_name)
            if not selected_columns:
                skipped.append((file, "No columns selected to read"))
                continue
            # Resolve names from the full schema before selecting columns, so a
            # rename group also works when only some of its fields are loaded.
            renamed = dict(zip(column_names, renamed_field_names(column_names, dataset_name)))
            table = pq.read_table(file, columns=selected_columns)
            tables.append(table.rename_columns([renamed[name] for name in table.column_names]))
        except (pa.lib.ArrowInvalid, OSError, EOFError) as exc:
            skipped.append((file, str(exc)))
    print()

    for file, reason in skipped:
        print(f"Skipped unreadable parquet file; not plotted: {file}")
        print(f"  {reason}")

    if not tables:
        raise ValueError(f"No readable parquet files found in {dataset_dir}")

    print(
        f"Opened {len(tables)}/{len(files)} {dataset_name} parquet files; "
        f"skipped {len(skipped)}."
    )
    return pa.concat_tables(tables, promote_options="default")


def numeric_columns(table) -> list[str]:
    import pyarrow as pa

    columns = []
    for field in table.schema:
        if pa.types.is_integer(field.type) or pa.types.is_floating(field.type):
            columns.append(field.name)
    return columns


def plottable_columns(table, dataset_name: str) -> list[str]:
    columns = numeric_columns(table)
    return [column for column in columns if column not in SKIP_DEFAULT_COLUMNS]


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def signal_matches(pattern: str, dataset_name: str, column: str) -> bool:
    return pattern == column or pattern == f"{dataset_name}.{column}"


def parse_columns(value: str | None, disabled_value: str | None, table, dataset_name: str) -> list[str]:
    available = plottable_columns(table, dataset_name)
    requested = split_csv(value)
    disabled = split_csv(disabled_value)
    if value:
        columns = [
            column
            for column in available
            if any(signal_matches(pattern, dataset_name, column) for pattern in requested)
        ]
        missing = [
            pattern
            for pattern in requested
            if "." not in pattern or pattern.split(".", 1)[0] == dataset_name
            if not any(signal_matches(pattern, dataset_name, column) for column in available)
        ]
        if missing:
            raise ValueError(f"Columns not found for {dataset_name}: {', '.join(missing)}")
    else:
        columns = [column for column in available if is_default_signal(dataset_name, column)]

    return [
        column
        for column in columns
        if not any(signal_matches(pattern, dataset_name, column) for pattern in disabled)
    ]


def column_as_float(table, name: str):
    import numpy as np

    return np.asarray(table[name].combine_chunks().to_numpy(zero_copy_only=False), dtype=float)


def rolling_average(values, window_samples: int):
    import numpy as np

    if window_samples <= 1 or values.size == 0:
        return values

    window_samples = min(window_samples, values.size)
    cumsum = np.cumsum(values, dtype=float)
    totals = cumsum.copy()
    totals[window_samples:] = cumsum[window_samples:] - cumsum[:-window_samples]
    counts = np.minimum(np.arange(1, values.size + 1, dtype=float), float(window_samples))
    return totals / counts


def signal_as_float(table, dataset_name: str, name: str):
    values = column_as_float(table, name)
    if dataset_name == "gse" and name in GSE2V1_EXTERNAL_ADC_BITS:
        import numpy as np

        if "externalAdcValidBitmask" not in table.column_names:
            print(f"Warning: {name}: no externalAdcValidBitmask recorded; samples marked invalid.")
            return np.full(values.shape, np.nan)
        masks = column_as_float(table, "externalAdcValidBitmask")
        valid_masks = np.isfinite(masks) & (masks >= 0) & (masks <= 255) & (masks == np.floor(masks))
        safe_masks = np.where(valid_masks, masks, 0).astype(np.uint8)
        valid = np.isfinite(values) & ((safe_masks & GSE2V1_EXTERNAL_ADC_BITS[name]) != 0)
        invalid_count = int((~valid).sum())
        if invalid_count:
            print(f"{name}: {invalid_count}/{values.size} invalid samples shown as gaps.")
        values = np.where(valid, values, np.nan)

    scales = {"nidaq": NIDAQ_SCALES, "gse": GSE_SCALES}.get(dataset_name, {})
    if name in scales:
        scale, offset = scales[name]
        values = values * scale + offset

    if dataset_name == "nidaq" and name in NIDAQ_SCALES:
        window_samples = ROLLING_AVERAGE_SAMPLES.get(name)
        if window_samples is not None:
            values = rolling_average(values, window_samples)
        return values
    return values


def make_x(table, x_name: str):
    import numpy as np

    if x_name == "index" or x_name not in table.column_names:
        if x_name == DEFAULT_X_COLUMN and "timestamp" in table.column_names:
            print(f"Warning: {DEFAULT_X_COLUMN!r} not found. Falling back to packet timestamp.")
            x_name = "timestamp"
        else:
            return np.arange(table.num_rows, dtype=float), "sample index"

    if x_name == "index":
        return np.arange(table.num_rows, dtype=float), "sample index"

    x = column_as_float(table, x_name)
    if x.size > 1 and np.any(np.diff(x) < 0):
        print(f"Warning: x column {x_name!r} goes backwards. Timestamp reset is present in this recording.")
    return x, x_name


def downsample(x, ys: dict[str, object], max_points: int):
    import numpy as np

    if max_points <= 0 or x.size <= max_points:
        return x, ys
    stride = int(np.ceil(x.size / max_points))
    downsampled_x = x[::stride]
    print(
        f"Downsampled plotted data from {x.size} to {downsampled_x.size} samples "
        f"because --max-points={max_points}."
    )
    return downsampled_x, {name: y[::stride] for name, y in ys.items()}


def enable_legend_toggling(fig, legend, lines) -> None:
    """Click a legend label or line sample to toggle its plotted signal."""
    entries = list(zip(legend.get_lines(), legend.get_texts(), lines))
    for sample, _, _ in entries:
        sample.set_pickradius(5)

    def on_click(event):
        if event.button != 1:
            return
        for sample, text, line in entries:
            if sample.contains(event)[0] or text.contains(event)[0]:
                visible = not line.get_visible()
                line.set_visible(visible)
                alpha = 1.0 if visible else 0.2
                sample.set_alpha(alpha)
                text.set_alpha(alpha)
                fig.canvas.draw_idle()
                break

    # Canvas clicks also work when the legend overlaps a twinned axis.
    fig.canvas.mpl_connect("button_press_event", on_click)


def new_right_axis(axis):
    """Space additional right-hand axes so their ticks and labels stay readable."""
    offset = 60 * (len(axis.figure.axes) - 1)
    extra = axis.twinx()
    extra.spines["right"].set_position(("outward", offset))
    return extra


def signal_plot_axis(axis, default_axis, dataset_name: str, column: str, alt_axes: dict):
    for group, config in ALT_Y_AXES.items():
        if not any(signal_matches(pattern, dataset_name, column) for pattern in config["signals"]):
            continue
        if group not in alt_axes:
            extra = new_right_axis(axis)
            extra.set_ylabel(config["label"])
            extra.set_ylim(*config["limits"])
            color = plot_style(dataset_name, column).get("color")
            if color is not None:
                extra.yaxis.label.set_color(color)
                extra.tick_params(axis="y", colors=color)
                extra.spines["right"].set_color(color)
            alt_axes[group] = extra
        return alt_axes[group]
    return default_axis


def gse_plot_axes(axis, columns: list[str], *, combined: bool):
    """Keep analog readings separate from the fixed 0/1 internal-state axis."""
    has_states = any("InternalState" in column for column in columns)
    has_sensors = any("InternalState" not in column for column in columns)
    axes = {}
    if has_states:
        states = new_right_axis(axis) if combined or has_sensors else axis
        states.set_ylabel("GSE internal state")
        states.set_ylim(-0.05, 1.05)
        axes[True] = states
    if has_sensors:
        if not combined:
            sensor_columns = [column for column in columns if "InternalState" not in column]
            if all(column in GSE_SCALES for column in sensor_columns):
                format_pressure_axis(axis)
            else:
                axis.set_ylabel("GSE sensor value (scaled where configured)")
        axes[False] = axis
    return axes


def saved_signal_label(dataset_name: str, column: str, values) -> str:
    import numpy as np

    label = f"{dataset_name}.{column}"
    return label if np.isfinite(values).any() else f"{label} (INVALID)"


def plot_dataset(
    *,
    session_dir: Path,
    dataset_name: str,
    columns_arg: str | None,
    disabled_arg: str | None,
    x_name: str,
    max_points: int,
    output: Path | None,
) -> None:
    import matplotlib.pyplot as plt

    table = read_dataset(session_dir, dataset_name, x_name)
    columns = parse_columns(columns_arg, disabled_arg, table, dataset_name)
    if not columns:
        raise ValueError("No numeric columns selected to plot")

    x, x_label = make_x(table, x_name)
    ys = {column: signal_as_float(table, dataset_name, column) for column in columns}
    x, ys = downsample(x, ys, max_points)
    print(f"Plotted {len(columns)} {dataset_name} signals from {x.size} samples.")

    fig, axis = plt.subplots(figsize=(14, 7))
    gse_axes = gse_plot_axes(axis, columns, combined=False) if dataset_name == "gse" else {}
    alt_axes = {}

    for column in columns:
        default_axis = gse_axes["InternalState" in column] if dataset_name == "gse" else axis
        target_axis = signal_plot_axis(axis, default_axis, dataset_name, column, alt_axes)
        target_axis.plot(
            x,
            ys[column],
            linewidth=1.0,
            label=saved_signal_label(dataset_name, column, ys[column]),
            **plot_style(dataset_name, column),
        )

    axis.set_xlabel(x_label)
    if dataset_name == "nidaq":
        format_pressure_axis(axis)
    else:
        axis.grid(True, alpha=0.3)
    lines = [line for plot_axis in fig.axes for line in plot_axis.get_lines()]
    legend = axis.legend(lines, [line.get_label() for line in lines], loc="upper left", fontsize="small", ncols=2)
    enable_legend_toggling(fig, legend, lines)
    fig.suptitle(f"{dataset_name} data: {session_dir.name}")
    fig.tight_layout()

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150)
        print(f"Wrote {output}")
    else:
        plt.show()


def plot_combined_datasets(
    *,
    session_dir: Path,
    dataset_names: list[str],
    columns_arg: str | None,
    disabled_arg: str | None,
    x_name: str,
    max_points: int,
    output: Path | None,
) -> None:
    import matplotlib.pyplot as plt

    fig, axis = plt.subplots(figsize=(14, 7))
    x_label = x_name
    plotted = 0
    alt_axes = {}

    for dataset_name in dataset_names:
        table = read_dataset(session_dir, dataset_name, x_name)
        columns = parse_columns(columns_arg, disabled_arg, table, dataset_name)
        if not columns:
            continue

        x, x_label = make_x(table, x_name)
        ys = {column: signal_as_float(table, dataset_name, column) for column in columns}
        x, ys = downsample(x, ys, max_points)
        print(f"Plotted {len(columns)} {dataset_name} signals from {x.size} samples.")

        gse_axes = gse_plot_axes(axis, columns, combined=True) if dataset_name == "gse" else {}
        for column in columns:
            default_axis = gse_axes["InternalState" in column] if dataset_name == "gse" else axis
            target_axis = signal_plot_axis(axis, default_axis, dataset_name, column, alt_axes)
            target_axis.plot(
                x,
                ys[column],
                linewidth=1.0,
                label=saved_signal_label(dataset_name, column, ys[column]),
                **plot_style(dataset_name, column),
            )
            plotted += 1

    if plotted == 0:
        raise ValueError("No numeric columns selected to plot")
    print(f"Plotted {plotted} signals total.")

    axis.set_xlabel(x_label)
    format_pressure_axis(axis)
    lines = [line for plot_axis in fig.axes for line in plot_axis.get_lines()]
    legend = axis.legend(lines, [line.get_label() for line in lines], loc="upper left", fontsize="small", ncols=3)
    enable_legend_toggling(fig, legend, lines)
    fig.suptitle(f"combined data: {session_dir.name}")
    fig.tight_layout()

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150)
        print(f"Wrote {output}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot saved GUI2.1 Parquet data.")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--session", type=Path, default=None, help="Session folder. Defaults to newest in data-root.")
    parser.add_argument("--dataset", choices=[*sorted(DATASETS), "both"], default="both")
    parser.add_argument("--columns", default=None, help="Comma-separated columns to plot. Prefix with gse. or nidaq. when needed.")
    parser.add_argument("--disable", "--exclude", dest="disabled", default=None, help="Comma-separated signals to hide. Prefix with gse. or nidaq. when needed.")
    parser.add_argument("--x", default=DEFAULT_X_COLUMN, help="X column name, or 'index'. Defaults to storageTimestamp.")
    parser.add_argument("--max-points", type=int, default=0, help="Downsample to at most this many points. Defaults to 0, which plots all raw samples.")
    parser.add_argument("--output", type=Path, default=None, help="PNG path. If omitted, opens an interactive window.")
    args = parser.parse_args()

    dataset_names = sorted(DATASETS) if args.dataset == "both" else [args.dataset]
    session_dir = (
        args.session
        if args.session is not None
        else (
            find_latest_common_session(args.data_root, dataset_names)
            if args.dataset == "both"
            else find_latest_session(args.data_root, args.dataset)
        )
    )
    if not session_dir.is_absolute():
        session_dir = args.data_root / session_dir

    if args.dataset == "both":
        plot_combined_datasets(
            session_dir=session_dir,
            dataset_names=dataset_names,
            columns_arg=args.columns,
            disabled_arg=args.disabled,
            x_name=args.x,
            max_points=args.max_points,
            output=args.output,
        )
    else:
        plot_dataset(
            session_dir=session_dir,
            dataset_name=args.dataset,
            columns_arg=args.columns,
            disabled_arg=args.disabled,
            x_name=args.x,
            max_points=args.max_points,
            output=args.output,
        )


if __name__ == "__main__":
    main()
