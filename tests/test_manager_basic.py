"""Run with: python -m unittest src.data_handeling.manager.testing.test_manager_basic -v"""

import unittest

try:
    from src.data_handeling.manager import manager as manager_module
except ModuleNotFoundError:  # pragma: no cover - import/path dependency
    manager_module = None


@unittest.skipIf(
    manager_module is None,
    "manager package dependencies are required",
)
class ManagerBasicTests(unittest.TestCase):
    def test_manager_module_imports(self):
        self.assertIsNotNone(manager_module)

    def test_exports_expected_symbols(self):
        self.assertTrue(hasattr(manager_module, "SharedRingBuffer"))
        self.assertTrue(hasattr(manager_module, "AbstractWorker"))

    def test___all___contains_expected_symbols(self):
        self.assertTrue(hasattr(manager_module, "__all__"))
        self.assertIn("SharedRingBuffer", manager_module.__all__)
        self.assertIn("AbstractWorker", manager_module.__all__)

    def test_exported_symbols_are_classes(self):
        self.assertTrue(isinstance(manager_module.SharedRingBuffer, type))
        self.assertTrue(isinstance(manager_module.AbstractWorker, type))


if __name__ == "__main__":
    unittest.main()
