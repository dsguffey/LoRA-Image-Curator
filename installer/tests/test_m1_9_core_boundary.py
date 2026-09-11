"""M1.9 contracts for the Core/Florence boundary and explicit acquisition consent."""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from install_manager.bootstrap import STEPS as CORE_STEPS, disclosure, execute as execute_core
from install_manager.capabilities import lic_capabilities
from install_manager.component_catalog import (
    ComponentAction, ComponentFacts, ComponentOperationQueue, ComponentPhase,
    component_action, load_component_catalog, product_ready,
)
from install_manager.florence_component import (
    STEPS as FLORENCE_STEPS, execute as execute_florence,
    inspect_installed as inspect_florence_installed,
    inspect_recovery as inspect_florence_recovery,
)
from install_manager.journal import OperationJournal
from install_manager.manager_ui import ManagerShell, active_launch_contract
from install_manager.managed_move import _source_florence_state
from install_manager.managed_install import launch
from install_manager.recovery import (
    bootstrap_plan_digest, core_bootstrap_plan_digest, inspect_bootstrap_recovery,
)
from install_manager.release_channel import validate_channel


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "src/install_manager"
RECIPES = DELIVERY / "recipes"


class Variable:
    def __init__(self, value):
        self.value = str(value)

    def get(self):
        return self.value

    def set(self, value):
        self.value = str(value)


class CoreBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = load_component_catalog(RECIPES / "lic-components.json")
        cls.by_id = {item.component_id: item for item in cls.components}

    def test_new_core_lock_excludes_all_florence_and_gpu_packages(self):
        core = json.loads((RECIPES / "core-windows-x64-v2.json").read_text(encoding="utf-8"))
        names = {item["name"].casefold() for item in core["wheels"]}
        self.assertEqual(names, {"pip", "setuptools", "wheel", "packaging", "numpy", "pillow", "send2trash"})
        self.assertTrue(names.isdisjoint({"torch", "torchvision", "transformers", "timm", "einops",
                                          "huggingface-hub", "tokenizers", "safetensors"}))
        self.assertNotIn("model", json.dumps(core).casefold())

    def test_florence_delta_and_core_compose_to_historical_full_lock(self):
        core = json.loads((RECIPES / "core-windows-x64-v2.json").read_text(encoding="utf-8"))
        delta = json.loads((RECIPES / "florence-windows-nvidia-cu130-v1.json").read_text(encoding="utf-8"))
        full = json.loads((RECIPES / "base-windows-nvidia-cu130.json").read_text(encoding="utf-8"))
        names = lambda value: {item["name"].casefold() for item in value["wheels"]}
        self.assertTrue(names(core).isdisjoint(names(delta)))
        self.assertEqual(names(core) | names(delta), names(full))
        wheels = lambda value: {item["name"].casefold(): item for item in value["wheels"]}
        self.assertEqual({**wheels(core), **wheels(delta)}, wheels(full))

    def test_catalog_has_one_required_core_and_four_optional_capabilities(self):
        required = [item for item in self.components if item.required_for_readiness]
        optional = [item for item in self.components if not item.required_for_readiness]
        self.assertEqual([item.component_id for item in required], ["lic-core"])
        self.assertEqual({item.component_id for item in optional},
                         {"florence-captioning", "face-analysis", "body-analysis", "video-extraction"})
        self.assertTrue(self.by_id["florence-captioning"].managed_install)

    def test_core_disclosure_excludes_florence_and_authorizes_only_core(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = disclosure(DELIVERY, Path(directory) / "LIC")
        text = json.dumps(plan)
        self.assertEqual(plan["download_bytes_without_reuse"], 100_000_000)
        self.assertNotIn("Florence", text)
        self.assertNotIn("Hugging Face", text)
        self.assertNotIn("PyTorch", text)
        self.assertIn("Install Core", plan["summary"])
        self.assertIn("Optional AI providers are installed separately", plan["summary"])

    def test_core_readiness_and_launch_do_not_depend_on_florence(self):
        facts = {item.component_id: ComponentFacts() for item in self.components}
        facts["lic-core"] = ComponentFacts(ComponentPhase.INSTALLED, verified=True)
        self.assertTrue(product_ready(self.components, facts))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"; app.mkdir(); (app / "app.py").write_text("", encoding="utf-8")
            python = root / "venv/Scripts/python.exe"; python.parent.mkdir(parents=True); python.write_bytes(b"")
            manager = root / "Manager/current/LIC Install Manager.exe"; manager.parent.mkdir(parents=True); manager.write_bytes(b"")
            record = {"state": "active", "root": str(root), "application": str(app),
                      "python": str(python), "manager": str(manager)}
            self.assertTrue(active_launch_contract(record, root))
        captioning = next(item for item in lic_capabilities() if item.capability_id == "captioning")
        self.assertEqual(captioning.tier, "optional")
        self.assertFalse(captioning.default_selected)

    def test_old_and_new_channel_identities_remain_distinct(self):
        old = json.loads((RECIPES / "lic-candidate.json").read_text(encoding="utf-8"))
        new = json.loads((RECIPES / "lic-core-m19-candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_channel(old, qualification=True, activation_capable=True)["schema_version"], 1)
        self.assertEqual(validate_channel(new, qualification=True, activation_capable=True)["schema_version"], 2)
        self.assertIn("model_digest", old)
        self.assertNotIn("model_digest", new)
        self.assertNotEqual(old["profile"], new["profile"])

    def test_new_core_recovery_does_not_bind_optional_model_location(self):
        channel = json.loads((RECIPES / "lic-core-m19-candidate.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); install = base / "LIC"; models_a = base / "A"; models_b = base / "B"
            journal = OperationJournal.create(install / "State/operations", "bootstrap", target_path=install,
                                              plan_digest=core_bootstrap_plan_digest(channel, install),
                                              artifacts=[channel], steps=CORE_STEPS,
                                              inputs={"install_root": str(install), "identity_contract": "lic-core-v2"})
            journal.set_status("cancelled")
            before = journal.path.read_bytes()
            recovery = inspect_bootstrap_recovery(journal.path, current_install_root=install,
                                                  current_model_root=models_b)
            self.assertTrue(recovery.resumable)
            self.assertFalse(recovery.model_identity_bound)
            self.assertEqual(journal.path.read_bytes(), before)

    def test_historical_recovery_still_binds_its_authorized_model_location(self):
        channel = json.loads((RECIPES / "lic-candidate.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); install = base / "LIC"; models_a = base / "A"; models_b = base / "B"
            journal = OperationJournal.create(install / "State/operations", "bootstrap", target_path=install,
                                              plan_digest=bootstrap_plan_digest(channel, install, models_a),
                                              artifacts=[channel], steps=("old",),
                                              inputs={"install_root": str(install), "model_root": str(models_a)})
            journal.set_status("cancelled")
            before = journal.path.read_bytes()
            recovery = inspect_bootstrap_recovery(journal.path, current_install_root=install,
                                                  current_model_root=models_b)
            self.assertTrue(recovery.blocked)
            self.assertTrue(recovery.model_identity_bound)
            self.assertEqual(journal.path.read_bytes(), before)

    def test_exact_historical_step_plan_dispatches_to_legacy_executor(self):
        from install_manager.legacy_bootstrap import STEPS as LEGACY_STEPS
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "LIC"; journal = root / "State/operations/bootstrap.json"
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({"steps": [{"name": name} for name in LEGACY_STEPS]}), encoding="utf-8")
            models = Path(directory) / "models"
            with patch("install_manager.legacy_bootstrap.execute", return_value={"legacy": True}) as legacy:
                result = execute_core(DELIVERY, root, model_root=models, resume=True)
            self.assertEqual(result, {"legacy": True})
            self.assertEqual(legacy.call_args.kwargs["model_root"], models)
            self.assertTrue(legacy.call_args.kwargs["resume"])


class ExplicitAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = {item.component_id: item for item in
                          load_component_catalog(RECIPES / "lic-components.json")}

    def _shell(self, base: Path):
        shell = ManagerShell.__new__(ManagerShell)
        shell.delivery = DELIVERY
        shell.root = base / "LIC"
        shell.application_path = Variable(shell.root)
        shell.model_path = Variable(base / "Models")
        shell.component_paths = {"florence-captioning": shell.model_path}
        shell.component_facts = {name: ComponentFacts() for name in self.components}
        shell.component_facts["lic-core"] = ComponentFacts(ComponentPhase.INSTALLED, verified=True)
        shell.operation_queue = ComponentOperationQueue()
        shell.florence_recovery = None
        shell.recovery_journal_path = None
        shell.recovery = None
        shell.install_component = Mock()
        shell.show_page = Mock()
        return shell

    def test_passive_component_state_and_actions_never_invoke_acquisition(self):
        florence = self.components["florence-captioning"]
        acquire = Mock(side_effect=AssertionError("passive state must not acquire"))
        facts = ComponentFacts()
        self.assertEqual(component_action(florence, facts), ComponentAction.INSTALL)
        self.assertEqual(acquire.call_count, 0)

    def test_browse_validates_existing_florence_without_starting_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); selected = base / "existing-model"; selected.mkdir()
            shell = self._shell(base)
            with patch("install_manager.manager_ui.filedialog.askdirectory", return_value=str(selected)), \
                 patch("install_manager.manager_ui.discover_provider_candidates", return_value=(
                     SimpleNamespace(provider_root=selected, resource_path=selected),)), \
                 patch("install_manager.manager_ui.inspect_model_storage",
                       return_value={"status": "compatible", "snapshot": str(selected),
                                     "expected_bytes": 42, "message": "compatible"}) as inspect_local:
                shell.choose_component_path(self.components["florence-captioning"])
            inspect_local.assert_called_once()
            shell.install_component.assert_not_called()
            self.assertEqual(shell.component_facts["florence-captioning"].phase, ComponentPhase.PARTIAL)

    def test_florence_install_starts_only_from_explicit_primary_action(self):
        with tempfile.TemporaryDirectory() as directory:
            shell = self._shell(Path(directory))
            shell._start_optional_operation = Mock()
            shell.component_primary_action(self.components["florence-captioning"])
            shell._start_optional_operation.assert_called_once()

    def test_missing_core_remains_unready_until_explicit_install_core(self):
        core = self.components["lic-core"]
        facts = {name: ComponentFacts() for name in self.components}
        self.assertFalse(product_ready(tuple(self.components.values()), facts))
        self.assertEqual(component_action(core, facts["lic-core"]), ComponentAction.INSTALL)

    def test_optional_florence_is_blocked_until_core_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            shell = self._shell(Path(directory))
            shell.component_facts["lic-core"] = ComponentFacts()
            shell._start_florence_operation = Mock()
            shell.component_primary_action(self.components["florence-captioning"])
            shell._start_florence_operation.assert_not_called()
            self.assertIn("Install and verify", shell.component_facts["florence-captioning"].detail)

    def test_repair_is_an_explicit_action_before_any_callback(self):
        core = self.components["lic-core"]
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.REPAIR_REQUIRED)),
                         ComponentAction.REPAIR)


class FlorenceRecoveryAndReuseTests(unittest.TestCase):
    def _fake_profile(self):
        lock = lambda name: SimpleNamespace(profile=name, wheels=(), expected_inventory=(),
                                            digest=lambda: hashlib.sha256(name.encode()).hexdigest())
        model = SimpleNamespace(revision="a" * 40, digest=lambda: "d" * 64)
        return lock("core"), lock("florence"), lock("combined"), model

    def test_canceled_florence_journal_is_resumable_and_inspection_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / "LIC"; models = base / "Models"
            with patch("install_manager.florence_component.profile", return_value=self._fake_profile()):
                digest = __import__("install_manager.florence_component", fromlist=["operation_digest"]).operation_digest(
                    DELIVERY, root, models)
                journal = OperationJournal.create(root / "State/operations", "florence", target_path=root,
                                                  plan_digest=digest, artifacts=[{"component": "florence-managed-v1"}],
                                                  steps=FLORENCE_STEPS,
                                                  inputs={"install_root": str(root), "model_root": str(models)})
                journal.set_status("cancelled")
                before = journal.path.read_bytes()
                recovery = inspect_florence_recovery(DELIVERY, root, models)
            self.assertTrue(recovery.resumable)
            self.assertFalse(recovery.blocked)
            self.assertEqual(journal.path.read_bytes(), before)

    def test_changed_florence_model_path_cannot_expand_old_resume_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / "LIC"; models = base / "Models"; other = base / "Other"
            with patch("install_manager.florence_component.profile", return_value=self._fake_profile()):
                module = __import__("install_manager.florence_component", fromlist=["operation_digest"])
                journal = OperationJournal.create(root / "State/operations", "florence", target_path=root,
                                                  plan_digest=module.operation_digest(DELIVERY, root, models),
                                                  artifacts=[{"component": "florence-managed-v1"}],
                                                  steps=FLORENCE_STEPS,
                                                  inputs={"install_root": str(root), "model_root": str(models)})
                journal.set_status("cancelled")
                recovery = inspect_florence_recovery(DELIVERY, root, other)
            self.assertTrue(recovery.blocked)
            self.assertFalse(recovery.resumable)

    def test_legacy_active_record_surfaces_existing_florence_separately(self):
        record = {"channel": {"schema_version": 1}}
        with patch("install_manager.florence_component.inspect_model_storage",
                   return_value={"status": "compatible", "snapshot": "C:/model"}):
            state = inspect_florence_installed(DELIVERY, Path("C:/LIC"), Path("C:/Models"), record)
        self.assertTrue(state["legacy"])
        self.assertTrue(state["ready"])

    def test_move_classifies_legacy_and_managed_florence_for_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            component = Path(directory) / "florence.json"
            self.assertEqual(_source_florence_state({"channel": {"schema_version": 1}}, component),
                             (True, False))
            component.write_text(json.dumps({"state": "installed"}), encoding="utf-8")
            self.assertEqual(_source_florence_state({"channel": {"schema_version": 2}}, component),
                             (True, True))
            component.write_text("not-json", encoding="utf-8")
            self.assertEqual(_source_florence_state({"channel": {"schema_version": 2}}, component),
                             (False, False))

    def test_existing_verified_model_never_constructs_huggingface_downloader(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / "LIC"; models = base / "Models"
            active = root / "State/installations/lic-lite.json"; active.parent.mkdir(parents=True)
            active.write_text(json.dumps({"state": "active", "root": str(root.resolve())}), encoding="utf-8")
            snapshot = models / "snapshot"; snapshot.mkdir(parents=True)
            fake_profile = self._fake_profile()
            validation = {"passed": True}
            with patch("install_manager.florence_component.profile", return_value=fake_profile), \
                 patch("install_manager.florence_component.process_lock", return_value=nullcontext()), \
                 patch("install_manager.florence_component.validate_environment", return_value=validation), \
                 patch("install_manager.florence_component.install_locked_wheels", return_value={"installed": []}), \
                 patch("install_manager.florence_component.inspect_model_storage",
                       return_value={"status": "compatible", "snapshot": str(snapshot),
                                     "hub_root": str(models / "huggingface/hub")}), \
                 patch("install_manager.florence_component.verify_snapshot",
                       return_value={"verified": True, "revision": "a" * 40}), \
                 patch("install_manager.florence_component.run_probe",
                       return_value={"passed": True, "network_attempts": [], "denied_writes": []}), \
                 patch("install_manager.florence_component.HuggingFaceSource") as downloader:
                result = execute_florence(DELIVERY, root, models)
            downloader.assert_not_called()
            self.assertEqual(result["snapshot"], str(snapshot))

    def test_normal_launch_uses_verified_optional_component_hub_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); app = root / "app"; app.mkdir(); (app / "app.py").write_text("")
            python = root / "venv/Scripts/python.exe"; python.parent.mkdir(parents=True); python.write_bytes(b"")
            manager = root / "Manager/current/LIC Install Manager.exe"; manager.parent.mkdir(parents=True); manager.write_bytes(b"")
            old_models = root / "old-models"; new_hub = root / "selected/huggingface/hub"
            record_path = root / "State/installations/lic-lite.json"; record_path.parent.mkdir(parents=True)
            record_path.write_text(json.dumps({"state": "active", "root": str(root),
                                               "application": str(app), "python": str(python),
                                               "manager": str(manager), "model_root": str(old_models)}),
                                   encoding="utf-8")
            component = root / "State/components/florence.json"; component.parent.mkdir(parents=True)
            component.write_text(json.dumps({"state": "installed", "hub_root": str(new_hub)}),
                                 encoding="utf-8")
            runner = Mock(return_value="process")
            self.assertEqual(launch(root, runner=runner), "process")
            environment = runner.call_args.kwargs["env"]
            self.assertEqual(Path(environment["HF_HUB_CACHE"]), new_hub)
            self.assertEqual(Path(environment["HF_HOME"]), new_hub.parent)


if __name__ == "__main__":
    unittest.main()
