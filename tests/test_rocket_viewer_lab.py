from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tests.gui_runnables.rocket_viewer_lab import (
    ModelViewerLabApp,
    RocketViewerLabApp,
    _DEFAULT_SAMPLE_RATE_HZ,
)


class ModelViewerLabAppTests(unittest.TestCase):
    def test_legacy_alias_points_to_generic_app(self) -> None:
        self.assertIs(RocketViewerLabApp, ModelViewerLabApp)

    def test_app_initializes_against_temp_obj_and_advances_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cad_dir = tmp / "cad"
            compiled_dir = tmp / "compiled"
            cad_dir.mkdir()
            compiled_dir.mkdir()
            (cad_dir / "demo.obj").write_text(_demo_obj_text(), encoding="utf-8")

            app = ModelViewerLabApp(
                seed=7,
                cad_dir=cad_dir,
                compiled_dir=compiled_dir,
                max_rows_per_tick=4,
            )
            had_update = app.advance(4.0 / _DEFAULT_SAMPLE_RATE_HZ)
            primary = app.viewer.body_snapshot("body_20")
            secondary = app.viewer.body_snapshot("body_21")
            pose = app.viewer.pose_snapshot()
            app.close()

        self.assertTrue(had_update)
        self.assertIsNotNone(primary.timestamp)
        self.assertIsNotNone(secondary.timestamp)
        self.assertIsNotNone(primary.value)
        self.assertIsNotNone(secondary.value)
        self.assertIsNotNone(pose.timestamp)


def _demo_obj_text() -> str:
    return "\n".join(
        [
            "o Demo",
            "v 0 0 0",
            "v 1 0 0",
            "v 0 1 0",
            "v 0 0 1",
            "vn 0 0 1",
            "vn 1 0 0",
            "g Body1:20",
            "f 1//1 2//1 3//1",
            "g Body1:21",
            "f 1//2 3//2 4//2",
            "",
        ]
    )


if __name__ == "__main__":
    unittest.main()
