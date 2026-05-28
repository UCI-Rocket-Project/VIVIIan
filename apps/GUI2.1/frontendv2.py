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
    draw_table,
)
from gui_gse2v1 import GseCommandClient, make_gse2v1_command_buttons

BUTTON_STATES = {} #dictionary of all button states
COMMAND_STATES = {} #dictionary of all command states this is things from the different boards 
def draw_command_buttons(imgui, buttons: tuple[Button, ...]) -> None:
    imgui.text_unformatted("commands")
    imgui.columns(3, "command_buttons", borders=False)
    for button in buttons:
        button.render(imgui)
        imgui.next_column()
    imgui.columns(1)









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
    nidaq_graph = NidaqGraph(next(server for server in servers if server.name == "nidaq"))
    command_buttons = make_gse2v1_command_buttons(gse_command_client, next(server for server in servers if server.name == "gse"))

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
        draw_command_buttons(imgui, command_buttons)
        imgui.separator()
        for server in servers:
            draw_table(imgui, server)
            if server.name == "nidaq":
                nidaq_graph.draw(imgui)
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
