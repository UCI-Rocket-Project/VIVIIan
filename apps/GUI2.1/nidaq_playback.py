"""Scripted NIDAQ pressure playback for running the GUI without hardware.

This is playback, not a simulation: it replays a fixed pressure profile on a
timeline and does not respond to valve commands. It exists so the pressure-
driven parts of a procedure — threshold crossings and the 3 psi/min decay
criterion — can be exercised end to end.

It publishes in volts to the same Flight endpoint ``nidaq_gse.py`` would, so
backend decimates and stores it exactly as it would real hardware and nothing
downstream can tell the difference.

Because it ignores valve state, pressures and valve positions can disagree if
the operator moves faster or slower than the timeline. Segment changes are
printed so you can see where the profile is.

Run:
    python apps/GUI2.1/nidaq_playback.py [--profile pass|fail] [--no-stdin]

Environment:
    GSE_SIM_PROFILE         pass | fail   (default pass)
    GSE_SIM_DECAY_PSI_MIN   decay rate override, psi/min
    GSE_DECAY_WINDOW        measurement window, used to size the decay segment
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

from frontendv2 import PT_SCALES
from nidaq_gse import NIDAQ_FIELD_NAMES, NIDAQ_FLIGHT_BIND, NIDAQ_ROWS_PER_FRAME

FRAMES_PER_SECOND = 20.0
RECONNECT_SECONDS = 1.0

PASS_DECAY_PSI_PER_MIN = 1.2
FAIL_DECAY_PSI_PER_MIN = 6.0

DECAY_WINDOW_SECONDS = float(os.environ.get("GSE_DECAY_WINDOW", "180.0"))
DECAY_SEGMENT_SECONDS = max(90.0, DECAY_WINDOW_SECONDS + 60.0)

COPV = "COPV"
LOX = "LOXTANK"
LNG = "LNGTANK"
VENT = "VENT"

AMBIENT = {COPV: 0.0, LOX: 0.0, LNG: 0.0, VENT: 0.0}


class Segment:
    """A named span with a linear ramp from one pressure set to another."""

    def __init__(self, name: str, seconds: float, end: dict[str, float], note: str = "") -> None:
        self.name = name
        self.seconds = float(seconds)
        self.end = end
        self.note = note

    def value_at(self, start: dict[str, float], t: float) -> dict[str, float]:
        fraction = 1.0 if self.seconds <= 0 else min(1.0, max(0.0, t / self.seconds))
        return {
            key: start.get(key, 0.0) + (self.end.get(key, start.get(key, 0.0)) - start.get(key, 0.0)) * fraction
            for key in AMBIENT
        }


def build_profile(decay_psi_per_min: float) -> list[Segment]:
    """The §3 Pressure Decay pressure history, roughly to scale."""
    decay_drop = decay_psi_per_min * (DECAY_SEGMENT_SECONDS / 60.0)
    charged = {COPV: 210.0, LOX: 235.0, LNG: 235.0, VENT: 300.0}
    decayed = {key: value - decay_drop for key, value in charged.items()}

    return [
        Segment("ambient", 25.0, dict(AMBIENT), "waiting for the operator to start"),
        Segment("copv fill", 45.0, {COPV: 380.0, LOX: 0.0, LNG: 0.0, VENT: 0.0},
                "crosses the 350 psig cutoff"),
        Segment("copv hold", 30.0, {COPV: 380.0, LOX: 0.0, LNG: 0.0, VENT: 0.0},
                "leak check, tank vents close"),
        Segment("tank press", 45.0, {COPV: 180.0, LOX: 55.0, LNG: 55.0, VENT: 290.0},
                "PV 2 closed, PV 1 open"),
        Segment("settle", 40.0, {COPV: 180.0, LOX: 55.0, LNG: 55.0, VENT: 290.0},
                "Table 14 readout"),
        Segment("fill to 200", 45.0, dict(charged), "crosses the 200/250 psig cutoffs"),
        Segment("gse depress", 30.0, dict(charged), "bottle closed, GSE vented"),
        Segment("decay", DECAY_SEGMENT_SECONDS, decayed,
                f"{decay_psi_per_min:.1f} psi/min on every section"),
        Segment("mvas dump", 20.0, dict(AMBIENT), "depressurising through MVAS"),
        Segment("safed", 30.0, dict(AMBIENT), "ALL OFF"),
    ]


class Playback:
    def __init__(self, decay_psi_per_min: float) -> None:
        self.segments = build_profile(decay_psi_per_min)
        self.index = 0
        self.segment_started_at = time.monotonic()
        self.start_values = dict(AMBIENT)
        self.current = dict(AMBIENT)
        self.paused = False
        self._lock = threading.Lock()
        self._announce()

    def _announce(self) -> None:
        segment = self.segments[self.index]
        print(
            f"nidaq playback: [{self.index + 1}/{len(self.segments)}] {segment.name} "
            f"({segment.seconds:.0f}s) — {segment.note}",
            flush=True,
        )

    def jump(self, delta: int) -> None:
        with self._lock:
            self.start_values = dict(self.current)
            self.index = (self.index + delta) % len(self.segments)
            self.segment_started_at = time.monotonic()
            self._announce()

    def toggle_pause(self) -> None:
        with self._lock:
            self.paused = not self.paused
            print(f"nidaq playback: {'paused' if self.paused else 'running'}", flush=True)

    def update(self, now: float) -> dict[str, float]:
        with self._lock:
            segment = self.segments[self.index]
            elapsed = now - self.segment_started_at
            if self.paused:
                self.segment_started_at = now - min(elapsed, segment.seconds)
                elapsed = min(elapsed, segment.seconds)
            self.current = segment.value_at(self.start_values, elapsed)
            if not self.paused and elapsed >= segment.seconds:
                self.start_values = dict(self.current)
                self.index = (self.index + 1) % len(self.segments)
                self.segment_started_at = now
                self._announce()
            return dict(self.current)


def to_volts(pressures: dict[str, float]) -> np.ndarray:
    """One row of NIDAQ channels, in volts, inverting the GUI's calibration."""
    row = np.zeros(len(NIDAQ_FIELD_NAMES), dtype=np.float64)
    for i, field_name in enumerate(NIDAQ_FIELD_NAMES):
        psi = pressures.get(field_name)
        if psi is None:
            continue
        scale, offset = PT_SCALES.get(field_name, (1.0, 0.0))
        row[i] = (psi - offset) / scale if scale else 0.0
    return row


def _stdin_reader(playback: Playback) -> None:
    print("nidaq playback: n = next segment, p = previous, h = pause/resume", flush=True)
    for line in sys.stdin:
        command = line.strip().lower()
        if command == "n":
            playback.jump(1)
        elif command == "p":
            playback.jump(-1)
        elif command == "h":
            playback.toggle_pause()
        elif command in ("q", "quit"):
            os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scripted NIDAQ pressure playback")
    parser.add_argument("--profile", choices=("pass", "fail"),
                        default=os.environ.get("GSE_SIM_PROFILE", "pass"))
    parser.add_argument("--no-stdin", action="store_true",
                        help="do not read segment commands from stdin")
    args = parser.parse_args()

    decay = float(
        os.environ.get(
            "GSE_SIM_DECAY_PSI_MIN",
            FAIL_DECAY_PSI_PER_MIN if args.profile == "fail" else PASS_DECAY_PSI_PER_MIN,
        )
    )

    playback = Playback(decay)
    print(
        f"nidaq playback: profile={args.profile} decay={decay:.1f} psi/min "
        f"window={DECAY_WINDOW_SECONDS:.0f}s -> {NIDAQ_FLIGHT_BIND}",
        flush=True,
    )

    if not args.no_stdin and sys.stdin is not None and sys.stdin.isatty():
        threading.Thread(target=_stdin_reader, args=(playback,), daemon=True).start()

    schema = pa.schema([(name, pa.float64()) for name in NIDAQ_FIELD_NAMES])
    period = 1.0 / FRAMES_PER_SECOND
    writer = None
    client = None

    while True:
        try:
            if writer is None:
                client = flight.connect(NIDAQ_FLIGHT_BIND)
                descriptor = flight.FlightDescriptor.for_path("nidaq_playback")
                writer, _ = client.do_put(descriptor, schema)
                print(f"nidaq playback: connected to {NIDAQ_FLIGHT_BIND}", flush=True)

            started = time.monotonic()
            row = to_volts(playback.update(started))
            frame = np.tile(row, (NIDAQ_ROWS_PER_FRAME, 1))
            batch = pa.RecordBatch.from_arrays(
                [pa.array(frame[:, i], type=pa.float64()) for i in range(len(NIDAQ_FIELD_NAMES))],
                schema=schema,
            )
            writer.write_batch(batch)

            remaining = period - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"nidaq playback: {type(e).__name__}: {e}; reconnecting", flush=True)
            writer = None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                client = None
            time.sleep(RECONNECT_SECONDS)

    print("nidaq playback: stopped", flush=True)


if __name__ == "__main__":
    main()
