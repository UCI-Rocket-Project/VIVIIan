from __future__ import annotations

from collections.abc import Callable

from ucirplgui import config


class BackendSimDeviceInterface:
    """Scaffold for the backend device boundary.

    This class is the intended home for a simulated telemetry source first,
    and later for hardware-facing integration or a wrapped DeviceInterface.
    """

    interface_id = "backend_sim_device"

    def stream_names(self) -> tuple[str, ...]:
        return config.TELEMETRY_STREAMS

    def build_source_task(self) -> Callable[..., None]:
        # TODO: Replace this placeholder with a real source task that writes
        # float64 telemetry batches into the pipeline.
        def _source_task(*_args: object, **_kwargs: object) -> None:
            raise NotImplementedError(
                "UCIRPLGUI backend scaffold only. Implement telemetry generation here."
            )

        return _source_task

    def build_publish_contract(self) -> dict[str, str]:
        return {
            config.SIGNAL_STREAM: "TODO: 2xN float64 telemetry batch for graph data",
            config.PRESSURE_STREAM: "TODO: 2xN float64 telemetry batch for gauge data",
        }
