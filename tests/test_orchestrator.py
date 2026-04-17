from __future__ import annotations

import time
import unittest

import numpy as np

from viviian.orchestrator import Orchestrator
from pythusa import Pipeline


FRAME = np.array([1.0, 2.0], dtype=np.float32)


def source_task(samples) -> None:
    samples.write(FRAME)


def sink_task(samples) -> None:
    while True:
        frame = samples.read()
        if frame is None:
            time.sleep(0.001)
            continue
        return


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_is_pipeline_subclass(self) -> None:
        orchestrator = Orchestrator("desk")
        self.assertIsInstance(orchestrator, Pipeline)
        orchestrator.close()

    def test_add_internal_graph_records_upstream_edges(self) -> None:
        orchestrator = Orchestrator("desk")
        orchestrator.add_internal_graph("processing_tools", {"device_input", "storage_tools"})

        self.assertTrue(orchestrator.internal_graph.has_node("processing_tools"))
        self.assertTrue(
            orchestrator.internal_graph.has_edge("device_input", "processing_tools")
        )
        self.assertTrue(
            orchestrator.internal_graph.has_edge("storage_tools", "processing_tools")
        )
        orchestrator.close()

    def test_add_internal_graph_rejects_invalid_names(self) -> None:
        orchestrator = Orchestrator("desk")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            orchestrator.add_internal_graph("")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            orchestrator.add_internal_graph("processing_tools", {""})
        orchestrator.close()

    def test_pipeline_lifecycle_runs_through_orchestrator(self) -> None:
        with Orchestrator("desk") as orchestrator:
            orchestrator.add_stream("samples", shape=(2,), dtype=np.float32)
            orchestrator.add_task("source", fn=source_task, writes={"samples": "samples"})
            orchestrator.add_task("sink", fn=sink_task, reads={"samples": "samples"})

            orchestrator.compile()
            self.assertTrue(orchestrator._compiled)

            orchestrator.run()
            self.assertTrue(orchestrator._started)

        self.assertTrue(orchestrator._closed)


if __name__ == "__main__":
    unittest.main()
