"""M1.7 activation, launch, capability and shortcut behavior tests."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from install_manager.capabilities import CapabilityDescriptor, first_run_contract, lic_capabilities
from install_manager.first_launch import friendly_error, progress_view
from install_manager.managed_install import activate, launch
from install_manager.recipe import load_recipe


class CapabilityUxTests(unittest.TestCase):
    def test_m17_recipe_explicitly_authorizes_install_and_activation(self):
        recipe = load_recipe(Path(__file__).parents[1] / "src/install_manager/recipes/lic.json")
        self.assertTrue(recipe.install_execution_allowed)
        self.assertTrue(recipe.activation_allowed)

    def test_required_are_not_choices_and_optional_default_off(self):
        items = lic_capabilities()
        self.assertTrue(all(c.default_selected for c in items if c.tier == "required"))
        self.assertTrue(all(not c.default_selected for c in items if c.tier == "optional"))
        self.assertTrue(all(c.name != c.provider for c in items))

    def test_component_manager_is_revisitable_and_actions_follow_state(self):
        contract = first_run_contract("C:/LIC")
        self.assertTrue(contract["can_revisit"])
        self.assertEqual(contract["flow"], ("install-and-update", "move-installation", "help"))
        self.assertEqual(contract["install_action"], "component-state-action")
        self.assertTrue(contract["completion"]["launch_action"])
        self.assertEqual(contract["shortcuts"], {"start_menu": True, "desktop": False})

    def test_details_extension_can_represent_future_provider_disclosures(self):
        details = {
            "affiliation_disclaimer": "Independent integration",
            "license_summary": "Commercial-use restriction applies to the model weights",
            "notices": ["LICENSE", "NOTICE"], "provenance": "reviewed release",
            "tested_identity": {"version": "1.2.3", "revision": "abc"},
            "source_hosts": ["example.invalid"], "hashes": {"sha256": "0" * 64},
            "download_bytes": 1, "install_bytes": 2, "compatibility": "known-compatible",
            "hardware": ["GPU"], "reuse_existing": "verify-before-use",
            "storage_policy": "shared", "ownership": "manager-owned",
            "status": "available", "status_reason": "qualified",
        }
        capability = CapabilityDescriptor(
            "optional-example", "Advanced example", "Adds an example capability.",
            "The example capability remains unavailable.", "optional", False, "Example provider",
            details, dependencies=("required-example",), actions=("details", "help", "use-existing"))
        self.assertEqual(capability.details, details)
        self.assertEqual(capability.dependencies, ("required-example",))
        self.assertIn("help", capability.actions)

    def test_progress_reports_truthful_terminal_state(self):
        completed = progress_view({"message": "Installed and ready", "terminal": "success"})
        self.assertEqual(completed["mode"], "idle")
        self.assertEqual(completed["value"], 100.0)
        self.assertFalse(completed["working"])

    def test_primary_error_is_actionable_without_raw_exception(self):
        message = friendly_error(RuntimeError("urlopen failed with secret low-level detail"))
        self.assertIn("download", message.lower())
        self.assertIn("try again", message.lower())
        self.assertNotIn("urlopen", message.lower())


class ActivationTests(unittest.TestCase):
    def _tree(self, base: Path):
        root, delivery, manager = base/"LIC space é", base/"delivery", base/"manager"
        (root/"State/operations").mkdir(parents=True)
        (delivery/"artifacts").mkdir(parents=True)
        (manager/"_internal").mkdir(parents=True)
        (manager/"LIC Install Manager.exe").write_bytes(b"exe")
        (manager/"_internal/payload-index.json").write_text("{}")
        return root, delivery, manager

    def _preflight(self, root: Path):
        model_root = root / "Shared/Models"
        model_root.mkdir(parents=True, exist_ok=True)
        model = SimpleNamespace(source="huggingface", repository="example/model",
                                revision="a" * 40)
        return {"criteria": {"criteria_met": True}, "channel": {"release_id": "fixture"},
                "model_root": str(model_root), "model_identity": model,
                "model_snapshot": str(model_root / "fixture-snapshot"),
                "model": {"verified": True}}

    def test_activation_blocked_before_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            root, delivery, manager = self._tree(Path(d))
            with self.assertRaises(FileNotFoundError):
                activate(delivery, root, manager)

    def test_activation_idempotent_and_launch_contract(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root, delivery, manager = self._tree(Path(d))
            app = root/"Apps/LIC-Lite/candidate-1/package/extracted"
            python = root/"Apps/LIC-Lite/candidate-1/venv/Scripts/python.exe"
            app.mkdir(parents=True); python.parent.mkdir(parents=True)
            (app/"app.py").write_text("pass"); python.write_bytes(b"python")
            preflight = self._preflight(root)
            stack.enter_context(patch("install_manager.managed_install.revalidate_testing_ready", return_value=preflight))
            def shortcut(command, **kwargs):
                Path(kwargs["env"]["IM_SHORTCUT_PATH"]).write_bytes(b"lnk")
                return Mock(returncode=0)
            env = patch.dict(os.environ, {"APPDATA": str(Path(d)/"appdata"), "USERPROFILE": str(Path(d)/"user")})
            env.start(); stack.callback(env.stop)
            first = activate(delivery, root, manager, shortcut_runner=shortcut)
            second = activate(delivery, root, manager, shortcut_runner=shortcut)
            self.assertEqual(first, second)
            self.assertEqual(first["state"], "active")
            self.assertTrue(first["choices"]["start_menu"]); self.assertFalse(first["choices"]["desktop"])
            self.assertEqual(set(first["shortcuts"]), {"start_menu_app", "start_menu_manager"})
            self.assertIn("--launch", first["shortcuts"]["start_menu_app"]["arguments"])
            self.assertIn("--manage", first["shortcuts"]["start_menu_manager"]["arguments"])
            popen = Mock(return_value="process")
            self.assertEqual(launch(root, runner=popen), "process")
            command = popen.call_args.args[0]
            self.assertEqual(Path(command[0]), python)
            self.assertEqual(command[1:4], ["-I", "-B", "-c"])
            self.assertIn("sys.path.insert(0,sys.argv[1])", command[4])
            self.assertEqual(Path(command[5]), app)
            self.assertEqual(Path(command[6]), app / "app.py")
            self.assertEqual(popen.call_args.kwargs["cwd"], app)
            self.assertNotIn("PYTHONPATH", popen.call_args.kwargs["env"])
            self.assertTrue(Path(popen.call_args.kwargs["env"]["APPDATA"]).is_relative_to(root))
            self.assertTrue(Path(popen.call_args.kwargs["env"]["LOCALAPPDATA"]).is_relative_to(root))

    def test_interrupted_activation_can_retry_without_publishing_early(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root, delivery, manager = self._tree(Path(d))
            preflight = self._preflight(root)
            stack.enter_context(patch("install_manager.managed_install.revalidate_testing_ready", return_value=preflight))
            stack.enter_context(patch("install_manager.managed_install.shortcut_locations", return_value={
                "start_menu_app": Path(d)/"start-app.lnk", "start_menu_manager": Path(d)/"start-manager.lnk",
                "desktop": Path(d)/"desk.lnk"}))
            def shortcut(command, **kwargs): Path(kwargs["env"]["IM_SHORTCUT_PATH"]).write_bytes(b"lnk")
            with self.assertRaises(InterruptedError):
                activate(delivery, root, manager, shortcut_runner=shortcut,
                         boundary=lambda step: (_ for _ in ()).throw(InterruptedError()) if step == "create_shortcuts" else None)
            self.assertFalse((root/"State/installations/lic-lite.json").exists())
            result = activate(delivery, root, manager, shortcut_runner=shortcut)
            self.assertEqual(result["state"], "active")

    def test_shortcut_choices_are_honored_and_persisted(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            root, delivery, manager = self._tree(Path(d))
            preflight = self._preflight(root)
            stack.enter_context(patch("install_manager.managed_install.revalidate_testing_ready", return_value=preflight))
            stack.enter_context(patch("install_manager.managed_install.shortcut_locations", return_value={
                "start_menu_app": Path(d)/"start-app.lnk", "start_menu_manager": Path(d)/"start-manager.lnk",
                "desktop": Path(d)/"desktop.lnk"}))
            def shortcut(command, **kwargs): Path(kwargs["env"]["IM_SHORTCUT_PATH"]).write_bytes(b"lnk")
            result = activate(delivery, root, manager, start_menu=False, desktop=True,
                              shortcut_runner=shortcut)
            self.assertEqual(result["choices"], {"start_menu": False, "desktop": True})
            self.assertEqual(set(result["shortcuts"]), {"desktop"})

    def test_failed_preflight_preserves_staged_install(self):
        with tempfile.TemporaryDirectory() as d:
            root, delivery, manager = self._tree(Path(d))
            staged = root / "Apps/LIC-Lite/candidate-1/package/extracted/preserve.txt"
            staged.parent.mkdir(parents=True)
            staged.write_text("preserve")
            with patch("install_manager.managed_install.revalidate_testing_ready",
                       side_effect=RuntimeError("readiness failed")):
                with self.assertRaisesRegex(RuntimeError, "readiness failed"):
                    activate(delivery, root, manager)
            self.assertEqual(staged.read_text(), "preserve")
            self.assertFalse((root / "State/installations/lic-lite.json").exists())

    def test_isolated_launch_can_import_verified_sibling_modules(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app = root / "Apps/LIC-Lite/candidate-1/package/extracted"
            app.mkdir(parents=True)
            (app / "sibling.py").write_text("VALUE = 'ok'\n")
            (app / "app.py").write_text("from sibling import VALUE\nfrom pathlib import Path\nPath('launched.txt').write_text(VALUE)\n")
            record = {"schema_version": 1, "state": "active", "root": root.as_posix(),
                      "python": sys.executable, "application": str(app)}
            path = root / "State/installations/lic-lite.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(record))
            process = launch(root)
            self.assertEqual(process.wait(timeout=15), 0)
            self.assertEqual((app / "launched.txt").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
