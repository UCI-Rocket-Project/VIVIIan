from __future__ import annotations

from pathlib import Path
import tempfile
import tomllib
import unittest

import numpy as np

from viviian.gui_utils import (
    GradientStop,
    ModelBodyBinding,
    ModelViewer,
    ModelViewerConfig,
    build_pose_batch_from_direction_vectors,
    build_pose_batch_from_matrices,
    compile_obj_to_cache,
    discover_single_obj_asset,
    resolve_compiled_obj_assets,
)


class FakeReader:
    def __init__(
        self,
        *,
        shape: tuple[int, int],
        dtype: np.dtype,
        frames: list[np.ndarray] | None = None,
    ) -> None:
        self.shape = shape
        self.dtype = np.dtype(dtype)
        self._frames = [
            np.asarray(frame, dtype=self.dtype).reshape(self.shape) for frame in (frames or [])
        ]
        self.blocking_calls: list[bool] = []

    def set_blocking(self, blocking: bool) -> None:
        self.blocking_calls.append(bool(blocking))

    def push(self, frame: np.ndarray) -> None:
        self._frames.append(np.asarray(frame, dtype=self.dtype).reshape(self.shape))

    def read(self) -> np.ndarray | None:
        if not self._frames:
            return None
        return self._frames.pop(0).copy()


class ModelViewerTests(unittest.TestCase):
    def test_model_body_binding_supports_endpoint_configuration(self) -> None:
        binding = ModelBodyBinding(
            binding_id="tank_a",
            mesh_part_name="g_Body1:20",
            value_stream_name="tank_a.level",
            low_value=0.0,
            low_color_rgba=(0.1, 0.2, 0.3, 1.0),
            high_value=100.0,
            high_color_rgba=(0.9, 0.8, 0.1, 1.0),
            default_color_rgba=(0.2, 0.2, 0.2, 1.0),
        )

        self.assertEqual(binding.binding_id, "tank_a")
        self.assertEqual(binding.part_id, "tank_a")
        self.assertEqual(binding.range_min, 0.0)
        self.assertEqual(binding.range_max, 100.0)
        self.assertEqual(binding.gradient_stops[0].color_rgba, (0.1, 0.2, 0.3, 1.0))

    def test_model_body_binding_accepts_legacy_gradient_signature(self) -> None:
        binding = ModelBodyBinding(
            part_id="legacy",
            mesh_part_name="g_Body1:20",
            value_stream_name="legacy.level",
            range_min=10.0,
            range_max=30.0,
            gradient_stops=(
                GradientStop(position=0.0, color_rgba=(0.0, 0.0, 1.0, 1.0)),
                GradientStop(position=1.0, color_rgba=(1.0, 0.0, 0.0, 1.0)),
            ),
        )

        self.assertEqual(binding.binding_id, "legacy")
        self.assertEqual(binding.low_value, 10.0)
        self.assertEqual(binding.high_value, 30.0)
        self.assertEqual(binding.default_color_rgba, (0.0, 0.0, 1.0, 1.0))

    def test_config_round_trip_preserves_generic_viewer_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cache_path, manifest_path = _write_mesh_bundle(tmp)
            config = ModelViewerConfig(
                "flight",
                title="Flight View",
                mesh_cache_path=str(cache_path.resolve()),
                manifest_path=str(manifest_path.resolve()),
                pose_stream_name="pose",
                default_body_color_rgba=(0.2, 0.2, 0.2, 1.0),
                other_body_alpha=0.25,
                body_bindings=(
                    ModelBodyBinding(
                        binding_id="hot_a",
                        mesh_part_name="g_Body1:20",
                        value_stream_name="hot_a",
                        low_value=0.0,
                        low_color_rgba=(0.0, 0.2, 0.7, 1.0),
                        high_value=100.0,
                        high_color_rgba=(1.0, 0.2, 0.2, 1.0),
                    ),
                ),
            )

            export_path = config.export(tmp / "viewer.toml")
            rebuilt = ModelViewerConfig.reconstruct(export_path)

        self.assertEqual(rebuilt.viewer_id, "flight")
        self.assertEqual(rebuilt.pose_stream_name, "pose")
        self.assertEqual(rebuilt.other_body_alpha, 0.25)
        self.assertEqual(rebuilt.body_bindings[0].mesh_part_name, "g_Body1:20")

    def test_bind_sets_reader_backpressure_for_scalar_and_pose_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            viewer = self._make_viewer(Path(tmpdir), backpressure_mode="blocking")
            scalar_reader = FakeReader(shape=(2, 4), dtype=np.float64)
            pose_reader = FakeReader(shape=(13, 4), dtype=np.float64)

            viewer.bind(
                {
                    "hot_a": scalar_reader,
                    "pose": pose_reader,
                }
            )

        self.assertEqual(scalar_reader.blocking_calls, [True])
        self.assertEqual(pose_reader.blocking_calls, [True])

    def test_consume_updates_latest_body_color_and_preserves_unbound_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            viewer = self._make_viewer(Path(tmpdir))
            scalar_reader = FakeReader(
                shape=(2, 3),
                dtype=np.float64,
                frames=[
                    np.array(
                        [
                            [0.0, 1.0, 2.0],
                            [0.0, 50.0, 75.0],
                        ],
                        dtype=np.float64,
                    )
                ],
            )
            pose_reader = FakeReader(
                shape=(13, 1),
                dtype=np.float64,
                frames=[build_pose_batch_from_matrices(timestamps=[0.0], positions_xyz=[0.0, 0.0, 0.0], rotation_matrices=np.eye(3))],
            )
            viewer.bind({"hot_a": scalar_reader, "pose": pose_reader})

            had_update = viewer.consume()
            snapshot = viewer.body_snapshot("hot_a")

        self.assertTrue(had_update)
        self.assertEqual(snapshot.timestamp, 2.0)
        self.assertEqual(snapshot.value, 75.0)
        np.testing.assert_allclose(snapshot.color_rgba, (0.75, 0.0, 0.25, 1.0))
        np.testing.assert_allclose(
            viewer.mesh_part_color("g_Body1:21"),
            (0.18, 0.205, 0.24, viewer.other_body_alpha),
        )

    def test_consume_resets_body_state_when_stream_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            viewer = self._make_viewer(Path(tmpdir))
            scalar_reader = FakeReader(shape=(2, 1), dtype=np.float64)
            pose_reader = FakeReader(
                shape=(13, 1),
                dtype=np.float64,
                frames=[build_pose_batch_from_matrices(timestamps=[0.0], positions_xyz=[0.0, 0.0, 0.0], rotation_matrices=np.eye(3))],
            )
            viewer.bind({"hot_a": scalar_reader, "pose": pose_reader})
            scalar_reader.push(np.array([[100.0], [100.0]], dtype=np.float64))
            viewer.consume()
            scalar_reader.push(np.array([[0.0], [25.0]], dtype=np.float64))

            viewer.consume()
            snapshot = viewer.body_snapshot("hot_a")

        self.assertEqual(snapshot.timestamp, 0.0)
        self.assertEqual(snapshot.value, 25.0)
        np.testing.assert_allclose(snapshot.color_rgba, (0.25, 0.0, 0.75, 1.0))

    def test_pose_stream_accepts_legacy_orientation_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            viewer = self._make_viewer(Path(tmpdir))
            scalar_reader = FakeReader(shape=(2, 1), dtype=np.float64)
            rotation = np.asarray(
                [
                    [1.0, 0.0, 0.0],
                    [0.0, 0.0, -1.0],
                    [0.0, 1.0, 0.0],
                ],
                dtype=np.float64,
            )
            pose_reader = FakeReader(
                shape=(10, 1),
                dtype=np.float64,
                frames=[np.vstack((np.array([[1.5]], dtype=np.float64), rotation.reshape(9, 1)))],
            )
            viewer.bind({"hot_a": scalar_reader, "pose": pose_reader})

            viewer.consume()
            pose = viewer.pose_snapshot()

        self.assertEqual(pose.timestamp, 1.5)
        self.assertEqual(pose.position_xyz, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(pose.matrix3x3(), rotation)

    def test_invalid_pose_matrix_is_ignored_after_last_good_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            viewer = self._make_viewer(Path(tmpdir))
            scalar_reader = FakeReader(shape=(2, 1), dtype=np.float64)
            valid_pose = build_pose_batch_from_matrices(
                timestamps=[2.0],
                positions_xyz=[1.0, 2.0, 3.0],
                rotation_matrices=np.eye(3),
            )
            invalid_pose = np.vstack(
                (
                    np.array([[3.0]], dtype=np.float64),
                    np.array([[4.0], [5.0], [6.0]], dtype=np.float64),
                    np.zeros((9, 1), dtype=np.float64),
                )
            )
            pose_reader = FakeReader(
                shape=(13, 1),
                dtype=np.float64,
                frames=[valid_pose, invalid_pose],
            )
            viewer.bind({"hot_a": scalar_reader, "pose": pose_reader})

            viewer.consume()
            pose = viewer.pose_snapshot()

        self.assertEqual(pose.timestamp, 2.0)
        self.assertEqual(pose.position_xyz, (1.0, 2.0, 3.0))
        np.testing.assert_allclose(pose.matrix3x3(), np.eye(3))

    def test_build_pose_batches_support_matrices_and_direction_vectors(self) -> None:
        matrix_batch = build_pose_batch_from_matrices(
            timestamps=[0.0, 1.0],
            positions_xyz=[[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]],
            rotation_matrices=np.repeat(np.eye(3, dtype=np.float64)[None, :, :], 2, axis=0),
        )
        direction_batch = build_pose_batch_from_direction_vectors(
            timestamps=[0.0, 1.0],
            positions_xyz=[0.0, 0.0, 0.0],
            directions_xyz=[[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]],
        )

        self.assertEqual(matrix_batch.shape, (13, 2))
        self.assertEqual(direction_batch.shape, (13, 2))
        np.testing.assert_allclose(direction_batch[1:4, 1], np.array([0.0, 0.0, 0.0]))

    def test_compile_obj_to_cache_and_resolve_compiled_assets_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cad_dir = tmp / "cad"
            compiled_dir = tmp / "compiled"
            cad_dir.mkdir()
            compiled_dir.mkdir()
            obj_path = cad_dir / "demo.obj"
            obj_path.write_text(_demo_obj_text(), encoding="utf-8")

            discovered = discover_single_obj_asset(cad_dir)
            cache_path, manifest_path = compile_obj_to_cache(discovered, compiled_dir / "demo_mesh")
            resolved_cache, resolved_manifest = resolve_compiled_obj_assets(
                obj_path=discovered,
                compiled_dir=compiled_dir,
            )

            manifest_data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
            compiled = np.load(cache_path)

            self.assertEqual(discovered, obj_path)
            self.assertTrue(cache_path.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(resolved_cache.is_file())
            self.assertTrue(resolved_manifest.is_file())
            self.assertEqual(manifest_data["kind"], "model_mesh_manifest")
            self.assertEqual(compiled["vertices"].shape, (6, 3))
            self.assertEqual(len(manifest_data["mesh_parts"]), 2)

    def _make_viewer(self, tmp: Path, **config_overrides) -> ModelViewer:
        cache_path, manifest_path = _write_mesh_bundle(tmp)
        config = ModelViewerConfig(
            "flight",
            title="Flight View",
            mesh_cache_path=str(cache_path.resolve()),
            manifest_path=str(manifest_path.resolve()),
            pose_stream_name="pose",
            body_bindings=(
                ModelBodyBinding(
                    binding_id="hot_a",
                    mesh_part_name="g_Body1:20",
                    value_stream_name="hot_a",
                    low_value=0.0,
                    low_color_rgba=(0.0, 0.0, 1.0, 1.0),
                    high_value=100.0,
                    high_color_rgba=(1.0, 0.0, 0.0, 1.0),
                    default_color_rgba=(0.2, 0.2, 0.2, 1.0),
                ),
            ),
            **config_overrides,
        )
        return config.build_viewer()


def _write_mesh_bundle(tmp: Path) -> tuple[Path, Path]:
    cache_path = tmp / "mesh.npz"
    manifest_path = tmp / "mesh.toml"
    vertices = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.0, -1.0],
        ],
        dtype=np.float32,
    )
    normals = np.asarray(
        [
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, -1.0, 0.0],
        ],
        dtype=np.float32,
    )
    indices = np.arange(vertices.shape[0], dtype=np.uint32)
    np.savez(cache_path, vertices=vertices, normals=normals, indices=indices)
    manifest_path.write_text(
        "\n".join(
            [
                "format_version = 1",
                'kind = "model_mesh_manifest"',
                "",
                "mesh_cache_version = 1",
                'source_asset_kind = "obj"',
                f'source_asset_path = "{(tmp / "source.obj").resolve()}"',
                'source_sha256 = "deadbeef"',
                f'cache_file = "{cache_path.name}"',
                "",
                "[[mesh_parts]]",
                'part_id = "g_Body1:20"',
                'mesh_part_name = "g_Body1:20"',
                "vertex_start = 0",
                "vertex_count = 3",
                "index_start = 0",
                "index_count = 3",
                "",
                "[[mesh_parts]]",
                'part_id = "g_Body1:21"',
                'mesh_part_name = "g_Body1:21"',
                "vertex_start = 3",
                "vertex_count = 3",
                "index_start = 3",
                "index_count = 3",
                "",
                "[[mesh_parts]]",
                'part_id = "g_Body1:22"',
                'mesh_part_name = "g_Body1:22"',
                "vertex_start = 6",
                "vertex_count = 3",
                "index_start = 6",
                "index_count = 3",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return cache_path, manifest_path


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
