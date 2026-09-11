"""Focused contracts for the M1.7 customer GUI correction and managed move."""
from contextlib import ExitStack, nullcontext
import hashlib
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from install_manager.capabilities import lic_capabilities
from install_manager.bootstrap import ensure_selected_model
from install_manager.hf_source import hf_snapshot_target
from install_manager.first_launch import show
from install_manager.manager_ui import (
    FIRST_RUN_SECTIONS, INSTALLED_SECTIONS, MANAGER_NAME, PRODUCT_NAME,
    capability_detail_contract, compact_path, model_status_presentation,
)
from install_manager.managed_move import MOVE_STEPS, move_installation, move_plan
from install_manager.model_resources import ModelFile, ModelSnapshot
from install_manager.storage import inspect_model_storage, storage_review, validate_model_root


def fixture_model(content=b"verified-model"):
    item = ModelFile("weights.bin", len(content), "sha256", hashlib.sha256(content).hexdigest())
    return ModelSnapshot(1, "florence-fixture", "huggingface", "example/Florence-fixture",
                         "a" * 40, "florence", (item,), {}, {}, {})


class ManagerShellContractTests(unittest.TestCase):
    def test_persistent_navigation_orders_are_workflow_ordered(self):
        expected = ("Install & Update", "Move Installation", "Help")
        self.assertEqual(FIRST_RUN_SECTIONS, expected)
        self.assertEqual(INSTALLED_SECTIONS, expected)

    def test_details_are_human_first_and_identifiers_are_advanced(self):
        plan = {"details": {
            "LIC Lite": {"version": "0.28.4", "artifact": "lic-id", "sha256": "f" * 64,
                         "source": "reviewed source"},
            "Florence caption model": {"model": "example/model", "revision": "a" * 40,
                                        "digest": "b" * 64},
        }}
        details = capability_detail_contract(lic_capabilities()[0], plan, Path("C:/LIC"), Path("D:/Models"))
        fields = dict(field for _heading, section in details["sections"] for field in section)
        advanced = dict(details["advanced"])
        self.assertIn("What this does", fields)
        self.assertIn("Publisher", fields)
        self.assertNotIn("Revision", fields)
        self.assertIn("Artifact ID", advanced)
        self.assertFalse(any(value.lstrip().startswith(("{", "[")) for value in fields.values()))

        captioning = capability_detail_contract(lic_capabilities()[1], plan, Path("C:/LIC"), Path("D:/Models"))
        caption_advanced = dict(captioning["advanced"])
        self.assertIn("Revision", caption_advanced)

    def test_customer_facing_branding_is_not_internal_lic_shorthand(self):
        self.assertEqual(PRODUCT_NAME, "LoRA Image Curator")
        self.assertEqual(MANAGER_NAME, "LIC Install Manager")

    def test_model_status_is_plain_and_distinguishes_reuse_from_download(self):
        reusable = model_status_presentation({"reusable": True, "status": "reusable"})
        missing = model_status_presentation({"reusable": False, "status": "missing", "download_bytes": 2_500_000_000})
        incompatible = model_status_presentation({"reusable": False, "status": "incompatible"})
        self.assertIn("Existing compatible model found", reusable)
        self.assertIn("No model download", reusable)
        self.assertIn("will be downloaded", missing)
        self.assertIn(PRODUCT_NAME, incompatible)

    def test_long_paths_are_compacted_without_losing_the_complete_value(self):
        path = "C:/A very long folder/with a user selected location/that needs to stay readable/Florence"
        shown = compact_path(path, limit=42)
        self.assertTrue(shown.endswith("…"))
        self.assertLessEqual(len(shown), 42)
        self.assertEqual(compact_path(path, limit=200), path)

    def test_first_launch_wrapper_supports_a_quiet_auto_closing_probe(self):
        self.assertIn("quiet", inspect.signature(show).parameters)


class IndependentStorageTests(unittest.TestCase):
    def test_bounded_huggingface_parent_discovery_accepts_one_verified_snapshot(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            base = Path(d)
            cache_parent = base / "Shared Models" / "huggingface" / "hub"
            snapshot = cache_parent / "models--example--Florence-fixture" / "snapshots" / ("a" * 40)
            snapshot.mkdir(parents=True)
            (snapshot / "weights.bin").write_bytes(content)
            result = inspect_model_storage(base / "delivery", base / "Shared Models")
            self.assertTrue(result["reusable"])
            self.assertEqual(Path(result["snapshot"]), snapshot.resolve())

    def test_multiple_bounded_verified_snapshots_are_not_chosen_silently(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            base = Path(d)
            for parent in (base / "huggingface" / "hub", base / "hub"):
                snapshot = parent / "models--example--Florence-fixture" / "snapshots" / ("a" * 40)
                snapshot.mkdir(parents=True); (snapshot / "weights.bin").write_bytes(content)
            result = inspect_model_storage(base / "delivery", base)
            self.assertEqual(result["status"], "ambiguous")
    def test_model_path_is_independent_and_optional_reuse_does_not_change_core_estimate(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            base = Path(d)
            app, models = base / "Application", base / "Independent Models"
            snapshot = hf_snapshot_target(models, model)
            snapshot.mkdir(parents=True)
            (snapshot / "weights.bin").write_bytes(content)
            review = storage_review(base / "delivery", app, models, base_download_bytes=1000)
            self.assertTrue(review["locations_independent"])
            self.assertNotEqual(Path(review["model_root"]), Path(review["application_root"]) / "Shared/Models")
            self.assertTrue(review["model"]["reusable"])
            self.assertEqual(review["download_bytes"], 1000)

    def test_missing_incomplete_and_incompatible_models_are_not_reused(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            base = Path(d)
            missing = inspect_model_storage(base / "delivery", base / "missing")
            self.assertEqual(missing["status"], "missing")
            snapshot = hf_snapshot_target(base / "incomplete", model)
            snapshot.mkdir(parents=True)
            incomplete = inspect_model_storage(base / "delivery", base / "incomplete")
            self.assertEqual(incomplete["status"], "incompatible")
            (snapshot / "weights.bin").write_bytes(b"X" * len(content))
            incompatible = inspect_model_storage(base / "delivery", base / "incomplete")
            self.assertEqual(incompatible["status"], "incompatible")
            self.assertFalse(incompatible["reusable"])

    def test_model_location_validation_does_not_create_selected_folder(self):
        with tempfile.TemporaryDirectory() as d:
            selected = Path(d) / "new models"
            self.assertEqual(validate_model_root(selected), selected.resolve())
            self.assertFalse(selected.exists())

    def test_selected_verified_hf_snapshot_is_reused_without_writing_a_sidecar(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d:
            models = Path(d) / "models"
            snapshot = hf_snapshot_target(models, model)
            snapshot.mkdir(parents=True)
            (snapshot / "weights.bin").write_bytes(content)
            fetch = Mock(side_effect=AssertionError("verified content must not be downloaded"))
            result = ensure_selected_model(model, snapshot, models, model_source=None, fetch_file=fetch)
            self.assertEqual(result["reuse"], "selected-existing")
            self.assertFalse(snapshot.with_name(snapshot.name + ".resource.json").exists())
            fetch.assert_not_called()

    def test_standalone_exact_snapshot_is_reused_without_cache_parent_names(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            snapshot = Path(d) / ("a" * 40)
            snapshot.mkdir(); (snapshot / "weights.bin").write_bytes(content)
            result = inspect_model_storage(Path(d) / "delivery", snapshot)
            self.assertEqual(result["status"], "compatible")
            self.assertTrue(result["reusable"])
            self.assertTrue(result["flat_snapshot"])

    def test_snapshot_inside_huggingface_cache_records_exact_hub_root(self):
        model, content = fixture_model(), b"verified-model"
        with tempfile.TemporaryDirectory() as d, patch("install_manager.storage.model_from_delivery", return_value=model):
            root = Path(d) / "shared"
            snapshot = hf_snapshot_target(root, model)
            snapshot.mkdir(parents=True); (snapshot / "weights.bin").write_bytes(content)
            result = inspect_model_storage(Path(d) / "delivery", snapshot)
            self.assertEqual(result["status"], "compatible")
            self.assertEqual(Path(result["hub_root"]), root / "huggingface/hub")


class ManagedMoveTests(unittest.TestCase):
    def _source(self, base: Path):
        source, destination, delivery = base / "Source LIC", base / "Destination LIC", base / "delivery"
        for folder, filename in (("Shared/Runtimes/python-3.14.6", "python.exe"),
                                 ("Cache", "wheel.bin"),
                                 ("Apps/LIC-Lite/candidate-1/package", "payload.txt"),
                                 ("State/User", "catalog.sqlite"),
                                 ("Manager/current", "LIC Install Manager.exe")):
            path = source / folder
            path.mkdir(parents=True, exist_ok=True)
            (path / filename).write_bytes(filename.encode())
        (source / "Manager/current/_internal").mkdir()
        (source / "Manager/current/_internal/payload-index.json").write_text("{}")
        models = base / "Shared AI Models"
        models.mkdir()
        record = {"schema_version": 1, "state": "active", "root": source.as_posix(),
                  "application": str(source / "Apps/LIC-Lite/candidate-1/package/extracted"),
                  "python": str(source / "Apps/LIC-Lite/candidate-1/venv/Scripts/python.exe"),
                  "manager": str(source / "Manager/current/LIC Install Manager.exe"),
                  "model_root": str(models), "channel": {"release_id": "fixture"},
                  "choices": {"start_menu": True, "desktop": False}, "shortcuts": {}}
        record_path = source / "State/installations/lic-lite.json"
        record_path.parent.mkdir(parents=True)
        record_path.write_text(json.dumps(record))
        (source / "unrelated-user-file.txt").write_text("preserve")
        return source, destination, delivery, models, record_path.read_bytes()

    def _patches(self, base: Path, *, validation_error=None):
        model = SimpleNamespace(source="huggingface", repository="example/model", revision="a" * 40)
        lock = SimpleNamespace(profile="fixture", wheels=())
        stack = ExitStack()
        stack.enter_context(patch("install_manager.managed_move.validate_root", side_effect=lambda root, **_: root))
        stack.enter_context(patch("install_manager.managed_move.reject_reparse_entries"))
        stack.enter_context(patch("install_manager.managed_move.process_lock", side_effect=lambda _: nullcontext()))
        stack.enter_context(patch("install_manager.managed_move.profile",
                                  return_value=(None, None, lock, model, {"release_id": "fixture"})))
        create = stack.enter_context(patch("install_manager.managed_move.create_final_path_venv"))
        def create_venv(_runtime, destination, **_kwargs):
            python = destination / "Scripts/python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"private python")
            return python
        create.side_effect = create_venv
        stack.enter_context(patch("install_manager.managed_move.install_locked_wheels"))
        stack.enter_context(patch("install_manager.managed_move.verify_snapshot",
                                  return_value={"verified": True, "revision": "a" * 40}))
        validate = stack.enter_context(patch("install_manager.managed_move._validate_destination"))
        if validation_error:
            validate.side_effect = validation_error
        else:
            validate.return_value = {"passed": True}
        links = {"start_menu_app": base / "links/app.lnk",
                 "start_menu_manager": base / "links/manager.lnk", "desktop": base / "links/desktop.lnk"}
        stack.enter_context(patch("install_manager.managed_move.shortcut_locations", return_value=links))
        shortcut = stack.enter_context(patch("install_manager.managed_move._shortcut"))
        def make_shortcut(path, target, arguments):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"link")
            return {"path": str(path), "target": str(target), "arguments": arguments, "owned": True}
        shortcut.side_effect = make_shortcut
        return stack, create

    def test_plan_keeps_independent_models_and_rebuilds_environment(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); source, destination, _delivery, models, _record = self._source(base)
            plan = move_plan(source, destination, models, copy_models=False)
            self.assertEqual(plan["models"]["action"], "remain")
            self.assertEqual(Path(plan["models"]["destination"]), models.resolve())
            self.assertEqual(plan["environment"]["action"], "rebuild-at-final-path")
            self.assertFalse(plan["old_installation"]["automatic_deletion"])

    def test_success_rebuilds_then_publishes_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); source, destination, delivery, models, before_record = self._source(base)
            stack, create = self._patches(base)
            with stack:
                result = move_installation(delivery, source, destination)
            self.assertEqual(Path(result["root"]), destination.resolve())
            self.assertEqual(Path(result["model_root"]), models.resolve())
            self.assertTrue((source / "unrelated-user-file.txt").is_file())
            self.assertEqual((source / "State/installations/lic-lite.json").read_bytes(), before_record)
            self.assertTrue((destination / "State/installations/lic-lite.json").is_file())
            self.assertEqual(create.call_args.args[1], destination / "Apps/LIC-Lite/candidate-1/venv")
            self.assertIn(str(destination.resolve()), result["shortcuts"]["start_menu_app"]["arguments"])

    def test_failed_validation_never_publishes_or_damages_source(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); source, destination, delivery, _models, before_record = self._source(base)
            stack, _create = self._patches(base, validation_error=RuntimeError("destination invalid"))
            with stack, self.assertRaisesRegex(RuntimeError, "destination invalid"):
                move_installation(delivery, source, destination)
            self.assertFalse((destination / "State/installations/lic-lite.json").exists())
            self.assertEqual((source / "State/installations/lic-lite.json").read_bytes(), before_record)
            self.assertEqual((source / "unrelated-user-file.txt").read_text(), "preserve")

    def test_interrupted_move_resumes_verified_work(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); source, destination, delivery, _models, _record = self._source(base)
            stack, _create = self._patches(base)
            interrupted = {"done": False}
            def boundary(step):
                if step == "copy_application" and not interrupted["done"]:
                    interrupted["done"] = True
                    raise InterruptedError("test interruption")
            with stack:
                with self.assertRaises(InterruptedError):
                    move_installation(delivery, source, destination, boundary=boundary)
                result = move_installation(delivery, source, destination)
            journal = next((source / "State/operations").glob("move-*.json"))
            evidence = json.loads(journal.read_text())
            self.assertEqual(evidence["status"], "succeeded")
            self.assertEqual(tuple(step["name"] for step in evidence["steps"]), MOVE_STEPS)
            self.assertEqual(Path(result["root"]), destination.resolve())


if __name__ == "__main__":
    unittest.main()
