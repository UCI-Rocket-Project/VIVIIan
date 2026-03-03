"""Run with: python -m unittest src.data_handeling.workers.testing.test_worker_basic -v"""

import unittest

try:
    from src.data_handeling.workers import AbstractWorker, CallType
except ModuleNotFoundError:  # pragma: no cover - import/path dependency
    AbstractWorker = None
    CallType = None


@unittest.skipIf(
    AbstractWorker is None or CallType is None,
    "worker package dependencies are required",
)
class AbstractWorkerBasicTests(unittest.TestCase):
    @staticmethod
    def _identity(data):
        return data

    def test_imports(self):
        self.assertIsNotNone(AbstractWorker)
        self.assertIsNotNone(CallType)

    def test_requires_proc_func(self):
        with self.assertRaises(ValueError):
            AbstractWorker(
                proc_func=None,
                call_type=CallType.TIME,
                call_value=1.0,
            )

    def test_requires_call_type(self):
        with self.assertRaises(ValueError):
            AbstractWorker(
                proc_func=self._identity,
                call_type=None,
                call_value=1.0,
            )

    def test_time_call_type_requires_call_value(self):
        with self.assertRaises(ValueError):
            AbstractWorker(
                proc_func=self._identity,
                call_type=CallType.TIME,
                call_value=None,
            )

    def test_data_call_type_requires_call_value(self):
        with self.assertRaises(ValueError):
            AbstractWorker(
                proc_func=self._identity,
                call_type=CallType.DATA,
                call_value=None,
            )

    def test_onetime_allows_missing_call_value(self):
        worker = AbstractWorker(
            proc_func=self._identity,
            call_type=CallType.ONETIME,
            call_value=None,
        )
        self.assertIsNone(worker.call_value)
        self.assertEqual(worker.call_type, CallType.ONETIME)

    def test_valid_time_configuration(self):
        worker = AbstractWorker(
            proc_func=self._identity,
            call_type=CallType.TIME,
            call_value=0.01,
        )
        self.assertEqual(worker.call_value, 0.01)
        self.assertEqual(worker.call_type, CallType.TIME)


if __name__ == "__main__":
    unittest.main()
