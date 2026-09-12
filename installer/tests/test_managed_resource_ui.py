"""Focused presentation contracts for the managed-resource Manager UI."""
from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.acquisition import AcquisitionFailure
from install_manager.component_catalog import ComponentFacts, ComponentPhase, ComponentOperationQueue
from install_manager.manager_ui import (ManagerShell, acquisition_error_presentation,
                                        clipboard_actions, component_status_text)


class ErrorPresentationTests(unittest.TestCase):
    def test_each_structured_download_failure_has_plain_card_copy_and_diagnostics(self):
        expected = {
            "network-unavailable": "No internet connection",
            "dns-unavailable": "could not reach",
            "not-found": "404",
            "server-error": "temporarily unavailable",
            "timeout": "timed out",
            "tls-certificate": "secure connection",
            "http-error": "HTTP error",
            "verification-failure": "did not match the approved file",
        }
        for category, phrase in expected.items():
            with self.subTest(category=category):
                card, technical = acquisition_error_presentation(
                    AcquisitionFailure(category, "fixture-artifact", "fixture.example", 2, "HTTP 503"))
                self.assertIn(phrase, card)
                self.assertIn("Category:", technical)
                self.assertIn("fixture-artifact", technical)

    def test_pause_wording_explains_retained_work(self):
        definition = type("Definition", (), {"managed_install": True})()
        text = component_status_text(definition, ComponentFacts(ComponentPhase.PARTIAL, resumable=True))
        self.assertIn("Resume", text)
        self.assertIn("preserved", text)
        self.assertNotEqual(text, "Stopped")


class GlobalImportUiTests(unittest.TestCase):
    def test_normal_cards_do_not_offer_per_provider_import_details_or_help(self):
        source = inspect.getsource(ManagerShell._render_component_card)
        self.assertNotIn("import_component_resources", source)
        self.assertNotIn("show_component_details", source)
        self.assertNotIn("show_help(", source)
        global_source = inspect.getsource(ManagerShell._render_global_import)
        self.assertIn("import_all_resources", global_source)
        self.assertIn("original files are left unchanged", global_source.casefold())

    def test_global_import_completion_refreshes_resources_without_rebuilding_progress(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.global_import_active = True
        shell.global_import_progress = 0.0
        shell.global_import_detail = ""
        shell.global_import_diagnostic = ""
        status = Mock()
        shell.global_import_widgets = {"status": status}
        shell.operation_queue = ComponentOperationQueue()
        shell.operation_queue.submit("__global_import__", "Import")
        shell.busy = True
        shell._refresh_managed_resource_facts = Mock()
        shell.current_page = "Other"
        shell.show_page = Mock()
        shell._handle_global_import_event("global-imported", {
            "imported": ("one",), "already_present": (), "invalid": 0, "unrecognized": 2,
        })
        self.assertFalse(shell.global_import_active)
        self.assertFalse(shell.busy)
        self.assertIn("Import complete", shell.global_import_detail)
        shell._refresh_managed_resource_facts.assert_called_once()
        status.assert_not_called()  # terminal rendering occurs once, after the stable result exists

    def test_clipboard_actions_distinguish_informational_and_editable_text(self):
        self.assertEqual(clipboard_actions(False), ("Copy", "Select All"))
        self.assertEqual(clipboard_actions(True), ("Cut", "Copy", "Paste", "Select All"))

    def test_help_centralizes_import_storage_provider_and_troubleshooting_reference(self):
        source = inspect.getsource(ManagerShell.page_help)
        for expected in (
            "IMPORTING EXISTING RESOURCES",
            "DOWNLOADS AND MANAGED STORAGE",
            "OPTIONAL FEATURES",
            "TROUBLESHOOTING",
            "Data\\\\Downloads",
            "deletes the originals",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("show_help(", inspect.getsource(ManagerShell._render_component_card))

    def test_import_progress_updates_its_existing_controls_without_rebuilding_the_page(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.global_import_active = True
        shell.global_import_progress = 0.0
        shell.global_import_detail = ""
        shell.global_import_diagnostic = ""
        status = Mock()
        progress = Mock()
        shell.global_import_widgets = {"status": status, "progress": progress}
        shell.current_page = "Install & Update"
        shell.show_page = Mock()
        shell._handle_global_import_event("global-import-progress", {
            "phase": "copying", "message": "Copying SFace…", "current": 2, "total": 4, "name": "SFace",
        })
        self.assertIn("Copying", shell.global_import_detail)
        self.assertEqual(shell.global_import_progress, 50.0)
        status.set.assert_called_once_with("Copying SFace…")
        progress.configure.assert_called_with(mode="determinate", value=50.0)
        shell.show_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
