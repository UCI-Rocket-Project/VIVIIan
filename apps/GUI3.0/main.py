"""UCIRPL Ops View -- GUI3.0.

A second operator front end for the same stack GUI2.1 drives. It imports the
engine, the connectors and the command plumbing from ``apps/GUI2.1`` and
replaces only the rendering layer, so GUI2.0 and GUI2.1 are untouched and there
is exactly one copy of the state machine, the valve tables and the calibration.

    python apps/GUI3.0/main.py          # against whatever is already running
    python apps/GUI3.0/run_sim.py       # against the simulator, whole stack

Layout follows ``UCIRPL Ops View.dc.html``; ``README.md`` records how each slot
in that design maps onto this procedure.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUI21 = HERE.parent / "GUI2.1"

# GUI2.1's modules import each other by bare name, so its directory has to be on
# the path before anything below is imported. Ours goes first, so a name we
# define always wins over one of theirs.
for entry in (str(HERE), str(GUI21)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import theme as T  # noqa: E402
import events as E  # noqa: E402
import fonts as F  # noqa: E402
from draw import Hold, Painter, Rect  # noqa: E402
from history import History  # noqa: E402
from model import OpsModel, TREND_CHANNELS  # noqa: E402
from panels import (  # noqa: E402
    commands, pid, sequence, strips, telemetry, topbar,
)

from captcha import Captcha67Gate  # noqa: E402
from generic_connector import LatestServer  # noqa: E402
from gse21connector import GSE2V1_ECHO_FIELD_NAMES, GSE2V1_FIELD_NAMES  # noqa: E402
from nidaq_gse import NIDAQ_FIELD_NAMES  # noqa: E402
from gui_gse2v1 import (  # noqa: E402
    GseCommandClient,
    make_gse2v1_command_buttons,
    sync_gse2v1_command_buttons_from_echo,
)
from frontendv2 import PT_SCALES  # noqa: E402
from state_machine import ControlContext, Dispatcher  # noqa: E402
from procedures.operations import GseEffector, GseValveMap, attach_manual_listener  # noqa: E402
from procedures.pressure_decay import build_machine  # noqa: E402

WINDOW_TITLE = "UCIRPL Ops View"
FLIGHT_ENDPOINTS = (
    ("grpc://0.0.0.0:8819", "gse", GSE2V1_FIELD_NAMES),
    ("grpc://0.0.0.0:8820", "echo", GSE2V1_ECHO_FIELD_NAMES),
    ("grpc://0.0.0.0:8826", "nidaq", NIDAQ_FIELD_NAMES),
)


def build_layout(p: Painter, width: float, height: float, has_alarm: bool):
    """Every region of the dashboard, in screen pixels."""
    screen = Rect(0.0, 0.0, width, height)

    top, rest = screen.cut_top(p.px(T.TOPBAR_H))
    alarm = None
    if has_alarm:
        alarm, rest = rest.cut_top(p.px(T.ALARM_H))

    controls, body = rest.cut_bottom(p.px(T.CONTROLS_H))
    strip_row, middle = body.cut_bottom(p.px(T.STRIPS_H))

    left, rest = middle.cut_left(p.px(T.LEFT_W))
    right, centre = rest.cut_right(p.px(T.RIGHT_W))

    return {
        "top": top,
        "alarm": alarm,
        "left": left,
        "centre": centre,
        "right": right,
        "strips": strip_row,
        # Everything the Ops tab tiles, as one rectangle: the Commands page
        # takes the same space rather than opening a window over it.
        "body": body,
        "controls": controls,
    }


def run(servers: tuple) -> None:
    import glfw
    from imgui_bundle import imgui
    from imgui_bundle.python_backends.glfw_backend import GlfwRenderer
    from OpenGL import GL as gl

    gse_server = next(s for s in servers if s.name == "gse")
    echo_server = next(s for s in servers if s.name == "echo")
    nidaq_server = next(s for s in servers if s.name == "nidaq")

    if not glfw.init():
        raise SystemExit("glfw failed to initialise")
    window = glfw.create_window(1920, 1080, WINDOW_TITLE, None, None)
    if not window:
        glfw.terminate()
        raise SystemExit("could not create a window")
    glfw.make_context_current(window)
    glfw.swap_interval(1)

    imgui.create_context()
    loaded_fonts = F.load(imgui)
    renderer = GlfwRenderer(window)

    # -- control stack, all of it GUI2.1's -----------------------------------
    command_client = GseCommandClient()
    make_gse2v1_command_buttons(command_client, gse_server)

    ctx = ControlContext(
        gse_server=gse_server,
        echo_server=echo_server,
        nidaq_server=nidaq_server,
        scales=PT_SCALES,
        valves=GseValveMap(),
    )
    dispatcher = Dispatcher(build_machine(), ctx, GseEffector(command_client))
    attach_manual_listener(dispatcher)

    event_log = E.EventLog()
    E.attach(dispatcher, event_log)

    model = OpsModel(
        dispatcher=dispatcher,
        ctx=ctx,
        history=History(TREND_CHANNELS),
        event_log=event_log,
        captcha=Captcha67Gate(),
    )
    hold = Hold()

    while not glfw.window_should_close(window):
        glfw.poll_events()
        renderer.process_inputs()
        imgui.new_frame()

        width, height = glfw.get_framebuffer_size(window)
        if width == 0 or height == 0:      # minimised
            imgui.end_frame()
            continue

        sync_gse2v1_command_buttons_from_echo(command_client, echo_server)
        dispatcher.tick()
        model.update(gse_server, nidaq_server)

        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(float(width), float(height)))
        imgui.push_style_var(imgui.StyleVar_.window_border_size, 0.0)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(0.0, 0.0))
        imgui.push_style_color(imgui.Col_.window_bg, imgui.ImVec4(0, 0, 0, 0))
        imgui.begin(
            "##ops_root",
            flags=(
                imgui.WindowFlags_.no_decoration
                | imgui.WindowFlags_.no_move
                | imgui.WindowFlags_.no_resize
                | imgui.WindowFlags_.no_saved_settings
                | imgui.WindowFlags_.no_bring_to_front_on_focus
                | imgui.WindowFlags_.no_scrollbar
                | imgui.WindowFlags_.no_scroll_with_mouse
            ),
        )

        p = Painter(imgui, loaded_fonts, T.scale_for(width, height), hold)
        p.fill(Rect(0, 0, float(width), float(height)), T.BG)

        alarm = model.alarm()
        layout = build_layout(p, float(width), float(height), alarm is not None)

        topbar.draw(p, layout["top"], model, (gse_server, echo_server, nidaq_server))
        if layout["alarm"] is not None:
            topbar.draw_alarm(p, layout["alarm"], model)
        if model.tab == "commands":
            commands.draw_page(p, layout["body"], model)
        else:
            sequence.draw(p, layout["left"], model)
            pid.draw(p, layout["centre"], model)
            telemetry.draw(p, layout["right"], model)
            strips.draw(p, layout["strips"], model)
            # Arm, resume and abort stay with the procedure they drive; ABORT
            # is never a tab away.
            commands.draw_controls(p, layout["controls"], model)

        model.captcha.render(imgui)

        imgui.end()
        imgui.pop_style_color()
        imgui.pop_style_var(2)

        imgui.render()
        gl.glViewport(0, 0, width, height)
        gl.glClearColor(0.027, 0.035, 0.039, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        renderer.render(imgui.get_draw_data())
        glfw.swap_buffers(window)

    renderer.shutdown()
    glfw.terminate()


def main() -> None:
    servers = tuple(
        LatestServer(address, name, fields) for address, name, fields in FLIGHT_ENDPOINTS
    )
    for server in servers:
        threading.Thread(target=server.serve, daemon=True).start()
        print("[gui3] %s listening" % server.name)
    run(servers)


if __name__ == "__main__":
    main()
