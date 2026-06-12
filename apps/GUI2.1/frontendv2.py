from __future__ import annotations

import threading

from gse21connector import (
    GSE2V1_ECHO_FIELD_NAMES,
    GSE2V1_FIELD_NAMES,
)
from nidaq_gse import NIDAQ_FIELD_NAMES
from generic_connector import LatestServer
from gui_elements import (
    APP_BACKGROUND_COLOR,
    Button,
    NidaqGraph,
    MVAS_STATE,
    draw_table,
    valve_state,
)
from gui_gse2v1 import (
    GSE2V1_COMMAND_BUTTONS,
    GseCommandClient,
    make_gse2v1_command_buttons,
    sync_gse2v1_command_buttons_from_echo,
)

BUTTON_STATES = {} #dictionary of all button states
COMMAND_STATES = {} #dictionary of all command states this is things from the different boards 
def make_valve_states(gse_server: LatestServer) -> dict[str, valve_state]:
    return {
        button_id: valve_state(
            server=gse_server,
            field=config["status_field"],
            label=config["display_name"],
            invert=button_id in ("pv2", "tank_vent"),
        )
        for button_id, config in GSE2V1_COMMAND_BUTTONS.items()
        if config.get("status_field") is not None
    }


def draw_command_buttons(
    imgui,
    buttons: tuple[Button, ...],
    valve_states: dict[str, valve_state],
) -> None:
    imgui.text_unformatted("commands")
    imgui.columns(3, "command_buttons", borders=False)
    for button in buttons:
        button.render(imgui)
        state_display = valve_states.get(button.button_id)
        if state_display is not None:
            imgui.same_line()
            state_display.render(imgui)
        imgui.next_column()
    imgui.columns(1)

PT_SCALES = {
    "LOXPOT": (402.45048,0),
    "LNGPOT": (402.45048,0),
}






def run_imgui(servers: tuple[LatestServer, ...]) -> None:
    import glfw
    from imgui_bundle import imgui, implot
    from imgui_bundle.python_backends.glfw_backend import GlfwRenderer
    from OpenGL import GL as gl

    glfw.init()
    window = glfw.create_window(1200, 800, "frontendv2", None, None)
    glfw.make_context_current(window)
    glfw.swap_interval(0)
    imgui.create_context()
    implot.create_context()
    renderer = GlfwRenderer(window)
    
    gse_command_client = GseCommandClient()
    gse_server = next(server for server in servers if server.name == "gse")
    echo_server = next(server for server in servers if server.name == "echo")
    nidaq_server = next(server for server in servers if server.name == "nidaq")
    
    nidaq_graph_general = NidaqGraph(
        nidaq_server,
        title="nidaq graph",
        graph_id="nidaq_graph_general",
        field_names=PT_SCALES,
    )

  
    command_buttons = make_gse2v1_command_buttons(gse_command_client, gse_server)
    valve_states = make_valve_states(gse_server)
    mvas_state = MVAS_STATE(
        server=nidaq_server,
        label="MVAS",
    )

    while not glfw.window_should_close(window):
        glfw.poll_events()
        renderer.process_inputs()
        imgui.new_frame()

        width, height = glfw.get_framebuffer_size(window)
        imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
        imgui.set_next_window_size(imgui.ImVec2(float(width), float(height)))
        imgui.push_style_var(imgui.StyleVar_.window_border_size, 0.0)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(8.0, 8.0))
        imgui.begin(
            "##root",
            flags=(
                imgui.WindowFlags_.no_decoration
                | imgui.WindowFlags_.no_move
                | imgui.WindowFlags_.no_resize
                | imgui.WindowFlags_.no_saved_settings
                | imgui.WindowFlags_.no_bring_to_front_on_focus
            ),
        )
        imgui.text_unformatted(f"FPS: {imgui.get_io().framerate:.1f}")
        imgui.separator()
        sync_gse2v1_command_buttons_from_echo(gse_command_client, echo_server)
        draw_command_buttons(imgui, command_buttons, valve_states)
        imgui.separator()
        mvas_state.render(imgui)
        imgui.separator()
        for server in servers: 
            if server.name == "nidaq":
                nidaq_graph_general.draw(imgui)
                imgui.separator()
        for server in servers:
            draw_table(imgui, server)
            imgui.separator()
        imgui.end()
        imgui.pop_style_var(2)

        imgui.render()
        gl.glClearColor(*APP_BACKGROUND_COLOR)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        renderer.render(imgui.get_draw_data())
        glfw.swap_buffers(window)

    renderer.shutdown()
    implot.destroy_context()
    glfw.terminate()


def main() -> None:
    servers = tuple(
        LatestServer(address, name, fields)
        for address, name, fields in (
            ("grpc://0.0.0.0:8819", "gse", GSE2V1_FIELD_NAMES),
            ("grpc://0.0.0.0:8820", "echo", GSE2V1_ECHO_FIELD_NAMES),
            ("grpc://0.0.0.0:8826", "nidaq", NIDAQ_FIELD_NAMES),
        )
    )

    for server in servers:
        threading.Thread(target=server.serve, daemon=True).start()
        print(f"{server.name} listening")

    run_imgui(servers)


if __name__ == "__main__":
    main()
