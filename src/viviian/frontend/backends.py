from __future__ import annotations

from dataclasses import dataclass
import importlib
import time
from typing import Any, Protocol, Sequence


class BackendSession(Protocol):
    imgui: Any

    def begin_frame(self) -> None:
        ...

    def end_frame(self) -> None:
        ...

    def should_close(self) -> bool:
        ...

    def close(self) -> None:
        ...


class BackendSpec(Protocol):
    def create(self, window_title: str) -> BackendSession:
        ...


class _FakeDrawList:
    def add_rect_filled(self, *_args: object) -> None:
        return None

    def add_rect(self, *_args: object) -> None:
        return None

    def add_line(self, *_args: object) -> None:
        return None

    def add_polyline(self, *_args: object) -> None:
        return None

    def add_circle_filled(self, *_args: object) -> None:
        return None

    def add_text(self, *_args: object) -> None:
        return None


class _FakeIO:
    def __init__(self, delta_time: float) -> None:
        self.delta_time = float(delta_time)


class _HeadlessStyle:
    def __init__(self) -> None:
        self.colors: dict[int, tuple[float, float, float, float]] = {}
        self.window_rounding = 0.0
        self.child_rounding = 0.0
        self.frame_rounding = 0.0
        self.grab_rounding = 0.0
        self.scrollbar_rounding = 0.0
        self.popup_rounding = 0.0
        self.tab_rounding = 0.0
        self.window_border_size = 0.0
        self.child_border_size = 0.0
        self.frame_border_size = 0.0
        self.window_padding = (0.0, 0.0)


class HeadlessImgui:
    COLOR_BUTTON = 1
    COLOR_BUTTON_HOVERED = 2
    COLOR_BUTTON_ACTIVE = 3
    COLOR_WINDOW_BACKGROUND = 4
    COLOR_CHILD_BACKGROUND = 5
    COLOR_TITLE_BACKGROUND = 6
    COLOR_TITLE_BACKGROUND_ACTIVE = 7
    COLOR_FRAME_BACKGROUND = 8
    COLOR_FRAME_BACKGROUND_HOVERED = 9
    COLOR_FRAME_BACKGROUND_ACTIVE = 10
    COLOR_BORDER = 11

    def __init__(
        self,
        *,
        button_presses: Sequence[bool] | None = None,
        delta_time: float = 1.0 / 60.0,
    ) -> None:
        self._presses = list(button_presses or ())
        self._draw_list = _FakeDrawList()
        self._io = _FakeIO(delta_time)
        self._style = _HeadlessStyle()

    def begin(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def end(self) -> None:
        return None

    def new_frame(self) -> None:
        return None

    def render(self) -> None:
        return None

    def get_draw_data(self) -> None:
        return None

    def get_style(self) -> _HeadlessStyle:
        return self._style

    def push_style_color(self, *_args: object) -> None:
        return None

    def pop_style_color(self, *_args: object) -> None:
        return None

    def push_item_width(self, *_args: object) -> None:
        return None

    def pop_item_width(self) -> None:
        return None

    def button(self, *_args: object, **_kwargs: object) -> bool:
        if self._presses:
            return bool(self._presses.pop(0))
        return False

    def text_disabled(self, *_args: object) -> None:
        return None

    def text_colored(self, *_args: object) -> None:
        return None

    def text_unformatted(self, *_args: object) -> None:
        return None

    def same_line(self, *_args: object) -> None:
        return None

    def spacing(self) -> None:
        return None

    def image(self, *_args: object, **_kwargs: object) -> None:
        return None

    def get_content_region_available(self) -> tuple[float, float]:
        return (320.0, 240.0)

    def get_cursor_screen_pos(self) -> tuple[float, float]:
        return (12.0, 18.0)

    def dummy(self, *_args: object) -> None:
        return None

    def get_window_draw_list(self) -> _FakeDrawList:
        return self._draw_list

    def get_color_u32_rgba(self, *rgba: float) -> int:
        r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
        return (a << 24) | (b << 16) | (g << 8) | r

    def get_io(self) -> _FakeIO:
        return self._io

    def calc_text_size(self, text: str) -> tuple[float, float]:
        return (max(6.0, len(text) * 6.0), 10.0)


@dataclass(frozen=True, slots=True)
class HeadlessBackend:
    max_frames: int = 1
    button_presses: tuple[bool, ...] = ()
    delta_time: float = 1.0 / 60.0
    frame_sleep_s: float = 0.0

    def create(self, window_title: str) -> "_HeadlessSession":
        return _HeadlessSession(
            window_title=window_title,
            max_frames=self.max_frames,
            button_presses=self.button_presses,
            delta_time=self.delta_time,
            frame_sleep_s=self.frame_sleep_s,
        )


class _HeadlessSession:
    def __init__(
        self,
        *,
        window_title: str,
        max_frames: int,
        button_presses: Sequence[bool],
        delta_time: float,
        frame_sleep_s: float,
    ) -> None:
        self.window_title = window_title
        self.imgui = HeadlessImgui(
            button_presses=button_presses,
            delta_time=delta_time,
        )
        self._max_frames = int(max_frames)
        self._frame_sleep_s = float(frame_sleep_s)
        self._frames_rendered = 0
        self._patches: list[tuple[Any, Any]] = []
        self._install_imgui_patches()

    def begin_frame(self) -> None:
        self.imgui.new_frame()

    def end_frame(self) -> None:
        self.imgui.render()
        self._frames_rendered += 1
        if self._frame_sleep_s > 0.0:
            time.sleep(self._frame_sleep_s)

    def should_close(self) -> bool:
        return self._frames_rendered >= self._max_frames

    def close(self) -> None:
        while self._patches:
            module, original = self._patches.pop()
            setattr(module, "_require_imgui", original)

    def _install_imgui_patches(self) -> None:
        for module_name in (
            "gui_utils.buttons",
            "gui_utils.graphs",
            "gui_utils.gauges",
            "gui_utils._3dmodel",
        ):
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError:
                continue
            original = getattr(module, "_require_imgui", None)
            if original is None:
                continue
            self._patches.append((module, original))
            setattr(module, "_require_imgui", self._require_imgui)

    def _require_imgui(self) -> HeadlessImgui:
        return self.imgui


@dataclass(frozen=True, slots=True)
class GlfwBackend:
    width: int = 1280
    height: int = 900
    clear_color: tuple[float, float, float, float] = (0.020, 0.030, 0.050, 1.0)
    vsync: int = 1

    def create(self, window_title: str) -> "_GlfwSession":
        return _GlfwSession(window_title=window_title, config=self)


class _GlfwSession:
    def __init__(self, *, window_title: str, config: GlfwBackend) -> None:
        try:
            import glfw
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "glfw is required for the frontend runtime. Install it with 'pip install glfw'."
            ) from exc

        try:
            import imgui
            from imgui.integrations.glfw import GlfwRenderer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "imgui with the GLFW integration is required for the frontend runtime."
            ) from exc

        try:
            from OpenGL import GL as gl
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PyOpenGL is required for the frontend runtime. Install it with 'pip install PyOpenGL'."
            ) from exc

        self._glfw = glfw
        self._imgui_module = imgui
        self._renderer_cls = GlfwRenderer
        self._gl = gl
        self._config = config
        self.imgui = imgui
        self._window = None
        self._renderer = None

        self._init_window(window_title)

    def begin_frame(self) -> None:
        self._glfw.poll_events()
        assert self._renderer is not None
        self._renderer.process_inputs()
        self.imgui.new_frame()

    def end_frame(self) -> None:
        self.imgui.render()
        clear = self._config.clear_color
        self._gl.glClearColor(*clear)
        self._gl.glClear(self._gl.GL_COLOR_BUFFER_BIT)
        assert self._renderer is not None
        self._renderer.render(self.imgui.get_draw_data())
        assert self._window is not None
        self._glfw.swap_buffers(self._window)

    def should_close(self) -> bool:
        assert self._window is not None
        return bool(self._glfw.window_should_close(self._window))

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.shutdown()
            self._renderer = None
        self._glfw.terminate()

    def _init_window(self, window_title: str) -> None:
        glfw = self._glfw
        if not glfw.init():
            raise RuntimeError("failed to initialize glfw")

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 2)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)

        window = glfw.create_window(
            int(self._config.width),
            int(self._config.height),
            window_title,
            None,
            None,
        )
        if not window:
            glfw.terminate()
            raise RuntimeError("failed to create frontend window")

        glfw.make_context_current(window)
        glfw.swap_interval(int(self._config.vsync))
        self.imgui.create_context()
        self._window = window
        self._renderer = self._renderer_cls(window)


__all__ = [
    "BackendSession",
    "BackendSpec",
    "GlfwBackend",
    "HeadlessBackend",
    "HeadlessImgui",
]
