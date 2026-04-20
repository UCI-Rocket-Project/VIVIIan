from __future__ import annotations

from ucirplgui.device_interfaces import FrontendOperatorDesk
from ucirplgui import config


def main() -> int:
    desk = FrontendOperatorDesk()
    frontend = desk.build_frontend()

    print("UCIRPLGUI frontend scaffold loaded.")
    print(f"Frontend name: {frontend.name}")
    print(f"Window title: {config.WINDOW_TITLE}")
    print(f"Theme placeholder: {config.THEME_NAME}")
    print(f"Telemetry streams: {', '.join(desk.required_streams())}")
    print(f"Output stream: {desk.output_stream_name()}")
    print("Planned widgets:")
    for widget_name in desk.planned_widgets():
        print(f"  - {widget_name}")
    print("Next TODOs:")
    print("  - add the first widgets in ucirplgui.components.dashboard")
    print("  - bind signal_stream and pressure_stream to real readers")
    print("  - connect ui_state to a writer once the control surface is defined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
