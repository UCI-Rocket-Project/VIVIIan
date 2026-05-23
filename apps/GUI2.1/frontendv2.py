from __future__ import annotations

import threading

import pyarrow as pa
import pyarrow.flight as flight

from gse21connector import (
    GSE2V1_COMMAND_FIELD_NAMES,
    GSE2V1_ECHO_FIELD_NAMES,
    GSE2V1_FIELD_NAMES,
    GSE2V1_NUM_COMMAND_SIGNALS,
)
from nidaq_gse import NIDAQ_FIELD_NAMES
from generic_connector import LatestServer
from gui_elements import Button, draw_table, NidaqGraph


class GseCommandClient:
    def __init__(self) -> None:
        self.row = [0.0] * GSE2V1_NUM_COMMAND_SIGNALS
        self.schema = pa.schema([(name, pa.float64()) for name in GSE2V1_COMMAND_FIELD_NAMES])
        self.descriptor = flight.FlightDescriptor.for_path("gse2v1_commands")
        self.writer = None

    def send(self) -> None:
        if self.writer is None:
            client = flight.connect("grpc://127.0.0.1:8827")
            self.writer, _ = client.do_put(self.descriptor, self.schema)
        batch = pa.RecordBatch.from_arrays(
            [pa.array([value], type=pa.float64()) for value in self.row],
            schema=self.schema,
        )
        self.writer.write_batch(batch)


def make_command_buttons(client: GseCommandClient) -> tuple[Button, ...]:
    buttons = []
    for index, name in enumerate(GSE2V1_COMMAND_FIELD_NAMES):
        button = Button(
            f"cmd_{name}",
            name,
            width=260.0,
            toggle_on_click=True,
            status_color=(0.18, 0.18, 0.18, 1.0),
        )

        def send(button: Button, index: int = index) -> None:
            client.row[index] = 1.0 if button.state else 0.0
            button.set_status_color(
                (0.0, 0.7, 0.15, 1.0) if button.state else (0.18, 0.18, 0.18, 1.0)
            )
            client.send()

        button.on_click = send
        buttons.append(button)
    return tuple(buttons)


def draw_command_buttons(imgui, buttons: tuple[Button, ...]) -> None:
    imgui.text_unformatted("commands")
    imgui.columns(3, "command_buttons", border=False)
    for button in buttons:
        button.render(imgui)
        imgui.next_column()
    imgui.columns(1)









def run_imgui(servers: tuple[LatestServer, ...]) -> None:
    import glfw
    import imgui
    from imgui.integrations.glfw import GlfwRenderer
    from OpenGL import GL as gl

    glfw.init()
    window = glfw.create_window(1200, 800, "frontendv2", None, None)
    glfw.make_context_current(window)
    glfw.swap_interval(1)
    imgui.create_context()
    renderer = GlfwRenderer(window)
    nidaq_graph = NidaqGraph(next(server for server in servers if server.name == "nidaq"))
    command_buttons = make_command_buttons(GseCommandClient())

    while not glfw.window_should_close(window):
        glfw.poll_events()
        renderer.process_inputs()
        imgui.new_frame()

        width, height = glfw.get_framebuffer_size(window)
        imgui.set_next_window_position(0.0, 0.0)
        imgui.set_next_window_size(float(width), float(height))
        imgui.push_style_var(imgui.STYLE_WINDOW_BORDERSIZE, 0.0)
        imgui.push_style_var(imgui.STYLE_WINDOW_PADDING, (8.0, 8.0))
        imgui.begin(
            "##root",
            flags=(
                imgui.WINDOW_NO_DECORATION
                | imgui.WINDOW_NO_MOVE
                | imgui.WINDOW_NO_RESIZE
                | imgui.WINDOW_NO_SAVED_SETTINGS
                | imgui.WINDOW_NO_BRING_TO_FRONT_ON_FOCUS
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
        gl.glClearColor(0.1, 0.1, 0.1, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        renderer.render(imgui.get_draw_data())
        glfw.swap_buffers(window)

    renderer.shutdown()
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
