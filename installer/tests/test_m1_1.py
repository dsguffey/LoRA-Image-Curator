"""M1.1 tests: deterministic LIC planning, bounded detection and safe staging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from install_manager.detection import detect_explicit
from install_manager.models import ResourceState
from install_manager.planner import build_plan
from install_manager.recipe import LicRecipe
from install_manager.reporting import write_report
from install_manager.staging import stage_package
from install_manager.verification import verify_package


REQUIRED = (
    "README.txt", "LICENSE", "THIRD_PARTY_NOTICE.md", "provider_registry.json",
    "SBOM.spdx.json", "VERSION.txt", "Run LoRA Image Curator.bat",
    "Run LoRA Image Curator - Diagnostic.bat", "Setup and Launch LoRA Image Curator.bat",
    "requirements.txt", "requirements-body.txt", "app.py", "setup_assistant.py",
)
RUNTIME = (
    "constraints-lic.txt", "constraints-nvidia.txt", "lic_dependencies/__init__.py",
    "lic_dependencies/profile.py", "lic_dependencies/profile.json", "lic_dependencies/installer.py",
    "lic_dependencies/insightface_build.py", "lic_dependencies/upstream.py",
    "lic_dependencies/runtime_probe.py", "lic_dependencies/validation.py",
)


def make_fixture(root: Path, *, extra: dict[str, bytes] | None = None,
                 stale_manifest: bool = False) -> Path:
    """Create a minimal deterministic LIC source-package archive."""
    files = {name: f"fixture:{name}\n".encode() for name in (*REQUIRED, *RUNTIME)}
    files.update(extra or {})
    lines = "".join(f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files))
    if stale_manifest and "app.py" in files:
        original = hashlib.sha256(b"fixture:app.py\n").hexdigest()
        lines = lines.replace(hashlib.sha256(files["app.py"]).hexdigest(), original)
    path = root / "lic.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
        archive.writestr("RELEASE_MANIFEST.sha256", lines.encode())
    return path


def recipe_for(package: Path) -> LicRecipe:
    """Bind the fixture to a trusted descriptor with its measured identity."""
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    return LicRecipe(
        application_id="lora-image-curator", display_name="LoRA Image Curator", version="0.28.4",
        artifact_id="fixture", source="test fixture", expected_sha256=digest,
        required_archive_files=(*REQUIRED, *RUNTIME), required_runtime_inputs=RUNTIME,
        activation_allowed=False, install_execution_allowed=False,
    )


class M11Tests(unittest.TestCase):
    """Keep every test inside a disposable directory."""

    def test_valid_package_plan_and_repeated_determinism(self) -> None:
        """A valid package produces the same plan bytes on repeated calls."""
        with tempfile.TemporaryDirectory(prefix="im-m11-") as temporary:
            root = Path(temporary)
            package = make_fixture(root)
            recipe = recipe_for(package)
            inspection = verify_package(package, recipe)
            self.assertTrue(inspection.verified)
            candidates = detect_explicit([root / "missing-model", package], kind="resource")
            plan_one = build_plan(recipe, inspection, root / "stage", candidates)
            plan_two = build_plan(recipe, inspection, root / "stage", candidates)
            self.assertEqual(plan_one.canonical_json(), plan_two.canonical_json())
            self.assertEqual(plan_one.digest(), plan_two.digest())
            self.assertFalse(plan_one.activation_allowed)
            self.assertFalse(plan_one.installation_executed)
            self.assertEqual([action.state for action in plan_one.actions][-1], ResourceState.NOT_EXECUTED)

    def test_bounded_detection_probes_only_explicit_paths(self) -> None:
        """Detection does not recurse into a supplied directory or inspect drives."""
        with tempfile.TemporaryDirectory(prefix="im-detect-") as temporary:
            root = Path(temporary)
            (root / "nested" / "hidden.bin").parent.mkdir()
            (root / "nested" / "hidden.bin").write_bytes(b"hidden")
            result = detect_explicit([root / "nested"], kind="model")
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].exists)
            self.assertFalse(result[0].is_file)
            self.assertEqual(result[0].sha256, "")

    def test_successful_staging_confined_and_no_activation(self) -> None:
        """Staging copies and extracts only below the explicit stage root."""
        with tempfile.TemporaryDirectory(prefix="im-stage-") as temporary:
            root = Path(temporary)
            package = make_fixture(root)
            stage = stage_package(package, root / "stage", recipe_for(package))
            self.assertTrue(Path(stage.copied_package).is_file())
            self.assertTrue((Path(stage.extracted_directory) / "lic_dependencies" / "profile.json").is_file())
            self.assertFalse(stage.activation_allowed)
            self.assertFalse(stage.installation_executed)
            self.assertEqual(Path(stage.extracted_directory).resolve().parent, Path(stage.stage_directory).resolve())
            self.assertFalse((root / "venv").exists())

    def test_hash_mismatch_rejected_before_staging_writes(self) -> None:
        """A trusted digest failure leaves the requested stage absent."""
        with tempfile.TemporaryDirectory(prefix="im-hash-") as temporary:
            root = Path(temporary)
            package = make_fixture(root)
            recipe = recipe_for(package)
            package.write_bytes(package.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "unverified"):
                stage_package(package, root / "stage", recipe)
            self.assertFalse((root / "stage").exists())

    def test_manifest_hash_mismatch_rejected(self) -> None:
        """A changed payload cannot pass its embedded manifest."""
        with tempfile.TemporaryDirectory(prefix="im-manifest-") as temporary:
            root = Path(temporary)
            package = make_fixture(root, extra={"app.py": b"changed\n"}, stale_manifest=True)
            inspection = verify_package(package, recipe_for(package))
            self.assertFalse(inspection.integrity_ok)
            self.assertTrue(any("hash mismatch" in error for error in inspection.errors))

    def test_missing_required_file_rejected(self) -> None:
        """Completeness fails even when the remaining manifest hashes are valid."""
        with tempfile.TemporaryDirectory(prefix="im-complete-") as temporary:
            root = Path(temporary)
            package = make_fixture(root)
            recipe = recipe_for(package)
            files = {name: f"fixture:{name}\n".encode() for name in (*REQUIRED, *RUNTIME) if name != RUNTIME[4]}
            lines = "".join(f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files))
            with zipfile.ZipFile(package, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                archive.writestr("RELEASE_MANIFEST.sha256", lines.encode())
            inspection = verify_package(package, recipe)
            self.assertFalse(inspection.completeness_ok)
            self.assertTrue(any("required" in error for error in inspection.errors))

    def test_malicious_archive_paths_rejected(self) -> None:
        """Traversal and absolute names are rejected before extraction."""
        for bad_name in ("../escape.txt", "/absolute.txt", "C:drive.txt", "folder\\escape.txt"):
            with self.subTest(bad_name=bad_name), tempfile.TemporaryDirectory(prefix="im-path-") as temporary:
                root = Path(temporary)
                package = make_fixture(root, extra={bad_name: b"bad"})
                inspection = verify_package(package, recipe_for(package))
                self.assertFalse(inspection.integrity_ok)
                self.assertTrue(any("Unsafe" in error for error in inspection.errors))

    def test_corrupt_archive_rejected(self) -> None:
        """Non-ZIP bytes never reach a staging directory."""
        with tempfile.TemporaryDirectory(prefix="im-corrupt-") as temporary:
            root = Path(temporary)
            package = root / "corrupt.zip"
            package.write_bytes(b"not a zip")
            recipe = LicRecipe("lora-image-curator", "LoRA Image Curator", "0.28.4", "fixture", "fixture",
                               hashlib.sha256(package.read_bytes()).hexdigest(), REQUIRED, RUNTIME, False, False)
            self.assertFalse(verify_package(package, recipe).integrity_ok)

    def test_recipe_rejects_executable_fields(self) -> None:
        """Declarative recipes cannot smuggle arbitrary hooks or commands."""
        with self.assertRaisesRegex(ValueError, "Executable"):
            LicRecipe.from_dict({"application_id": "x", "display_name": "x", "version": "1",
                                 "artifact_id": "x", "source": "x", "expected_sha256": "0" * 64,
                                 "required_archive_files": [], "required_runtime_inputs": [], "hook": "run"})

    def test_report_output_is_confined_to_approved_root(self) -> None:
        """A report path cannot turn the diagnostic CLI into an arbitrary writer."""
        with tempfile.TemporaryDirectory(prefix="im-report-") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "inside"):
                write_report({"ok": True}, root / "outside.json", allowed_root=root / "stage")


if __name__ == "__main__":
    unittest.main()
