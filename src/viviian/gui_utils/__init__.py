from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import sys

from . import theme
from .buttons import ButtonStateUpdate, MomentaryButton, SetpointButton, StateButton, ToggleButton
from .gauges import AnalogNeedleGauge, LedBarGauge, SensorGauge, reconstruct_gauge
from .graphs import GraphSeries, SensorGraph
from .operator import (
    ConsoleComponent,
    EventLogPanel,
    EventRecord,
    KeyValuePanel,
    KeyValueRow,
    MicroButton,
    OperatorToolbar,
    ProcedureCarousel,
    ProcedureStep,
    ReadoutCard,
    Subbar,
    TelemetryCard,
    TelemetryFilmstrip,
    TelemetryTicker,
    ToolbarButton,
    ToolbarMeter,
    ToolbarSearch,
)

_model3d = None
_model3d_name = f"{__name__}._3dmodel"
_source_path = Path(__file__).with_name("3dmodel.py")
if _source_path.exists():
    _spec = importlib.util.spec_from_file_location(_model3d_name, _source_path)
else:
    _cache_dir = Path(__file__).with_name("__pycache__")
    _bytecode = sorted(_cache_dir.glob("3dmodel*.pyc"))
    if _bytecode:
        _loader = importlib.machinery.SourcelessFileLoader(_model3d_name, str(_bytecode[0]))
        _spec = importlib.util.spec_from_loader(_model3d_name, _loader)
    else:
        _spec = None

if _spec is not None and _spec.loader is not None:
    _model3d = importlib.util.module_from_spec(_spec)
    sys.modules[_model3d_name] = _model3d
    _spec.loader.exec_module(_model3d)

if _model3d is not None:
    GradientStop = _model3d.GradientStop
    ModelBodyBinding = _model3d.ModelBodyBinding
    ModelBodySnapshot = _model3d.ModelBodySnapshot
    ModelPoseSnapshot = _model3d.ModelPoseSnapshot
    ModelViewer = _model3d.ModelViewer
    ModelViewerConfig = _model3d.ModelViewerConfig
    RocketPartBinding = _model3d.RocketPartBinding
    RocketPartSnapshot = _model3d.RocketPartSnapshot
    RocketViewer = _model3d.RocketViewer
    RocketViewerConfig = _model3d.RocketViewerConfig
    build_pose_batch_from_direction_vectors = _model3d.build_pose_batch_from_direction_vectors
    build_pose_batch_from_matrices = _model3d.build_pose_batch_from_matrices
    compile_obj_to_cache = _model3d.compile_obj_to_cache
    compile_step_to_cache = _model3d.compile_step_to_cache
    discover_single_obj_asset = _model3d.discover_single_obj_asset
    discover_single_step_asset = _model3d.discover_single_step_asset
    resolve_compiled_obj_assets = _model3d.resolve_compiled_obj_assets
    resolve_compiled_step_assets = _model3d.resolve_compiled_step_assets

__all__ = [
    "ButtonStateUpdate",
    "MomentaryButton",
    "SetpointButton",
    "StateButton",
    "ToggleButton",
    "AnalogNeedleGauge",
    "GraphSeries",
    "LedBarGauge",
    "SensorGraph",
    "SensorGauge",
    "reconstruct_gauge",
    "ConsoleComponent",
    "EventLogPanel",
    "EventRecord",
    "KeyValuePanel",
    "KeyValueRow",
    "MicroButton",
    "OperatorToolbar",
    "ProcedureCarousel",
    "ProcedureStep",
    "ReadoutCard",
    "Subbar",
    "TelemetryCard",
    "TelemetryFilmstrip",
    "TelemetryTicker",
    "ToolbarButton",
    "ToolbarMeter",
    "ToolbarSearch",
    "theme",
]

if _model3d is not None:
    __all__.extend(
        [
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
    )
