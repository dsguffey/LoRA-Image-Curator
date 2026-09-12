"""Contracts for concise recovery guidance and retained provider diagnostics."""
from __future__ import annotations

from pathlib import Path
import queue
import socket
import sys
import unittest
import urllib.error
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.component_catalog import ComponentFacts, ComponentPhase, load_component_catalog
from install_manager.acquisition import classify_acquisition_error
from install_manager.manager_ui import ManagerShell, component_detail_contract, friendly_error


class UserFacingErrorTests(unittest.TestCase):
    def test_transport_failures_keep_structured_categories_for_later_ui_work(self):
        not_found = urllib.error.HTTPError("https://example.test/a", 404, "missing", {}, None)
        server = urllib.error.HTTPError("https://example.test/a", 503, "down", {}, None)
        try:
            self.assertEqual(classify_acquisition_error(not_found), "not-found")
            self.assertEqual(classify_acquisition_error(server), "server-error")
        finally:
            not_found.close()
            server.close()
        self.assertEqual(classify_acquisition_error(
            urllib.error.URLError(socket.gaierror(11001, "host not found"))), "dns-unavailable")
        self.assertEqual(classify_acquisition_error(TimeoutError()), "timeout")

    def test_populated_target_explains_that_no_files_were_overwritten(self):
        message = friendly_error(FileExistsError("The target is populated; existing files will not be adopted or overwritten"))
        self.assertIn("already contains files", message)
        self.assertIn("new empty folder", message)

    def test_optional_ffmpeg_failure_keeps_diagnostic_out_of_card_text(self):
        delivery = ROOT / "src/install_manager/recipes/lic-components.json"
        definition = next(item for item in load_component_catalog(delivery)
                          if item.component_id == "video-extraction")
        shell = ManagerShell.__new__(ManagerShell)
        shell.events = queue.Queue()
        shell.events.put(("component-failed", "video-extraction", "unexpected worker failure"))
        shell.component_facts = {"video-extraction": ComponentFacts(ComponentPhase.PREPARING)}
        shell.component_by_id = {"video-extraction": definition}
        shell.component_paths = {}
        shell.component_recoveries = {}
        shell.operation_queue = Mock(); shell.busy = True; shell.current_page = "Other"
        shell.show_page = Mock(); shell.window = Mock(); shell.window.after = Mock()
        shell.florence_recovery = None; shell.recovery = None
        shell._load_component_recovery = Mock(return_value=None)
        shell.poll()
        facts = shell.component_facts["video-extraction"]
        self.assertEqual(facts.phase, ComponentPhase.ERROR)
        self.assertIn("FFmpeg could not be configured", facts.detail)
        self.assertEqual(facts.diagnostic, "unexpected worker failure")
        details = component_detail_contract(definition, facts)
        self.assertIn(("Diagnostic", "unexpected worker failure"), details["advanced"])


if __name__ == "__main__":
    unittest.main()
