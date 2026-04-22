from __future__ import annotations

__all__ = [
    "ECUDeviceInterface",
    "EXTRECUDeviceInterface",
    "GSEDeviceInterface",
    "LoadCellDeviceInterface",
    "build_ucirplgui_pipeline",
    "run_device_interface",
    "run_frontend",
]


def __getattr__(name: str):
    if name == "build_ucirplgui_pipeline":
        from .backend import build_ucirplgui_pipeline
        return build_ucirplgui_pipeline
    if name == "run_frontend":
        from .frontend.frontend import run_frontend
        return run_frontend
    if name in {
        "ECUDeviceInterface",
        "EXTRECUDeviceInterface",
        "GSEDeviceInterface",
        "LoadCellDeviceInterface",
        "run_device_interface",
    }:
        from .device_interfaces import (
            ECUDeviceInterface,
            EXTRECUDeviceInterface,
            GSEDeviceInterface,
            LoadCellDeviceInterface,
            run_device_interface,
        )
        mapping = {
            "ECUDeviceInterface": ECUDeviceInterface,
            "EXTRECUDeviceInterface": EXTRECUDeviceInterface,
            "GSEDeviceInterface": GSEDeviceInterface,
            "LoadCellDeviceInterface": LoadCellDeviceInterface,
            "run_device_interface": run_device_interface,
        }
        return mapping[name]
    raise AttributeError(f"module 'ucirplgui' has no attribute {name!r}")
