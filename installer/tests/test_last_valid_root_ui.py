"""Focused regression tests for remembered-root and asynchronous Browse state."""
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from install_manager.component_catalog import ComponentFacts
from install_manager.delivery_main import startup_root
from install_manager.last_valid_root import read_last_valid_root, write_last_valid_root
from install_manager.manager_ui import ManagerShell
from install_manager.root_state import SelectedRootState
from install_manager.root_validation import RootValidation


class LastValidRootTests(unittest.TestCase):
    def test_only_explicit_success_writes_the_pointer_and_relaunch_reads_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pointer = base / "state/last-valid-root.json"
            first, second = base / "First LIC", base / "Second LIC"
            first.mkdir(); second.mkdir()
            self.assertIsNone(read_last_valid_root(pointer))
            write_last_valid_root(first, pointer)
            self.assertEqual(read_last_valid_root(pointer), first.resolve())
            # Merely selecting a second root cannot change the durable pointer.
            validation = RootValidation()
            validation.begin(second)
            self.assertEqual(read_last_valid_root(pointer), first.resolve())
            write_last_valid_root(second, pointer)
            self.assertEqual(read_last_valid_root(pointer), second.resolve())

    def test_missing_remembered_root_is_retained_for_revalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "LIC"
            root.mkdir()
            pointer = base / "last-valid-root.json"
            write_last_valid_root(root, pointer)
            root.rmdir()
            self.assertEqual(read_last_valid_root(pointer), root.resolve())
            with self.assertRaises(ValueError):
                write_last_valid_root(root, pointer)

    def test_invalid_pointer_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            pointer = Path(directory) / "state.json"
            pointer.write_text('{"schema_version": 1, "root": "relative"}', encoding="utf-8")
            self.assertIsNone(read_last_valid_root(pointer))

    def test_startup_restores_remembered_root_without_overriding_explicit_cli_root(self):
        remembered = Path(r"C:\Users\Person\Desktop\LoRA Image Curator")
        explicit = Path(r"D:\Chosen\LoRA Image Curator")
        with patch("install_manager.delivery_main.read_last_valid_root", return_value=remembered), \
             patch("install_manager.delivery_main.default_install_root", return_value=Path(r"C:\Default")):
            self.assertEqual(startup_root(None), remembered)
            self.assertEqual(startup_root(explicit), explicit)
            self.assertEqual(startup_root(explicit, manage_mode=True), remembered)
            self.assertEqual(startup_root(None, allow_remembered=False), Path(r"C:\Default"))

    def test_late_root_a_result_cannot_replace_root_b(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            validation = RootValidation()
            a = validation.begin(base / "A")
            b = validation.begin(base / "B")
            self.assertFalse(validation.accept(a, base / "A"))
            self.assertTrue(validation.in_progress)
            self.assertTrue(validation.accept(b, base / "B"))
            self.assertFalse(validation.in_progress)

    def test_browse_paints_selection_and_locks_before_starting_worker(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.review_mode = False
        shell.operation_queue = SimpleNamespace(active=None)
        shell.root_validation = RootValidation()
        shell.application_path = Mock()
        shell.components = (SimpleNamespace(component_id="lic-core"),)
        shell.component_facts = {"lic-core": ComponentFacts()}
        shell.current_page = "Install & Update"
        shell.show_page = Mock()
        shell.window = SimpleNamespace(after=Mock())
        shell.events = queue.Queue()
        selected = Path(tempfile.gettempdir()) / "Selected LIC"
        ManagerShell._begin_root_validation(shell, selected)
        self.assertTrue(shell.validation_in_progress)
        shell.application_path.set.assert_called_once_with(str(selected.resolve()))
        shell.show_page.assert_called_once_with("Install & Update", anchor_id="lic-core")
        self.assertTrue(shell.events.empty())
        shell.window.after.assert_called_once()
        self.assertEqual(shell.window.after.call_args.args[0], 25)

    def test_failed_validation_does_not_replace_last_known_good(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            good = base / "Good"; good.mkdir()
            bad = base / "Missing"
            pointer = base / "last-valid-root.json"
            write_last_valid_root(good, pointer)
            shell = ManagerShell.__new__(ManagerShell)
            shell.root_validation = RootValidation()
            generation = shell.root_validation.begin(bad)
            shell.validation_in_progress = True
            shell.component_facts = {"lic-core": ComponentFacts()}
            shell.current_page = "Install & Update"
            shell._reconcile_selected_root = Mock()
            shell._refresh_managed_resource_facts = Mock()
            shell.show_page = Mock()
            shell.components = (SimpleNamespace(component_id="lic-core"),)
            shell._finish_root_validation(generation, SelectedRootState(bad.resolve(), "install", "Core is absent."))
            self.assertEqual(read_last_valid_root(pointer), good.resolve())
            self.assertIn("does not exist", shell.component_facts["lic-core"].detail)
            self.assertFalse(shell.validation_in_progress)

    def test_stale_completion_cannot_reconcile_or_persist(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.root_validation = RootValidation()
        first = Path(tempfile.gettempdir()) / "First"
        second = Path(tempfile.gettempdir()) / "Second"
        old_generation = shell.root_validation.begin(first)
        shell.root_validation.begin(second)
        shell._reconcile_selected_root = Mock()
        with patch("install_manager.manager_ui.write_last_valid_root") as writer:
            shell._finish_root_validation(old_generation, SelectedRootState(first.resolve(), "ready", "Ready"))
        shell._reconcile_selected_root.assert_not_called()
        writer.assert_not_called()

    def test_successful_completion_reconciles_then_persists_and_unlocks(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.root_validation = RootValidation()
        selected = Path(tempfile.gettempdir()) / "Validated"
        generation = shell.root_validation.begin(selected)
        shell.validation_in_progress = True
        shell._reconcile_selected_root = Mock()
        shell._refresh_managed_resource_facts = Mock()
        shell.show_page = Mock()
        shell.current_page = "Install & Update"
        shell.model_path = Mock()
        shell.component_paths = {}
        state = SelectedRootState(selected.resolve(), "ready", "Ready")
        snapshot = {"record": {"state": "active"}, "plan": {}, "model_root": selected / "Data/Models",
                    "journal_path": None, "bootstrap_recovery": None, "florence_recovery": None,
                    "component_recoveries": {}, "model_evidence": None,
                    "download_summaries": {}, "resource_statuses": {},
                    "facts": {"lic-core": ComponentFacts()}}
        with patch("install_manager.manager_ui.write_last_valid_root") as writer:
            shell._finish_root_validation(generation, state, snapshot)
        shell._reconcile_selected_root.assert_not_called()
        self.assertEqual(shell.root, selected.resolve())
        writer.assert_called_once_with(selected.resolve())
        self.assertFalse(shell.validation_in_progress)
        shell.show_page.assert_called_once_with("Install & Update", anchor_id="lic-core")

    def test_unsafe_actions_are_blocked_while_validating(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.validation_in_progress = True
        shell.launch = Mock()
        shell.review_mode = False
        shell.import_all_resources()
        shell.component_primary_action(SimpleNamespace(component_id="lic-core"))
        shell.launch_lic()
        shell.launch.assert_not_called()

    def test_action_enablement_tracks_validation_and_actual_core_readiness(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.review_mode = False
        shell.component_facts = {"lic-core": ComponentFacts()}
        core = SimpleNamespace(component_id="lic-core")
        provider = SimpleNamespace(component_id="face-analysis")
        shell.validation_in_progress = True
        self.assertFalse(shell._action_enabled(core, shell.component_facts["lic-core"]))
        self.assertFalse(shell._action_enabled(provider, ComponentFacts()))
        shell.validation_in_progress = False
        self.assertTrue(shell._action_enabled(core, shell.component_facts["lic-core"]))
        self.assertFalse(shell._action_enabled(provider, ComponentFacts()))
        shell.component_facts["lic-core"].verified = True
        self.assertTrue(shell._action_enabled(provider, ComponentFacts()))


if __name__ == "__main__":
    unittest.main()
