from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from .configure import (
    parse_color_rgba,
    read_toml_document,
    require_keys,
    require_kind,
    toml_bool,
    toml_float_array,
    toml_header,
    toml_string,
    write_toml_document,
)

BackpressureMode = Literal["latest_only", "blocking"]
ColorRGBA = tuple[float, float, float, float]

_DEFAULT_CAD_DIR = Path("gui_assets/cad")
_DEFAULT_COMPILED_DIR = Path("gui_assets/compiled")
_MODEL_VIEWER_KIND = "model_viewer"
_LEGACY_VIEWER_KIND = "rocket_viewer"
_MODEL_MESH_MANIFEST_KIND = "model_mesh_manifest"
_LEGACY_MESH_MANIFEST_KIND = "rocket_mesh_manifest"
_MESH_CACHE_VERSION = 1
_DEFAULT_VIEWPORT_HEIGHT = 420.0
_DEFAULT_BACKGROUND_COLOR = (0.030, 0.038, 0.055, 1.0)
_DEFAULT_BODY_COLOR = (0.180, 0.205, 0.240, 1.0)
_DEFAULT_OTHER_BODY_ALPHA = 0.38
_DEFAULT_CAMERA_DISTANCE = 11.0
_DEFAULT_CAMERA_AZIMUTH_DEG = 30.0
_DEFAULT_CAMERA_ELEVATION_DEG = 18.0
_VIEWER_FOV_Y_DEGREES = 35.0
_VIEWER_FIT_MARGIN = 1.18
_CAMERA_ORBIT_SENSITIVITY = 0.35
_CAMERA_PAN_SENSITIVITY = 0.0022
_CAMERA_ZOOM_DAMPING = 0.12
_MIN_CAMERA_DISTANCE = 0.25


@dataclass(frozen=True, slots=True)
class GradientStop:
    position: float
    color_rgba: ColorRGBA

    def __post_init__(self) -> None:
        position = float(self.position)
        if not math.isfinite(position) or position < 0.0 or position > 1.0:
            raise ValueError("GradientStop.position must be a finite float between 0.0 and 1.0.")
        object.__setattr__(self, "position", position)
        object.__setattr__(
            self,
            "color_rgba",
            parse_color_rgba(self.color_rgba, field_name="GradientStop.color_rgba"),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GradientStop":
        require_keys(data, "gradient stop", "position", "color_rgba")
        return cls(
            position=float(data["position"]),
            color_rgba=parse_color_rgba(data["color_rgba"]),
        )


@dataclass(frozen=True, slots=True, init=False)
class ModelBodyBinding:
    binding_id: str
    mesh_part_name: str
    value_stream_name: str
    low_value: float
    low_color_rgba: ColorRGBA
    high_value: float
    high_color_rgba: ColorRGBA
    default_color_rgba: ColorRGBA

    def __init__(
        self,
        *,
        binding_id: str | None = None,
        mesh_part_name: str,
        value_stream_name: str,
        low_value: float | None = None,
        low_color_rgba: Sequence[float] | None = None,
        high_value: float | None = None,
        high_color_rgba: Sequence[float] | None = None,
        default_color_rgba: Sequence[float] | None = None,
        part_id: str | None = None,
        range_min: float | None = None,
        range_max: float | None = None,
        gradient_stops: Sequence[GradientStop | Mapping[str, Any]] | None = None,
    ) -> None:
        resolved_binding_id = str(binding_id or part_id or "").strip()
        resolved_mesh_part_name = str(mesh_part_name).strip()
        resolved_stream_name = str(value_stream_name).strip()
        if not resolved_binding_id:
            raise ValueError("binding_id must be non-empty.")
        if not resolved_mesh_part_name:
            raise ValueError("mesh_part_name must be non-empty.")
        if not resolved_stream_name:
            raise ValueError("value_stream_name must be non-empty.")

        if low_value is None or low_color_rgba is None or high_value is None or high_color_rgba is None:
            resolved_low_value, resolved_low_color, resolved_high_value, resolved_high_color = (
                _resolve_legacy_binding_range(
                    range_min=range_min,
                    range_max=range_max,
                    gradient_stops=gradient_stops,
                )
            )
        else:
            resolved_low_value = float(low_value)
            resolved_high_value = float(high_value)
            resolved_low_color = parse_color_rgba(low_color_rgba, field_name="low_color_rgba")
            resolved_high_color = parse_color_rgba(high_color_rgba, field_name="high_color_rgba")

        if not math.isfinite(resolved_low_value) or not math.isfinite(resolved_high_value):
            raise ValueError("ModelBodyBinding values must be finite floats.")
        if resolved_high_value <= resolved_low_value:
            raise ValueError("high_value must be greater than low_value.")

        resolved_default_color = (
            resolved_low_color
            if default_color_rgba is None
            else parse_color_rgba(default_color_rgba, field_name="default_color_rgba")
        )

        object.__setattr__(self, "binding_id", resolved_binding_id)
        object.__setattr__(self, "mesh_part_name", resolved_mesh_part_name)
        object.__setattr__(self, "value_stream_name", resolved_stream_name)
        object.__setattr__(self, "low_value", resolved_low_value)
        object.__setattr__(self, "low_color_rgba", resolved_low_color)
        object.__setattr__(self, "high_value", resolved_high_value)
        object.__setattr__(self, "high_color_rgba", resolved_high_color)
        object.__setattr__(self, "default_color_rgba", resolved_default_color)

    def __repr__(self) -> str:
        return (
            "ModelBodyBinding("
            f"binding_id={self.binding_id!r}, "
            f"mesh_part_name={self.mesh_part_name!r}, "
            f"value_stream_name={self.value_stream_name!r}, "
            f"low_value={self.low_value}, "
            f"high_value={self.high_value})"
        )

    @property
    def part_id(self) -> str:
        return self.binding_id

    @property
    def range_min(self) -> float:
        return self.low_value

    @property
    def range_max(self) -> float:
        return self.high_value

    @property
    def gradient_stops(self) -> tuple[GradientStop, GradientStop]:
        return (
            GradientStop(position=0.0, color_rgba=self.low_color_rgba),
            GradientStop(position=1.0, color_rgba=self.high_color_rgba),
        )

    def resolve_color(self, value: float) -> ColorRGBA:
        clamped = min(max(float(value), self.low_value), self.high_value)
        ratio = (clamped - self.low_value) / max(self.high_value - self.low_value, 1e-12)
        return _lerp_color(self.low_color_rgba, self.high_color_rgba, ratio)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelBodyBinding":
        require_keys(
            data,
            "model body binding",
            "mesh_part_name",
            "value_stream_name",
        )
        return cls(
            binding_id=(str(data["binding_id"]) if data.get("binding_id") is not None else None),
            part_id=(str(data["part_id"]) if data.get("part_id") is not None else None),
            mesh_part_name=str(data["mesh_part_name"]),
            value_stream_name=str(data["value_stream_name"]),
            low_value=(float(data["low_value"]) if data.get("low_value") is not None else None),
            low_color_rgba=data.get("low_color_rgba"),
            high_value=(float(data["high_value"]) if data.get("high_value") is not None else None),
            high_color_rgba=data.get("high_color_rgba"),
            default_color_rgba=data.get("default_color_rgba"),
            range_min=(float(data["range_min"]) if data.get("range_min") is not None else None),
            range_max=(float(data["range_max"]) if data.get("range_max") is not None else None),
            gradient_stops=data.get("gradient_stops"),
        )


@dataclass(frozen=True, slots=True)
class ModelBodySnapshot:
    binding_id: str
    mesh_part_name: str
    timestamp: float | None
    value: float | None
    color_rgba: ColorRGBA


@dataclass(frozen=True, slots=True)
class ModelPoseSnapshot:
    timestamp: float | None
    position_xyz: tuple[float, float, float]
    rotation_matrix: tuple[float, ...]

    def matrix3x3(self) -> np.ndarray:
        return np.asarray(self.rotation_matrix, dtype=np.float64).reshape(3, 3)


@dataclass(frozen=True, slots=True)
class MeshPartRecord:
    part_id: str
    mesh_part_name: str
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MeshPartRecord":
        require_keys(
            data,
            "mesh part",
            "part_id",
            "mesh_part_name",
            "vertex_start",
            "vertex_count",
            "index_start",
            "index_count",
        )
        return cls(
            part_id=str(data["part_id"]),
            mesh_part_name=str(data["mesh_part_name"]),
            vertex_start=int(data["vertex_start"]),
            vertex_count=int(data["vertex_count"]),
            index_start=int(data["index_start"]),
            index_count=int(data["index_count"]),
        )


@dataclass(frozen=True, slots=True)
class MeshManifest:
    manifest_path: Path
    mesh_cache_path: Path
    source_asset_kind: str
    source_asset_path: Path
    source_sha256: str
    mesh_parts: tuple[MeshPartRecord, ...]

    def __post_init__(self) -> None:
        if not self.mesh_parts:
            raise ValueError("Mesh manifest must contain at least one mesh part.")
        names: set[str] = set()
        for part in self.mesh_parts:
            if part.mesh_part_name in names:
                raise ValueError(f"Duplicate mesh_part_name {part.mesh_part_name!r} in manifest.")
            names.add(part.mesh_part_name)

    @property
    def mesh_parts_by_name(self) -> dict[str, MeshPartRecord]:
        return {part.mesh_part_name: part for part in self.mesh_parts}


@dataclass(frozen=True, slots=True, init=False)
class ModelViewerConfig:
    viewer_id: str
    title: str
    mesh_cache_path: str
    manifest_path: str
    pose_stream_name: str
    model_alignment_matrix: tuple[float, ...]
    default_body_color_rgba: ColorRGBA
    other_body_alpha: float
    camera_distance: float
    camera_azimuth_deg: float
    camera_elevation_deg: float
    backpressure_mode: BackpressureMode
    show_labels: bool
    show_legend: bool
    show_axes: bool
    background_color_rgba: ColorRGBA
    body_bindings: tuple[ModelBodyBinding, ...]

    def __init__(
        self,
        viewer_id: str,
        *,
        title: str,
        mesh_cache_path: str,
        manifest_path: str,
        pose_stream_name: str | None = None,
        orientation_stream_name: str | None = None,
        model_alignment_matrix: Sequence[float] | None = None,
        default_body_color_rgba: Sequence[float] = _DEFAULT_BODY_COLOR,
        other_body_alpha: float = _DEFAULT_OTHER_BODY_ALPHA,
        camera_distance: float = _DEFAULT_CAMERA_DISTANCE,
        camera_azimuth_deg: float = _DEFAULT_CAMERA_AZIMUTH_DEG,
        camera_elevation_deg: float = _DEFAULT_CAMERA_ELEVATION_DEG,
        backpressure_mode: BackpressureMode = "latest_only",
        show_labels: bool = True,
        show_legend: bool = True,
        show_axes: bool = True,
        background_color_rgba: Sequence[float] = _DEFAULT_BACKGROUND_COLOR,
        body_bindings: Sequence[ModelBodyBinding | Mapping[str, Any]] | None = None,
        parts: Sequence[ModelBodyBinding | Mapping[str, Any]] | None = None,
    ) -> None:
        resolved_viewer_id = str(viewer_id).strip()
        resolved_title = str(title).strip()
        resolved_cache = str(mesh_cache_path).strip()
        resolved_manifest = str(manifest_path).strip()
        resolved_pose_stream = str(pose_stream_name or orientation_stream_name or "").strip()
        if not resolved_viewer_id:
            raise ValueError("viewer_id must be non-empty.")
        if not resolved_title:
            raise ValueError("title must be non-empty.")
        if not resolved_cache:
            raise ValueError("mesh_cache_path must be non-empty.")
        if not resolved_manifest:
            raise ValueError("manifest_path must be non-empty.")
        if not resolved_pose_stream:
            raise ValueError("pose_stream_name must be non-empty.")
        if backpressure_mode not in ("latest_only", "blocking"):
            raise ValueError("backpressure_mode must be 'latest_only' or 'blocking'.")

        parsed_alignment = _parse_alignment_matrix(model_alignment_matrix)
        parsed_default_body_color = parse_color_rgba(
            default_body_color_rgba,
            field_name="default_body_color_rgba",
        )
        parsed_background_color = parse_color_rgba(
            background_color_rgba,
            field_name="background_color_rgba",
        )
        if not math.isfinite(float(other_body_alpha)) or not (0.0 <= float(other_body_alpha) <= 1.0):
            raise ValueError("other_body_alpha must be a finite float between 0.0 and 1.0.")
        if not math.isfinite(float(camera_distance)) or float(camera_distance) <= 0.0:
            raise ValueError("camera_distance must be a finite float greater than 0.")
        if not math.isfinite(float(camera_azimuth_deg)) or not math.isfinite(float(camera_elevation_deg)):
            raise ValueError("camera azimuth and elevation must be finite floats.")

        raw_bindings = body_bindings if body_bindings is not None else parts
        parsed_bindings = tuple(_coerce_body_binding(item) for item in (raw_bindings or ()))
        _validate_body_bindings(parsed_bindings)

        object.__setattr__(self, "viewer_id", resolved_viewer_id)
        object.__setattr__(self, "title", resolved_title)
        object.__setattr__(self, "mesh_cache_path", resolved_cache)
        object.__setattr__(self, "manifest_path", resolved_manifest)
        object.__setattr__(self, "pose_stream_name", resolved_pose_stream)
        object.__setattr__(self, "model_alignment_matrix", parsed_alignment)
        object.__setattr__(self, "default_body_color_rgba", parsed_default_body_color)
        object.__setattr__(self, "other_body_alpha", float(other_body_alpha))
        object.__setattr__(self, "camera_distance", float(camera_distance))
        object.__setattr__(self, "camera_azimuth_deg", float(camera_azimuth_deg))
        object.__setattr__(self, "camera_elevation_deg", float(camera_elevation_deg))
        object.__setattr__(self, "backpressure_mode", backpressure_mode)
        object.__setattr__(self, "show_labels", bool(show_labels))
        object.__setattr__(self, "show_legend", bool(show_legend))
        object.__setattr__(self, "show_axes", bool(show_axes))
        object.__setattr__(self, "background_color_rgba", parsed_background_color)
        object.__setattr__(self, "body_bindings", parsed_bindings)

    def __repr__(self) -> str:
        return (
            "ModelViewerConfig("
            f"viewer_id={self.viewer_id!r}, "
            f"title={self.title!r}, "
            f"pose_stream_name={self.pose_stream_name!r}, "
            f"body_bindings={[item.binding_id for item in self.body_bindings]!r})"
        )

    @property
    def orientation_stream_name(self) -> str:
        return self.pose_stream_name

    @property
    def parts(self) -> tuple[ModelBodyBinding, ...]:
        return self.body_bindings

    def build_viewer(self) -> "ModelViewer":
        return ModelViewer(self)

    def export(self, path: str | Path) -> Path:
        lines = toml_header(_MODEL_VIEWER_KIND)
        lines.extend(
            [
                f"viewer_id = {toml_string(self.viewer_id)}",
                f"title = {toml_string(self.title)}",
                f"mesh_cache_path = {toml_string(self.mesh_cache_path)}",
                f"manifest_path = {toml_string(self.manifest_path)}",
                f"pose_stream_name = {toml_string(self.pose_stream_name)}",
                f"model_alignment_matrix = {toml_float_array(self.model_alignment_matrix)}",
                f"default_body_color_rgba = {toml_float_array(self.default_body_color_rgba)}",
                f"other_body_alpha = {self.other_body_alpha!r}",
                f"camera_distance = {self.camera_distance!r}",
                f"camera_azimuth_deg = {self.camera_azimuth_deg!r}",
                f"camera_elevation_deg = {self.camera_elevation_deg!r}",
                f"backpressure_mode = {toml_string(self.backpressure_mode)}",
                f"show_labels = {toml_bool(self.show_labels)}",
                f"show_legend = {toml_bool(self.show_legend)}",
                f"show_axes = {toml_bool(self.show_axes)}",
                f"background_color_rgba = {toml_float_array(self.background_color_rgba)}",
                "",
            ]
        )

        for binding in self.body_bindings:
            lines.extend(
                [
                    "[[body_bindings]]",
                    f"binding_id = {toml_string(binding.binding_id)}",
                    f"mesh_part_name = {toml_string(binding.mesh_part_name)}",
                    f"value_stream_name = {toml_string(binding.value_stream_name)}",
                    f"low_value = {binding.low_value!r}",
                    f"low_color_rgba = {toml_float_array(binding.low_color_rgba)}",
                    f"high_value = {binding.high_value!r}",
                    f"high_color_rgba = {toml_float_array(binding.high_color_rgba)}",
                    f"default_color_rgba = {toml_float_array(binding.default_color_rgba)}",
                    "",
                ]
            )
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "ModelViewerConfig":
        data = read_toml_document(path)
        require_kind(data, _MODEL_VIEWER_KIND, _LEGACY_VIEWER_KIND)
        require_keys(
            data,
            "model_viewer",
            "viewer_id",
            "title",
            "mesh_cache_path",
            "manifest_path",
        )
        return cls(
            viewer_id=str(data["viewer_id"]),
            title=str(data["title"]),
            mesh_cache_path=str(data["mesh_cache_path"]),
            manifest_path=str(data["manifest_path"]),
            pose_stream_name=(str(data["pose_stream_name"]) if data.get("pose_stream_name") is not None else None),
            orientation_stream_name=(
                str(data["orientation_stream_name"])
                if data.get("orientation_stream_name") is not None
                else None
            ),
            model_alignment_matrix=data.get("model_alignment_matrix"),
            default_body_color_rgba=data.get("default_body_color_rgba", _DEFAULT_BODY_COLOR),
            other_body_alpha=float(data.get("other_body_alpha", _DEFAULT_OTHER_BODY_ALPHA)),
            camera_distance=float(data.get("camera_distance", _DEFAULT_CAMERA_DISTANCE)),
            camera_azimuth_deg=float(data.get("camera_azimuth_deg", _DEFAULT_CAMERA_AZIMUTH_DEG)),
            camera_elevation_deg=float(
                data.get("camera_elevation_deg", _DEFAULT_CAMERA_ELEVATION_DEG)
            ),
            backpressure_mode=str(data.get("backpressure_mode", "latest_only")),
            show_labels=bool(data.get("show_labels", True)),
            show_legend=bool(data.get("show_legend", True)),
            show_axes=bool(data.get("show_axes", True)),
            background_color_rgba=data.get("background_color_rgba", _DEFAULT_BACKGROUND_COLOR),
            body_bindings=tuple(
                ModelBodyBinding.from_dict(item)
                for item in data.get("body_bindings", data.get("parts", ()))
            ),
        )


class _BodyRuntime:
    def __init__(self, binding: ModelBodyBinding) -> None:
        self.binding = binding
        self.reset()

    def reset(self) -> None:
        self.last_timestamp: float | None = None
        self.value: float | None = None
        self.color_rgba: ColorRGBA = self.binding.default_color_rgba

    def apply_batch(self, frame: np.ndarray) -> bool:
        timestamps, values = _normalize_scalar_batch(frame)
        if timestamps.size == 0:
            return False

        latest_timestamp = float(timestamps[-1])
        latest_value = float(values[-1])
        if self.last_timestamp is not None and latest_timestamp <= self.last_timestamp:
            self.reset()
        self.last_timestamp = latest_timestamp
        self.value = latest_value
        self.color_rgba = self.binding.resolve_color(latest_value)
        return True

    def snapshot(self) -> ModelBodySnapshot:
        return ModelBodySnapshot(
            binding_id=self.binding.binding_id,
            mesh_part_name=self.binding.mesh_part_name,
            timestamp=self.last_timestamp,
            value=self.value,
            color_rgba=self.color_rgba,
        )


class _PoseRuntime:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.snapshot = ModelPoseSnapshot(
            timestamp=None,
            position_xyz=(0.0, 0.0, 0.0),
            rotation_matrix=tuple(np.eye(3, dtype=np.float64).reshape(9)),
        )

    def apply_batch(self, frame: np.ndarray) -> bool:
        timestamps, positions, matrices = _normalize_pose_batch(frame)
        if timestamps.size == 0:
            return False

        latest_timestamp = float(timestamps[-1])
        latest_position = tuple(float(item) for item in positions[-1])
        latest_matrix = tuple(float(item) for item in matrices[-1].reshape(9))
        if self.snapshot.timestamp is not None and latest_timestamp <= self.snapshot.timestamp:
            self.reset()
        self.snapshot = ModelPoseSnapshot(
            timestamp=latest_timestamp,
            position_xyz=latest_position,
            rotation_matrix=latest_matrix,
        )
        return True


@dataclass(slots=True)
class _CameraState:
    distance: float
    azimuth_deg: float
    elevation_deg: float
    target_offset: np.ndarray
    base_distance: float
    base_azimuth_deg: float
    base_elevation_deg: float

    @classmethod
    def from_config(cls, config: ModelViewerConfig) -> "_CameraState":
        return cls(
            distance=config.camera_distance,
            azimuth_deg=config.camera_azimuth_deg,
            elevation_deg=config.camera_elevation_deg,
            target_offset=np.zeros(3, dtype=np.float64),
            base_distance=config.camera_distance,
            base_azimuth_deg=config.camera_azimuth_deg,
            base_elevation_deg=config.camera_elevation_deg,
        )

    def reset(self) -> None:
        self.distance = self.base_distance
        self.azimuth_deg = self.base_azimuth_deg
        self.elevation_deg = self.base_elevation_deg
        self.target_offset[:] = 0.0


@dataclass(slots=True)
class _MeshCacheData:
    vertices: np.ndarray
    normals: np.ndarray
    indices: np.ndarray
    bounds_center: np.ndarray
    bounds_radius: float


@dataclass(slots=True)
class _OpenGLResources:
    gl: Any
    program: int
    vao: int
    vertex_vbo: int
    normal_vbo: int
    ebo: int
    axis_vao: int
    axis_vbo: int
    fbo: int
    color_texture: int
    depth_rbo: int
    framebuffer_size: tuple[int, int]
    mesh: _MeshCacheData


class _OpenGLMeshRenderer:
    def __init__(self, mesh_cache_path: str | Path, background_color_rgba: ColorRGBA) -> None:
        try:
            from OpenGL import GL as gl
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PyOpenGL is required for ModelViewer rendering. Install it with 'pip install PyOpenGL'."
            ) from exc

        self._gl = gl
        self._background_color_rgba = tuple(float(channel) for channel in background_color_rgba)
        self._resources = self._create_resources(Path(mesh_cache_path))

    def close(self) -> None:
        if self._resources is None:
            return
        gl = self._gl
        resources = self._resources
        gl.glDeleteVertexArrays(1, np.array([resources.vao], dtype=np.uint32))
        gl.glDeleteVertexArrays(1, np.array([resources.axis_vao], dtype=np.uint32))
        gl.glDeleteBuffers(1, np.array([resources.vertex_vbo], dtype=np.uint32))
        gl.glDeleteBuffers(1, np.array([resources.normal_vbo], dtype=np.uint32))
        gl.glDeleteBuffers(1, np.array([resources.ebo], dtype=np.uint32))
        gl.glDeleteBuffers(1, np.array([resources.axis_vbo], dtype=np.uint32))
        gl.glDeleteFramebuffers(1, np.array([resources.fbo], dtype=np.uint32))
        gl.glDeleteTextures(1, np.array([resources.color_texture], dtype=np.uint32))
        gl.glDeleteRenderbuffers(1, np.array([resources.depth_rbo], dtype=np.uint32))
        gl.glDeleteProgram(resources.program)
        self._resources = None

    def render(
        self,
        *,
        width: int,
        height: int,
        manifest: MeshManifest,
        part_colors: Mapping[str, ColorRGBA],
        camera: _CameraState,
        model_alignment_matrix: tuple[float, ...],
        pose: ModelPoseSnapshot,
        show_axes: bool,
    ) -> int:
        if width <= 0 or height <= 0:
            raise ValueError("Viewport width and height must be positive integers.")
        resources = self._resources
        if resources is None:
            raise RuntimeError("Renderer has already been closed.")
        if resources.framebuffer_size != (width, height):
            self._resize_framebuffer(width, height)
            resources = self._resources
            assert resources is not None

        gl = self._gl
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, resources.fbo)
        gl.glViewport(0, 0, width, height)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glClearColor(*self._background_color_rgba)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

        projection = _perspective_matrix(
            fov_y_degrees=_VIEWER_FOV_Y_DEGREES,
            aspect_ratio=float(width) / max(float(height), 1.0),
            near=0.05,
            far=max(resources.mesh.bounds_radius * 24.0, 50.0),
        )
        fit_distance = _fit_camera_distance(
            bounds_radius=resources.mesh.bounds_radius,
            fov_y_degrees=_VIEWER_FOV_Y_DEGREES,
            aspect_ratio=float(width) / max(float(height), 1.0),
            margin=_VIEWER_FIT_MARGIN,
        )
        _ensure_camera_fit(camera, fit_distance)
        target = camera.target_offset
        view, eye, up = _camera_view_matrix(resources.mesh.bounds_radius, camera, target)
        pose_matrix = pose.matrix3x3()
        alignment = np.asarray(model_alignment_matrix, dtype=np.float64).reshape(3, 3)
        model = np.eye(4, dtype=np.float64)
        model[:3, :3] = pose_matrix @ alignment
        model[:3, 3] = -(pose_matrix @ alignment @ resources.mesh.bounds_center)
        mvp = projection @ view @ model

        gl.glUseProgram(resources.program)
        gl.glBindVertexArray(resources.vao)

        u_mvp = gl.glGetUniformLocation(resources.program, "u_mvp")
        u_model = gl.glGetUniformLocation(resources.program, "u_model")
        u_color = gl.glGetUniformLocation(resources.program, "u_color")
        u_light_dir = gl.glGetUniformLocation(resources.program, "u_light_dir")
        gl.glUniformMatrix4fv(u_mvp, 1, gl.GL_TRUE, np.asarray(mvp, dtype=np.float32))
        gl.glUniformMatrix4fv(u_model, 1, gl.GL_TRUE, np.asarray(model, dtype=np.float32))
        gl.glUniform3f(u_light_dir, -0.42, 0.68, 0.59)

        for part in manifest.mesh_parts:
            rgba = part_colors[part.mesh_part_name]
            gl.glUniform4f(u_color, *rgba)
            gl.glDrawElements(
                gl.GL_TRIANGLES,
                int(part.index_count),
                gl.GL_UNSIGNED_INT,
                ctypes.c_void_p(int(part.index_start) * np.dtype(np.uint32).itemsize),
            )

        if show_axes:
            axis_mvp = projection @ view
            gl.glUniformMatrix4fv(u_mvp, 1, gl.GL_TRUE, np.asarray(axis_mvp, dtype=np.float32))
            gl.glUniformMatrix4fv(u_model, 1, gl.GL_TRUE, np.asarray(np.eye(4), dtype=np.float32))
            gl.glBindVertexArray(resources.axis_vao)
            for color, offset in (
                ((0.960, 0.220, 0.180, 1.0), 0),
                ((0.280, 0.860, 0.360, 1.0), 2),
                ((0.220, 0.540, 0.960, 1.0), 4),
            ):
                gl.glUniform4f(u_color, *color)
                gl.glDrawArrays(gl.GL_LINES, offset, 2)

        gl.glBindVertexArray(0)
        gl.glUseProgram(0)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        return int(resources.color_texture)

    def _create_resources(self, mesh_cache_path: Path) -> _OpenGLResources:
        gl = self._gl
        mesh = _load_mesh_cache(mesh_cache_path)
        program = _build_shader_program(gl)

        vao = gl.glGenVertexArrays(1)
        gl.glBindVertexArray(vao)

        vertex_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vertex_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, mesh.vertices.nbytes, mesh.vertices, gl.GL_STATIC_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 0, ctypes.c_void_p(0))

        normal_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, normal_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, mesh.normals.nbytes, mesh.normals, gl.GL_STATIC_DRAW)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, False, 0, ctypes.c_void_p(0))

        ebo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ebo)
        gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, mesh.indices.nbytes, mesh.indices, gl.GL_STATIC_DRAW)

        axis_points = np.asarray(
            [
                [0.0, 0.0, 0.0],
                [1.4, 0.0, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, 1.4, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 1.4],
            ],
            dtype=np.float32,
        )
        axis_vao = gl.glGenVertexArrays(1)
        axis_vbo = gl.glGenBuffers(1)
        gl.glBindVertexArray(axis_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, axis_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, axis_points.nbytes, axis_points, gl.GL_STATIC_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 0, ctypes.c_void_p(0))

        gl.glBindVertexArray(0)
        fbo, texture, depth = _create_framebuffer(gl, 1, 1)
        return _OpenGLResources(
            gl=gl,
            program=program,
            vao=vao,
            vertex_vbo=vertex_vbo,
            normal_vbo=normal_vbo,
            ebo=ebo,
            axis_vao=axis_vao,
            axis_vbo=axis_vbo,
            fbo=fbo,
            color_texture=texture,
            depth_rbo=depth,
            framebuffer_size=(1, 1),
            mesh=mesh,
        )

    def _resize_framebuffer(self, width: int, height: int) -> None:
        resources = self._resources
        if resources is None:
            return
        gl = self._gl
        gl.glDeleteFramebuffers(1, np.array([resources.fbo], dtype=np.uint32))
        gl.glDeleteTextures(1, np.array([resources.color_texture], dtype=np.uint32))
        gl.glDeleteRenderbuffers(1, np.array([resources.depth_rbo], dtype=np.uint32))
        fbo, texture, depth = _create_framebuffer(gl, width, height)
        resources.fbo = fbo
        resources.color_texture = texture
        resources.depth_rbo = depth
        resources.framebuffer_size = (width, height)


class ModelViewer:
    def __init__(self, config: ModelViewerConfig) -> None:
        self.config = config
        self.viewer_id = config.viewer_id
        self.title = config.title
        self.mesh_cache_path = str(config.mesh_cache_path)
        self.manifest_path = str(config.manifest_path)
        self.pose_stream_name = config.pose_stream_name
        self.model_alignment_matrix = config.model_alignment_matrix
        self.default_body_color_rgba = config.default_body_color_rgba
        self.other_body_alpha = config.other_body_alpha
        self.camera_distance = config.camera_distance
        self.camera_azimuth_deg = config.camera_azimuth_deg
        self.camera_elevation_deg = config.camera_elevation_deg
        self.backpressure_mode = config.backpressure_mode
        self.show_labels = config.show_labels
        self.show_legend = config.show_legend
        self.show_axes = config.show_axes
        self.background_color_rgba = config.background_color_rgba
        self.body_bindings = config.body_bindings

        self._manifest = _load_mesh_manifest(self.manifest_path)
        _validate_config_paths(config, self._manifest)
        _validate_bindings_against_manifest(self.body_bindings, self._manifest)

        self._body_runtime = {
            binding.binding_id: _BodyRuntime(binding) for binding in self.body_bindings
        }
        self._binding_by_mesh_part = {
            binding.mesh_part_name: binding.binding_id for binding in self.body_bindings
        }
        self._scalar_readers: dict[str, Any] = {}
        self._pose_reader: Any | None = None
        self._pose_runtime = _PoseRuntime()
        self._camera_state = _CameraState.from_config(config)
        self._renderer: _OpenGLMeshRenderer | None = None

    def __repr__(self) -> str:
        return (
            "ModelViewer("
            f"viewer_id={self.viewer_id!r}, "
            f"title={self.title!r}, "
            f"pose_stream_name={self.pose_stream_name!r}, "
            f"body_bindings={[item.binding_id for item in self.body_bindings]!r})"
        )

    def bind(self, readers: Mapping[str, Any]) -> None:
        bound_scalar_readers: dict[str, Any] = {}
        for binding in self.body_bindings:
            reader = readers.get(binding.value_stream_name)
            if reader is None:
                raise KeyError(
                    f"ModelViewer {self.viewer_id!r} requires reader {binding.value_stream_name!r}."
                )
            _validate_scalar_reader(binding, reader)
            if hasattr(reader, "set_blocking"):
                reader.set_blocking(self.backpressure_mode == "blocking")
            bound_scalar_readers[binding.value_stream_name] = reader

        pose_reader = readers.get(self.pose_stream_name)
        if pose_reader is None:
            raise KeyError(f"ModelViewer {self.viewer_id!r} requires reader {self.pose_stream_name!r}.")
        _validate_pose_reader(self.pose_stream_name, pose_reader)
        if hasattr(pose_reader, "set_blocking"):
            pose_reader.set_blocking(self.backpressure_mode == "blocking")

        self._scalar_readers = bound_scalar_readers
        self._pose_reader = pose_reader
        self.reset_state()

    def reset_state(self) -> None:
        for runtime in self._body_runtime.values():
            runtime.reset()
        self._pose_runtime.reset()

    def reset_camera(self) -> None:
        self._camera_state.reset()

    def consume(self) -> bool:
        had_update = False

        if self._pose_reader is not None:
            for frame in _drain_reader(self._pose_reader):
                if self._pose_runtime.apply_batch(frame):
                    had_update = True

        for binding in self.body_bindings:
            reader = self._scalar_readers.get(binding.value_stream_name)
            if reader is None:
                continue
            runtime = self._body_runtime[binding.binding_id]
            for frame in _drain_reader(reader):
                if runtime.apply_batch(frame):
                    had_update = True

        return had_update

    def render(self) -> None:
        imgui = _require_imgui()
        imgui.text_unformatted(self.title)
        if imgui.button("Reset Camera", width=0.0, height=28.0):
            self.reset_camera()

        if self.show_legend and self.body_bindings:
            imgui.same_line()
            imgui.text_disabled(
                f"{len(self.body_bindings)} highlighted bodies | pose stream: {self.pose_stream_name}"
            )

        available = imgui.get_content_region_available()
        viewport_width = int(max(320.0, _component(available, 0, fallback=640.0)))
        available_height = _component(available, 1, fallback=_DEFAULT_VIEWPORT_HEIGHT)
        viewport_height = int(max(_DEFAULT_VIEWPORT_HEIGHT, available_height))
        texture_id = self._render_to_texture(viewport_width, viewport_height)
        imgui.image(
            texture_id,
            float(viewport_width),
            float(viewport_height),
            uv0=(0.0, 1.0),
            uv1=(1.0, 0.0),
        )
        self._apply_viewport_controls(imgui)

        if self.show_legend and self.body_bindings:
            self._render_legend(imgui)

    def build_dashboard_hooks(
        self,
        readers: Mapping[str, Any],
    ) -> tuple[Callable[[], bool], Callable[[], None]]:
        self.bind(readers)
        return self.consume, self.render

    def export(self, path: str | Path) -> Path:
        return self.config.export(path)

    @classmethod
    def reconstruct(cls, path: str | Path) -> "ModelViewer":
        return ModelViewer(ModelViewerConfig.reconstruct(path))

    def body_snapshot(self, binding_id: str) -> ModelBodySnapshot:
        runtime = self._body_runtime[binding_id]
        return runtime.snapshot()

    def pose_snapshot(self) -> ModelPoseSnapshot:
        snapshot = self._pose_runtime.snapshot
        return ModelPoseSnapshot(
            timestamp=snapshot.timestamp,
            position_xyz=tuple(snapshot.position_xyz),
            rotation_matrix=tuple(snapshot.rotation_matrix),
        )

    def mesh_part_color(self, mesh_part_name: str) -> ColorRGBA:
        binding_id = self._binding_by_mesh_part.get(mesh_part_name)
        if binding_id is None:
            return _with_alpha(self.default_body_color_rgba, self.other_body_alpha)
        return self._body_runtime[binding_id].color_rgba

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None

    def _render_to_texture(self, width: int, height: int) -> int:
        if self._renderer is None:
            self._renderer = _OpenGLMeshRenderer(
                self.mesh_cache_path,
                background_color_rgba=self.background_color_rgba,
            )
        part_colors = {
            part.mesh_part_name: self.mesh_part_color(part.mesh_part_name)
            for part in self._manifest.mesh_parts
        }
        return self._renderer.render(
            width=width,
            height=height,
            manifest=self._manifest,
            part_colors=part_colors,
            camera=self._camera_state,
            model_alignment_matrix=self.model_alignment_matrix,
            pose=self._pose_runtime.snapshot,
            show_axes=self.show_axes,
        )

    def _apply_viewport_controls(self, imgui: Any) -> None:
        hovered = bool(getattr(imgui, "is_item_hovered", lambda: False)())
        if not hovered:
            return
        io = imgui.get_io()
        mouse_delta = getattr(io, "mouse_delta", (0.0, 0.0))
        delta_x = _component(mouse_delta, 0, fallback=0.0)
        delta_y = _component(mouse_delta, 1, fallback=0.0)

        is_mouse_dragging = getattr(imgui, "is_mouse_dragging", None)
        if callable(is_mouse_dragging) and is_mouse_dragging(0):
            self._camera_state.azimuth_deg -= float(delta_x) * _CAMERA_ORBIT_SENSITIVITY
            self._camera_state.elevation_deg = max(
                -89.0,
                min(89.0, self._camera_state.elevation_deg - float(delta_y) * _CAMERA_ORBIT_SENSITIVITY),
            )

        if callable(is_mouse_dragging) and is_mouse_dragging(1):
            view, eye, up = _camera_view_matrix(
                bounds_radius=1.0,
                camera=self._camera_state,
                target=np.zeros(3, dtype=np.float64),
            )
            del view
            forward = _normalize_vector(-eye)
            right = _normalize_vector(np.cross(forward, up))
            scale = max(self._camera_state.distance, 1.0) * _CAMERA_PAN_SENSITIVITY
            self._camera_state.target_offset -= right * float(delta_x) * scale
            self._camera_state.target_offset += up * float(delta_y) * scale

        wheel = float(getattr(io, "mouse_wheel", 0.0))
        if wheel != 0.0:
            zoom_factor = math.exp(-wheel * _CAMERA_ZOOM_DAMPING)
            self._camera_state.distance = max(
                _MIN_CAMERA_DISTANCE,
                self._camera_state.distance * zoom_factor,
            )

    def _render_legend(self, imgui: Any) -> None:
        imgui.separator()
        pose = self.pose_snapshot()
        if pose.timestamp is None:
            imgui.text_disabled("Pose: no samples yet")
        else:
            px, py, pz = pose.position_xyz
            imgui.text_disabled(
                f"Pose t={pose.timestamp:.3f}s | pos=({px:.3f}, {py:.3f}, {pz:.3f})"
            )

        for binding in self.body_bindings:
            snapshot = self.body_snapshot(binding.binding_id)
            label = binding.binding_id if self.show_labels else binding.mesh_part_name
            if snapshot.value is None or snapshot.timestamp is None:
                suffix = "default"
            else:
                suffix = f"t={snapshot.timestamp:.3f}s | value={snapshot.value:.3f}"
            imgui.text_colored(
                f"{label} [{binding.mesh_part_name}] | {suffix}",
                *snapshot.color_rgba,
            )


def discover_single_obj_asset(cad_dir: str | Path | None = None) -> Path:
    return _discover_single_asset(cad_dir or _DEFAULT_CAD_DIR, suffixes=(".obj",))


def discover_single_step_asset(cad_dir: str | Path | None = None) -> Path:
    return _discover_single_asset(cad_dir or _DEFAULT_CAD_DIR, suffixes=(".step", ".stp"))


def compile_obj_to_cache(
    obj_path: str | Path,
    target: str | Path,
) -> tuple[Path, Path]:
    source = Path(obj_path)
    if source.suffix.lower() != ".obj":
        raise ValueError(f"Expected an OBJ asset, got {source.suffix!r}.")
    if not source.is_file():
        raise FileNotFoundError(source)

    cache_path, manifest_path = _resolve_direct_compile_targets(source, target)
    parts, vertices, normals, indices = _parse_obj_asset(source)
    with cache_path.open("wb") as handle:
        np.savez(handle, vertices=vertices, normals=normals, indices=indices)
    manifest_lines = toml_header(_MODEL_MESH_MANIFEST_KIND)
    source_sha = _sha256_file(source)
    manifest_lines.extend(
        [
            f"mesh_cache_version = {_MESH_CACHE_VERSION}",
            f"source_asset_kind = {toml_string('obj')}",
            f"source_asset_path = {toml_string(str(source.resolve()))}",
            f"source_sha256 = {toml_string(source_sha)}",
            f"cache_file = {toml_string(cache_path.name)}",
            "",
        ]
    )
    for part in parts:
        manifest_lines.extend(
            [
                "[[mesh_parts]]",
                f"part_id = {toml_string(part.part_id)}",
                f"mesh_part_name = {toml_string(part.mesh_part_name)}",
                f"vertex_start = {part.vertex_start}",
                f"vertex_count = {part.vertex_count}",
                f"index_start = {part.index_start}",
                f"index_count = {part.index_count}",
                "",
            ]
        )
    write_toml_document(manifest_path, "\n".join(manifest_lines).rstrip() + "\n")
    return cache_path, manifest_path


def compile_step_to_cache(_step_path: str | Path, _target: str | Path) -> tuple[Path, Path]:
    raise NotImplementedError("STEP compilation is not implemented. Convert the asset to OBJ first.")


def resolve_compiled_obj_assets(
    *,
    obj_path: str | Path,
    compiled_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    source = Path(obj_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    compiled_root = Path(compiled_dir or _DEFAULT_COMPILED_DIR)
    compiled_root.mkdir(parents=True, exist_ok=True)
    digest = _sha256_file(source)
    prefix = compiled_root / f"{source.stem}_{digest[:12]}"
    cache_path = prefix.with_suffix(".npz")
    manifest_path = prefix.with_suffix(".toml")
    if cache_path.is_file() and manifest_path.is_file():
        try:
            manifest = _load_mesh_manifest(manifest_path)
        except Exception:
            manifest = None
        if manifest is not None and manifest.source_sha256 == digest and manifest.source_asset_kind == "obj":
            return cache_path, manifest_path
    return compile_obj_to_cache(source, prefix)


def resolve_compiled_step_assets(
    *,
    step_path: str | Path,
    compiled_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    source = Path(step_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    compiled_root = Path(compiled_dir or _DEFAULT_COMPILED_DIR)
    compiled_root.mkdir(parents=True, exist_ok=True)
    digest = _sha256_file(source)
    prefix = compiled_root / f"{source.stem}_{digest[:12]}"
    cache_path = prefix.with_suffix(".npz")
    manifest_path = prefix.with_suffix(".toml")
    if cache_path.is_file() and manifest_path.is_file():
        manifest = _load_mesh_manifest(manifest_path)
        if manifest.source_sha256 == digest and manifest.source_asset_kind in ("step", "stp"):
            return cache_path, manifest_path
    raise NotImplementedError("STEP cache resolution is not implemented. Convert the asset to OBJ first.")


def build_pose_batch_from_matrices(
    *,
    timestamps: Sequence[float],
    positions_xyz: Sequence[Sequence[float]] | Sequence[float],
    rotation_matrices: Sequence[Sequence[float]] | np.ndarray,
) -> np.ndarray:
    timestamp_array = np.asarray(timestamps, dtype=np.float64).reshape(-1)
    if timestamp_array.size == 0:
        return np.empty((13, 0), dtype=np.float64)
    position_array = _coerce_pose_vectors(positions_xyz, rows=timestamp_array.size, field_name="positions_xyz")
    matrix_array = _coerce_rotation_matrix_rows(rotation_matrices, rows=timestamp_array.size)
    return np.vstack((timestamp_array.reshape(1, -1), position_array.T, matrix_array.reshape(-1, 9).T))


def build_pose_batch_from_direction_vectors(
    *,
    timestamps: Sequence[float],
    positions_xyz: Sequence[Sequence[float]] | Sequence[float],
    directions_xyz: Sequence[Sequence[float]] | Sequence[float],
    up_vectors_xyz: Sequence[Sequence[float]] | Sequence[float] | None = None,
) -> np.ndarray:
    timestamp_array = np.asarray(timestamps, dtype=np.float64).reshape(-1)
    if timestamp_array.size == 0:
        return np.empty((13, 0), dtype=np.float64)
    position_array = _coerce_pose_vectors(positions_xyz, rows=timestamp_array.size, field_name="positions_xyz")
    direction_array = _coerce_pose_vectors(
        directions_xyz,
        rows=timestamp_array.size,
        field_name="directions_xyz",
    )
    if up_vectors_xyz is None:
        up_array = np.repeat(np.asarray([[0.0, 1.0, 0.0]], dtype=np.float64), timestamp_array.size, axis=0)
    else:
        up_array = _coerce_pose_vectors(
            up_vectors_xyz,
            rows=timestamp_array.size,
            field_name="up_vectors_xyz",
        )
    matrices = np.asarray(
        [
            _rotation_matrix_from_direction_up(direction_array[index], up_array[index])
            for index in range(timestamp_array.size)
        ],
        dtype=np.float64,
    )
    return build_pose_batch_from_matrices(
        timestamps=timestamp_array,
        positions_xyz=position_array,
        rotation_matrices=matrices,
    )


def _resolve_legacy_binding_range(
    *,
    range_min: float | None,
    range_max: float | None,
    gradient_stops: Sequence[GradientStop | Mapping[str, Any]] | None,
) -> tuple[float, ColorRGBA, float, ColorRGBA]:
    if range_min is None or range_max is None or not gradient_stops:
        raise ValueError(
            "Provide low/high value-color endpoints or the legacy range_min/range_max/gradient_stops set."
        )
    parsed_stops = tuple(_coerce_gradient_stop(item) for item in gradient_stops)
    if len(parsed_stops) < 2:
        raise ValueError("gradient_stops must contain at least two stops.")
    ordered = tuple(sorted(parsed_stops, key=lambda item: item.position))
    return (
        float(range_min),
        ordered[0].color_rgba,
        float(range_max),
        ordered[-1].color_rgba,
    )


def _coerce_gradient_stop(value: GradientStop | Mapping[str, Any]) -> GradientStop:
    if isinstance(value, GradientStop):
        return value
    return GradientStop.from_dict(value)


def _coerce_body_binding(value: ModelBodyBinding | Mapping[str, Any]) -> ModelBodyBinding:
    if isinstance(value, ModelBodyBinding):
        return value
    return ModelBodyBinding.from_dict(value)


def _validate_body_bindings(body_bindings: Sequence[ModelBodyBinding]) -> None:
    binding_ids: set[str] = set()
    mesh_part_names: set[str] = set()
    stream_names: set[str] = set()
    for binding in body_bindings:
        if binding.binding_id in binding_ids:
            raise ValueError(f"Duplicate binding_id {binding.binding_id!r}.")
        if binding.mesh_part_name in mesh_part_names:
            raise ValueError(f"Duplicate mesh_part_name {binding.mesh_part_name!r}.")
        if binding.value_stream_name in stream_names:
            raise ValueError(f"Duplicate value_stream_name {binding.value_stream_name!r}.")
        binding_ids.add(binding.binding_id)
        mesh_part_names.add(binding.mesh_part_name)
        stream_names.add(binding.value_stream_name)


def _parse_alignment_matrix(value: Sequence[float] | None) -> tuple[float, ...]:
    if value is None:
        matrix = np.eye(3, dtype=np.float64)
    else:
        matrix = np.asarray(value, dtype=np.float64).reshape(-1)
        if matrix.size != 9:
            raise ValueError("model_alignment_matrix must contain exactly 9 floats.")
        matrix = matrix.reshape(3, 3)
    if not np.all(np.isfinite(matrix)):
        raise ValueError("model_alignment_matrix must contain only finite floats.")
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 1e-8:
        raise ValueError("model_alignment_matrix must be invertible.")
    return tuple(float(item) for item in matrix.reshape(9))


def _load_mesh_manifest(path: str | Path) -> MeshManifest:
    data = read_toml_document(path)
    require_kind(data, _MODEL_MESH_MANIFEST_KIND, _LEGACY_MESH_MANIFEST_KIND)
    require_keys(
        data,
        "model mesh manifest",
        "source_asset_kind",
        "source_asset_path",
        "source_sha256",
        "cache_file",
        "mesh_parts",
    )
    manifest_path = Path(path)
    mesh_cache_path = manifest_path.parent / str(data["cache_file"])
    return MeshManifest(
        manifest_path=manifest_path,
        mesh_cache_path=mesh_cache_path,
        source_asset_kind=str(data["source_asset_kind"]),
        source_asset_path=Path(str(data["source_asset_path"])),
        source_sha256=str(data["source_sha256"]),
        mesh_parts=tuple(MeshPartRecord.from_dict(item) for item in data["mesh_parts"]),
    )


def _validate_config_paths(config: ModelViewerConfig, manifest: MeshManifest) -> None:
    configured_cache = Path(config.mesh_cache_path).resolve()
    configured_manifest = Path(config.manifest_path).resolve()
    if configured_manifest != manifest.manifest_path.resolve():
        raise ValueError(
            f"manifest_path {configured_manifest!s} does not match loaded manifest {manifest.manifest_path!s}."
        )
    if configured_cache != manifest.mesh_cache_path.resolve():
        raise ValueError(
            f"mesh_cache_path {configured_cache!s} does not match manifest cache {manifest.mesh_cache_path!s}."
        )


def _validate_bindings_against_manifest(
    body_bindings: Sequence[ModelBodyBinding],
    manifest: MeshManifest,
) -> None:
    mesh_part_names = manifest.mesh_parts_by_name
    for binding in body_bindings:
        if binding.mesh_part_name not in mesh_part_names:
            raise ValueError(
                f"mesh_part_name {binding.mesh_part_name!r} does not exist in {manifest.manifest_path!s}."
            )


def _validate_scalar_reader(binding: ModelBodyBinding, reader: Any) -> None:
    shape = getattr(reader, "shape", None)
    dtype = getattr(reader, "dtype", None)
    if shape is None or dtype is None:
        raise TypeError(
            f"Reader {binding.value_stream_name!r} must expose shape and dtype attributes."
        )
    shape_tuple = tuple(shape)
    if len(shape_tuple) != 2 or shape_tuple[0] != 2 or shape_tuple[1] < 1:
        raise ValueError(
            f"Reader {binding.value_stream_name!r} must have shape (2, rows), got {shape_tuple}."
        )
    numpy_dtype = np.dtype(dtype)
    if numpy_dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise ValueError(
            f"Reader {binding.value_stream_name!r} must use float32 or float64, got {numpy_dtype}."
        )


def _validate_pose_reader(stream_name: str, reader: Any) -> None:
    shape = getattr(reader, "shape", None)
    dtype = getattr(reader, "dtype", None)
    if shape is None or dtype is None:
        raise TypeError(f"Reader {stream_name!r} must expose shape and dtype attributes.")
    shape_tuple = tuple(shape)
    if len(shape_tuple) != 2 or shape_tuple[0] not in (10, 13) or shape_tuple[1] < 1:
        raise ValueError(
            f"Reader {stream_name!r} must have shape (10, rows) or (13, rows), got {shape_tuple}."
        )
    numpy_dtype = np.dtype(dtype)
    if numpy_dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise ValueError(f"Reader {stream_name!r} must use float32 or float64, got {numpy_dtype}.")


def _drain_reader(reader: Any) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    if hasattr(reader, "read"):
        while True:
            frame = reader.read()
            if frame is None:
                return frames
            frames.append(np.asarray(frame))
    if not hasattr(reader, "look") or not hasattr(reader, "increment"):
        raise TypeError("Reader must expose read() or look()/increment().")
    while True:
        view = reader.look()
        if view is None:
            return frames
        frame = np.frombuffer(view, dtype=np.dtype(reader.dtype)).reshape(tuple(reader.shape)).copy()
        del view
        reader.increment()
        frames.append(frame)


def _normalize_scalar_batch(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    batch = np.asarray(frame)
    if batch.ndim != 2 or batch.shape[0] != 2:
        raise ValueError(f"Expected scalar batch shape (2, rows), got {batch.shape}.")
    if batch.dtype not in (np.float32, np.float64):
        raise ValueError(f"Expected float32 or float64 scalar batch, got {batch.dtype}.")
    timestamps = np.asarray(batch[0], dtype=np.float64)
    values = np.asarray(batch[1], dtype=np.float64)
    finite_mask = np.isfinite(timestamps) & np.isfinite(values)
    timestamps = timestamps[finite_mask]
    values = values[finite_mask]
    if timestamps.size == 0:
        return timestamps, values
    if timestamps.size > 1 and np.any(np.diff(timestamps) < 0.0):
        order = np.argsort(timestamps, kind="stable")
        timestamps = timestamps[order]
        values = values[order]
    return timestamps, values


def _normalize_pose_batch(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    batch = np.asarray(frame)
    if batch.ndim != 2 or batch.shape[0] not in (10, 13):
        raise ValueError(f"Expected pose batch shape (10, rows) or (13, rows), got {batch.shape}.")
    if batch.dtype not in (np.float32, np.float64):
        raise ValueError(f"Expected float32 or float64 pose batch, got {batch.dtype}.")

    timestamps = np.asarray(batch[0], dtype=np.float64)
    if batch.shape[0] == 13:
        positions = np.asarray(batch[1:4], dtype=np.float64).T
        matrices_flat = np.asarray(batch[4:13], dtype=np.float64).T
    else:
        positions = np.zeros((batch.shape[1], 3), dtype=np.float64)
        matrices_flat = np.asarray(batch[1:10], dtype=np.float64).T

    normalized_rows: list[tuple[float, np.ndarray, np.ndarray]] = []
    for index, timestamp in enumerate(timestamps):
        if not math.isfinite(float(timestamp)):
            continue
        position = positions[index]
        matrix = matrices_flat[index].reshape(3, 3)
        if not np.all(np.isfinite(position)):
            continue
        normalized_matrix = _normalize_rotation_matrix(matrix)
        if normalized_matrix is None:
            continue
        normalized_rows.append((float(timestamp), position.astype(np.float64), normalized_matrix))

    if not normalized_rows:
        return (
            np.empty(0, dtype=np.float64),
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 3, 3), dtype=np.float64),
        )

    normalized_rows.sort(key=lambda item: item[0])
    return (
        np.asarray([item[0] for item in normalized_rows], dtype=np.float64),
        np.asarray([item[1] for item in normalized_rows], dtype=np.float64),
        np.asarray([item[2] for item in normalized_rows], dtype=np.float64),
    )


def _normalize_rotation_matrix(matrix: np.ndarray) -> np.ndarray | None:
    candidate = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
    if not np.all(np.isfinite(candidate)):
        return None
    row_norms = np.linalg.norm(candidate, axis=1)
    column_norms = np.linalg.norm(candidate, axis=0)
    if np.any(row_norms < 1e-8) or np.any(column_norms < 1e-8):
        return None
    try:
        u, singular_values, vh = np.linalg.svd(candidate)
    except np.linalg.LinAlgError:
        return None
    if singular_values[-1] < 1e-8 or singular_values[0] / singular_values[-1] > 50.0:
        return None
    rotation = u @ vh
    if np.linalg.det(rotation) < 0.0:
        u[:, -1] *= -1.0
        rotation = u @ vh
    residual = np.linalg.norm(candidate - rotation, ord="fro") / max(
        np.linalg.norm(candidate, ord="fro"),
        1e-9,
    )
    if residual > 0.35:
        return None
    return rotation.astype(np.float64)


def _lerp_color(low: ColorRGBA, high: ColorRGBA, ratio: float) -> ColorRGBA:
    t = min(max(float(ratio), 0.0), 1.0)
    return tuple(
        float(low[index] + (high[index] - low[index]) * t) for index in range(4)
    )  # type: ignore[return-value]


def _with_alpha(color: ColorRGBA, alpha: float) -> ColorRGBA:
    return (float(color[0]), float(color[1]), float(color[2]), float(alpha))


def _discover_single_asset(directory: str | Path, *, suffixes: Sequence[str]) -> Path:
    path = Path(directory)
    if not path.is_dir():
        raise FileNotFoundError(path)
    assets = sorted(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file() and candidate.suffix.lower() in tuple(item.lower() for item in suffixes)
    )
    if len(assets) != 1:
        joined = ", ".join(item.name for item in assets) or "<none>"
        raise ValueError(
            f"Expected exactly one asset in {path!s} with suffixes {tuple(suffixes)!r}, found {len(assets)}: {joined}."
        )
    return assets[0]


def _resolve_direct_compile_targets(source: Path, target: str | Path) -> tuple[Path, Path]:
    target_path = Path(target)
    if target_path.exists() and target_path.is_dir():
        target_path.mkdir(parents=True, exist_ok=True)
        base_prefix = target_path / source.stem
    elif target_path.name == "":
        target_path.mkdir(parents=True, exist_ok=True)
        base_prefix = target_path / source.stem
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        base_prefix = target_path
    cache_path = base_prefix.with_suffix(".npz")
    manifest_path = base_prefix.with_suffix(".toml")
    return cache_path, manifest_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_obj_asset(path: Path) -> tuple[list[MeshPartRecord], np.ndarray, np.ndarray, np.ndarray]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    part_vertices: dict[str, list[tuple[float, float, float]]] = {}
    part_normals: dict[str, list[tuple[float, float, float]]] = {}
    first_seen_order: list[str] = []
    current_group = "default"
    current_object: str | None = None

    def current_part_name() -> str:
        if current_group != "default":
            return current_group
        if current_object is not None:
            return current_object
        return "default"

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            if not tokens:
                continue
            kind = tokens[0]
            if kind == "v" and len(tokens) >= 4:
                positions.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
                continue
            if kind == "vn" and len(tokens) >= 4:
                normals.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
                continue
            if kind == "g":
                current_group = _normalize_mesh_name("g", tokens[1:] or ("default",))
                continue
            if kind == "o":
                current_object = _normalize_mesh_name("o", tokens[1:] or ("default",))
                continue
            if kind != "f" or len(tokens) < 4:
                continue

            part_name = current_part_name()
            vertex_bucket = part_vertices.setdefault(part_name, [])
            normal_bucket = part_normals.setdefault(part_name, [])
            if part_name not in first_seen_order:
                first_seen_order.append(part_name)

            face_vertices = [_parse_obj_face_vertex(item) for item in tokens[1:]]
            for tri_indices in range(1, len(face_vertices) - 1):
                triangle = (face_vertices[0], face_vertices[tri_indices], face_vertices[tri_indices + 1])
                triangle_positions = [
                    np.asarray(positions[_resolve_obj_index(vertex.position_index, len(positions))], dtype=np.float64)
                    for vertex in triangle
                ]
                triangle_normals = _triangle_normals(
                    triangle_positions,
                    triangle,
                    normals,
                )
                for position, normal in zip(triangle_positions, triangle_normals):
                    vertex_bucket.append(tuple(float(item) for item in position))
                    normal_bucket.append(tuple(float(item) for item in normal))

    ordered_vertices: list[np.ndarray] = []
    ordered_normals: list[np.ndarray] = []
    ordered_parts: list[MeshPartRecord] = []
    vertex_cursor = 0
    for part_name in first_seen_order:
        part_vertex_array = np.asarray(part_vertices[part_name], dtype=np.float32).reshape(-1, 3)
        part_normal_array = np.asarray(part_normals[part_name], dtype=np.float32).reshape(-1, 3)
        if part_vertex_array.shape != part_normal_array.shape:
            raise ValueError(f"Vertex and normal arrays do not match for part {part_name!r}.")
        ordered_vertices.append(part_vertex_array)
        ordered_normals.append(part_normal_array)
        count = int(part_vertex_array.shape[0])
        ordered_parts.append(
            MeshPartRecord(
                part_id=part_name,
                mesh_part_name=part_name,
                vertex_start=vertex_cursor,
                vertex_count=count,
                index_start=vertex_cursor,
                index_count=count,
            )
        )
        vertex_cursor += count

    if not ordered_vertices:
        raise ValueError(f"No triangulated geometry was discovered in {path!s}.")

    vertices_array = np.vstack(ordered_vertices).astype(np.float32, copy=False)
    normals_array = np.vstack(ordered_normals).astype(np.float32, copy=False)
    indices_array = np.arange(vertices_array.shape[0], dtype=np.uint32)
    return ordered_parts, vertices_array, normals_array, indices_array


@dataclass(frozen=True, slots=True)
class _ObjFaceVertex:
    position_index: int
    normal_index: int | None


def _parse_obj_face_vertex(token: str) -> _ObjFaceVertex:
    parts = token.split("/")
    if not parts[0]:
        raise ValueError(f"OBJ face token {token!r} is missing a position index.")
    position_index = int(parts[0])
    normal_index: int | None = None
    if len(parts) >= 3 and parts[2]:
        normal_index = int(parts[2])
    elif len(parts) == 2 and parts[1]:
        normal_index = None
    return _ObjFaceVertex(position_index=position_index, normal_index=normal_index)


def _resolve_obj_index(index: int, total: int) -> int:
    resolved = index - 1 if index > 0 else total + index
    if resolved < 0 or resolved >= total:
        raise IndexError(f"OBJ index {index} is out of range for total {total}.")
    return resolved


def _triangle_normals(
    triangle_positions: Sequence[np.ndarray],
    triangle: Sequence[_ObjFaceVertex],
    normals: Sequence[tuple[float, float, float]],
) -> list[np.ndarray]:
    resolved: list[np.ndarray] = []
    for vertex in triangle:
        if vertex.normal_index is None:
            break
        normal = np.asarray(normals[_resolve_obj_index(vertex.normal_index, len(normals))], dtype=np.float64)
        resolved.append(_normalize_vector(normal))
    if len(resolved) == 3:
        return resolved

    edge_a = triangle_positions[1] - triangle_positions[0]
    edge_b = triangle_positions[2] - triangle_positions[0]
    face_normal = _normalize_vector(np.cross(edge_a, edge_b))
    return [face_normal, face_normal, face_normal]


def _normalize_mesh_name(prefix: str, parts: Sequence[str]) -> str:
    tail = "_".join(str(item).strip() for item in parts if str(item).strip())
    return f"{prefix}_{tail or 'default'}"


def _load_mesh_cache(path: Path) -> _MeshCacheData:
    data = np.load(path)
    required_keys = {"vertices", "normals", "indices"}
    missing = required_keys.difference(set(data.files))
    if missing:
        joined = ", ".join(sorted(missing))
        raise ValueError(f"Mesh cache {path!s} is missing keys: {joined}.")
    vertices = np.asarray(data["vertices"], dtype=np.float32).reshape(-1, 3)
    normals = np.asarray(data["normals"], dtype=np.float32).reshape(-1, 3)
    indices = np.asarray(data["indices"], dtype=np.uint32).reshape(-1)
    if vertices.shape != normals.shape:
        raise ValueError("Mesh cache vertices and normals must have the same shape.")
    if indices.size == 0 or vertices.shape[0] == 0:
        raise ValueError("Mesh cache must contain at least one triangle.")
    bounds_min = np.min(vertices, axis=0).astype(np.float64)
    bounds_max = np.max(vertices, axis=0).astype(np.float64)
    center = 0.5 * (bounds_min + bounds_max)
    radius = float(max(np.linalg.norm(bounds_max - bounds_min) * 0.5, 1.0))
    return _MeshCacheData(
        vertices=vertices,
        normals=normals,
        indices=indices,
        bounds_center=center,
        bounds_radius=radius,
    )


def _build_shader_program(gl: Any) -> int:
    vertex_shader = gl.glCreateShader(gl.GL_VERTEX_SHADER)
    gl.glShaderSource(
        vertex_shader,
        """
        #version 150 core
        in vec3 a_position;
        in vec3 a_normal;
        uniform mat4 u_mvp;
        uniform mat4 u_model;
        out vec3 v_normal;
        void main() {
            gl_Position = u_mvp * vec4(a_position, 1.0);
            v_normal = mat3(u_model) * a_normal;
        }
        """,
    )
    gl.glCompileShader(vertex_shader)
    _require_shader_status(gl, vertex_shader, "vertex")

    fragment_shader = gl.glCreateShader(gl.GL_FRAGMENT_SHADER)
    gl.glShaderSource(
        fragment_shader,
        """
        #version 150 core
        uniform vec4 u_color;
        uniform vec3 u_light_dir;
        in vec3 v_normal;
        out vec4 frag_color;
        void main() {
            vec3 normal = normalize(v_normal);
            float diffuse = max(dot(normal, normalize(u_light_dir)), 0.0);
            float lighting = 0.28 + (0.72 * diffuse);
            frag_color = vec4(u_color.rgb * lighting, u_color.a);
        }
        """,
    )
    gl.glCompileShader(fragment_shader)
    _require_shader_status(gl, fragment_shader, "fragment")

    program = gl.glCreateProgram()
    gl.glAttachShader(program, vertex_shader)
    gl.glAttachShader(program, fragment_shader)
    gl.glBindAttribLocation(program, 0, "a_position")
    gl.glBindAttribLocation(program, 1, "a_normal")
    gl.glLinkProgram(program)
    linked = gl.glGetProgramiv(program, gl.GL_LINK_STATUS)
    if not linked:
        message = gl.glGetProgramInfoLog(program).decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenGL shader program failed to link: {message}")
    gl.glDeleteShader(vertex_shader)
    gl.glDeleteShader(fragment_shader)
    return int(program)


def _require_shader_status(gl: Any, shader: int, label: str) -> None:
    compiled = gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS)
    if compiled:
        return
    message = gl.glGetShaderInfoLog(shader).decode("utf-8", errors="replace")
    raise RuntimeError(f"OpenGL {label} shader failed to compile: {message}")


def _create_framebuffer(gl: Any, width: int, height: int) -> tuple[int, int, int]:
    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)

    color_texture = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, color_texture)
    gl.glTexImage2D(
        gl.GL_TEXTURE_2D,
        0,
        gl.GL_RGBA,
        width,
        height,
        0,
        gl.GL_RGBA,
        gl.GL_UNSIGNED_BYTE,
        None,
    )
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
    gl.glFramebufferTexture2D(
        gl.GL_FRAMEBUFFER,
        gl.GL_COLOR_ATTACHMENT0,
        gl.GL_TEXTURE_2D,
        color_texture,
        0,
    )

    depth_rbo = gl.glGenRenderbuffers(1)
    gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, depth_rbo)
    gl.glRenderbufferStorage(gl.GL_RENDERBUFFER, gl.GL_DEPTH_COMPONENT24, width, height)
    gl.glFramebufferRenderbuffer(
        gl.GL_FRAMEBUFFER,
        gl.GL_DEPTH_ATTACHMENT,
        gl.GL_RENDERBUFFER,
        depth_rbo,
    )

    status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    if status != gl.GL_FRAMEBUFFER_COMPLETE:
        raise RuntimeError(f"OpenGL framebuffer is incomplete (status={status}).")
    return int(fbo), int(color_texture), int(depth_rbo)


def _perspective_matrix(
    *,
    fov_y_degrees: float,
    aspect_ratio: float,
    near: float,
    far: float,
) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_y_degrees) * 0.5)
    matrix = np.zeros((4, 4), dtype=np.float64)
    matrix[0, 0] = f / max(aspect_ratio, 1e-6)
    matrix[1, 1] = f
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = (2.0 * far * near) / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def _fit_camera_distance(
    *,
    bounds_radius: float,
    fov_y_degrees: float,
    aspect_ratio: float,
    margin: float,
) -> float:
    radius = max(float(bounds_radius), 1e-6)
    vertical_half_angle = math.radians(fov_y_degrees) * 0.5
    horizontal_half_angle = math.atan(math.tan(vertical_half_angle) * max(float(aspect_ratio), 1e-6))
    limiting_half_angle = max(min(vertical_half_angle, horizontal_half_angle), math.radians(5.0))
    fit_distance = radius / math.sin(limiting_half_angle)
    return max(fit_distance * float(margin), _MIN_CAMERA_DISTANCE)


def _ensure_camera_fit(camera: _CameraState, fit_distance: float) -> None:
    epsilon = 1e-6
    if camera.base_distance + epsilon >= fit_distance:
        return
    if camera.distance <= camera.base_distance + epsilon:
        camera.distance = fit_distance
    camera.base_distance = fit_distance


def _camera_view_matrix(
    bounds_radius: float,
    camera: _CameraState,
    target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    del bounds_radius
    distance = max(camera.distance, _MIN_CAMERA_DISTANCE)
    azimuth = math.radians(camera.azimuth_deg)
    elevation = math.radians(camera.elevation_deg)
    eye = target + distance * np.asarray(
        [
            math.cos(elevation) * math.cos(azimuth),
            math.sin(elevation),
            math.cos(elevation) * math.sin(azimuth),
        ],
        dtype=np.float64,
    )
    up = np.asarray([0.0, 1.0, 0.0], dtype=np.float64)
    return _look_at_matrix(eye, target, up), eye, up


def _look_at_matrix(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    forward = _normalize_vector(target - eye)
    right = _normalize_vector(np.cross(forward, up))
    corrected_up = np.cross(right, forward)
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, :3] = right
    matrix[1, :3] = corrected_up
    matrix[2, :3] = -forward
    matrix[0, 3] = -float(np.dot(right, eye))
    matrix[1, 3] = -float(np.dot(corrected_up, eye))
    matrix[2, 3] = float(np.dot(forward, eye))
    return matrix


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(arr))
    if norm < 1e-12:
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    return arr / norm


def _coerce_pose_vectors(
    values: Sequence[Sequence[float]] | Sequence[float],
    *,
    rows: int,
    field_name: str,
) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        if array.size != 3:
            raise ValueError(f"{field_name} must contain 3 floats or an array shaped (rows, 3).")
        return np.repeat(array.reshape(1, 3), rows, axis=0)
    if array.shape != (rows, 3):
        raise ValueError(f"{field_name} must have shape ({rows}, 3), got {array.shape}.")
    return array


def _coerce_rotation_matrix_rows(values: Sequence[Sequence[float]] | np.ndarray, *, rows: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 2 and array.shape == (3, 3):
        return np.repeat(array.reshape(1, 3, 3), rows, axis=0)
    if array.ndim == 2 and array.shape == (rows, 9):
        return array.reshape(rows, 3, 3)
    if array.shape != (rows, 3, 3):
        raise ValueError(
            f"rotation_matrices must have shape ({rows}, 3, 3) or ({rows}, 9), got {array.shape}."
        )
    return array


def _rotation_matrix_from_direction_up(direction: np.ndarray, up: np.ndarray) -> np.ndarray:
    forward = _normalize_vector(direction)
    upward = _normalize_vector(up)
    if abs(float(np.dot(forward, upward))) > 0.995:
        upward = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    right = _normalize_vector(np.cross(upward, forward))
    corrected_up = _normalize_vector(np.cross(forward, right))
    return np.column_stack((right, corrected_up, forward)).astype(np.float64)


def _component(value: Any, index: int, *, fallback: float) -> float:
    if isinstance(value, tuple):
        return float(value[index])
    if isinstance(value, list):
        return float(value[index])
    attr = "x" if index == 0 else "y"
    if hasattr(value, attr):
        return float(getattr(value, attr))
    return float(fallback)


def _require_imgui() -> Any:
    try:
        import imgui
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "imgui is required for ModelViewer rendering. Install a Dear ImGui binding."
        ) from exc
    return imgui


RocketPartBinding = ModelBodyBinding
RocketPartSnapshot = ModelBodySnapshot
RocketViewerConfig = ModelViewerConfig
RocketViewer = ModelViewer

__all__ = [
    "GradientStop",
    "ModelBodyBinding",
    "ModelBodySnapshot",
    "ModelPoseSnapshot",
    "ModelViewer",
    "ModelViewerConfig",
    "RocketPartBinding",
    "RocketPartSnapshot",
    "RocketViewer",
    "RocketViewerConfig",
    "build_pose_batch_from_direction_vectors",
    "build_pose_batch_from_matrices",
    "compile_obj_to_cache",
    "compile_step_to_cache",
    "discover_single_obj_asset",
    "discover_single_step_asset",
    "resolve_compiled_obj_assets",
    "resolve_compiled_step_assets",
]
