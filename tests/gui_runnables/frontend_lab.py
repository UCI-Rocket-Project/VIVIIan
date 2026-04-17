from __future__ import annotations

import argparse
import threading
import time
from typing import Any, Sequence
import uuid

import numpy as np

from pythusa._buffers.ring import SharedRingBuffer
from pythusa._pipeline._stream_io import make_reader_binding, make_writer_binding

from viviian.frontend import Frontend, GlfwBackend
from viviian.gui_utils import AnalogNeedleGauge, GraphSeries, MomentaryButton, SensorGraph, ToggleButton
from tests.gui_runnables._support import BufferedFrameReader

_WINDOW_TITLE = "Frontend Lab"
_WINDOW_SIZE = (1280, 900)
_SAMPLE_RATE_HZ = 60.0
_ROWS_PER_BATCH = 12
_BATCH_SLEEP_S = 0.05


class FrontendFeed:
    def __init__(
        self,
        *,
        signal_reader: BufferedFrameReader,
        pressure_reader: BufferedFrameReader,
        sample_rate_hz: float = _SAMPLE_RATE_HZ,
        rows_per_batch: int = _ROWS_PER_BATCH,
        batch_sleep_s: float = _BATCH_SLEEP_S,
    ) -> None:
        self.signal_reader = signal_reader
        self.pressure_reader = pressure_reader
        self.sample_rate_hz = float(sample_rate_hz)
        self.rows_per_batch = int(rows_per_batch)
        self.batch_sleep_s = float(batch_sleep_s)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_index = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="frontend-lab-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            timestamps = (
                np.arange(self._sample_index, self._sample_index + self.rows_per_batch, dtype=np.float64)
                / self.sample_rate_hz
            )
            phase = 2.0 * np.pi * timestamps
            signal_values = np.sin(phase) + (0.20 * np.sin(phase * 4.0))
            pressure_values = 55.0 + (25.0 * np.sin(phase * 0.5))

            self.signal_reader.prime(np.vstack((timestamps, signal_values)))
            self.pressure_reader.prime(np.vstack((timestamps, pressure_values)))
            self._sample_index += self.rows_per_batch
            time.sleep(self.batch_sleep_s)


class StatePrinter:
    """Polls the output ring reader and prints each state snapshot."""

    def __init__(self, *, reader: Any, slot_ids: tuple[str, ...]) -> None:
        self._reader = reader
        self._slot_ids = slot_ids
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="state-printer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            frame = self._reader.read()
            if frame is not None:
                pairs = ", ".join(f"{sid}={v:.3f}" for sid, v in zip(self._slot_ids, frame))
                print(f"frontend state -> [{pairs}]")
            else:
                time.sleep(0.05)


def _make_state_ring(
    frontend: Frontend,
) -> tuple[SharedRingBuffer, SharedRingBuffer, Any, Any]:
    name = f"fe_{uuid.uuid4().hex[:8]}"
    size = frontend.output_ring_size()
    shape = frontend.output_shape
    w_ring = SharedRingBuffer(
        name=name, create=True, size=size,
        num_readers=1, reader=SharedRingBuffer._NO_READER, cache_align=False,
    )
    r_ring = SharedRingBuffer(
        name=name, create=False, size=size,
        num_readers=1, reader=0, cache_align=False,
    )
    writer = make_writer_binding(w_ring, name="state", shape=shape, dtype=np.float64)
    reader = make_reader_binding(r_ring, name="state", shape=shape, dtype=np.float64)
    return w_ring, r_ring, writer, reader


def _close_ring(ring: SharedRingBuffer, *, unlink: bool) -> None:
    try:
        ring.close()
    finally:
        if unlink:
            try:
                ring.unlink()
            except FileNotFoundError:
                pass


def build_frontend() -> Frontend:
    frontend = Frontend("frontend_lab")
    frontend.add(
        SensorGraph(
            "signal_graph",
            title="Synthetic Signal",
            series=(
                GraphSeries(
                    series_id="signal",
                    label="signal",
                    stream_name="signal_stream",
                    color_rgba=(0.16, 0.73, 0.78, 1.0),
                ),
            ),
            window_seconds=10.0,
            max_points_per_series=4096,
            show_series_controls=False,
            stable_y=True,
        )
    )
    frontend.add(
        AnalogNeedleGauge(
            gauge_id="pressure_gauge",
            label="Pressure",
            stream_name="pressure_stream",
            low_value=0.0,
            high_value=100.0,
            width=340.0,
            height=190.0,
        )
    )
    frontend.add(
        ToggleButton(
            button_id="arm_toggle",
            label="Arm",
            state_id="desk.arm",
            state=False,
        )
    )
    frontend.add(
        MomentaryButton(
            button_id="pulse_button",
            label="Pulse",
            state_id="desk.pulse",
            state=1.0,
        )
    )
    return frontend


def run() -> None:
    frontend = build_frontend()
    frontend.compile()

    signal_reader = BufferedFrameReader(max_rows=64)
    pressure_reader = BufferedFrameReader(max_rows=64)
    feed = FrontendFeed(signal_reader=signal_reader, pressure_reader=pressure_reader)

    w_ring, r_ring, writer, state_reader = _make_state_ring(frontend)
    slot_ids = tuple(slot.component_id for slot in frontend.output_slots)
    printer = StatePrinter(reader=state_reader, slot_ids=slot_ids)

    task = frontend.build_task(
        backend=GlfwBackend(width=_WINDOW_SIZE[0], height=_WINDOW_SIZE[1]),
        window_title=_WINDOW_TITLE,
    )

    feed.start()
    printer.start()
    try:
        task(
            signal_stream=signal_reader,
            pressure_stream=pressure_reader,
            output=writer,
        )
    finally:
        feed.stop()
        printer.stop()
        _close_ring(r_ring, unlink=False)
        _close_ring(w_ring, unlink=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone frontend runtime lab.")
    parser.parse_args(argv)
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
