from __future__ import annotations

import unittest

from pythusa import Pipeline
from viviian.frontend import Frontend

from ucirplgui import BackendSimDeviceInterface, FrontendOperatorDesk, build_ucirplgui_pipeline
from ucirplgui import config


class ScaffoldImportTests(unittest.TestCase):
    def test_backend_scaffold_exposes_expected_stream_names(self) -> None:
        backend = BackendSimDeviceInterface()

        self.assertEqual(backend.stream_names(), config.TELEMETRY_STREAMS)
        self.assertEqual(
            tuple(backend.build_publish_contract()),
            config.TELEMETRY_STREAMS,
        )

    def test_frontend_scaffold_builds_frontend_shell(self) -> None:
        desk = FrontendOperatorDesk()
        frontend = desk.build_frontend()

        self.assertIsInstance(frontend, Frontend)
        self.assertEqual(frontend.name, "ucirplgui_frontend")
        self.assertEqual(desk.required_streams(), config.TELEMETRY_STREAMS)
        self.assertEqual(desk.output_stream_name(), config.UI_STATE_STREAM)

    def test_pipeline_builder_returns_pipeline_shell(self) -> None:
        pipeline = build_ucirplgui_pipeline()

        self.assertIsInstance(pipeline, Pipeline)
        self.assertEqual(pipeline.name, "ucirplgui")
        pipeline.close()


if __name__ == "__main__":
    unittest.main()
