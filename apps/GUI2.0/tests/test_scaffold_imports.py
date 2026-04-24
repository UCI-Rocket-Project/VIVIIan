from __future__ import annotations

import unittest

from ucirplgui import (
    ECUDeviceInterface,
    EXTRECUDeviceInterface,
    GSEDeviceInterface,
    LoadCellDeviceInterface,
    run_device_interface,
    run_frontend,
)
from ucirplgui.backend import main as backend_main


class ScaffoldImportTests(unittest.TestCase):
    def test_top_level_exports_resolve_live_runtime_symbols(self) -> None:
        self.assertTrue(callable(run_frontend))
        self.assertTrue(callable(run_device_interface))
        self.assertEqual(ECUDeviceInterface.__name__, "ECUDeviceInterface")
        self.assertEqual(EXTRECUDeviceInterface.__name__, "EXTRECUDeviceInterface")
        self.assertEqual(GSEDeviceInterface.__name__, "GSEDeviceInterface")
        self.assertEqual(LoadCellDeviceInterface.__name__, "LoadCellDeviceInterface")

    def test_backend_main_is_importable(self) -> None:
        self.assertTrue(callable(backend_main))


if __name__ == "__main__":
    unittest.main()
