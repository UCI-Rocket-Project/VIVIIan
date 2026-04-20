from __future__ import annotations

from ucirplgui.device_interfaces import BackendSimDeviceInterface


def main() -> int:
    backend = BackendSimDeviceInterface()

    print("UCIRPLGUI backend scaffold loaded.")
    print(f"Interface id: {backend.interface_id}")
    print(f"Telemetry streams: {', '.join(backend.stream_names())}")
    print("Publish contract:")
    for stream_name, description in backend.build_publish_contract().items():
        print(f"  - {stream_name}: {description}")
    print("Next TODOs:")
    print("  - implement build_source_task() with synthetic or hardware-backed batches")
    print("  - decide whether this class wraps viviian.deviceinterface.DeviceInterface")
    print("  - define the real batch shape and publish cadence in ucirplgui.config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
