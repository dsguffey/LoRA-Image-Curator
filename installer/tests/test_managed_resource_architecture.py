from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from install_manager.bootstrap_layout import layout, validate_root
from install_manager.component_catalog import (ComponentAction, ComponentFacts,
                                                ComponentOperationQueue, ComponentPhase,
                                                component_action, load_component_catalog)
from install_manager.component_operations import operation_download_summary, operation_plan
from install_manager.lic_face_settings import (read_face_model_root, write_body_model_path,
                                               write_face_model_root)
from install_manager.managed_resources import (ImportCandidate, ManagedResourceSpec,
                                               approved_resource_specs,
                                               component_resource_status, discover_import,
                                               import_resources, managed_data_layout)


DELIVERY = ROOT / "src/install_manager"


def spec(root: Path, content: bytes = b"approved", *, name: str = "yunet.onnx",
         identity: str = "fixture:yunet", components=("face-analysis",)) -> ManagedResourceSpec:
    digest = hashlib.sha256(content).hexdigest()
    return ManagedResourceSpec(
        identity, "YuNet face detector", "model-file", tuple(components),
        root / "Data/Models/opencv-zoo/revision" / name, name, len(content),
        (("sha256", digest),),
        {"artifact_id": identity.replace(":", "-"), "version": "1", "sha256": digest})


class ManagedLayoutTests(unittest.TestCase):
    def test_data_paths_are_deterministic_without_moving_qualified_core(self):
        root = Path(r"C:\LIC Root")
        first = managed_data_layout(root)
        self.assertEqual(first, managed_data_layout(root))
        self.assertEqual(first["models"], root / "Data/Models")
        self.assertEqual(first["downloads"], root / "Data/Downloads")
        self.assertEqual(layout(root)["runtime"], root / "Shared/Runtimes/python-3.14.6")
        self.assertEqual(layout(root)["application"], root / "Apps/LIC-Lite/candidate-1/package")

    def test_shared_opencv_has_one_identity_and_destination(self):
        specs = approved_resource_specs(DELIVERY, Path(r"C:\LIC"))
        opencv = [item for item in specs if item.artifact.get("artifact_id") == "opencv-contrib-python"]
        self.assertEqual(len(opencv), 1)
        self.assertIn("face-analysis", opencv[0].component_ids)
        self.assertIn("body-analysis", opencv[0].component_ids)
        self.assertIn("Data\\Packages\\opencv-contrib-python", str(opencv[0].destination))

    def test_provider_resources_use_models_tasks_and_tools(self):
        specs = approved_resource_specs(DELIVERY, Path(r"C:\LIC"))
        florence = next(item for item in specs if item.identity.startswith("florence:") )
        body = next(item for item in specs if item.identity.startswith("artifact:mediapipe-"))
        ffmpeg = next(item for item in specs if item.kind == "tool-directory")
        self.assertIn("Data\\Models\\huggingface\\hub", str(florence.destination))
        self.assertIn("Data\\Tasks\\mediapipe", str(body.destination))
        self.assertIn("Data\\Tools\\ffmpeg", str(ffmpeg.destination))


class ImportTests(unittest.TestCase):
    def test_piecemeal_valid_import_copies_verifies_and_forgets_source(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "source"; source.mkdir()
            content = b"approved"; original = source / "yunet.onnx"; original.write_bytes(content)
            one = spec(root, content)
            with mock.patch("install_manager.managed_resources.approved_resource_specs", return_value=(one,)):
                plan = discover_import(DELIVERY, root, source)
                self.assertEqual([(item.spec.identity, item.state) for item in plan.candidates],
                                 [(one.identity, "new")])
                result = import_resources(DELIVERY, root, source)
                self.assertEqual(result["imported"], [one.identity])
                self.assertEqual(original.read_bytes(), content)
                self.assertEqual(one.destination.read_bytes(), content)
                original.unlink()
                status = component_resource_status(DELIVERY, root, "face-analysis")
                self.assertTrue(status["resource_complete"])
                record = json.loads((root / "Data/State/resources.json").read_text())
                self.assertNotIn(str(source), json.dumps(record))

    def test_invalid_and_unrelated_items_do_not_block_valid_items(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "source"; source.mkdir()
            one = spec(root)
            (source / "yunet.onnx").write_bytes(b"wrong")
            (source / "unrelated.txt").write_text("ignore")
            other = spec(root, b"second", name="sface.onnx", identity="fixture:sface")
            (source / "sface.onnx").write_bytes(b"second")
            with mock.patch("install_manager.managed_resources.approved_resource_specs",
                            return_value=(one, other)):
                result = import_resources(DELIVERY, root, source)
            self.assertEqual(result["invalid"], 1)
            self.assertEqual(result["unrecognized"], 1)
            self.assertFalse(one.destination.exists())
            self.assertEqual(other.destination.read_bytes(), b"second")

    def test_identical_skips_copy_and_replacement_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "source"; source.mkdir()
            one = spec(root); one.destination.parent.mkdir(parents=True); one.destination.write_bytes(b"approved")
            incoming = source / "yunet.onnx"; incoming.write_bytes(b"approved")
            with mock.patch("install_manager.managed_resources.approved_resource_specs", return_value=(one,)):
                first = import_resources(DELIVERY, root, source,
                                         confirm_replace=lambda _: self.fail("identical prompted"))
                self.assertEqual(first["already_present"], [one.identity])
                one.destination.write_bytes(b"conflict")
                declined = import_resources(DELIVERY, root, source, confirm_replace=lambda _: False)
                self.assertEqual(one.destination.read_bytes(), b"conflict")
                self.assertEqual(declined["replacement_declined"], [one.identity])
                accepted = import_resources(DELIVERY, root, source, confirm_replace=lambda _: True)
                self.assertEqual(accepted["imported"], [one.identity])
                self.assertEqual(one.destination.read_bytes(), b"approved")
                self.assertEqual(incoming.read_bytes(), b"approved")

    def test_pause_occurs_at_file_boundary_and_preserves_completed_copy(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "source"; source.mkdir()
            one = spec(root); (source / one.filename).write_bytes(b"approved")
            calls = {"count": 0}
            def paused():
                calls["count"] += 1
                return calls["count"] > 1
            with mock.patch("install_manager.managed_resources.approved_resource_specs", return_value=(one,)):
                with self.assertRaisesRegex(RuntimeError, "paused"):
                    import_resources(DELIVERY, root, source, pause_requested=paused)
            self.assertTrue(one.destination.is_file())
            self.assertTrue((root / "Data/State/resources.json").is_file())

    def test_pre_core_import_is_allowed_but_unknown_data_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "LIC"; source = base / "source"; source.mkdir()
            item = spec(root); (source / item.filename).write_bytes(b"approved")
            with mock.patch("install_manager.managed_resources.approved_resource_specs",
                            return_value=(item,)):
                import_resources(DELIVERY, root, source)
                self.assertEqual(validate_root(root, delivery=DELIVERY), root.resolve())
                unknown = root / "Data/Models/unrecognized.bin"
                unknown.parent.mkdir(parents=True, exist_ok=True)
                unknown.write_bytes(b"unknown")
                with self.assertRaises(FileExistsError):
                    validate_root(root, delivery=DELIVERY)

    def test_one_imported_shared_package_is_available_to_two_features(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "source"; source.mkdir()
            shared = spec(root, b"wheel", name="opencv.whl", identity="fixture:opencv",
                          components=("face-analysis", "body-analysis"))
            (source / shared.filename).write_bytes(b"wheel")
            with mock.patch("install_manager.managed_resources.approved_resource_specs",
                            return_value=(shared,)):
                import_resources(DELIVERY, root, source)
                self.assertTrue(component_resource_status(
                    DELIVERY, root, "face-analysis")["resource_complete"])
                self.assertTrue(component_resource_status(
                    DELIVERY, root, "body-analysis")["resource_complete"])

    def test_extracted_ffmpeg_requires_all_approved_member_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root = base / "managed"; source = base / "ffmpeg"; (source / "bin").mkdir(parents=True)
            files = {"ffmpeg.exe": b"ffmpeg", "ffprobe.exe": b"ffprobe", "LICENSE.txt": b"license"}
            (source / "bin/ffmpeg.exe").write_bytes(files["ffmpeg.exe"])
            (source / "bin/ffprobe.exe").write_bytes(files["ffprobe.exe"])
            (source / "LICENSE.txt").write_bytes(files["LICENSE.txt"])
            members = tuple({"source": name, "destination": name, "size": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()}
                            for name, data in files.items())
            tool = ManagedResourceSpec(
                "fixture:ffmpeg", "FFmpeg approved build", "tool-directory",
                ("video-extraction",), root / "Data/Tools/ffmpeg/1", "ffmpeg.zip",
                None, (("sha256", "0" * 64),),
                {"artifact_id": "ffmpeg", "version": "1", "sha256": "0" * 64}, members)
            with mock.patch("install_manager.managed_resources.approved_resource_specs", return_value=(tool,)):
                result = import_resources(DELIVERY, root, source)
            self.assertEqual(result["imported"], [tool.identity])
            self.assertEqual((tool.destination / "ffmpeg.exe").read_bytes(), b"ffmpeg")


class InstallPlanningTests(unittest.TestCase):
    def _active_root(self, base: Path) -> Path:
        root = base / "LIC"; target = root / "State/installations/lic-lite.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"state": "active", "root": str(root)}))
        return root

    def test_optional_install_refuses_arbitrary_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._active_root(Path(td))
            with self.assertRaisesRegex(ValueError, "managed resources"):
                operation_plan(DELIVERY, root, "face-analysis", Path(td) / "external")

    def test_download_summary_counts_only_missing_managed_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._active_root(Path(td))
            with mock.patch("install_manager.component_operations.managed_artifact_path",
                            side_effect=lambda d, r, aid, version, digest:
                            Path(td) / "managed.whl" if aid == "opencv-contrib-python" else None):
                summary = operation_download_summary(DELIVERY, root, "face-analysis")
            self.assertGreater(summary["artifact_count"], 1)
            self.assertEqual(summary["cached_artifact_count"], 1)
            self.assertGreater(summary["download_bytes"], 0)

    def test_passive_resource_status_never_acquires(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("install_manager.acquisition.acquire_artifact") as acquire:
            component_resource_status(DELIVERY, Path(td), "florence-captioning")
            acquire.assert_not_called()


class PauseAndBridgeTests(unittest.TestCase):
    def test_labels_and_queue_semantics(self):
        catalog = {item.component_id: item for item in load_component_catalog(
            DELIVERY / "recipes/lic-components.json")}
        active = ComponentFacts(ComponentPhase.DOWNLOADING)
        queued = ComponentFacts(ComponentPhase.QUEUED)
        self.assertEqual(component_action(catalog["lic-core"], active), ComponentAction.CANCEL)
        self.assertEqual(ComponentAction.CANCEL.value, "Pause")
        self.assertEqual(component_action(catalog["face-analysis"], queued).value, "Remove from queue")
        queue = ComponentOperationQueue(); first, _ = queue.submit("lic-core", "Install")
        queue.submit("face-analysis", "Install")
        self.assertEqual(queue.cancel("face-analysis"), "queue-canceled")
        self.assertFalse(first.token.requested())
        self.assertEqual(queue.cancel("lic-core"), "canceling")
        self.assertEqual(queue.cancel("lic-core"), "canceling")
        self.assertTrue(first.token.requested())

    def test_face_bridge_uses_managed_path_and_other_provider_preferences_are_retired(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); appdata = root / "State/User/AppData/Roaming"
            managed = root / "Data/Models/opencv-zoo/revision"
            write_face_model_root(appdata, managed)
            self.assertEqual(read_face_model_root(appdata), str(managed))
            settings = json.loads((appdata / "LoRAImageCurator/settings.json").read_text())
            self.assertNotIn("install_manager_provider_paths", settings)

    def test_body_bridge_uses_the_managed_task_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); appdata = root / "State/User/AppData/Roaming"
            target = appdata / "LoRAImageCurator/settings.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"video_last_destination": "keep"}), encoding="utf-8")
            task = root / "Data/Tasks/mediapipe/0.10.35/pose_landmarker_full.task"
            write_body_model_path(appdata, task)
            settings = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(settings["body_model_path"], str(task))
            self.assertEqual(settings["video_last_destination"], "keep")
            self.assertNotIn("install_manager_provider_paths", settings)


if __name__ == "__main__":
    unittest.main()
