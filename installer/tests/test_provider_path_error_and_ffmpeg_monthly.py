"""Regression coverage for provider path budget, pip diagnosis, and retained FFmpeg."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from install_manager.active_venv import managed_venv, managed_generation, provider_venv_parent
from install_manager.artifacts import AcquiredArtifact
from install_manager.compatibility_profiles import (accepted_installed_profile,
                                                    recommended_profile)
from install_manager.core_repair import _generation
from install_manager.dependencies import (DependencyInstallError,
                                          dependency_failure_details,
                                          install_locked_wheels)
from install_manager.dependency_lock import load_dependency_lock
from install_manager.florence_component import STEPS, inspect_recovery, operation_digest
from install_manager.journal import OperationJournal
from install_manager.manager_ui import ManagerShell, provider_failure_presentation
from install_manager.component_catalog import ComponentFacts, ComponentPhase, progress_presentation
from install_manager.component_state import (component_from_manifest, empty_inventory,
                                             replace_component, write_inventory)
from install_manager.managed_resources import component_resource_status
from install_manager.provider_venv import new_generation


DELIVERY = Path(__file__).resolve().parents[1] / "src/install_manager"
PROFILES = DELIVERY / "recipes/compatibility/profiles"


class ProviderPathAndErrorTests(unittest.TestCase):
    def test_realistic_long_root_uses_short_durable_provider_generation(self):
        root = Path(r"C:\Users\John Smith\Desktop\LoRA Image Curator")
        with patch.object(Path, "home", return_value=Path(r"C:\Users\John Smith")):
            short = new_generation(root)
            self.assertEqual(short.parent, provider_venv_parent(root))
            self.assertTrue(managed_generation(root, short))
        previous = root / "Apps/LIC-Lite/candidate-1/venv-provider-123456789abc"
        deep = Path("Lib/site-packages/torch-2.13.0+cu130.dist-info/licenses/third_party/"
                    "kineto/libkineto/third_party/dynolog/third_party/prometheus-cpp/"
                    "3rdparty/civetweb/src/third_party/duktape-1.5.2/LICENSE.txt")
        self.assertGreaterEqual(len(str(previous)) - len(str(short)), 35)
        self.assertLess(len(str(short / deep)), 240)
        self.assertTrue(managed_generation(root, previous))
        self.assertFalse(managed_generation(root, root / "P/p../../outside"))

    def test_new_generation_can_activate_relaunch_and_be_repaired(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "LoRA Image Curator"
            with patch.object(Path, "home", return_value=Path(temp) / "User"):
                candidate = new_generation(root)
                python = candidate / "Scripts/python.exe"
                python.parent.mkdir(parents=True)
                python.write_bytes(b"test-interpreter")
                record = {"state": "active", "root": str(root.resolve()), "python": str(python)}
                location = root / "State/installations/lic-lite.json"
                location.parent.mkdir(parents=True)
                location.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(managed_venv(root), candidate)
                self.assertEqual(_generation(root, candidate), candidate)
                self.assertFalse(managed_generation(root, root / "Elsewhere/p123456789abc"))

    def test_pip_failure_keeps_actionable_summary_and_technical_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = load_dependency_lock(DELIVERY / "recipes/core-windows-x64-v2.json")
            artifacts = tuple(AcquiredArtifact(w.artifact, str(root / w.artifact.filename),
                                               w.artifact.expected_sha256, w.artifact.expected_size or 1,
                                               "verified", True, True, 0) for w in lock.wheels)
            def failed_child(command, **kwargs):
                kwargs["stdout"].write("ERROR: Could not install packages due to an OSError: "
                                       "[WinError 206] The filename or extension is too long: "
                                       "C:\\LIC\\P\\p123\\Lib\\site-packages\\torch-2.13.0+cu130.dist-info\\licenses\\deep\n")
                return SimpleNamespace(returncode=1)
            with patch("install_manager.dependencies.child_run", side_effect=failed_child):
                with self.assertRaises(DependencyInstallError) as raised:
                    install_locked_wheels(root / "P/p123/Scripts/python.exe", lock, artifacts,
                                          root / "Logs/florence-dependencies.log")
            error = raised.exception
            self.assertEqual(dependency_failure_details(error)["package"], "torch")
            definition = SimpleNamespace(component_id="florence-captioning", capability="Image captioning")
            for value in (error, "DependencyInstallError: " + str(error)):
                message, technical = provider_failure_presentation(definition, value)
                self.assertIn("installing dependencies", message)
                self.assertIn("path was too long", message)
                self.assertIn("Verified downloads were preserved", message)
                self.assertIn("Exit code: 1", technical)
                self.assertIn("torch-2.13.0", technical)
                self.assertIn("florence-dependencies.log", technical)

    def test_florence_failed_journal_retains_pip_error_after_relaunch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "LIC"
            models = root / "Data/Models"
            journal = OperationJournal.create(root / "State/operations", "florence",
                                              target_path=root,
                                              plan_digest=operation_digest(DELIVERY, root, models),
                                              artifacts=[], steps=STEPS,
                                              inputs={"install_root": str(root.resolve()),
                                                      "model_root": str(models.resolve())})
            journal.set_status("failed", failure='DependencyInstallError: {"kind":"dependency-install"}')
            recovered = inspect_recovery(DELIVERY, root, models)
            self.assertEqual(recovered.status, "failed")
            self.assertIn("dependency-install", recovered.failure)

    def test_monthly_ffmpeg_is_new_identity_and_old_installs_remain_accepted(self):
        current = recommended_profile(PROFILES)
        self.assertEqual(current.profile_id, "2026-09-26.1")
        archive = current.component_by_id("video-extraction").raw["resources"][0]["artifact"]
        self.assertEqual(archive["expected_sha256"],
                         "f6274bbd9c247f9e90c1bbed066b03ed4a3907cece2fb91be6dd352393936365")
        prior = accepted_installed_profile(PROFILES, {
            "profile_id": "2026-09-26",
            "digest": "235b50d01905018046cda02dd93df37efb0adba7d20a81be74f4332bfb6e492e",
        }, {"lic-core", "video-extraction"})
        self.assertEqual(prior.profile_id, "2026-09-26")
        self.assertEqual(prior.component_by_id("video-extraction").raw["resources"][0]
                         ["artifact"]["expected_sha256"],
                         "a86187b579310debe4241b4b4f4cefdac3ca294cfde76f775993445d19cf5b1d")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "LIC"
            inventory = replace_component(empty_inventory(root, prior),
                component_from_manifest(prior.component_by_id("video-extraction"),
                                        state="installed", enabled=True,
                                        readiness={"version": "test", "passed": True}, resources=()))
            write_inventory(root, inventory)
            with patch("install_manager.managed_resources._destination_state",
                       return_value="already-present"):
                status = component_resource_status(DELIVERY, root, "video-extraction")
            self.assertTrue(status["resource_complete"])
            self.assertEqual(status["available"][0].artifact["expected_sha256"],
                             "a86187b579310debe4241b4b4f4cefdac3ca294cfde76f775993445d19cf5b1d")

    def test_provider_download_does_not_pretend_per_file_bytes_are_overall_progress(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.events = queue.Queue()
        shell.events.put(("component-progress", "florence-captioning",
                          {"kind": "download", "message": "Receiving a verified file",
                           "step": 3, "total_steps": 6,
                           "downloaded_bytes": 500, "total_bytes": 1000}))
        facts = ComponentFacts(ComponentPhase.DOWNLOADING,
                               completed_bytes=80, total_bytes=100)
        shell.component_facts = {"florence-captioning": facts}
        shell.operation_queue = Mock(active=None)
        shell.current_page = "Help"
        shell.window = Mock()
        shell.poll()
        self.assertEqual(facts.detail, "[3/6] Receiving a verified file")
        self.assertIsNone(facts.total_bytes)
        self.assertEqual(progress_presentation(facts)["mode"], "indeterminate")

    def test_same_page_redraw_keeps_active_card_at_same_viewport_anchor(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.current_page = "Install & Update"
        shell.canvas = Mock()
        shell.canvas.yview.return_value = (0.42, 0.62)
        shell.canvas.winfo_rooty.return_value = 100
        shell.canvas.bbox.return_value = (0, 0, 1000, 1000)
        shell.canvas.after_idle.side_effect = lambda callback: callback()
        old, new = Mock(), Mock()
        old.winfo_exists.return_value = new.winfo_exists.return_value = True
        old.winfo_rooty.return_value, new.winfo_rooty.return_value = 200, 240
        shell.component_widgets = {"florence-captioning": {"frame": old}}
        shell.nav_buttons = {}
        shell.clear = Mock()
        shell.page_install_and_update = lambda: shell.component_widgets.update(
            {"florence-captioning": {"frame": new}})
        shell.show_page("Install & Update", anchor_id="florence-captioning")
        self.assertAlmostEqual(shell.canvas.yview_moveto.call_args.args[0], 0.46)


if __name__ == "__main__":
    unittest.main()
