"""Focused 0.7.1 cancellation and optional-provider preference contracts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.acquisition import (AcquisitionCancelled, AcquisitionPolicy,
                                         acquire_artifact)
from install_manager.artifacts import ArtifactDescriptor
from install_manager.cancellation_cleanup import (delete_unfinished_acquisitions,
                                                   unfinished_acquisitions)
from install_manager.component_catalog import (ComponentFacts, ComponentOperationQueue,
                                               ComponentPhase, OperationRequest, load_component_catalog)
from install_manager.journal import OperationJournal
from install_manager.lic_face_settings import (read_face_model_root, read_provider_location,
                                               write_provider_location)
from install_manager.manager_ui import primary_label, provider_root_from_resource
from install_manager.manager_ui import ManagerShell


class _Response:
    status = 200
    headers = {"Content-Length": "6"}
    def __init__(self): self._sent = False
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, _size):
        if self._sent: return b""
        self._sent = True
        return b"broken"


class CancellationContractsTests(unittest.TestCase):
    def test_queue_state_and_cancel_labels_distinguish_active_and_queued(self):
        catalog = {item.component_id: item for item in load_component_catalog(
            ROOT / "src/install_manager/recipes/lic-components.json")}
        queue = ComponentOperationQueue()
        queue.submit("lic-core", "Install")
        queue.submit("face-analysis", "Install")
        self.assertEqual(queue.state_for("lic-core"), "active")
        self.assertEqual(queue.state_for("face-analysis"), "queued")
        self.assertEqual(queue.cancel("lic-core"), "canceling")
        self.assertEqual(queue.cancel("face-analysis"), "queue-canceled")
        self.assertEqual(primary_label(catalog["lic-core"], ComponentFacts(ComponentPhase.CANCELING)), "Canceling…")

    def test_keep_and_delete_are_limited_to_attempt_owned_unvalidated_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install"
            journal = OperationJournal.create(root / "State/operations", "bootstrap", target_path=root,
                                              plan_digest="a" * 64, artifacts=[], steps=("one",))
            partial = root / "Cache/partial/download.partial"; partial.parent.mkdir(parents=True)
            partial.write_bytes(b"unfinished")
            verified = root / "Cache/verified/keep.whl"; verified.parent.mkdir(parents=True)
            verified.write_bytes(b"verified")
            journal.record_unvalidated_acquisition(partial)
            journal.record_unvalidated_acquisition(verified)  # rejected by bounded cleanup predicate
            self.assertEqual(unfinished_acquisitions(journal), (partial.resolve(),))
            self.assertEqual(delete_unfinished_acquisitions(journal), (partial.resolve(),))
            self.assertFalse(partial.exists())
            self.assertTrue(verified.exists())
            self.assertEqual(journal.data["unvalidated_acquisitions"], [str(verified.resolve())])

    def test_cancelled_download_keeps_only_its_explicit_partial_for_user_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = ArtifactDescriptor("fixture", "1", "fixture.bin", "https://example.com/fixture.bin",
                                            "0" * 64, "fixture", "fixture", "unknown", 6)
            observed = []
            with self.assertRaises(AcquisitionCancelled):
                acquire_artifact(descriptor, root, AcquisitionPolicy(("example.com",)), opener=lambda *_: _Response(),
                                 progress=lambda _event: (_ for _ in ()).throw(AcquisitionCancelled("stop")),
                                 partial_observer=lambda path, state: observed.append((path, state)),
                                 keep_partial_on_cancel=True)
            path = next(path for path, state in observed if state == "created")
            self.assertTrue(path.exists())
            self.assertIn((path, "unvalidated"), observed)

    def test_core_cancel_between_prepare_and_activation_never_activates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "LIC"
            request = OperationRequest("lic-core", "Install")
            shell = ManagerShell.__new__(ManagerShell)
            shell.application_path = SimpleNamespace(get=lambda: str(root))
            shell.model_path = SimpleNamespace(get=lambda: str(root / "Models"))
            shell.start_menu = SimpleNamespace(get=lambda: False)
            shell.desktop = SimpleNamespace(get=lambda: False)
            shell.component_facts = {"lic-core": ComponentFacts()}
            shell.delivery = ROOT / "src/install_manager"
            shell.operation_queue = ComponentOperationQueue()
            shell.operation_queue.submit("lic-core", "Install")
            shell.recovery_journal_path = None
            shell.recovery = None
            shell.root = root
            shell.busy = False
            shell.events = queue.Queue()
            shell.show_page = Mock()
            shell.prepare = Mock(side_effect=lambda *_args: request.token.request())
            shell.activate = Mock()
            class ImmediateThread:
                def __init__(self, target, daemon): self.target = target
                def start(self): self.target()
            with patch("install_manager.manager_ui.validate_root"), \
                 patch("install_manager.manager_ui.threading.Thread", ImmediateThread):
                shell._start_core_operation(request, resume=False)
            shell.activate.assert_not_called()
            self.assertEqual(shell.events.get_nowait()[0], "component-canceled")


class ProviderPreferenceTests(unittest.TestCase):
    def test_all_optional_provider_preferences_preserve_unrelated_lic_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            appdata = Path(temporary)
            target = appdata / "LoRAImageCurator/settings.json"; target.parent.mkdir()
            target.write_text(json.dumps({"video_last_destination": "keep", "other": {"value": 1}}), encoding="utf-8")
            roots = {
                "florence-captioning": r"D:\Models\Florence",
                "face-analysis": r"D:\Models\Face",
                "body-analysis": r"D:\Models\Body",
                "video-extraction": r"D:\Tools\FFmpeg",
            }
            for component_id, value in roots.items():
                write_provider_location(appdata, component_id, value)
            self.assertEqual(read_face_model_root(appdata), roots["face-analysis"])
            for component_id, value in roots.items():
                self.assertEqual(read_provider_location(appdata, component_id), value)
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(raw["video_last_destination"], "keep")
            self.assertEqual(raw["other"], {"value": 1})

    def test_inventory_resource_projects_to_the_human_provider_folder(self):
        self.assertEqual(provider_root_from_resource("body-analysis", r"D:\Models\Body\pose_landmarker_full.task"),
                         Path(r"D:\Models\Body"))
        self.assertEqual(provider_root_from_resource("face-analysis", r"D:\Models\Face\yunet.onnx"),
                         Path(r"D:\Models\Face"))
        self.assertEqual(provider_root_from_resource("florence-captioning", r"D:\Models\snapshot"),
                         Path(r"D:\Models\snapshot"))


if __name__ == "__main__":
    unittest.main()
