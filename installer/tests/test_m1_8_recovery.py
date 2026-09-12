"""Focused regression tests for cancelled-setup browsing and recovery."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from install_manager.bootstrap import BootstrapIdentityMismatch, execute
from install_manager.component_catalog import (ComponentAction, ComponentFacts, ComponentPhase,
                                                component_action, load_component_catalog,
                                                validate_existing_selection)
from install_manager.journal import OperationJournal
from install_manager.manager_ui import ManagerShell, identity_controls_editable
from install_manager.recovery import (inspect_bootstrap_recovery, restored_recovery_locations,
                                      validate_new_recovery_target, bootstrap_plan_digest)
from install_manager.storage import default_model_root
from test_m1_4 import BootstrapTests


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/install_manager/recipes/lic-components.json"


def _journal(root: Path, model_root: Path, *, status="cancelled") -> Path:
    channel = {"release_id": "fixture"}
    journal = OperationJournal.create(
        root / "State/operations", "bootstrap", target_path=root,
        plan_digest=bootstrap_plan_digest(channel, root, model_root), artifacts=[channel],
        steps=("acquire_runtime", "acquire_dependencies"),
        inputs={"install_root": str(root.resolve()), "model_root": str(model_root.resolve())},
    )
    journal.set_step("acquire_runtime", "completed", evidence={"sha256": "fixture"})
    journal.set_status(status, failure="AcquisitionCancelled: fixture")
    return journal.path


class _Value:
    def __init__(self, value):
        self.value = str(value)

    def get(self):
        return self.value

    def set(self, value):
        self.value = str(value)


class RecoveryStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = {item.component_id: item for item in load_component_catalog(CATALOG)}

    def test_cancelled_matching_identity_is_resume_setup_not_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "LIC"
            models = default_model_root(root)
            path = _journal(root, models)
            recovery = inspect_bootstrap_recovery(
                path, current_install_root=root, current_model_root=models)
            facts = ComponentFacts(ComponentPhase.PARTIAL, resumable=recovery.resumable,
                                   recovery_blocked=recovery.blocked)
            self.assertTrue(recovery.resumable)
            self.assertEqual(recovery.completed_steps, 1)
            self.assertEqual(component_action(self.components["lic-core"], facts),
                             ComponentAction.RESUME)
            self.assertEqual(ComponentAction.RESUME.value, "Resume setup")

    def test_model_root_mismatch_is_actionable_and_journal_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, recorded, current = base / "LIC", base / "models-a", base / "models-b"
            path = _journal(root, recorded)
            before = path.read_bytes()
            recovery = inspect_bootstrap_recovery(
                path, current_install_root=root, current_model_root=current)
            self.assertTrue(recovery.blocked)
            self.assertFalse(recovery.resumable)
            self.assertEqual(recovery.mismatch_fields, ("model location",))
            self.assertIn("different model location", recovery.summary)
            self.assertEqual((recovery.recorded_model_root, recovery.current_model_root),
                             (recorded.resolve(), current.resolve()))
            facts = ComponentFacts(ComponentPhase.PARTIAL, recovery_blocked=True)
            self.assertEqual(component_action(self.components["lic-core"], facts), ComponentAction.NONE)
            self.assertEqual(path.read_bytes(), before)

    def test_shell_blocks_mismatched_resume_before_prepare_is_invoked(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, recorded, current = base / "LIC", base / "models-a", base / "models-b"
            path = _journal(root, recorded)
            before = path.read_bytes()
            shell = ManagerShell.__new__(ManagerShell)
            shell.recovery_journal_path = path
            shell.application_path = _Value(root)
            shell.model_path = _Value(current)
            shell.component_facts = {"lic-core": ComponentFacts(
                ComponentPhase.PARTIAL, resumable=True)}
            shell.prepare = Mock()
            shell.show_page = Mock()
            shell.recovery = None
            shell.component_primary_action(self.components["lic-core"])
            shell.prepare.assert_not_called()
            shell.show_page.assert_called_once_with("Install & Update")
            self.assertTrue(shell.recovery.blocked)
            self.assertEqual(path.read_bytes(), before)

    def test_restore_recorded_locations_immediately_makes_recovery_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, recorded = base / "LIC", base / "models-a"
            path = _journal(root, recorded)
            mismatched = inspect_bootstrap_recovery(
                path, current_install_root=root, current_model_root=base / "models-b")
            before = path.read_bytes()
            install, models = restored_recovery_locations(mismatched)
            restored = inspect_bootstrap_recovery(
                path, current_install_root=install, current_model_root=models)
            self.assertTrue(restored.resumable)
            self.assertFalse(restored.blocked)
            self.assertEqual(path.read_bytes(), before)

    def test_shell_restore_changes_only_ui_values_and_needs_no_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, recorded = base / "LIC", base / "models-a"
            path = _journal(root, recorded)
            shell = ManagerShell.__new__(ManagerShell)
            shell.recovery_journal_path = path
            shell.application_path = _Value(root)
            shell.model_path = _Value(base / "models-b")
            shell.component_facts = {"lic-core": ComponentFacts()}
            shell.delivery = base / "delivery"
            shell.model_evidence = None
            shell.show_page = Mock()
            shell.recovery = inspect_bootstrap_recovery(
                path, current_install_root=root, current_model_root=base / "models-b")
            before = path.read_bytes()
            with patch("install_manager.manager_ui.inspect_model_storage",
                       return_value={"status": "missing"}):
                shell.restore_recorded_locations()
            self.assertEqual(Path(shell.application_path.get()), root.resolve())
            self.assertEqual(Path(shell.model_path.get()), recorded.resolve())
            self.assertTrue(shell.recovery.resumable)
            shell.show_page.assert_called_once_with("Install & Update")
            self.assertEqual(path.read_bytes(), before)

    def test_application_root_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, models = base / "LIC-A", base / "models"
            path = _journal(root, models)
            recovery = inspect_bootstrap_recovery(
                path, current_install_root=base / "LIC-B", current_model_root=models)
            self.assertEqual(recovery.mismatch_fields, ("application location",))
            self.assertIn("different application location", recovery.summary)

    def test_new_installation_requires_distinct_empty_root_and_preserves_old_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old, models = base / "old", base / "models"
            path = _journal(old, models)
            recovery = inspect_bootstrap_recovery(
                path, current_install_root=old, current_model_root=models)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "different empty location"):
                validate_new_recovery_target(old, recovery)
            populated = base / "populated"; populated.mkdir(); (populated / "keep.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                validate_new_recovery_target(populated, recovery)
            fresh = validate_new_recovery_target(base / "fresh", recovery)
            self.assertEqual(fresh, (base / "fresh").resolve())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual((populated / "keep.txt").read_text(), "keep")

    def test_active_core_phases_lock_identity_controls(self):
        for phase in (ComponentPhase.PREPARING, ComponentPhase.DOWNLOADING,
                      ComponentPhase.VERIFYING, ComponentPhase.INSTALLING,
                      ComponentPhase.CANCELING):
            self.assertFalse(identity_controls_editable(phase), phase)
        for phase in (ComponentPhase.NOT_INSTALLED, ComponentPhase.PARTIAL,
                      ComponentPhase.ERROR, ComponentPhase.REPAIR_REQUIRED):
            self.assertTrue(identity_controls_editable(phase), phase)

    def test_active_core_handlers_do_not_open_identity_pickers(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.component_facts = {"lic-core": ComponentFacts(ComponentPhase.DOWNLOADING)}
        with patch("install_manager.manager_ui.filedialog.askdirectory") as picker:
            shell.choose_application()
            shell.choose_models()
        picker.assert_not_called()

    def test_incomplete_face_pair_is_not_accepted_as_a_legacy_provider(self):
        definition = self.components["face-analysis"]
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory) / "models/face"
            pack.mkdir(parents=True)
            (pack / "det.onnx").write_bytes(b"fixture")
            facts = validate_existing_selection(definition, pack)
            self.assertEqual(facts.phase, ComponentPhase.INCOMPATIBLE)
            self.assertEqual(component_action(definition, facts), ComponentAction.REPAIR)
            self.assertEqual(facts.selected_path, str(pack.resolve()))
            self.assertFalse(facts.verified)

    def test_genuine_component_states_retain_distinct_actions(self):
        core = self.components["lic-core"]
        self.assertEqual(component_action(core, ComponentFacts()), ComponentAction.INSTALL)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.REPAIR_REQUIRED)),
                         ComponentAction.REPAIR)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.INSTALLED, verified=True)),
                         ComponentAction.CHECK_UPDATES)
        self.assertEqual(component_action(core, ComponentFacts(
            ComponentPhase.UPDATE_AVAILABLE, verified=True, approved_update_available=True)),
            ComponentAction.UPDATE)


class BootstrapRecoveryIntegrationTests(unittest.TestCase):
    def test_new_core_resume_ignores_optional_model_selection_without_mutating_journal(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            base = Path(directory)
            delivery, target = BootstrapTests().fixture(base, stack)
            models_a, models_b = base / "models-a", base / "models-b"
            requested = {"value": False}

            def boundary(step):
                if step == "acquire_runtime":
                    requested["value"] = True

            from install_manager.acquisition import AcquisitionCancelled
            with self.assertRaises(AcquisitionCancelled):
                execute(delivery, target, model_root=models_a, boundary=boundary,
                        cancel_requested=lambda: requested["value"])
            path = target / "State/operations/bootstrap.json"
            before = path.read_bytes()
            result = execute(delivery, target, model_root=models_b, resume=True)
            self.assertTrue(result["activation_preflight_passed"])
            self.assertNotEqual(path.read_bytes(), before)
            completed = OperationJournal.load(path)
            first = next(step for step in completed.data["steps"]
                         if step["name"] == "acquire_runtime")
            self.assertTrue(first["evidence"]["reused"])
            self.assertEqual(first["evidence"]["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
