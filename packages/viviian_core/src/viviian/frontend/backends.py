from __future__ import annotations

from dataclasses import dataclass
import importlib
import time
from typing import Any, Protocol, Sequence

from viviian.gui_utils import theme


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
        self.display_size = (1280.0, 900.0)
        self.fonts = _HeadlessFontAtlas()
        self.font_default: object | None = None


class _HeadlessFontAtlas:
    def add_font_default(self) -> object:
        return object()

    def add_font_from_file_ttf(self, *_args: object, **_kwargs: object) -> object:
        return object()

    def clear_fonts(self) -> None:
        return None


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
    COLOR_TEXT = 4
    COLOR_TEXT_DISABLED = 5
    COLOR_WINDOW_BACKGROUND = 6
    COLOR_CHILD_BACKGROUND = 7
    COLOR_TITLE_BACKGROUND = 8
    COLOR_TITLE_BACKGROUND_ACTIVE = 9
    COLOR_FRAME_BACKGROUND = 10
    COLOR_FRAME_BACKGROUND_HOVERED = 11
    COLOR_FRAME_BACKGROUND_ACTIVE = 12
    COLOR_BORDER = 13

    STYLE_FRAME_BORDER_SIZE = 1
    STYLE_FRAME_ROUNDING = 2
    STYLE_FRAME_PADDING = 3

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
        self._cursor_x = 12.0
        self._cursor_y = 18.0
        self._last_item_min = (12.0, 18.0)
        self._last_item_max = (12.0, 18.0)
        self._same_line = False

    def begin(self, *_args: object, **_kwargs: object) -> bool:
        self._cursor_x = 12.0
        self._cursor_y = 18.0
        self._same_line = False
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

    def push_style_var(self, *_args: object) -> None:
        return None

    def pop_style_var(self, *_args: object) -> None:
        return None

    def push_item_width(self, *_args: object) -> None:
        return None

    def pop_item_width(self) -> None:
        return None

    def button(self, *_args: object, **_kwargs: object) -> bool:
        width = float(_kwargs.get("width", 120.0) or 120.0)
        height = float(_kwargs.get("height", 28.0) or 28.0)
        self._place_item(width, height)
        if self._presses:
            return bool(self._presses.pop(0))
        return False

    def text_disabled(self, *_args: object) -> None:
        self._place_item(120.0, 16.0)
        return None

    def text_colored(self, *_args: object) -> None:
        self._place_item(120.0, 16.0)
        return None

    def text_unformatted(self, *_args: object) -> None:
        self._place_item(120.0, 16.0)
        return None

    def same_line(self, *_args: object) -> None:
        self._cursor_x = self._last_item_max[0] + 8.0
        self._cursor_y = self._last_item_min[1]
        self._same_line = True

    def spacing(self) -> None:
        self._same_line = False
        self._cursor_x = 12.0
        self._cursor_y = self._last_item_max[1] + 8.0
        return None

    def image(self, *_args: object, **_kwargs: object) -> None:
        self._place_item(160.0, 90.0)
        return None

    def input_text(self, _label: str, value: str, *_args: object, **_kwargs: object) -> tuple[bool, str]:
        self._place_item(220.0, 26.0)
        return False, value

    def separator(self) -> None:
        self._place_item(240.0, 8.0)

    def begin_group(self) -> None:
        return None

    def end_group(self) -> None:
        return None

    def push_font(self, *_args: object) -> None:
        return None

    def pop_font(self) -> None:
        return None

    def get_content_region_available(self) -> tuple[float, float]:
        return (max(120.0, 1260.0 - self._cursor_x), 900.0 - self._cursor_y)

    def get_cursor_screen_pos(self) -> tuple[float, float]:
        return (self._cursor_x, self._cursor_y)

    def get_item_rect_min(self) -> tuple[float, float]:
        return self._last_item_min

    def get_item_rect_max(self) -> tuple[float, float]:
        return self._last_item_max

    def dummy(self, width: float, height: float) -> None:
        self._place_item(float(width), float(height))

    def get_window_draw_list(self) -> _FakeDrawList:
        return self._draw_list

    def get_color_u32_rgba(self, *rgba: float) -> int:
        r, g, b, a = (max(0, min(255, int(channel * 255.0))) for channel in rgba)
        return (a << 24) | (b << 16) | (g << 8) | r

    def get_io(self) -> _FakeIO:
        return self._io

    def calc_text_size(self, text: str) -> tuple[float, float]:
        return (max(6.0, len(text) * 6.0), 10.0)

    def _place_item(self, width: float, height: float) -> None:
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        x0 = self._cursor_x
        y0 = self._cursor_y
        x1 = x0 + width
        y1 = y0 + height
        self._last_item_min = (x0, y0)
        self._last_item_max = (x1, y1)
        if self._same_line:
            self._cursor_x = x1 + 8.0
        else:
            self._cursor_x = 12.0
            self._cursor_y = y1 + 6.0
        self._same_line = False


@dataclass(frozen=True, slots=True)
class HeadlessBackend:
    max_frames: int = 1
    button_presses: tuple[bool, ...] = ()
    delta_time: float = 1.0 / 60.0
    frame_sleep_s: float = 0.0
    theme_name: theme.GuiThemeName = "legacy"

    def create(self, window_title: str) -> "_HeadlessSession":
        return _HeadlessSession(
            window_title=window_title,
            max_frames=self.max_frames,
            button_presses=self.button_presses,
            delta_time=self.delta_time,
            frame_sleep_s=self.frame_sleep_s,
            theme_name=self.theme_name,
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
        theme_name: theme.GuiThemeName,
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
        self._theme_name = theme_name
        self._install_imgui_patches()
        theme.apply_imgui_theme(self.imgui, theme_name=self._theme_name)

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
            "viviian.gui_utils.buttons",
            "viviian.gui_utils.graphs",
            "viviian.gui_utils.gauges",
            "viviian.gui_utils.operator",
            "viviian.gui_utils._3dmodel",
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
    theme_name: theme.GuiThemeName = "legacy"

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
        theme.apply_imgui_theme(self.imgui, theme_name=self._config.theme_name)
        self._window = window
        self._renderer = self._renderer_cls(window)


__all__ = [
    "BackendSession",
    "BackendSpec",
    "GlfwBackend",
    "HeadlessBackend",
    "HeadlessImgui",
]
