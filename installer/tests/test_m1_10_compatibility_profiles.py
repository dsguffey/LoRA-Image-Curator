"""M1.10 contracts for one immutable, universal LIC dependency profile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from install_manager.compatibility_profiles import (
    COMPONENT_KEYS, customer_browse_resources, file_digest,
    load_approved_profiles, load_compatibility_profile, plan_dependency_operation,
    profile_sort_key, recommended_profile,
)
from install_manager.recovery import inspect_bootstrap_recovery


ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "src/install_manager/recipes"
PROFILE_ROOT = RECIPES / "compatibility/profiles"
COMPONENT_ROOT = RECIPES / "compatibility/components"
BASE = PROFILE_ROOT / "2026-09-06.json"


class CompatibilityProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_compatibility_profile(BASE)

    def test_base_manifest_exactly_represents_accepted_environment(self):
        report = ROOT / "reports/lic-venv-rebuild-2026-09-02/gpu-validation.json"
        if not report.is_file():
            self.skipTest("Private accepted-environment report is absent from public staging")
        accepted = json.loads(report.read_text(encoding="utf-8"))["inventory"]
        self.assertEqual(self.profile.approval_basis, "accepted-canonical-environment")
        self.assertEqual(self.profile.python_version, "3.14.6")
        self.assertEqual(self.profile.package_inventory, accepted)
        self.assertEqual(len(accepted), 57)

    def test_dated_identity_and_same_day_revision_order(self):
        self.assertEqual(self.profile.profile_id, "2026-09-06")
        self.assertLess(profile_sort_key("2026-09-06"), profile_sort_key("2026-09-06.1"))
        self.assertLess(profile_sort_key("2026-09-06.1"), profile_sort_key("2026-09-06.2"))
        for bad in ("v1", "2026-09-06.0", "2026-9-6", "2026-09-06.latest",
                    "2026-02-30"):
            with self.assertRaises(ValueError):
                profile_sort_key(bad)

    def test_provider_versions_are_independent_from_profile_date(self):
        self.assertIn("1.0.1+lic.cuda13.1", self.profile.components["insightface"].native_version)
        self.assertIn("0.10.35", self.profile.components["mediapipe"].native_version)
        self.assertIn("26b734a54fdf", self.profile.components["florence"].native_version)
        self.assertNotIn(self.profile.profile_id, " ".join(
            component.native_version for component in self.profile.components.values()))

    def test_modular_references_are_exact_and_resolve_deterministically(self):
        raw = json.loads(BASE.read_text(encoding="utf-8"))
        self.assertEqual(tuple(raw["components"]), COMPONENT_KEYS)
        for ref in raw["components"].values():
            self.assertEqual(file_digest(COMPONENT_ROOT / ref["path"]), ref["sha256"])
        again = load_compatibility_profile(BASE)
        self.assertEqual(self.profile.digest, again.digest)
        self.assertEqual(self.profile.package_inventory, again.package_inventory)

    def test_component_change_cannot_silently_change_historical_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recipes/compatibility"
            (root / "profiles").mkdir(parents=True)
            (root / "components").mkdir()
            raw = json.loads(BASE.read_text(encoding="utf-8"))
            for ref in raw["components"].values():
                source = COMPONENT_ROOT / ref["path"]
                (root / "components" / ref["path"]).write_bytes(source.read_bytes())
            # Dependency locks remain under the copied recipes root.
            for name in ("core-windows-x64-v2.json", "florence-windows-nvidia-cu130-v1.json"):
                (root.parent / name).write_bytes((RECIPES / name).read_bytes())
            target = root / "profiles/2026-09-06.json"
            target.write_text(json.dumps(raw), encoding="utf-8")
            component = root / "components" / raw["components"]["ffmpeg"]["path"]
            component.write_bytes(component.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                load_compatibility_profile(target)

    def test_core_classification_is_independent_of_full_membership(self):
        self.assertEqual(self.profile.components["core"].tier, "core")
        self.assertTrue(all(self.profile.components[key].tier == "optional"
                            for key in COMPONENT_KEYS[1:]))
        self.assertEqual(self.profile.distributions["LIC Lite"], ("core",))
        self.assertEqual(self.profile.distributions["LIC Full"], COMPONENT_KEYS)

    def test_every_resolved_python_package_has_exact_artifact_identity(self):
        identities = {}
        for component in self.profile.components.values():
            for package in component.packages:
                self.assertEqual(len(package.sha256), 64)
                self.assertTrue(package.filename.endswith(".whl"))
                current = identities.setdefault(package.name,
                    (package.version, package.filename, package.sha256))
                self.assertEqual(current, (package.version, package.filename, package.sha256))
        self.assertEqual(set(identities), set(self.profile.package_inventory))
        self.assertEqual(identities["insightface"][2],
                         "0a0289e939b76878a981acdf83b536cc069ffa7c59eab3267f26dcd9877abbc5")

    def test_high_risk_shared_ownership_is_canonical(self):
        face = self.profile.components["insightface"]
        body = self.profile.components["mediapipe"]
        face_packages = {item.name: item for item in face.packages}
        body_packages = {item.name: item for item in body.packages}
        self.assertEqual(face_packages["opencv-contrib-python"].sha256,
                         body_packages["opencv-contrib-python"].sha256)
        self.assertEqual(face_packages["opencv-contrib-python"].scope, "shared")
        self.assertEqual(body_packages["opencv-contrib-python"].scope, "shared")
        self.assertIn("onnxruntime-gpu", face_packages)
        self.assertNotIn("onnxruntime", self.profile.package_inventory)
        self.assertNotIn("opencv-python", self.profile.package_inventory)

    def test_models_and_reference_binary_keep_exact_identity(self):
        face = self.profile.components["insightface"].raw["resources"][0]
        body = self.profile.components["mediapipe"].raw["resources"][0]
        ffmpeg = self.profile.components["ffmpeg"].raw["resources"][0]
        self.assertEqual(len(face["files"]), 5)
        self.assertEqual(body["accepted_runtime_sha256"],
                         "4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad")
        self.assertNotEqual(body["accepted_runtime_sha256"],
                            body["managed_acquisition_candidate_sha256"])
        self.assertFalse(ffmpeg["managed_acquisition"])
        self.assertEqual(ffmpeg["sha256"],
                         "ded8cdd9b5762cae6ec962918122fa81ec0faa98da5cc6af64365ac26ab8339e")

    def test_only_locally_approved_profile_can_be_recommended(self):
        profiles = load_approved_profiles(PROFILE_ROOT)
        self.assertEqual([item.profile_id for item in profiles], ["2026-09-06", "2026-09-07", "2026-09-10", "2026-09-11", "2026-09-25", "2026-09-26"])
        with patch("urllib.request.urlopen", side_effect=AssertionError("network is forbidden")):
            self.assertEqual(recommended_profile(PROFILE_ROOT).profile_id, "2026-09-26")

    def test_profile_loading_and_selection_perform_no_acquisition(self):
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [BASE, *COMPONENT_ROOT.glob("*.json")]}
        with patch("install_manager.acquisition.acquire_artifact",
                   side_effect=AssertionError("acquisition")):
            load_compatibility_profile(BASE)
            plan_dependency_operation(self.profile, checked={"insightface"}, installed=set())
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)

    def test_global_operation_requires_install_confirmation_and_never_removes(self):
        plan = plan_dependency_operation(
            self.profile,
            checked={"florence", "mediapipe"},
            installed={"florence", "insightface"},
        )
        self.assertEqual(plan["apply"], ("core", "florence"))
        self.assertEqual(plan["confirm_install"], ("mediapipe",))
        self.assertEqual(plan["preserve_unchecked"], ("insightface",))
        self.assertNotIn("remove", plan)

    def test_internal_wheels_are_not_customer_browse_resources(self):
        browse = customer_browse_resources()
        self.assertIn("dependency-profile", browse)
        self.assertIn("ffmpeg-executable", browse)
        for package in self.profile.package_inventory:
            self.assertNotIn(package, browse)

    def test_existing_m19_recovery_inspection_remains_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "LIC"
            journal = root / "State/operations/bootstrap.json"
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({
                "target_path": str(root.resolve()), "plan_digest": "x",
                "status": "cancelled", "steps": [], "inputs": {"install_root": str(root.resolve())},
            }), encoding="utf-8")
            before = journal.read_bytes()
            # The exact shape is intentionally invalid; inspection may reject it but cannot mutate it.
            try:
                inspect_bootstrap_recovery(journal, current_install_root=root,
                                           current_model_root=root / "Models")
            except (KeyError, ValueError):
                pass
            self.assertEqual(journal.read_bytes(), before)

    def test_tracked_manifests_contain_no_personal_absolute_paths(self):
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in [BASE, *COMPONENT_ROOT.glob("*.json")])
        self.assertNotRegex(text, r"[A-Za-z]:\\\\(?:Users|Design)\\\\")


if __name__ == "__main__":
    unittest.main()
