"""Focused M1.12B human-acceptance correction contracts."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from install_manager.component_catalog import (ComponentAction, ComponentFacts, ComponentPhase,
                                               component_action, load_component_catalog,
                                               validate_existing_selection)
from install_manager.provider_discovery import discover_provider_candidates
from install_manager.product import DEPENDENCY_PROFILE_ID, PRODUCT_VERSION


CATALOG = ROOT / "src/install_manager/recipes/lic-components.json"


class AcceptanceCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_id = {item.component_id: item for item in load_component_catalog(CATALOG)}

    def test_satisfied_core_is_not_left_on_resume(self):
        core = self.by_id["lic-core"]
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.PARTIAL, resumable=True)),
                         ComponentAction.RESUME)
        self.assertNotEqual(component_action(core, ComponentFacts(ComponentPhase.INSTALLED, verified=True)),
                            ComponentAction.RESUME)

    def test_multiple_valid_provider_folders_are_returned_without_a_choice(self):
        content = b"approved"
        definition = replace(self.by_id["body-analysis"],
                             identity={"sha256": hashlib.sha256(content).hexdigest()})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("candidate-a", "candidate-b"):
                folder = root / name; folder.mkdir()
                (folder / "pose_landmarker_full.task").write_bytes(content)
            candidates = discover_provider_candidates(ROOT / "src/install_manager", definition, root)
        self.assertEqual([candidate.provider_root.name for candidate in candidates],
                         ["candidate-a", "candidate-b"])

    def test_ffmpeg_folder_and_bin_layouts_are_discovered(self):
        definition = self.by_id["video-extraction"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct = root / "direct"; direct.mkdir(); (direct / "ffmpeg.exe").write_bytes(b"")
            nested = root / "nested" / "bin"; nested.mkdir(parents=True); (nested / "ffmpeg.exe").write_bytes(b"")
            self.assertEqual(discover_provider_candidates(ROOT / "src/install_manager", definition, direct)[0].provider_root, direct.resolve())
            self.assertEqual(discover_provider_candidates(ROOT / "src/install_manager", definition, nested.parent)[0].provider_root, nested.resolve())

    def test_ffmpeg_timeout_is_retryable_and_uses_thirty_seconds(self):
        definition = self.by_id["video-extraction"]
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "ffmpeg.exe"; executable.write_bytes(b"")
            runner = Mock(side_effect=__import__("subprocess").TimeoutExpired("ffmpeg", 30))
            facts = validate_existing_selection(definition, executable.parent, runner=runner)
        self.assertEqual(facts.phase, ComponentPhase.PARTIAL)
        self.assertIn("did not respond", facts.detail)
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)

    def test_product_version_is_canonical(self):
        self.assertEqual(PRODUCT_VERSION, "0.7.1")
        self.assertEqual(DEPENDENCY_PROFILE_ID, "2026-09-11")
