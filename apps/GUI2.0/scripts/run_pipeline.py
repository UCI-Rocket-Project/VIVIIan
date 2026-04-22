from __future__ import annotations

from ucirplgui.runtime import build_ucirplgui_pipeline
from ucirplgui.device_interfaces import BackendSimDeviceInterface, FrontendOperatorDesk
from ucirplgui import config


def main() -> int:
    backend = BackendSimDeviceInterface()
    frontend = FrontendOperatorDesk()
    pipeline = build_ucirplgui_pipeline()

    print("UCIRPLGUI pipeline scaffold loaded.")
    print(f"Pipeline name: {pipeline.name}")
    print("Planned stream graph:")
    print(f"  - backend -> {config.SIGNAL_STREAM}")
    print(f"  - backend -> {config.PRESSURE_STREAM}")
    print(f"  - frontend reads -> {', '.join(frontend.required_streams())}")
    print(f"  - frontend writes -> {frontend.output_stream_name()}")
    print("Next TODOs:")
    print("  - add Pipeline streams for telemetry and ui_state")
    print("  - register the backend source task")
    print("  - register the frontend task with read and write bindings")
    print(f"Backend scaffold id: {backend.interface_id}")
    print(f"Frontend scaffold id: {frontend.interface_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
