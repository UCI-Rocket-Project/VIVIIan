"""Read every Parquet file in ``data/`` and plot all 33 GSE signal channels."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pyarrow as pa
import pyarrow.parquet as pq

_gui21 = Path(__file__).resolve().parent
if str(_gui21) not in sys.path:
    sys.path.insert(0, str(_gui21))

from legacy_conn import GSE_FIELD_NAMES

DATA_DIR = _gui21 / "data" / "gse_raw_telemetry_data"

BOOL_FIELDS = {
    "igniterArmed",
    "igniterCurrent0",
    "igniterCurrent1",
    "igniterInternalState0",
    "igniterInternalState1",
    "alarmInternalState",
    "solenoidInternalStateGn2Fill",
    "solenoidInternalStateGn2Vent",
    "solenoidInternalStateGn2Disconnect",
    "solenoidInternalStateMvasFill",
    "solenoidInternalStateMvasVent",
    "solenoidInternalStateMvasOpen",
    "solenoidInternalStateMvasClose",
    "solenoidInternalStateLoxVent",
    "solenoidInternalStateLngVent",
}

ANALOG_GROUPS: dict[str, list[str]] = {
    "Supply Voltages": [
        "supplyVoltage0",
        "supplyVoltage1",
    ],
    "Solenoid Currents": [
        "solenoidCurrentGn2Fill",
        "solenoidCurrentGn2Vent",
        "solenoidCurrentGn2Disconnect",
        "solenoidCurrentMvasFill",
        "solenoidCurrentMvasVent",
        "solenoidCurrentMvasOpen",
        "solenoidCurrentMvasClose",
        "solenoidCurrentLoxVent",
        "solenoidCurrentLngVent",
    ],
    "Temperatures": [
        "temperatureEngine1",
        "temperatureEngine2",
    ],
    "Pressures": [
        "pressureGn2",
        "pressureLoxInjTee",
        "pressureVent",
        "pressureLoxMvas",
    ],
}


def load_all_parquet(data_dir: Path):
    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        print(f"No parquet files found in {data_dir}")
        sys.exit(1)
    tables = []
    for f in files:
        if f.stat().st_size < 12:
            print(f"Skipping {f.name}: too small ({f.stat().st_size} bytes)")
            continue
        try:
            t = pq.read_table(f)
            if t.num_rows > 0:
                tables.append(t)
        except Exception as e:
            print(f"Skipping {f.name}: {e}")
    if not tables:
        print("No valid parquet data found")
        sys.exit(1)
    return pa.concat_tables(tables)


def main() -> None:
    table = load_all_parquet(DATA_DIR)
    df = table.to_pandas()
    print(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

    present_cols = set(df.columns)
    time_col = df["packet_time"].values if "packet_time" in present_cols else range(len(df))

    num_groups = 1 + len(ANALOG_GROUPS)
    fig, axes = plt.subplots(num_groups, 1, figsize=(14, 3.5 * num_groups), sharex=True)

    ax = axes[0]
    bool_cols = [c for c in GSE_FIELD_NAMES if c in BOOL_FIELDS and c in present_cols]
    for i, col in enumerate(bool_cols):
        ax.step(time_col, df[col].values + i * 1.2, where="post", label=col, linewidth=0.8)
    ax.set_title("Digital / Bool Channels")
    ax.set_yticks([])
    ax.legend(loc="upper right", fontsize=6, ncol=3)

    for idx, (group_name, fields) in enumerate(ANALOG_GROUPS.items(), start=1):
        ax = axes[idx]
        for col in fields:
            if col in present_cols:
                ax.plot(time_col, df[col].values, label=col, linewidth=0.7)
        ax.set_title(group_name)
        ax.legend(loc="upper right", fontsize=7, ncol=2)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("packet_time")
    fig.suptitle("GSE Telemetry — All Channels", fontsize=14)
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
