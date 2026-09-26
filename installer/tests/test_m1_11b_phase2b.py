"""Focused contracts for the shared YuNet/SFace Face Analysis component."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from install_manager.compatibility_profiles import (dependency_profile_for_components,
                                                    load_approved_profiles, recommended_profile)
from install_manager.component_catalog import (ComponentFacts, ComponentPhase,
                                               load_component_catalog, validate_existing_selection)
from install_manager.component_operations import operation_download_summary, operation_plan
from install_manager.component_state import (component_from_manifest, empty_inventory,
                                             replace_component, write_inventory)
from install_manager.lic_face_settings import read_face_model_root, write_face_model_root
from install_manager.manager_ui import component_detail_contract


RECIPES = ROOT / "src/install_manager/recipes"
PROFILES = RECIPES / "compatibility/profiles"


class FaceComponentTests(unittest.TestCase):
    def setUp(self):
        self.definition = {item.component_id: item for item in load_component_catalog(
            RECIPES / "lic-components.json")}["face-analysis"]

    def test_profile_is_immutable_and_face_is_two_exact_artifacts(self):
        profiles = load_approved_profiles(PROFILES)
        self.assertEqual([item.profile_id for item in profiles], ["2026-09-06", "2026-09-07", "2026-09-10", "2026-09-11", "2026-09-25", "2026-09-26", "2026-09-26.1"])
        self.assertEqual(profiles[2].digest, "ac2da1f0e9a7d8cd606e72adfdcdb2e22abb86a5b9eb3b1815807edb80e2e443")
        self.assertEqual(profiles[3].digest, "ddd947d8dd52c28d4cb2ccae9ae998683807dd24dd5d1f42ebc95331886db6dd")
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

    def _active_root(self, base: Path, *installed_ids: str) -> Path:
        root = base / "LIC"
        active = root / "State/installations/lic-lite.json"
        active.parent.mkdir(parents=True)
        active.write_text(json.dumps({"state": "active", "root": root.resolve().as_posix()}),
                          encoding="utf-8")
        profile = recommended_profile(PROFILES)
        inventory = empty_inventory(root, profile)
        for component_id in ("lic-core", *installed_ids):
            manifest = profile.component_by_id(component_id)
            inventory = replace_component(inventory, component_from_manifest(
                manifest, state="installed", enabled=True,
                readiness={"version": "fixture", "passed": True}, resources=()))
        write_inventory(root, inventory)
        return root

    def _face_root(self, base: Path) -> Path:
        model_root = base / "Face Models"
        model_root.mkdir()
        write_face_model_root(base / "AppData", str(model_root))
        return model_root

    def test_clean_core_face_plan_includes_shared_opencv_and_both_models(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": str(Path(directory) / "AppData")}, clear=False):
            base = Path(directory)
            root = self._active_root(base)
            self._face_root(base)
            plan = operation_plan(ROOT / "src/install_manager", root, "face-analysis")
            names = {item["artifact_id"] for item in plan["dependency_artifacts"]}
            resources = {item["artifact_id"] for item in plan["resource_artifacts"]}
            self.assertEqual(names, {"opencv-contrib-python"})
            self.assertEqual(resources, {"opencv-yunet-2026may", "opencv-sface-2021dec"})
            self.assertNotIn("mediapipe", json.dumps(plan).casefold())
            summary = operation_download_summary(ROOT / "src/install_manager", root, "face-analysis")
            self.assertEqual(summary["download_bytes"], 92_748_670)
            fields = dict(field for _heading, section in component_detail_contract(
                self.definition, ComponentFacts(), plan=summary)["sections"] for field in section)
            self.assertEqual(fields["Download required"], "92.7 MB")

    def test_face_reuses_opencv_after_body_pose_without_acquisition(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": str(Path(directory) / "AppData")}, clear=False), \
                patch("install_manager.component_operations.acquire_artifact", side_effect=AssertionError("passive inspection acquired")):
            base = Path(directory)
            root = self._active_root(base, "body-analysis")
            self._face_root(base)
            plan = operation_plan(ROOT / "src/install_manager", root, "face-analysis")
            self.assertEqual(plan["dependency_artifacts"], [])
            summary = operation_download_summary(ROOT / "src/install_manager", root, "face-analysis")
            self.assertEqual(summary["download_bytes"], 38_926_091)

    def test_body_pose_reuses_opencv_when_face_is_installed_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._active_root(Path(directory), "face-analysis")
            plan = operation_plan(ROOT / "src/install_manager", root, "body-analysis")
            names = {item["artifact_id"] for item in plan["dependency_artifacts"]}
            self.assertNotIn("opencv-contrib-python", names)
            self.assertIn("mediapipe", names)

    def test_shared_opencv_composition_is_one_exact_approved_wheel(self):
        profile = recommended_profile(PROFILES)
        face = dependency_profile_for_components(profile, {"lic-core", "face-analysis"})
        body_face = dependency_profile_for_components(profile, {"lic-core", "body-analysis", "face-analysis"})
        self.assertEqual(face.expected_inventory["opencv-contrib-python"], "5.0.0.93")
        self.assertEqual([item.name for item in body_face.wheels].count("opencv-contrib-python"), 1)


if __name__ == "__main__":
    unittest.main()
