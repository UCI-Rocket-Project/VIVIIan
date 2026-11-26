"""Plot a named test's Parquet recordings. Edit SETTINGS below, then run this file.

Examples:
    python plot_test_data.py
    python plot_test_data.py --session 2026-11-24_22-09 --test ColdFlow
    python plot_test_data.py --time-reference end --start -600 --stop 0
    python plot_test_data.py --datasets nidaq --channels nidaq.COPV nidaq.LOXING
    python plot_test_data.py --output-rate-hz 10
    python plot_test_data.py --raw
    python plot_test_data.py --csv cold_flow_processed
    python plot_test_data.py --list-channels
    python plot_test_data.py --time-reference start --start 120 --stop 180 --output data_export/test.png

Only NumPy, PyArrow and Matplotlib are needed; acquisition processes are not started.
"""

from __future__ import annotations

from pathlib import Path


# ============================== EDITABLE SETTINGS ==============================

DATA_ROOT = Path(__file__).resolve().parent / "data"
DATA_EXPORT_DIR = Path(__file__).resolve().parent / "data_export"
SESSION = "2026-11-24_22-09"  # Folder directly inside DATA_ROOT (or an absolute path).
TEST_NAME = "ColdFlow"        # Folder inside the session. "" supports older layouts.

DATASET_FOLDERS = {
    "gse": "gse2v1_raw_telemetry_data",
    "nidaq": "nidaq_raw_telemetry_data",
}
DATASETS = ("gse", "nidaq")   # Load either or both; add other sources above if needed.

# Simultaneous, temporary renames: {recorded name: new name}, per dataset.
# Swaps are supported. Original Parquet files are never changed.
# Channel selections, signal rates, scales, axes, styles and hidden signals use NEW names.
FIELD_RENAMES = {
    "gse": {
        # "pressure2": "Extra pressure",
    },
    "nidaq": {
        # "LOXING": "LOX Injector",
    },
}

# Select names AFTER renaming, without dataset prefixes here.
# None = all numeric signals (except metadata); [] = no channels from this source.
CHANNELS = {
    "gse": ["Tank Vent", "LNG Tank", "LOX Tank", "pressure2"],
    "nidaq": ["COPV", "LOXING", "LNGING", "LOXPOT", "LNGPOT"],
}

# One time window shared by ALL sources, based on storageTimestamp, not file counts.
# "start": seconds from the earliest timestamp in the selected datasets.
# "end": seconds from the latest timestamp; e.g. START_TIME=-600, STOP_TIME=0.
# "absolute": Unix seconds or ISO datetime strings, e.g. "2026-11-24T22:15:00-08:00".
# ISO datetimes without an offset use this computer's local timezone.
# None means the beginning/end of the available recording, respectively.
TIME_REFERENCE = "start"
START_TIME = 500
STOP_TIME = 550
X_DISPLAY = "elapsed"        # "elapsed" (seconds from test start) or "clock" (local).

# Applied AFTER renaming: calibrated = recorded * multiplier + offset.
# "dataset.Signal" takes priority over an unprefixed "Signal" entry.
# A signal without an entry stays unchanged. These defaults come from plot_saved_data.py;
# edit them for the sensors/calibration used in your test.
SCALES = {
    "gse.Tank Vent": (402.94591, 1.87778),
    "gse.LNG Tank": (402.94591, 1.87778),
    "gse.LOX Tank": (402.94591, 1.87778),
    "gse.pressure2": (402.94591, 1.87778),
    "nidaq.COPV": (2284.4816, -45.4223),
    "nidaq.LOXING": (402.45048, 0),
    "nidaq.LNGING": (402.45048, 0),
    "nidaq.LOXPOT": (402.45048, 0),
    "nidaq.LNGPOT": (402.45048, 0),
}

# Output rates stay constant at every zoom level.
# 10 Hz = average samples into 0.1-second bins; None keeps original samples.
# All grids are anchored to the same test start and continue across file boundaries.
# Points use bin centers. Partial edge bins use the center of the selected portion.
# Empty bins stay gaps; rates above a source's sampling rate do NOT interpolate data.
# Any invalid sample makes its bin a gap, rather than hiding invalidity.
OUTPUT_RATE_HZ = 10         # Fallback for datasets/signals without an override.
DATASET_OUTPUT_RATES_HZ = {
    "gse": 100,
    "nidaq": 1000,
}
# Signal overrides use names AFTER renaming. None keeps that signal's raw samples.
# Priority: dataset.Signal > Signal > dataset rate > OUTPUT_RATE_HZ.
SIGNAL_OUTPUT_RATES_HZ = {
    # "nidaq.COPV": 200,
    # "LOXING": 50,
    # "nidaq.LNGPOT": None,
}
GAP_SECONDS = 1.0           # Break lines/bins spanning larger time gaps; None disables.

DEFAULT_AXIS = "pressure"
AXES = {
    "pressure": {
        "label": "Pressure (psi)", "side": "left", "scale": "linear",
        "limits": None, "major_tick": None, "minor_tick": None,
    },
    "copv": {
        "label": "COPV pressure (psi)", "side": "right", "scale": "linear",
        "limits": (0, 5000), "major_tick": None, "minor_tick": None,
    },
}
# Add axes above, then assign signals here. Unassigned signals use DEFAULT_AXIS.
# scale can be "linear", "log" or "symlog"; major_tick/minor_tick are linear spacings.
SIGNAL_AXES = {"COPV": "copv"}

# Matplotlib line options: color, linestyle, marker, markersize, linewidth, markevery...
# Examples of marker shapes: "o" circle, "s" square, "^" triangle, "x" cross.
# Unprefixed styles apply to either dataset; prefixed styles override individual options.
SIGNAL_STYLES = {
    "LNG Tank": {"color": "tab:blue"},
    "Tank Vent": {"color": "tab:orange"},
    "COPV": {"color": "tab:green"},
    "LOXING": {"color": "tab:red"},
    "LNGING": {"color": "tab:purple"},
    "LOX Tank": {"color": "tab:brown"},
    "LNGPOT": {"color": "tab:pink"},
    "LOXPOT": {"color": "tab:gray"},
    # "nidaq.COPV": {"marker": "o", "markersize": 3, "linestyle": "--"},
}
HIDDEN_SIGNALS = []          # Start hidden; click their legend entries to show them.
FIGURE_SIZE = (15, 8)
OUTPUT_FILE = None          # E.g. Path("data_export/ColdFlow.png"); None opens a window.
CSV_NAME = None             # E.g. "ColdFlow_processed"; --csv NAME overrides this.
# CSV includes time_s (elapsed), storageTimestamp (Unix seconds), and selected signals.
# Values are the same renamed/scaled/averaged samples passed to the plot.
# Different-rate timestamps are combined without interpolation; missing values are blank.
# Export covers the configured window and selected fields, including initially hidden traces.
# Interactive windows include a Measure menu in the bottom navigation toolbar.
# Its menu selects the Y axis, snapping, and Clear. Shift+click also snaps to data.
# M toggles measurement mode; Escape exits. Results appear below the signal legend.

# ============================ END EDITABLE SETTINGS ============================

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import heapq
import itertools
import math
import sys
import warnings

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from gse21connector import GSE2V1_EXTERNAL_ADC_BITS


TIME_COLUMN = "storageTimestamp"
METADATA_COLUMNS = {TIME_COLUMN, "timestamp", "magicHeader", "crc", "externalAdcValidBitmask"}
USE_CONFIGURED_RATES = object()


@dataclass
class RecordingFile:
    path: Path
    columns: set[str]
    # Unknown row-group bounds are represented by (-inf, inf), so they aren't skipped.
    groups: list[tuple[float, float]]
    start: float
    stop: float


@dataclass
class Catalog:
    dataset: str
    files: list[RecordingFile]
    # Final names -> recorded names, built from the full schemas before column selection.
    signals: dict[str, str]

    @property
    def start(self):
        return min(file.start for file in self.files)

    @property
    def stop(self):
        return max(file.stop for file in self.files)


@dataclass
class Signal:
    dataset: str
    name: str
    x: np.ndarray
    y: np.ndarray

    @property
    def key(self):
        return f"{self.dataset}.{self.name}"


@dataclass
class TimeGrid:
    rate: float
    first_bin: int
    x: np.ndarray  # Elapsed bin centers, shared by signals with the same output rate.
    tolerance: float

    def indices(self, elapsed):
        scaled = snap_bin_boundaries(np.asarray(elapsed) * self.rate, self.tolerance)
        # The selected stop is inclusive, including when it lands on a bin boundary.
        return np.clip(np.floor(scaled).astype(np.int64) - self.first_bin, 0, self.x.size - 1)


def snap_bin_boundaries(scaled, tolerance):
    """Avoid moving boundary samples to an adjacent bin through float roundoff."""
    nearest = np.rint(scaled)
    return np.where(np.abs(scaled - nearest) <= tolerance, nearest, scaled)


def validate_output_rate(output_rate):
    if output_rate is None:
        return
    try:
        valid = not isinstance(output_rate, bool) and math.isfinite(output_rate) and output_rate > 0
    except TypeError:
        valid = False
    if not valid:
        raise ValueError("Output rate must be positive and finite, or use None for raw samples.")


def make_time_grid(start, stop, origin, output_rate):
    validate_output_rate(output_rate)
    if output_rate is None:
        return None
    lo, hi = start - origin, stop - origin
    if hi <= lo:
        raise ValueError("The selected time window must have a positive duration.")
    rate = float(output_rate)
    # Recorded Unix timestamps have less precision than elapsed seconds near zero.
    tolerance = max(1e-12, abs(np.spacing(float(origin))) * rate)
    first = math.floor(snap_bin_boundaries(lo * rate, tolerance))
    last = math.ceil(snap_bin_boundaries(hi * rate, tolerance)) - 1
    last = max(first, last)
    bins = first + np.arange(last - first + 1, dtype=float)
    lower = np.maximum(bins / rate, lo)
    upper = np.minimum((bins + 1) / rate, hi)
    return TimeGrid(rate, first, lower + (upper - lower) / 2, tolerance)


def numeric_type(dtype):
    return pa.types.is_integer(dtype) or pa.types.is_floating(dtype) or pa.types.is_boolean(dtype)


def floats(table, name):
    return np.asarray(table[name].combine_chunks().to_numpy(zero_copy_only=False), dtype=float)


def inspect_dataset(test_dir: Path, dataset: str) -> Catalog:
    """Inspect footers, reading timestamps only when their statistics are unavailable."""
    directory = test_dir / DATASET_FOLDERS[dataset]
    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No Parquet files for {dataset}: {directory}")
    files, numeric, all_names = [], set(), set()
    for path in paths:
        try:
            with pq.ParquetFile(path) as parquet:
                schema = parquet.schema_arrow
                time_index = schema.get_field_index(TIME_COLUMN)
                if time_index < 0:
                    warnings.warn(f"Skipping {path}: no {TIME_COLUMN}; cannot align it by time.")
                    continue
                groups = []
                for index in range(parquet.metadata.num_row_groups):
                    stats = parquet.metadata.row_group(index).column(time_index).statistics
                    if stats is not None and stats.has_min_max:
                        lo, hi = float(stats.min), float(stats.max)
                        groups.append((lo, hi) if math.isfinite(lo) and math.isfinite(hi)
                                      else (-math.inf, math.inf))
                    else:
                        groups.append((-math.inf, math.inf))
                if not groups:
                    continue
                if any(not math.isfinite(lo) for lo, _ in groups):
                    times = floats(parquet.read(columns=[TIME_COLUMN]), TIME_COLUMN)
                    finite = times[np.isfinite(times)]
                    if not finite.size:
                        warnings.warn(f"Skipping {path}: no finite timestamps.")
                        continue
                    start, stop = float(finite.min()), float(finite.max())
                else:
                    start = min(lo for lo, _ in groups)
                    stop = max(hi for _, hi in groups)
                files.append(RecordingFile(path, set(schema.names), groups, start, stop))
                all_names.update(schema.names)
                numeric.update(field.name for field in schema if numeric_type(field.type))
        except (pa.ArrowInvalid, OSError, EOFError) as exc:
            warnings.warn(f"Skipping unreadable file {path}: {exc}")
    if not files:
        raise ValueError(f"No readable, timestamped recordings in {directory}")

    renames = FIELD_RENAMES.get(dataset, {})
    unknown = set(renames) - all_names
    if unknown:
        raise ValueError(f"Unknown rename sources for {dataset}: {sorted(unknown)}")
    if set(renames) & METADATA_COLUMNS:
        raise ValueError(f"Do not rename metadata columns in {dataset}.")
    final_names = [renames.get(name, name) for name in all_names]
    if len(set(final_names)) != len(final_names):
        raise ValueError(f"FIELD_RENAMES creates duplicate names in {dataset}; use a simultaneous swap.")
    signals = {renames.get(name, name): name for name in sorted(numeric - METADATA_COLUMNS)}
    print(f"{dataset}: {len(files)}/{len(paths)} readable files; "
          f"{len(signals)} numeric signals; {min(f.start for f in files):.3f}–"
          f"{max(f.stop for f in files):.3f} Unix seconds")
    return Catalog(dataset, files, signals)


def signal_setting(mapping, dataset, name, default=None):
    return mapping.get(f"{dataset}.{name}", mapping.get(name, default))


def make_signal_grids(catalogs, names, start, stop, origin, override=USE_CONFIGURED_RATES):
    """Resolve rates by final signal name; reuse grids for signals with the same rate."""
    cache = {None: None}
    grids = {}
    for catalog in catalogs:
        dataset = catalog.dataset
        grids[dataset] = {}
        default = DATASET_OUTPUT_RATES_HZ.get(dataset, OUTPUT_RATE_HZ)
        for name in names[dataset]:
            rate = (signal_setting(SIGNAL_OUTPUT_RATES_HZ, dataset, name, default)
                    if override is USE_CONFIGURED_RATES else override)
            try:
                validate_output_rate(rate)
            except ValueError as exc:
                raise ValueError(f"{dataset}.{name}: {exc}") from exc
            if rate not in cache:
                cache[rate] = make_time_grid(start, stop, origin, rate)
            grids[dataset][name] = cache[rate]
    return grids


def resolve_window(catalogs, reference, start, stop):
    first = min(catalog.start for catalog in catalogs)
    last = max(catalog.stop for catalog in catalogs)

    def endpoint(value, default):
        if value is None:
            return default
        if reference == "absolute":
            try:
                result = float(value)
            except (TypeError, ValueError):
                result = datetime.fromisoformat(str(value)).timestamp()
        else:
            result = (first if reference == "start" else last) + float(value)
        if not math.isfinite(result):
            raise ValueError("Time endpoints must be finite.")
        return result

    lo, hi = endpoint(start, first), endpoint(stop, last)
    if hi <= lo:
        raise ValueError("STOP_TIME must be after START_TIME. For the last ten minutes, "
                         "use reference='end', start=-600, stop=0.")
    if hi < first or lo > last:
        raise ValueError("The requested time window does not overlap this test.")
    lo, hi = max(lo, first), min(hi, last)
    print(f"Shared window: {lo - first:.3f}–{hi - first:.3f} s from test start "
          f"({hi - lo:.3f} s); reference={reference}")
    return lo, hi, first


def select_channels(catalog, requested):
    names = list(catalog.signals) if requested is None else list(requested)
    missing = set(names) - catalog.signals.keys()
    if missing:
        raise ValueError(f"Unknown channels for {catalog.dataset}, AFTER renaming: {sorted(missing)}. "
                         "Use --list-channels to see available names.")
    if len(set(names)) != len(names):
        raise ValueError(f"Duplicate channels selected for {catalog.dataset}")
    return names


def average_to_grid(x, y, grid, gap_seconds):
    """Average cropped, sorted samples into shared bins, retaining invalid/empty bins."""
    indices = grid.indices(x)
    counts = np.bincount(indices, minlength=grid.x.size)
    finite = np.isfinite(y)
    sums = np.bincount(indices, weights=np.where(finite, y, 0), minlength=grid.x.size)
    invalid = np.bincount(indices[~finite], minlength=grid.x.size) > 0
    values = np.full(grid.x.size, np.nan)
    np.divide(sums, counts, out=values, where=(counts > 0) & ~invalid)
    if gap_seconds is not None:
        breaks = np.flatnonzero(np.diff(x) > gap_seconds)
        for index in breaks:
            left, right = indices[index], indices[index + 1]
            if right == left:
                values[left] = np.nan  # One bin contains samples from both sides of a gap.
            elif right == left + 1:
                values[right] = np.nan  # Ensure a visible break when no empty bin intervenes.
    return grid.x, values


def raw_samples_with_gaps(x, y, gap_seconds):
    """Keep raw samples, inserting line breaks at large timestamp gaps."""
    if not x.size:
        return x, y
    breaks = (np.flatnonzero(np.diff(x) > gap_seconds) + 1
              if gap_seconds is not None else np.array([], dtype=int))
    edges = np.r_[0, breaks, x.size]
    out_x, out_y = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if out_x:
            out_x.append(np.array([np.nan]))
            out_y.append(np.array([np.nan]))
        out_x.append(x[lo:hi])
        out_y.append(y[lo:hi])
    return np.concatenate(out_x), np.concatenate(out_y)


def load_signals(catalog, names, start, stop, origin, grids, gap_seconds):
    if not names:
        return []
    chunks_x = []
    chunks_y = {name: [] for name in names}
    missing_columns, missing_mask = set(), False
    files_read, groups_read = 0, 0
    for file in catalog.files:
        if file.stop < start or file.start > stop:
            continue
        groups = [i for i, (lo, hi) in enumerate(file.groups) if hi >= start and lo <= stop]
        if not groups:
            continue
        originals = {catalog.signals[name] for name in names}
        columns = {TIME_COLUMN} | (originals & file.columns)
        needs_mask = catalog.dataset == "gse" and bool(originals & GSE2V1_EXTERNAL_ADC_BITS.keys())
        if needs_mask and "externalAdcValidBitmask" in file.columns:
            columns.add("externalAdcValidBitmask")
        try:
            with pq.ParquetFile(file.path) as parquet:
                table = parquet.read_row_groups(groups, columns=sorted(columns))
        except (pa.ArrowInvalid, OSError, EOFError) as exc:
            warnings.warn(f"Skipping unreadable file {file.path}: {exc}")
            continue
        files_read += 1
        groups_read += len(groups)
        times = floats(table, TIME_COLUMN)
        keep = np.isfinite(times) & (times >= start) & (times <= stop)
        if not np.any(keep):
            continue
        chunks_x.append(times[keep] - origin)
        masks = None
        if needs_mask:
            if "externalAdcValidBitmask" in columns:
                raw_masks = floats(table, "externalAdcValidBitmask")[keep]
                good = (np.isfinite(raw_masks) & (raw_masks >= 0) & (raw_masks <= 255)
                        & (raw_masks == np.floor(raw_masks)))
                masks = np.where(good, raw_masks, 0).astype(np.uint8)
            else:
                missing_mask = True
                masks = np.zeros(np.count_nonzero(keep), dtype=np.uint8)
        for name in names:
            original = catalog.signals[name]
            if original in columns:
                values = floats(table, original)[keep]
            else:
                missing_columns.add(original)
                values = np.full(np.count_nonzero(keep), np.nan)
            if needs_mask and original in GSE2V1_EXTERNAL_ADC_BITS:
                valid = (masks & GSE2V1_EXTERNAL_ADC_BITS[original]) != 0
                values = np.where(valid, values, np.nan)
            # Lookup uses the NEW name. Validity above deliberately uses the recorded name.
            multiplier, offset = signal_setting(SCALES, catalog.dataset, name, (1, 0))
            values = values * multiplier + offset
            chunks_y[name].append(np.where(np.isfinite(values), values, np.nan))

    if missing_columns:
        warnings.warn(f"{catalog.dataset}: channels missing from some selected files are gaps: "
                      f"{sorted(missing_columns)}")
    if missing_mask:
        warnings.warn("GSE files without externalAdcValidBitmask have invalid ADC readings; shown as gaps.")
    if not chunks_x:
        warnings.warn(f"{catalog.dataset}: no samples in the requested window.")
        return [Signal(catalog.dataset, name, grids[name].x, np.full(grids[name].x.size, np.nan))
                for name in names if grids[name] is not None]
    x = np.concatenate(chunks_x)
    # Stable ordering handles overlapping files/out-of-order packets without discarding duplicates.
    order = np.argsort(x, kind="stable") if np.any(np.diff(x) < 0) else None
    if order is not None:
        warnings.warn(f"{catalog.dataset}: timestamps went backwards; sorting samples by time.")
        x = x[order]
    result = []
    for name in names:
        grid = grids[name]
        y = np.concatenate(chunks_y[name])
        if order is not None:
            y = y[order]
        rx, ry = (average_to_grid(x, y, grid, gap_seconds) if grid is not None
                  else raw_samples_with_gaps(x, y, gap_seconds))
        if not np.isfinite(ry).any():
            warnings.warn(f"{catalog.dataset}.{name}: no valid values after calibration/reduction.")
        result.append(Signal(catalog.dataset, name, rx, ry))
    print(f"{catalog.dataset}: read {files_read}/{len(catalog.files)} files, {groups_read} row groups; "
          f"{x.size:,} input samples per signal.")
    for signal in result:
        grid = grids[signal.name]
        count = np.count_nonzero(np.isfinite(signal.x))
        reduction = f"{grid.rate:g} Hz; empty/invalid bins are gaps" if grid is not None else "raw samples"
        print(f"  {signal.key}: {count:,} points ({reduction}).")
    return result


def csv_export_path(name):
    """Accept a filename only, keeping exports inside DATA_EXPORT_DIR."""
    name = str(name).strip()
    filename = Path(name)
    if (not name or name in {".", ".."} or filename.name != name or filename.is_reserved()
            or any(char in name for char in '<>:"/\\|?*') or name.endswith(".")
            or any(ord(char) < 32 for char in name)):
        raise ValueError("--csv / CSV_NAME must be a filename, for example 'cold_flow_processed.csv'.")
    if not name.lower().endswith(".csv"):
        name += ".csv"
    return DATA_EXPORT_DIR / name


def export_csv(signals, origin, output):
    """Write the plotted samples on a union timeline, preserving rates and duplicates.

    Each Signal is already cropped, calibrated, averaged and sorted. Empty/invalid
    values become blank cells. Raw NaN timestamp separators are drawing artifacts
    and are omitted. Repeated real timestamps retain every sample in separate rows.
    """
    def samples(index, signal):
        for x, y in zip(signal.x, signal.y):
            if math.isfinite(x):
                yield float(x), index, float(y)

    streams = [samples(index, signal) for index, signal in enumerate(signals)]
    merged = heapq.merge(*streams, key=lambda sample: sample[0])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", TIME_COLUMN, *(signal.key for signal in signals)])
        for time, group in itertools.groupby(merged, key=lambda sample: sample[0]):
            columns = [[] for _ in signals]
            for _, index, value in group:
                columns[index].append(value if math.isfinite(value) else "")
            # Match duplicate occurrences without a Cartesian join or dropping samples.
            for row in itertools.zip_longest(*columns, fillvalue=""):
                writer.writerow([time, origin + time, *row])
                count += 1
    print(f"Wrote {output} ({count:,} rows; {len(signals)} signals).")
    return count


def enable_legend_toggling(fig, legend, lines):
    """Toggle traces from legend clicks, including while zoom/pan owns the toolbar."""
    entries = list(zip(legend.get_lines(), legend.get_texts(), lines))
    for handle, text, line in zip(legend.get_lines(), legend.get_texts(), lines):
        handle.set_pickradius(5)
        for artist in (handle, text):
            artist.set_alpha(1 if line.get_visible() else 0.25)

    def on_click(event):
        if event.button != 1:
            return
        for handle, text, line in entries:
            if not (handle.contains(event)[0] or text.contains(event)[0]):
                continue
            line.set_visible(not line.get_visible())
            for artist in (handle, text):
                artist.set_alpha(1 if line.get_visible() else 0.25)
            fig.canvas.draw_idle()
            break

    # Matplotlib suppresses pick_event while navigation holds the widget lock.
    fig.canvas.mpl_connect("button_press_event", on_click)
    return on_click


class TkMeasurementMenu:
    """Native controls in the existing TkAgg Home/Pan/Zoom toolbar."""

    def __init__(self, toolbar, tool):
        import tkinter as tk

        self.tool = tool
        self.active = tk.BooleanVar(master=toolbar, value=tool.active)
        self.axis = tk.IntVar(master=toolbar, value=tool.axis_index)
        self.snap = tk.BooleanVar(master=toolbar, value=tool.snap_to_data)
        self.button = tk.Menubutton(toolbar, text="Measure", relief="flat", borderwidth=1,
                                    direction="above")
        self.menu = tk.Menu(self.button, tearoff=False)
        self.button.configure(menu=self.menu)
        self.menu.add_checkbutton(label="Measure (M)", variable=self.active,
                                  command=lambda: tool.set_active(self.active.get()))
        self.menu.add_separator()
        self.axis_menu = tk.Menu(self.menu, tearoff=False)
        for index, name in enumerate(tool.axis_names):
            self.axis_menu.add_radiobutton(label=f"{name}: {tool.axes[name].get_ylabel()}",
                                           variable=self.axis, value=index,
                                           command=lambda: tool.select_axis(self.axis.get()))
        self.menu.add_cascade(label="Y axis", menu=self.axis_menu)
        self.menu.add_checkbutton(label="Snap to visible data", variable=self.snap,
                                  command=lambda: tool.set_snap(self.snap.get()))
        self.menu.add_command(label="Clear measurement", command=tool.clear)
        self.button.pack(side=tk.LEFT, padx=(8, 2))

        # Native buttons and keyboard navigation both turn off measurement first.
        for method, label in (("pan", "Pan"), ("zoom", "Zoom")):
            original = getattr(toolbar, method)

            def navigate(*args, original=original):
                if tool.active:
                    tool.set_active(False)
                return original(*args)

            setattr(toolbar, method, navigate)
            toolbar._buttons[label].configure(command=navigate)

    def sync(self):
        self.active.set(self.tool.active)
        self.axis.set(self.tool.axis_index)
        self.snap.set(self.tool.snap_to_data)
        self.button.configure(text="Measure: ON" if self.tool.active else "Measure")


class MeasurementTool:
    """A two-point ruler using the chosen y-axis, independent of twinned-axis picking."""

    def __init__(self, fig, axes, lines, x_display, legend, layout):
        self.fig, self.axes, self.lines = fig, axes, lines
        self.legend, self.layout = legend, layout
        self.axis_names = list(axes)
        self.axis_index = 0
        self.overlay = list(axes.values())[-1]
        self.seconds_per_x = 86400 if x_display == "clock" else 1
        self.active = False
        self.snap_to_data = False
        self.points = []
        self.artists = []
        self.readout = None
        self.status = "Measure: open the toolbar menu or press M."
        self.toolbar_menu = None
        self.attach_toolbar()
        fig.canvas.mpl_connect("button_press_event", self.on_click)
        fig.canvas.mpl_connect("key_press_event", self.on_key)

    def attach_toolbar(self):
        toolbar = self.fig.canvas.toolbar
        if self.toolbar_menu is None and hasattr(toolbar, "tk"):
            self.toolbar_menu = TkMeasurementMenu(toolbar, self)

    def set_status(self, text):
        self.status = text
        if self.fig.canvas.toolbar is not None:
            self.fig.canvas.toolbar.set_message(text)

    def sync_menu(self):
        if self.toolbar_menu is not None:
            self.toolbar_menu.sync()

    @property
    def axis(self):
        return self.axes[self.axis_names[self.axis_index]]

    @property
    def deltas(self):
        if len(self.points) != 2:
            return None
        (ax, ay), (bx, by) = self.points
        return (bx - ax) * self.seconds_per_x, by - ay

    def toggle(self, event=None):
        self.set_active(not self.active)

    def set_active(self, active):
        if active:
            self.active = False
            toolbar = self.fig.canvas.toolbar
            mode = str(getattr(toolbar, "mode", "")).lower()
            if "pan" in mode:
                toolbar.pan()
            elif "zoom" in mode:
                toolbar.zoom()
        self.active = bool(active)
        self.sync_menu()
        self.set_status("Click A, then B. Shift+click snaps to a visible trace."
                        if self.active else "Measure off. Press M to measure again.")
        self.fig.canvas.draw_idle()

    def clear(self, event=None):
        for artist in self.artists:
            artist.remove()
        self.artists.clear()
        self.points.clear()
        self.readout = None
        self.set_status("Click A, then B. Shift+click snaps to a visible trace."
                        if self.active else "Measure: open the toolbar menu or press M.")
        self.layout()
        self.fig.canvas.draw_idle()

    def cycle_axis(self, event=None):
        self.select_axis((self.axis_index + 1) % len(self.axis_names))

    def select_axis(self, index):
        if index != self.axis_index:
            self.axis_index = index
            self.clear()
        self.sync_menu()

    def set_snap(self, active):
        self.snap_to_data = bool(active)
        self.sync_menu()
        self.set_status(f"Snap to visible data {'on' if self.snap_to_data else 'off'}.")

    def position_readout(self, renderer, sidebar_left):
        if self.readout is not None:
            bbox = self.legend.get_window_extent(renderer)
            padding = 10 * self.fig.dpi / 72
            self.readout.set_position((sidebar_left, (bbox.y0 - padding) / self.fig.bbox.height))

    def on_key(self, event):
        if event.key == "m":
            self.toggle()
        elif event.key == "escape" and self.active:
            self.toggle()

    def snapped_point(self, event):
        """Find a nearby visible sample using screen distance on the selected axis."""
        best, radius_squared = None, (12 * self.fig.dpi / 72) ** 2
        for line in self.lines:
            if line.axes is not self.axis or not line.get_visible():
                continue
            points = np.column_stack((line.get_xdata(orig=False), line.get_ydata(orig=False)))
            points = points[np.isfinite(points).all(axis=1)]
            if not points.size:
                continue
            screen = line.get_transform().transform(points)
            visible = (np.isfinite(screen).all(axis=1)
                       & (screen[:, 0] >= self.axis.bbox.x0) & (screen[:, 0] <= self.axis.bbox.x1)
                       & (screen[:, 1] >= self.axis.bbox.y0) & (screen[:, 1] <= self.axis.bbox.y1))
            points, screen = points[visible], screen[visible]
            if not points.size:
                continue
            distances = np.sum((screen - [event.x, event.y]) ** 2, axis=1)
            index = np.argmin(distances)
            if distances[index] <= radius_squared:
                best, radius_squared = tuple(points[index]), distances[index]
        return best

    def draw_point(self, point):
        from matplotlib.lines import Line2D

        # Draw above all traces using the chosen scale without changing any data limits.
        line = Line2D([point[0]], [point[1]], transform=self.axis.transData, zorder=20,
                      marker="+", markersize=12, markeredgewidth=2, color="black", linestyle="none")
        self.overlay.add_artist(line)
        line.set_clip_path(self.overlay.patch)
        line.set_in_layout(False)
        self.artists.append(line)

    def on_click(self, event):
        if not self.active or event.button != 1 or not self.axis.bbox.contains(event.x, event.y):
            return
        if self.fig.canvas.widgetlock.locked():
            self.set_status("Measurement paused: turn off Zoom/Pan, or toggle Measure off and on.")
            self.fig.canvas.draw_idle()
            return
        snap = self.snap_to_data or (event.key is not None and "shift" in event.key)
        point = (self.snapped_point(event) if snap
                 else tuple(self.axis.transData.inverted().transform((event.x, event.y))))
        if point is None:
            self.set_status("No nearby visible sample on this Y axis. Click closer or turn off snapping.")
            self.fig.canvas.draw_idle()
            return
        if not np.isfinite(point).all():
            return
        if len(self.points) == 2:
            self.clear()
        self.points.append(point)
        self.draw_point(point)
        label = self.overlay.annotate("A" if len(self.points) == 1 else "B", xy=point,
                                      xycoords=self.axis.transData, xytext=(7, 7),
                                      textcoords="offset points", zorder=21, fontweight="bold")
        label.set_in_layout(False)
        self.artists.append(label)
        if len(self.points) == 1:
            self.set_status("Point A selected. Click B; Shift+click snaps to a visible trace.")
        else:
            dt, dy = self.deltas
            text = f"Δt = {dt:+.6g} s\nΔy = {dy:+.6g}\n{self.axis.get_ylabel()}"
            self.readout = self.fig.text(0, 0, text, ha="left", va="top", fontsize=9)
            self.readout.set_in_layout(False)
            self.artists.append(self.readout)
            self.layout()
            self.set_status(f"Δt = {dt:+.6g} s; Δy = {dy:+.6g} ({self.axis_names[self.axis_index]}). "
                            "Click to start again.")
        self.fig.canvas.draw_idle()


def plot_signals(signals, origin, window, title, x_display, interactive=True):
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from matplotlib.ticker import AutoMinorLocator, MaxNLocator, MultipleLocator, ScalarFormatter

    assignments = {signal.key: signal_setting(SIGNAL_AXES, signal.dataset, signal.name, DEFAULT_AXIS)
                   for signal in signals}
    used = list(dict.fromkeys([DEFAULT_AXIS, *assignments.values()]))
    unknown = set(used) - AXES.keys()
    if unknown:
        raise ValueError(f"Unknown axis names in SIGNAL_AXES/DEFAULT_AXIS: {sorted(unknown)}")
    if AXES[DEFAULT_AXIS].get("side", "left") != "left":
        raise ValueError("DEFAULT_AXIS must be on the left.")

    fig, main = plt.subplots(figsize=FIGURE_SIZE)
    axes = {DEFAULT_AXIS: main}
    sides = {"left": 1, "right": 0}
    for key in used:
        config = AXES[key]
        side = config.get("side", "right")
        if side not in sides:
            raise ValueError(f"Axis {key}: side must be 'left' or 'right'.")
        axis = main if key == DEFAULT_AXIS else main.twinx()
        if key != DEFAULT_AXIS:
            axis.spines[side].set_position(("outward", 55 * sides[side]))
            axis.yaxis.set_label_position(side)
            axis.yaxis.set_ticks_position(side)
            axis.spines["left" if side == "right" else "right"].set_visible(False)
            sides[side] += 1
            axes[key] = axis
        axis.set_ylabel(config.get("label", key))
        scale = config.get("scale", "linear")
        axis.set_yscale(scale)
        if config.get("limits") is not None:
            axis.set_ylim(*config["limits"])
        if scale == "linear":
            axis.yaxis.set_minor_locator(AutoMinorLocator())
        for setting, setter in (("major_tick", axis.yaxis.set_major_locator),
                                ("minor_tick", axis.yaxis.set_minor_locator)):
            spacing = config.get(setting)
            if spacing is not None:
                if scale != "linear" or spacing <= 0:
                    raise ValueError(f"Axis {key}: {setting} requires a positive spacing and linear scale.")
                setter(MultipleLocator(spacing))

    epoch = dates.date2num(datetime.fromtimestamp(origin).astimezone()) if x_display == "clock" else None
    palette = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    lines = []
    for index, signal in enumerate(signals):
        style = {"linewidth": 1.0, "linestyle": "--" if signal.dataset == "gse" else "-",
                 "color": palette[index % len(palette)]}
        style.update(SIGNAL_STYLES.get(signal.name, {}))
        style.update(SIGNAL_STYLES.get(signal.key, {}))
        style["label"] = signal.key + (" (INVALID)" if not np.isfinite(signal.y).any() else "")
        x = epoch + signal.x / 86400 if epoch is not None else signal.x
        line, = axes[assignments[signal.key]].plot(x, signal.y, **style)
        line.set_visible(signal.name not in HIDDEN_SIGNALS and signal.key not in HIDDEN_SIGNALS)
        lines.append(line)

    if x_display == "clock":
        tz = datetime.fromtimestamp(origin).astimezone().tzinfo
        locator = dates.AutoDateLocator(tz=tz, minticks=4, maxticks=10)
        main.xaxis.set_major_locator(locator)
        main.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator, tz=tz))
        main.set_xlabel(f"Time ({tz})")
        main.set_xlim(epoch + (window[0] - origin) / 86400,
                      epoch + (window[1] - origin) / 86400)
    else:
        main.xaxis.set_major_locator(MaxNLocator(nbins="auto", steps=[1, 2, 2.5, 5, 10]))
        main.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
        main.set_xlabel("Time from test start (s)")
        main.set_xlim(window[0] - origin, window[1] - origin)
    main.xaxis.set_minor_locator(AutoMinorLocator(4))
    main.grid(which="major", alpha=0.4, linewidth=0.7)
    main.grid(which="minor", alpha=0.18, linewidth=0.4)
    # Reserve only the measured legend/axis-label widths, including when maximized.
    left = 0.08 + 0.045 * (sides["left"] - 1)
    fig.subplots_adjust(left=left, right=0.8, bottom=0.12, top=0.9)
    legend = fig.legend(lines, [line.get_label() for line in lines], loc="upper left",
                        bbox_to_anchor=(0.99, 0.9), fontsize="small", frameon=False,
                        borderaxespad=0, handlelength=1.6, handletextpad=0.5, labelspacing=0.3)

    def layout_legend(event=None):
        renderer = fig.canvas.get_renderer()
        width = fig.bbox.width
        padding = 10 * fig.dpi / 72
        legend_width = legend.get_window_extent(renderer).width
        tool = getattr(fig, "_measurement_tool", None)
        if tool is not None and tool.readout is not None:
            legend_width = max(legend_width, tool.readout.get_window_extent(renderer).width)
        axis_width = max(axis.get_tightbbox(renderer).x1 - main.bbox.x1 for axis in axes.values())
        right = 1 - (legend_width + axis_width + 3 * padding) / width
        if right - left < 0.25:
            right = left + 0.25
        fig.subplots_adjust(right=right)
        sidebar_left = 1 - (legend_width + padding) / width
        legend.set_bbox_to_anchor((sidebar_left, 0.9))
        if tool is not None:
            tool.position_readout(renderer, sidebar_left)
        if event is not None:
            fig.canvas.draw_idle()

    layout_legend()
    fig.canvas.mpl_connect("resize_event", layout_legend)
    enable_legend_toggling(fig, legend, lines)
    fig.suptitle(title)
    if interactive:
        # Retain widgets/callback owners for the lifetime of the figure.
        fig._measurement_tool = MeasurementTool(fig, axes, lines, x_display, legend, layout_legend)
    return fig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--session", default=SESSION)
    parser.add_argument("--test", default=TEST_NAME)
    parser.add_argument("--datasets", nargs="+", choices=list(DATASET_FOLDERS), default=DATASETS)
    parser.add_argument("--channels", nargs="+", help="Renamed dataset.Signal names, or 'all'. Quote names with spaces.")
    parser.add_argument("--time-reference", choices=["start", "end", "absolute"], default=TIME_REFERENCE)
    parser.add_argument("--start", default=START_TIME)
    parser.add_argument("--stop", default=STOP_TIME)
    parser.add_argument("--x-display", choices=["elapsed", "clock"], default=X_DISPLAY)
    rate_options = parser.add_mutually_exclusive_group()
    rate_options.add_argument("--output-rate-hz", type=float, default=argparse.SUPPRESS,
                              help="Force this rate for ALL signals, overriding configured rates.")
    rate_options.add_argument("--raw", dest="output_rate_hz", action="store_const", const=None,
                              default=argparse.SUPPRESS,
                              help="Keep original samples for ALL signals, overriding configured rates.")
    parser.add_argument("--list-channels", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--csv", metavar="NAME", default=CSV_NAME,
                        help="Also write plotted data to data_export/NAME.csv (same fields, window and rates).")
    args = parser.parse_args(argv)
    try:
        csv_path = csv_export_path(args.csv) if args.csv is not None else None
    except ValueError as exc:
        parser.error(str(exc))
    rate_override = getattr(args, "output_rate_hz", USE_CONFIGURED_RATES)
    if rate_override is not USE_CONFIGURED_RATES:
        try:
            validate_output_rate(rate_override)
        except ValueError as exc:
            parser.error(str(exc))
    if GAP_SECONDS is not None and (not math.isfinite(GAP_SECONDS) or GAP_SECONDS <= 0):
        parser.error("GAP_SECONDS must be positive, or None")
    if args.time_reference not in {"start", "end", "absolute"} or args.x_display not in {"elapsed", "clock"}:
        parser.error("Invalid TIME_REFERENCE or X_DISPLAY setting")
    if not args.datasets or len(set(args.datasets)) != len(args.datasets):
        parser.error("Select at least one dataset without duplicates")

    session = Path(args.session)
    if not session.is_absolute():
        session = args.data_root / session
    test_dir = session / args.test
    print(f"Test directory: {test_dir}")
    catalogs = [inspect_dataset(test_dir, dataset) for dataset in args.datasets]
    if args.list_channels:
        for catalog in catalogs:
            for name, original in catalog.signals.items():
                print(f"  {catalog.dataset}.{name}" + (f"  (recorded as {original})" if name != original else ""))
        return

    selected = {dataset: CHANNELS.get(dataset) for dataset in args.datasets}
    if args.channels:
        selected = {dataset: None if args.channels == ["all"] else [] for dataset in args.datasets}
        if args.channels != ["all"]:
            for key in args.channels:
                dataset, separator, name = key.partition(".")
                if not separator or dataset not in selected:
                    parser.error(f"Channel '{key}' must be dataset.Signal from a selected dataset")
                selected[dataset].append(name)
    # Validate every requested channel before loading any signal arrays.
    names = {catalog.dataset: select_channels(catalog, selected[catalog.dataset]) for catalog in catalogs}
    start, stop, origin = resolve_window(catalogs, args.time_reference, args.start, args.stop)
    grids = make_signal_grids(catalogs, names, start, stop, origin, rate_override)
    signals = []
    for catalog in catalogs:
        signals.extend(load_signals(catalog, names[catalog.dataset], start, stop, origin,
                                    grids[catalog.dataset], GAP_SECONDS))
    if not signals:
        raise ValueError("No signals to plot; check CHANNELS, --datasets and the time window.")
    if csv_path is not None:
        export_csv(signals, origin, csv_path)
    if args.output is not None:
        import matplotlib
        matplotlib.use("Agg")
    fig = plot_signals(signals, origin, (start, stop), f"{session.name} / {args.test or '(session)'}",
                       args.x_display, interactive=args.output is None)
    import matplotlib.pyplot as plt
    if args.output is not None:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150)
        plt.close(fig)
        print(f"Wrote {output}")
    else:
        plt.show()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
