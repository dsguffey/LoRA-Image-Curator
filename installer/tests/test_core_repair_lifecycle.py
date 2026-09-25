"""Focused managed-replacement and selected-root regression tests."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main, skipUnless
from unittest.mock import Mock, patch
import tempfile

from install_manager import core_repair, root_state
from install_manager.bootstrap import STEPS as BOOTSTRAP_STEPS, profile as delivery_profile
from install_manager.compatibility_profiles import recommended_profile
from install_manager.component_catalog import ComponentOperationQueue
from install_manager.component_catalog import ComponentFacts, ComponentPhase
from install_manager.manager_ui import ManagerShell
from install_manager.component_state import (ResourceState, component_from_manifest,
                                             empty_inventory, replace_component, write_inventory)
from install_manager.journal import OperationJournal
from install_manager.managed_install import _atomic_json
from install_manager.recovery import core_bootstrap_plan_digest


DELIVERY = Path(__file__).resolve().parents[1] / "src/install_manager"


class RepairFixture(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lic-core-repair-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "LoRA Image Curator"
        self.root.mkdir()
        self.old = self.root / "Apps/LIC-Lite/candidate-1/venv"
        (self.old / "Scripts").mkdir(parents=True)
        (self.old / "Scripts/python.exe").write_bytes(b"old")
        self.profile = recommended_profile(DELIVERY / "recipes/compatibility/profiles")
        self.channel = delivery_profile(DELIVERY)[4]
        self.record = {"schema_version": 1, "product": "LIC Lite", "state": "active",
                       "root": self.root.as_posix(), "python": str(self.old / "Scripts/python.exe"),
                       "application": str(self.root / "Apps/LIC-Lite/candidate-1/package/extracted"),
                       "manager": str(self.root / "Manager/current/LIC Install Manager.exe"),
                       "model_root": str(self.root / "Data/Models"), "channel": self.channel}
        _atomic_json(self.root / core_repair.RECORD, self.record)
        core = self.profile.component_by_id("lic-core")
        resource = ResourceState("python-environment", "python-environment", "approved-profile-venv-v1",
                                 self.record["python"], "manager-owned", "rebuild", {},
                                 {"kind": "approved-profile"}, {"version": "v1", "passed": True})
        inventory = replace_component(empty_inventory(self.root, self.profile),
                                      component_from_manifest(core, state="installed", enabled=True,
                                                              readiness={"version": "lic-core-readiness-v2", "passed": True},
                                                              resources=(resource,)))
        write_inventory(self.root, inventory)

    def add_optional(self, component_id="face-analysis"):
        from install_manager.component_state import load_inventory
        inventory = load_inventory(self.root)
        optional = component_from_manifest(self.profile.component_by_id(component_id),
                                           state="installed", enabled=True,
                                           readiness={"version": "component-resource-set-v1", "passed": True},
                                           resources=())
        write_inventory(self.root, replace_component(inventory, optional))

    def fake_build(self, *, optional_failure=False, cleanup_failure=False):
        def acquire(descriptor, *_args):
            return SimpleNamespace(cache_path=str(self.root / descriptor.filename), verified=True)
        def create(_runtime, generation, **_kwargs):
            (generation / "Scripts").mkdir(parents=True)
            python = generation / "Scripts/python.exe"
            python.write_bytes(b"new")
            return python
        patches = [patch.object(core_repair, "_acquire", side_effect=acquire),
                   patch.object(core_repair, "create_final_path_venv", side_effect=create),
                   patch.object(core_repair, "install_locked_wheels", return_value={"offline": True}),
                   patch.object(core_repair, "_validate", return_value={"passed": True,
                                                                          "path_consistency": True}),
                   patch.object(core_repair, "_optional_check", side_effect=(
                       ValueError("provider model missing") if optional_failure else None),
                       return_value={"passed": True})]
        if cleanup_failure:
            patches.append(patch.object(core_repair.shutil, "rmtree", side_effect=PermissionError("files in use")))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)


class CoreReplacementTests(RepairFixture):
    def test_repair_builds_separate_generation_then_promotes(self):
        self.fake_build()
        result = core_repair.execute(DELIVERY, self.root)
        self.assertNotEqual(result["record"]["python"], self.record["python"])
        self.assertIn("venv-repair-", result["record"]["python"])
        self.assertTrue(Path(result["record"]["python"]).is_file())
        self.assertFalse(self.old.exists())
        journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        self.assertEqual(journal.data["status"], "succeeded")
        self.assertTrue(journal.data["final_validation"]["core_ready"])

    def test_previous_optional_is_reconstructed_and_absent_one_is_not(self):
        self.add_optional("face-analysis")
        self.fake_build()
        result = core_repair.execute(DELIVERY, self.root)
        self.assertTrue(result["optional_outcomes"]["face-analysis"]["passed"])
        self.assertNotIn("body-analysis", result["optional_outcomes"])

    def test_optional_validation_failure_keeps_core_ready_and_marks_not_ready(self):
        from install_manager.component_state import load_inventory
        self.add_optional("face-analysis")
        self.fake_build(optional_failure=True)
        result = core_repair.execute(DELIVERY, self.root)
        self.assertFalse(result["optional_outcomes"]["face-analysis"]["passed"])
        inventory = load_inventory(self.root)
        self.assertTrue(inventory.get("lic-core").readiness["passed"])
        self.assertFalse(inventory.get("face-analysis").readiness["passed"])

    def test_locked_cleanup_is_pending_not_failed(self):
        self.fake_build(cleanup_failure=True)
        result = core_repair.execute(DELIVERY, self.root)
        self.assertTrue(result["cleanup_pending"])
        self.assertTrue(self.old.exists())
        self.assertEqual(OperationJournal.load(self.root / core_repair.JOURNAL).data["status"], "succeeded")

    @skipUnless(os.name == "nt", "Windows delete-sharing behavior")
    def test_live_file_handle_defers_cleanup_before_old_copy_is_touched(self):
        from ctypes import wintypes
        self.fake_build()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        open_file = kernel.CreateFileW
        open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        open_file.restype = wintypes.HANDLE
        handle = open_file(str(self.old / "Scripts/python.exe"), 0x80000000, 1,
                           None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = core_repair.execute(DELIVERY, self.root)
            self.assertTrue(result["cleanup_pending"])
            self.assertTrue((self.old / "Scripts/python.exe").is_file())
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
        self.assertTrue(core_repair.retry_cleanup(self.root))
        self.assertFalse(self.old.exists())

    def test_pause_before_build_preserves_old_and_can_resume(self):
        self.fake_build()
        with self.assertRaises(core_repair.AcquisitionCancelled):
            core_repair.execute(DELIVERY, self.root, pause_requested=lambda: True)
        self.assertTrue((self.old / "Scripts/python.exe").is_file())
        self.assertTrue(core_repair.inspect_recovery(DELIVERY, self.root)["resumable"])
        result = core_repair.execute(DELIVERY, self.root, resume=True)
        self.assertIn("venv-repair-", result["record"]["python"])

    def test_failed_pre_promotion_validation_preserves_old(self):
        self.fake_build()
        with patch.object(core_repair, "_validate", side_effect=RuntimeError("bad candidate")):
            with self.assertRaisesRegex(RuntimeError, "bad candidate"):
                core_repair.execute(DELIVERY, self.root)
        self.assertEqual(json.loads((self.root / core_repair.RECORD).read_text())["python"],
                         self.record["python"])
        self.assertTrue(self.old.exists())

    def test_failed_promotion_restores_old_record(self):
        self.fake_build()
        original = core_repair.write_inventory
        calls = 0
        def write(root, inventory):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("record busy")
            return original(root, inventory)
        with patch.object(core_repair, "write_inventory", side_effect=write):
            with self.assertRaises(PermissionError):
                core_repair.execute(DELIVERY, self.root)
        self.assertEqual(json.loads((self.root / core_repair.RECORD).read_text())["python"],
                         self.record["python"])
        self.assertTrue(self.old.exists())

    def test_optional_package_unavailable_does_not_block_core(self):
        from install_manager.component_state import load_inventory
        self.add_optional("face-analysis")
        self.fake_build()
        original = core_repair._acquire
        def unavailable(descriptor, *args):
            if descriptor.artifact_id == "opencv-contrib-python":
                raise OSError("approved source unavailable")
            return original(descriptor, *args)
        with patch.object(core_repair, "_acquire", side_effect=unavailable):
            result = core_repair.execute(DELIVERY, self.root)
        self.assertTrue(result["record"]["python"])
        self.assertFalse(load_inventory(self.root).get("face-analysis").readiness["passed"])

    def test_fully_validated_interruption_reuses_replacement(self):
        self.fake_build()
        calls = 0
        def pause_after_validation():
            nonlocal calls
            calls += 1
            return calls == 3
        with self.assertRaises(core_repair.AcquisitionCancelled):
            core_repair.execute(DELIVERY, self.root, pause_requested=pause_after_validation)
        journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        candidate = Path(journal.data["repair"]["replacement_venv"])
        self.assertTrue(candidate.is_dir())
        result = core_repair.execute(DELIVERY, self.root, resume=True)
        self.assertEqual(Path(result["record"]["python"]).parent.parent, candidate)

    def test_interrupted_partially_built_generation_is_preserved_and_replaced(self):
        self.fake_build()
        with patch.object(core_repair, "install_locked_wheels", side_effect=RuntimeError("interrupted pip")):
            with self.assertRaises(RuntimeError):
                core_repair.execute(DELIVERY, self.root)
        old_journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        incomplete = Path(old_journal.data["repair"]["replacement_venv"])
        self.assertTrue(incomplete.exists())
        result = core_repair.execute(DELIVERY, self.root, resume=True)
        self.assertNotEqual(Path(result["record"]["python"]).parent.parent, incomplete)
        self.assertTrue(incomplete.exists())

    def test_promoted_before_cleanup_can_be_resumed(self):
        self.fake_build(cleanup_failure=True)
        first = core_repair.execute(DELIVERY, self.root)
        self.assertTrue(first["cleanup_pending"])
        journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        journal.set_status("running")
        second = core_repair.execute(DELIVERY, self.root, resume=True)
        self.assertEqual(first["record"]["python"], second["record"]["python"])
        self.assertTrue(second["cleanup_pending"])

    def test_post_promotion_validation_failure_rolls_back(self):
        self.fake_build()
        checks = 0
        def validate(*args):
            nonlocal checks
            checks += 1
            if checks > 1:
                raise RuntimeError("post-activation check failed")
            return {"passed": True, "path_consistency": True}
        with patch.object(core_repair, "_validate", side_effect=validate):
            with self.assertRaises(RuntimeError):
                core_repair.execute(DELIVERY, self.root)
        self.assertEqual(json.loads((self.root / core_repair.RECORD).read_text())["python"],
                         self.record["python"])
        self.assertTrue(self.old.exists())

    def test_repair_does_not_accept_other_profile_journal(self):
        self.fake_build()
        with self.assertRaises(core_repair.AcquisitionCancelled):
            core_repair.execute(DELIVERY, self.root, pause_requested=lambda: True)
        journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        journal.data["repair"]["profile"]["profile_id"] = "another-profile"
        journal._write()
        self.assertFalse(core_repair.inspect_recovery(DELIVERY, self.root)["resumable"])
        with self.assertRaises(ValueError):
            core_repair.execute(DELIVERY, self.root, resume=True)

    def test_later_damage_can_start_another_repair_and_keeps_history(self):
        self.fake_build()
        first = core_repair.execute(DELIVERY, self.root)
        Path(first["record"]["python"]).unlink()
        second = core_repair.execute(DELIVERY, self.root)
        self.assertNotEqual(first["record"]["python"], second["record"]["python"])
        self.assertEqual(len(list((self.root / "State/operations/history").glob("core-repair-*.json"))), 1)

    def test_stale_repair_record_is_archived_before_safe_new_attempt(self):
        self.fake_build()
        with self.assertRaises(core_repair.AcquisitionCancelled):
            core_repair.execute(DELIVERY, self.root, pause_requested=lambda: True)
        journal = OperationJournal.load(self.root / core_repair.JOURNAL)
        journal.data["repair"]["profile"]["profile_id"] = "obsolete"
        journal._write()
        result = core_repair.execute(DELIVERY, self.root)
        self.assertTrue(Path(result["record"]["python"]).is_file())
        self.assertEqual(len(list((self.root / "State/operations/history").glob("core-repair-*.json"))), 1)


class SelectedRootAndQueueTests(RepairFixture):
    def test_healthy_and_damaged_activated_roots(self):
        with patch.object(root_state, "verify_extracted"), patch.object(root_state, "validate_environment",
                                                                       return_value={"passed": True}):
            self.assertEqual(root_state.inspect_selected_root(DELIVERY, self.root).action, "ready")
        (self.old / "Scripts/python.exe").unlink()
        with patch.object(root_state, "verify_extracted"), patch.object(root_state, "validate_environment",
                                                                       side_effect=FileNotFoundError("venv")):
            self.assertEqual(root_state.inspect_selected_root(DELIVERY, self.root).action, "repair")

    def test_absent_and_stale_bootstrap_are_not_resume(self):
        empty = self.root / "Empty"
        self.assertEqual(root_state.inspect_selected_root(DELIVERY, empty).action, "install")
        journal = OperationJournal.create(empty / "State/operations", "bootstrap", target_path=empty,
                                          plan_digest=core_bootstrap_plan_digest(self.channel, empty),
                                          artifacts=[self.channel], steps=BOOTSTRAP_STEPS,
                                          inputs={"identity_contract": "lic-core-v2"})
        journal.set_status("running")
        self.assertEqual(root_state.inspect_selected_root(DELIVERY, empty).action, "resume-bootstrap")
        journal.set_status("failed", failure="unrecoverable")
        self.assertEqual(root_state.inspect_selected_root(DELIVERY, empty).action, "install")

    def test_queue_never_exposes_unstarted_next_as_active(self):
        queue = ComponentOperationQueue()
        _, started = queue.submit("lic-core", "Repair")
        self.assertTrue(started)
        queue.submit("face-analysis", "Install")
        queue.complete("lic-core")
        self.assertEqual(queue.state_for("face-analysis"), "queued")
        self.assertEqual(queue.start_next().component_id, "face-analysis")
        self.assertEqual(queue.state_for("face-analysis"), "active")

    def test_queue_can_remove_unstarted_work(self):
        queue = ComponentOperationQueue()
        queue.submit("lic-core", "Repair")
        queue.submit("face-analysis", "Install")
        self.assertEqual(queue.cancel("face-analysis"), "queue-canceled")
        self.assertEqual(queue.state_for("face-analysis"), "idle")

    def test_completed_bootstrap_is_historical_not_resume(self):
        empty = self.root / "Empty"
        journal = OperationJournal.create(empty / "State/operations", "bootstrap", target_path=empty,
                                          plan_digest=core_bootstrap_plan_digest(self.channel, empty),
                                          artifacts=[self.channel], steps=BOOTSTRAP_STEPS,
                                          inputs={"identity_contract": "lic-core-v2"})
        journal.set_validation({"activation_preflight_passed": True, "activated": True})
        journal.set_status("succeeded")
        self.assertEqual(root_state.inspect_selected_root(DELIVERY, empty).action, "install")

    def test_selected_root_reloads_record_and_clears_old_paths(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.delivery = DELIVERY
        shell.root = self.root
        shell.operation_queue = ComponentOperationQueue()
        shell.application_path = Mock()
        class Variable:
            def __init__(self):
                self.value = ""
            def set(self, value):
                self.value = value
            def get(self):
                return self.value
        shell.model_path = Variable()
        shell.component_paths = {"face-analysis": Mock()}
        shell.components = ()
        shell._initial_component_facts = Mock(return_value={"face-analysis": ComponentFacts()})
        shell._load_recovery = Mock(return_value=None)
        shell._load_florence_recovery = Mock(return_value=None)
        shell._refresh_managed_resource_facts = Mock()
        other = self.root / "Another"
        with patch("install_manager.manager_ui.inspect_selected_root",
                   return_value=root_state.SelectedRootState(other, "install", "Core absent")), \
             patch("install_manager.manager_ui.inspect_model_storage", return_value=None):
            shell._reconcile_selected_root(other)
        self.assertEqual(shell.root, other.resolve())
        self.assertIsNone(shell.record)
        self.assertEqual(shell.mode, "first-run")
        shell.component_paths["face-analysis"].set.assert_called_with("")

    def test_same_page_redraw_keeps_scroll(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.current_page = "Install & Update"
        shell.canvas = Mock()
        shell.canvas.yview.return_value = (0.42, 0.62)
        shell.canvas.after_idle.side_effect = lambda callback: callback()
        shell.nav_buttons = {}
        shell.clear = Mock()
        shell.page_install_and_update = Mock()
        shell.show_page("Install & Update")
        shell.canvas.yview_moveto.assert_called_with(0.42)

    def test_new_page_resets_scroll(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.current_page = "Help"
        shell.canvas = Mock()
        shell.canvas.yview.return_value = (0.42, 0.62)
        shell.canvas.after_idle.side_effect = lambda callback: callback()
        shell.nav_buttons = {}
        shell.clear = Mock()
        shell.page_install_and_update = Mock()
        shell.show_page("Install & Update")
        shell.canvas.yview_moveto.assert_called_with(0.0)

    def test_stale_florence_journal_offers_fresh_install(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.root = self.root
        shell.delivery = DELIVERY
        shell.component_facts = {"florence-captioning": ComponentFacts()}
        recovery = SimpleNamespace(blocked=True, resumable=False, completed_steps=2,
                                   total_steps=6, summary="Florence setup belongs to another model location.",
                                   journal_path=self.root / "State/operations/florence.json")
        with patch("install_manager.manager_ui.inspect_florence_recovery", return_value=recovery):
            shell._refresh_florence_recovery()
        self.assertEqual(shell.component_facts["florence-captioning"].phase,
                         ComponentPhase.NOT_INSTALLED)
        self.assertIsNone(shell.florence_recovery)

    def test_valid_florence_journal_remains_resumable(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.root = self.root
        shell.delivery = DELIVERY
        shell.component_facts = {"florence-captioning": ComponentFacts()}
        recovery = SimpleNamespace(blocked=False, resumable=True, completed_steps=2,
                                   total_steps=6, summary="Resume Florence.")
        with patch("install_manager.manager_ui.inspect_florence_recovery", return_value=recovery):
            shell._refresh_florence_recovery()
        self.assertTrue(shell.component_facts["florence-captioning"].resumable)


if __name__ == "__main__":
    main()
