from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .backends import BackendSpec, GlfwBackend
from .components import BaseComponentAdapter, RenderContext, adapt_component
from viviian.gui_utils._streaming import fan_out_reader_groups


@dataclass(frozen=True, slots=True)
class OutputSlotSpec:
    index: int
    component_id: str
    state_id: str | None
    initial_value: float


class Frontend:
    def __init__(self, name: str = "frontend") -> None:
        resolved_name = str(name).strip()
        if not resolved_name:
            raise ValueError("Frontend name must be non-empty.")
        self.name = resolved_name
        self._components: list[Any] = []
        self._compiled = False
        self._closed = False
        self._adapters: tuple[BaseComponentAdapter, ...] = ()
        self._required_reads: tuple[str, ...] = ()
        self._output_slots: tuple[OutputSlotSpec, ...] = ()

    def __enter__(self) -> "Frontend":
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add(self, component: Any) -> Any:
        self._ensure_mutable()
        self._components.append(component)
        return component

    def compile(self) -> "Frontend":
        self._ensure_open()
        if self._compiled:
            return self

        adapters = tuple(adapt_component(component) for component in self._components)
        self._validate_unique_component_ids(adapters)
        self._adapters = adapters
        self._required_reads = self._collect_required_reads(adapters)
        self._output_slots = self._collect_output_slots(adapters)
        self._compiled = True
        return self

    @property
    def required_reads(self) -> tuple[str, ...]:
        self.compile()
        return self._required_reads

    @property
    def output_shape(self) -> tuple[int, ...]:
        self.compile()
        return (len(self._output_slots),)

    @property
    def output_slots(self) -> tuple[OutputSlotSpec, ...]:
        self.compile()
        return self._output_slots

    def read_bindings(self) -> dict[str, str]:
        return {stream_name: stream_name for stream_name in self.required_reads}

    def write_bindings(
        self,
        stream_name: str,
        *,
        output_binding: str = "output",
    ) -> dict[str, str]:
        if not output_binding:
            raise ValueError("output_binding must be non-empty.")
        return {output_binding: stream_name}

    def build_task(
        self,
        *,
        output_binding: str = "output",
        backend: BackendSpec | None = None,
        window_title: str | None = None,
        fill_backend_window: bool = False,
    ) -> "FrontendTask":
        self.compile()
        return FrontendTask(
            name=self.name,
            window_title=(window_title or self.name),
            adapters=self._adapters,
            required_reads=self._required_reads,
            output_slots=self._output_slots,
            output_binding=output_binding,
            backend=(backend or GlfwBackend()),
            fill_backend_window=bool(fill_backend_window),
        )

    def output_ring_size(self, *, headroom_frames: int = 8) -> int:
        """Ring buffer bytes needed to hold the float64 state vector.

        Follows the spike sizing formula: max(4096, frame_nbytes * (headroom + 1) + 4096).
        """
        self.compile()
        n = len(self._output_slots)
        if n == 0:
            return 4096
        frame_nbytes = n * 8  # float64 = 8 bytes per element
        return max(4096, frame_nbytes * (headroom_frames + 1) + 4096)

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Frontend is closed.")

    def _ensure_mutable(self) -> None:
        self._ensure_open()
        if self._compiled:
            raise RuntimeError("Frontend has already been compiled.")

    @staticmethod
    def _validate_unique_component_ids(adapters: Sequence[BaseComponentAdapter]) -> None:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for adapter in adapters:
            if adapter.component_id in seen:
                duplicates.add(adapter.component_id)
            seen.add(adapter.component_id)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise ValueError(f"Frontend component ids must be unique. Duplicates: {names}.")

    @staticmethod
    def _collect_required_reads(adapters: Sequence[BaseComponentAdapter]) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for adapter in adapters:
            for stream_name in adapter.required_stream_names:
                if stream_name in seen:
                    continue
                seen.add(stream_name)
                ordered.append(stream_name)
        return tuple(ordered)

    @staticmethod
    def _collect_output_slots(adapters: Sequence[BaseComponentAdapter]) -> tuple[OutputSlotSpec, ...]:
        slots: list[OutputSlotSpec] = []
        slot_index = 0
        for adapter in adapters:
            if not adapter.is_writable:
                continue
            slots.append(
                OutputSlotSpec(
                    index=slot_index,
                    component_id=adapter.component_id,
                    state_id=adapter.state_id,
                    initial_value=float(adapter.initial_output_value),
                )
            )
            slot_index += 1
        return tuple(slots)


@dataclass(frozen=True, slots=True)
class FrontendTask:
    name: str
    window_title: str
    adapters: tuple[BaseComponentAdapter, ...]
    required_reads: tuple[str, ...]
    output_slots: tuple[OutputSlotSpec, ...]
    output_binding: str
    backend: BackendSpec
    fill_backend_window: bool = False

    def __call__(self, **bindings: Any) -> None:
        readers = self._resolve_reader_bindings(bindings)
        component_readers = self._fan_out_reader_bindings(readers)
        writer = self._resolve_writer_binding(bindings)
        backend_session = self.backend.create(self.window_title)

        try:
            for adapter in self.adapters:
                adapter.bind(component_readers[adapter.component_id])

            snapshot_dirty = bool(self.output_slots)
            if snapshot_dirty and writer is not None:
                snapshot_dirty = not self._try_write_snapshot(writer)

            while not backend_session.should_close():
                for adapter in self.adapters:
                    adapter.consume()

                backend_session.begin_frame()
                imgui = backend_session.imgui
                begin_kwargs: dict[str, Any] = {}
                if self.fill_backend_window:
                    display_size = getattr(imgui.get_io(), "display_size", (0.0, 0.0))
                    width = float(display_size[0]) if len(display_size) > 0 else 0.0
                    height = float(display_size[1]) if len(display_size) > 1 else 0.0
                    if width > 0.0 and height > 0.0:
                        if hasattr(imgui, "set_next_window_position"):
                            imgui.set_next_window_position(0.0, 0.0)
                        if hasattr(imgui, "set_next_window_size"):
                            imgui.set_next_window_size(width, height)
                    begin_kwargs["flags"] = self._fullscreen_window_flags(imgui)
                imgui.begin(self.window_title, **begin_kwargs)
                for index, adapter in enumerate(self.adapters):
                    context = self._render_context()
                    if adapter.render(context):
                        snapshot_dirty = True
                    if index < len(self.adapters) - 1:
                        imgui.spacing()
                imgui.end()
                backend_session.end_frame()

                if snapshot_dirty and writer is not None:
                    if self._try_write_snapshot(writer):
                        snapshot_dirty = False
                        if self._after_snapshot_written():
                            snapshot_dirty = True
        finally:
            for adapter in self.adapters:
                adapter.close()
            backend_session.close()

    def _resolve_reader_bindings(self, bindings: Mapping[str, Any]) -> dict[str, Any]:
        readers: dict[str, Any] = {}
        missing: list[str] = []
        for stream_name in self.required_reads:
            binding = bindings.get(stream_name)
            if binding is None:
                missing.append(stream_name)
                continue
            readers[stream_name] = binding
        if missing:
            names = ", ".join(repr(name) for name in missing)
            raise KeyError(f"Frontend task is missing required reader bindings: {names}.")
        return readers

    def _fan_out_reader_bindings(
        self,
        readers: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Any]]:
        reader_groups = fan_out_reader_groups(
            readers,
            tuple(adapter.required_stream_names for adapter in self.adapters),
        )
        return {
            adapter.component_id: group
            for adapter, group in zip(self.adapters, reader_groups)
        }

    def _resolve_writer_binding(self, bindings: Mapping[str, Any]) -> Any | None:
        if not self.output_slots:
            return bindings.get(self.output_binding)
        try:
            return bindings[self.output_binding]
        except KeyError as exc:
            raise KeyError(
                f"Frontend task requires output binding {self.output_binding!r} for writable controls."
            ) from exc

    def _render_context(self) -> RenderContext:
        state_map: dict[str, bool] = {}
        for adapter in self.adapters:
            if adapter.state_id is None:
                continue
            bool_state = adapter.bool_state()
            if bool_state is None:
                continue
            state_map[adapter.state_id] = bool_state
        return RenderContext(gate_states=state_map, interlock_states=state_map)

    def _snapshot_vector(self) -> np.ndarray:
        snapshot = np.empty(len(self.output_slots), dtype=np.float64)
        slot_index = 0
        for adapter in self.adapters:
            if not adapter.is_writable:
                continue
            snapshot[slot_index] = adapter.snapshot_value()
            slot_index += 1
        return snapshot

    def _try_write_snapshot(self, writer: Any) -> bool:
        snapshot = self._snapshot_vector()
        return bool(writer.write(snapshot))

    def _after_snapshot_written(self) -> bool:
        had_reset = False
        for adapter in self.adapters:
            if not adapter.is_writable:
                continue
            if adapter.after_snapshot_written():
                had_reset = True
        return had_reset

    @staticmethod
    def _fullscreen_window_flags(imgui: Any) -> int:
        flags = 0
        for name in (
            "WINDOW_NO_TITLE_BAR",
            "WINDOW_NO_RESIZE",
            "WINDOW_NO_MOVE",
            "WINDOW_NO_COLLAPSE",
            "WINDOW_NO_BRING_TO_FRONT_ON_FOCUS",
            "WINDOW_NO_SAVED_SETTINGS",
        ):
            flags |= int(getattr(imgui, name, 0))
        return flags


__all__ = [
    "Frontend",
    "FrontendTask",
    "OutputSlotSpec",
]
