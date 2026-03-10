"""Run with: python -m unittest tests.test_manager_basic -v"""

import unittest
from unittest.mock import MagicMock, patch

try:
    from viviian.ipc.manager import Manager, ProcessMetrics
    from viviian.ipc.ring_buffer import RingSpec
    from viviian.ipc.worker import TaskSpec
except ModuleNotFoundError:  # pragma: no cover - environment dependency
    Manager = None
    ProcessMetrics = None
    RingSpec = None
    TaskSpec = None


@unittest.skipIf(Manager is None, "manager dependencies are required")
class ManagerBasicTests(unittest.TestCase):
    def test_manager_module_exports_expected_symbols(self):
        from viviian.ipc import manager as manager_module

        self.assertTrue(hasattr(manager_module, "__all__"))
        self.assertIn("Manager", manager_module.__all__)
        self.assertIn("SharedRingBuffer", manager_module.__all__)
        self.assertIn("Worker", manager_module.__all__)
        self.assertIn("TaskSpec", manager_module.__all__)
        self.assertIn("EventSpec", manager_module.__all__)

    def test_create_ring_registers_spec_live_ring_and_counter(self):
        mgr = Manager()

        try:
            spec = RingSpec(name="rb", size=32, num_readers=2)
            returned = mgr.create_ring(spec)

            self.assertIs(returned, mgr)
            self.assertIs(mgr._ring_specs["rb"], spec)
            self.assertIn("rb", mgr._rings)
            self.assertEqual(mgr._ring_reader_counters["rb"], 0)
        finally:
            mgr.close()

    def test_collect_ring_pressures_ignores_failures(self):
        mgr = Manager()
        good_ring = MagicMock()
        good_ring.calculate_pressure.return_value = 73
        bad_ring = MagicMock()
        bad_ring.calculate_pressure.side_effect = RuntimeError("boom")

        pressures = mgr._collect_ring_pressures({"good": good_ring, "bad": bad_ring})

        self.assertEqual(pressures, {"good": 73})

    def test_sample_process_stores_latest_metrics_snapshot(self):
        mgr = Manager()
        mgr.create_task(
            TaskSpec(
                name="task",
                fn=lambda: None,
                reading_rings=("input",),
                writing_rings=("output",),
            )
        )
        proc = MagicMock()
        proc.pid = 4321
        ring_pressures = {"input": 15, "output": 88, "other": 99}
        metrics = {}

        ps = MagicMock()
        ps.cpu_percent.return_value = 12.5
        ps.memory_info.return_value = MagicMock(rss=9 * 1024 * 1024)
        ps.nice.return_value = 5

        with patch("viviian.ipc.manager.psutil.Process", return_value=ps):
            task_pressures = mgr._sample_process(
                "task",
                proc,
                ring_pressures,
                mgr._task_specs,
                metrics,
                123.456,
            )

        self.assertEqual(task_pressures, {"input": 15, "output": 88})
        self.assertIn("task", metrics)
        self.assertEqual(
            metrics["task"],
            ProcessMetrics(
                name="task",
                pid=4321,
                cpu_percent=12.5,
                memory_rss_mb=9.0,
                nice=5,
                ring_pressure={"input": 15, "output": 88},
                sampled_at=123.456,
            ),
        )

    def test_adjust_process_nice_uses_worst_ring_pressure(self):
        mgr = Manager()
        ps = MagicMock()

        with patch("viviian.ipc.manager.psutil.Process", return_value=ps):
            mgr._adjust_process_nice(MagicMock(pid=1), {"a": 81, "b": 40})
            mgr._adjust_process_nice(MagicMock(pid=2), {"a": 10})
            mgr._adjust_process_nice(MagicMock(pid=3), {})

        self.assertEqual(ps.nice.call_args_list[0].args, (-10,))
        self.assertEqual(ps.nice.call_args_list[1].args, (10,))
        self.assertEqual(len(ps.nice.call_args_list), 2)

    def test_start_monitor_starts_named_daemon_thread(self):
        mgr = Manager()
        fake_thread = MagicMock()

        with patch("viviian.ipc.manager.threading.Thread", return_value=fake_thread) as thread_cls:
            mgr.start_monitor(interval_s=0.25)

        kwargs = thread_cls.call_args.kwargs
        self.assertTrue(callable(kwargs["target"]))
        self.assertTrue(kwargs["daemon"])
        self.assertEqual(kwargs["name"], "viviian_monitor")
        fake_thread.start.assert_called_once_with()

    def test_get_metrics_returns_latest_snapshot_or_none(self):
        mgr = Manager()
        self.assertIsNone(mgr.get_metrics("missing"))

        snap = ProcessMetrics(
            name="task",
            pid=1,
            cpu_percent=1.0,
            memory_rss_mb=2.0,
            nice=0,
            ring_pressure={"rb": 50},
            sampled_at=3.0,
        )
        mgr._metrics["task"] = snap

        self.assertIs(mgr.get_metrics("task"), snap)


if __name__ == "__main__":
    unittest.main()
