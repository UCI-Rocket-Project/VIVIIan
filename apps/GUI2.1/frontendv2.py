from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import re
import threading

import pyarrow as pa
import pyarrow.flight as flight

from gse21connector import (
    GSE2V1_ECHO_FIELD_NAMES,
    GSE2V1_FIELD_NAMES,
    GSE2V1_EXTERNAL_ADC_FIELDS,
    gse2v1_field_valid,
)
from nidaq_gse import NIDAQ_FIELD_NAMES
from generic_connector import LatestServer
from gui_elements import (
    APP_BACKGROUND_COLOR,
    Button,
    NidaqGraph,
    SensorGraph,
    MVAS_STATE,
    draw_table,
    valve_state,
)
from gui_gse2v1 import (
    GSE2V1_COMMAND_BUTTONS,
    GseCommandClient,
    make_gse2v1_command_buttons,
    sync_gse2v1_command_buttons_from_echo,
    update_gse2v1_momentary_buttons,
)

BUTTON_STATES = {} #dictionary of all button states
COMMAND_STATES = {} #dictionary of all command states this is things from the different boards 

TEST_NAME_FLIGHT_CONNECT = "grpc://127.0.0.1:1629"


def sanitize_test_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", name)


class TestNameControl:
    def __init__(self, address: str = TEST_NAME_FLIGHT_CONNECT) -> None:
        self.name = ""
        self.status = ""
        self._client = flight.connect(address)
        self._schema = pa.schema([("test_name", pa.string())])
        self._descriptor = flight.FlightDescriptor.for_path("test_name")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-name-send")
        self._pending: Future[str] | None = None

    def _send(self, name: str) -> str:
        batch = pa.record_batch([[name]], schema=self._schema)
        writer, _ = self._client.do_put(
            self._descriptor, self._schema, options=flight.FlightCallOptions(timeout=2.0),
        )
        with writer:
            writer.write_batch(batch)
        return name

    def send(self) -> None:
        if self._pending is not None:
            return
        self.name = sanitize_test_name(self.name)
        if not self.name:
            self.status = "Enter a test name."
            return
        self.status = f"Sending test name: {self.name}"
        self._pending = self._executor.submit(self._send, self.name)

    def draw(self, imgui) -> None:
        if self._pending is not None and self._pending.done():
            try:
                self.status = f"Sent test name: {self._pending.result()}"
            except Exception as exc:
                self.status = f"Could not send test name: {exc}"
            self._pending = None

        _, entered_name = imgui.input_text("Test name", self.name)
        self.name = sanitize_test_name(entered_name)
        imgui.same_line()
        imgui.begin_disabled(self._pending is not None)
        if imgui.button("Set test name"):
            self.send()
        imgui.end_disabled()
        imgui.text_unformatted("Letters, numbers, underscores and dashes only.")
        if self.status:
            imgui.text_unformatted(self.status)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._client.close()


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
    # "ai0": (402.45048,0), # this is wrong
    # "ai1": (1,0),
    # "LNGTANK": (402.45048,-0.471844),
    # "LOXTANK": (402.45048,-0.471844),
    
    # "VENT": (402.45048,0),
    # "COPV": (1255.98144,0),#(24471.303,5.4077),
    "COPV": (2284.4816,-45.4223),#(24471.303,5.4077),
    # "ai7": (1,0),
    "LOXING": (402.45048,0),
    "LNGING": (402.45048,0),
    # "ai10": (1,0),
    "LOXPOT": (402.45048,0),
    "LNGPOT": (402.45048,0),
    # "PT10": (1,0),
    "Thrust": (1,0),
    "1": (1,0),
    "2": (1,0),
}

# Firmware pressure/force units pass through until channel calibrations are supplied.
GSE_SCALES = {
    "Tank Vent": (402.94591, 1.87778),
    "LNG Tank": (402.94591, 1.87778),
    "pressure2": (1.0, 0.0),
    "LOX Tank": (402.94591, 1.87778),
}
# Remove a field from this set to enable its trace and graph readouts again.
GSE_DISABLED_FIELDS = {
    "loadCellForce2", "loadCellForce3", "loadCellForce4", "loadCellForce5",
}






def run_imgui(servers: tuple[LatestServer, ...], test_name_control: TestNameControl) -> None:
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
    
    sensor_graph = SensorGraph(
        {
            "NI-DAQ": NidaqGraph(nidaq_server, field_names=PT_SCALES),
            "GSE": NidaqGraph(
                gse_server,
                field_names={
                    field: scale for field, scale in GSE_SCALES.items()
                    if field not in GSE_DISABLED_FIELDS
                },
                field_validity=gse2v1_field_valid,
            ),
        },
        title="Sensors: NI-DAQ + GSE Ext ADC",
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
        update_gse2v1_momentary_buttons(gse_command_client)
        draw_command_buttons(imgui, command_buttons, valve_states)
        imgui.separator()
        mvas_state.render(imgui)
        imgui.separator()
        sensor_graph.draw(imgui)
        imgui.separator()
        for server in servers:
            draw_table(
                imgui, server,
                field_validity=gse2v1_field_valid if server.name == "gse" else None,
            )
            imgui.separator()
        imgui.separator()
        test_name_control.draw(imgui)
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

    test_name_control = TestNameControl()
    try:
        run_imgui(servers, test_name_control)
    finally:
        test_name_control.close()


if __name__ == "__main__":
    main()
