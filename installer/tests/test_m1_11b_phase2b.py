"""Focused contracts for the shared YuNet/SFace Face Analysis component."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from install_manager.compatibility_profiles import load_approved_profiles, recommended_profile
from install_manager.component_catalog import ComponentPhase, load_component_catalog, validate_existing_selection
from install_manager.lic_face_settings import read_face_model_root, write_face_model_root


RECIPES = ROOT / "src/install_manager/recipes"
PROFILES = RECIPES / "compatibility/profiles"


class FaceComponentTests(unittest.TestCase):
    def setUp(self):
        self.definition = {item.component_id: item for item in load_component_catalog(
            RECIPES / "lic-components.json")}["face-analysis"]

    def test_profile_is_immutable_and_face_is_two_exact_artifacts(self):
        profiles = load_approved_profiles(PROFILES)
        self.assertEqual([item.profile_id for item in profiles], ["2026-09-06", "2026-09-07", "2026-09-10"])
        self.assertEqual(profiles[1].digest, "2f6af3aa5186cb8db82dc7ec59059bf3130fc1173e442e92a7939bd4e157d58a")
        self.assertEqual(len(recommended_profile(PROFILES).component_by_id("face-analysis").raw["resources"]), 2)
        self.assertEqual(self.definition.provider, "OpenCV YuNet + SFace")
        self.assertTrue(self.definition.managed_install)

    def test_existing_pair_requires_both_exact_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(validate_existing_selection(self.definition, folder).phase, ComponentPhase.INCOMPATIBLE)
            for artifact in self.definition.artifacts:
                (folder / artifact["filename"]).write_bytes(b"wrong")
            facts = validate_existing_selection(self.definition, folder)
            self.assertEqual(facts.phase, ComponentPhase.INCOMPATIBLE)
            self.assertIn("wrong size", facts.detail)

    def test_manager_and_lic_share_one_setting_without_losing_other_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory)
            target = appdata / "LoRAImageCurator/settings.json"
            target.parent.mkdir()
            target.write_text(json.dumps({"last_output_folder": "keep", "custom": "keep"}), encoding="utf-8")
            write_face_model_root(appdata, r"D:\Models\Face-A")
            self.assertEqual(read_face_model_root(appdata), r"D:\Models\Face-A")
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(raw["last_output_folder"], "keep")
            self.assertEqual(raw["custom"], "keep")


if __name__ == "__main__":
    unittest.main()
