"""Focused M1.12A provider-folder, recovery, and in-place-update contracts."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.component_catalog import ComponentFacts, ComponentPhase, load_component_catalog
from install_manager.component_operations import STEPS, inspect_recovery
from install_manager.provider_discovery import discover_provider_candidates


CATALOG = ROOT / "src/install_manager/recipes/lic-components.json"


class ProviderFolderDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_id = {item.component_id: item for item in load_component_catalog(CATALOG)}

    def test_mediapipe_discovers_direct_and_child_folder_without_network(self):
        content = b"approved-pose"
        definition = replace(self.by_id["body-analysis"], identity={"sha256": hashlib.sha256(content).hexdigest()})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); child = root / "MediaPipe"; child.mkdir()
            (child / "pose_landmarker_full.task").write_bytes(content)
            candidates = discover_provider_candidates(ROOT / "src/install_manager", definition, root)
        self.assertEqual([(item.provider_root.name, item.resource_path.name) for item in candidates],
                         [("MediaPipe", "pose_landmarker_full.task")])

    def test_face_discovers_exact_same_folder_pair_and_never_combines_folders(self):
        yunet, sface = b"yunet", b"sface"
        definition = replace(self.by_id["face-analysis"], artifacts=(
            {"filename": "face_detection_yunet_2026may.onnx", "size": len(yunet), "sha256": hashlib.sha256(yunet).hexdigest()},
            {"filename": "face_recognition_sface_2021dec.onnx", "size": len(sface), "sha256": hashlib.sha256(sface).hexdigest()},
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); valid = root / "Face Analysis"; valid.mkdir()
            (valid / "face_detection_yunet_2026may.onnx").write_bytes(yunet)
            (valid / "face_recognition_sface_2021dec.onnx").write_bytes(sface)
            candidates = discover_provider_candidates(ROOT / "src/install_manager", definition, root)
            self.assertEqual([item.provider_root for item in candidates], [valid.resolve()])
            split = root / "split"; split.mkdir()
            (split / "face_detection_yunet_2026may.onnx").write_bytes(yunet)
            self.assertEqual(discover_provider_candidates(ROOT / "src/install_manager", definition, split), ())


class InPlaceUpdateTests(unittest.TestCase):
    def test_progress_can_update_a_rendered_card_without_page_rebuild(self):
        # The controller boundary is intentionally tested without a Tk display.
        from install_manager.manager_ui import ManagerShell
        shell = ManagerShell.__new__(ManagerShell)
        definition = replace(load_component_catalog(CATALOG)[1], component_id="test-provider")
        shell.component_by_id = {"test-provider": definition}
        shell.component_facts = {"test-provider": ComponentFacts(ComponentPhase.DOWNLOADING, completed_bytes=5, total_bytes=10)}
        status, progress, button = Mock(), Mock(), Mock()
        shell.component_widgets = {"test-provider": {"status": status, "progress": progress, "primary": button}}
        shell.operation_queue = Mock()
        shell.operation_queue.state_for.return_value = "idle"
        shell.review_mode = False
        shell._update_component_card("test-provider")
        status.set.assert_called_once()
        progress.configure.assert_called()
