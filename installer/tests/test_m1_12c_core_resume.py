"""Focused Core resume/recovery contracts from the M1.12C human test."""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.bootstrap_layout import validate_root
from install_manager.component_catalog import ComponentFacts, ComponentPhase
from install_manager.manager_ui import ManagerShell, show_installed
from install_manager.first_launch import show_manager
from install_manager.recovery import BootstrapRecovery


class CoreResumeRecoveryTests(unittest.TestCase):
    def test_deleted_target_refuses_resume_without_creating_a_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deleted-target"
            cache = Path(temporary) / "verified-cache"; cache.mkdir()
            marker = cache / "approved-wheel.whl"; marker.write_bytes(b"preserve")
            with self.assertRaisesRegex(FileNotFoundError, "no longer exists"):
                validate_root(root, delivery=ROOT, resume=True)
            self.assertFalse(root.exists())
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_activation_failure_after_succeeded_bootstrap_is_not_suppressed(self):
        shell = ManagerShell.__new__(ManagerShell)
        root = Path("C:/fixture")
        shell.events = queue.Queue()
        shell.events.put(("component-failed", "lic-core", "shortcut creation failed"))
        shell.component_facts = {"lic-core": ComponentFacts(ComponentPhase.PREPARING)}
        shell.operation_queue = Mock()
        shell.busy = True
        shell.current_page = "Other"
        shell.show_page = Mock()
        shell.window = Mock()
        shell.window.after = Mock()
        shell.florence_recovery = None
        shell.component_recoveries = {}
        shell.recovery = BootstrapRecovery(root / "State/operations/bootstrap.json", "succeeded",
                                           root, root / "models", root, root / "models",
                                           8, 8, model_identity_bound=False, recipe_generation="core-v2")
        shell._refresh_recovery_state = Mock()
        shell.poll()
        facts = shell.component_facts["lic-core"]
        self.assertEqual(facts.phase, ComponentPhase.PARTIAL)
        self.assertTrue(facts.resumable)
        self.assertIn("activation could not finish", facts.detail)
        shell.operation_queue.complete.assert_called_once_with("lic-core")

    def test_successful_core_event_clears_live_recovery(self):
        # The previously added completion path must not retain a stale Resume state.
        shell = ManagerShell.__new__(ManagerShell)
        shell.recovery = object()
        shell.recovery_journal_path = Path("C:/fixture/State/operations/bootstrap.json")
        shell.component_facts = {"lic-core": ComponentFacts(ComponentPhase.PREPARING)}
        shell.events = queue.Queue()
        shell.events.put(("component-installed", "lic-core", {"root": "C:/fixture"}))
        shell.operation_queue = Mock(); shell.busy = True; shell.current_page = "Other"
        shell.show_page = Mock(); shell.window = Mock(); shell.window.after = Mock()
        shell.component_recoveries = {}; shell.florence_recovery = None
        shell.poll()
        self.assertIsNone(shell.recovery)
        self.assertIsNone(shell.recovery_journal_path)
        self.assertTrue(shell.component_facts["lic-core"].verified)

    def test_installed_manager_keeps_core_bootstrap_callbacks_for_new_installation(self):
        prepare, activate = Mock(), Mock()
        with patch("install_manager.first_launch.show_installed") as show_installed:
            show_manager(Path("C:/delivery"), Path("C:/existing"), Mock(), Mock(),
                         prepare=prepare, activate=activate)
        self.assertIs(show_installed.call_args.kwargs["prepare"], prepare)
        self.assertIs(show_installed.call_args.kwargs["activate"], activate)

    def test_installed_shell_receives_core_bootstrap_callbacks(self):
        prepare, activate = Mock(), Mock()
        record = {"state": "active"}
        with patch("install_manager.manager_ui.ManagerShell") as shell_type:
            show_installed(Path("C:/delivery"), Path("C:/existing"), Mock(), Mock(),
                           record=record, prepare=prepare, activate=activate)
        self.assertIs(shell_type.call_args.kwargs["prepare"], prepare)
        self.assertIs(shell_type.call_args.kwargs["activate"], activate)
        shell_type.return_value.run.assert_called_once_with()

    def test_missing_callbacks_explain_the_problem_without_attempting_setup(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.prepare = None
        shell.activate = None
        shell.application_path = Mock(); shell.application_path.get.return_value = "C:/new-lic"
        shell.model_path = Mock(); shell.model_path.get.return_value = "C:/new-lic/Shared/Models"
        shell.component_facts = {"lic-core": ComponentFacts(ComponentPhase.NOT_INSTALLED)}
        shell.operation_queue = Mock(); shell.show_page = Mock()
        shell._start_core_operation(Mock(), resume=False)
        facts = shell.component_facts["lic-core"]
        self.assertEqual(facts.phase, ComponentPhase.ERROR)
        self.assertIn("cannot start a new installation", facts.detail)
        self.assertIn("callbacks", facts.diagnostic)
        shell.operation_queue.complete.assert_called_once_with("lic-core")
