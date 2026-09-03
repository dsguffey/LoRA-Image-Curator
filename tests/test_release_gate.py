"""Release checksum, source coverage and nested runtime-package regressions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_release import check_release, portable_inputs
from tools.compile_project import manifest_release_files

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateTests(unittest.TestCase):
    """Exercise independent failure modes in disposable source copies."""

    def setUp(self) -> None:
        """Copy release-owned inputs only; never copy environments or datasets."""
        self.temporary = tempfile.TemporaryDirectory(prefix="lic-release-gate-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for source in [*manifest_release_files(ROOT), ROOT / "RELEASE_MANIFEST.sha256"]:
            target = self.root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def refresh(self, *, omit: str = "") -> None:
        """Refresh fixture hashes so coverage checks cannot rely on staleness."""
        manifest = self.root / "RELEASE_MANIFEST.sha256"
        names = [line.split("  ", 1)[1] for line in manifest.read_text().splitlines()]
        manifest.write_text("".join(
            f"{hashlib.sha256((self.root / n).read_bytes()).hexdigest()}  {n}\n"
            for n in names if n != omit), encoding="utf-8", newline="\n")

    def test_complete_source_passes(self) -> None:
        """A complete, independently hashed extraction passes."""
        self.refresh()
        self.assertEqual(check_release(self.root)["portable_files"], 78)

    def test_stale_hash_fails(self) -> None:
        """A changed source file cannot pass on presence alone."""
        self.refresh()
        with (self.root / "app.py").open("ab") as stream:
            stream.write(b"\n# tampered\n")
        with self.assertRaisesRegex(ValueError, "Stale release hashes"):
            check_release(self.root)

    def test_manifest_omission_fails(self) -> None:
        """Policy-required profile data must appear in the source inventory."""
        self.refresh(omit="lic_dependencies/profile.json")
        with self.assertRaisesRegex(ValueError, "missing from source manifest"):
            check_release(self.root)

    def test_required_portable_omission_fails(self) -> None:
        """Deleting an include is detected independently by required coverage."""
        path = self.root / "portable_source_payload_policy.json"
        policy = json.loads(path.read_text())
        policy["included_files"].remove("constraints-nvidia.txt")
        path.write_text(json.dumps(policy))
        self.refresh()
        with self.assertRaisesRegex(ValueError, "Required Portable Source files omitted"):
            check_release(self.root)

    def test_lazy_relative_import_omission_fails(self) -> None:
        """Even synchronized policy lists cannot omit runtime_probe's helper."""
        path = self.root / "portable_source_payload_policy.json"
        policy = json.loads(path.read_text())
        for key in ("included_files", "required_archive_files"):
            policy[key].remove("lic_dependencies/validation.py")
        path.write_text(json.dumps(policy))
        self.refresh()
        with self.assertRaisesRegex(ValueError, "Missing packaged local import.*validation"):
            check_release(self.root)

    def test_unknown_local_dependency_fails(self) -> None:
        """A misspelled or absent submodule fails without importing providers."""
        with (self.root / "setup_assistant.py").open("a") as stream:
            stream.write("\nfrom lic_dependencies.missing_helper import check\n")
        self.refresh()
        with self.assertRaisesRegex(ValueError, "Missing packaged local import.*missing_helper"):
            check_release(self.root)

    def test_new_tracked_source_omission_fails(self) -> None:
        """An omitted tracked source cannot be hidden by the old manifest."""
        self.refresh()
        with patch("tools.check_release.tracked_source_files", return_value={"new_runtime.py"}):
            with self.assertRaisesRegex(ValueError, "Tracked source omitted"):
                check_release(self.root)

    def test_generated_directory_fails(self) -> None:
        """Explicit membership cannot authorize an excluded cache directory."""
        name = "lic_dependencies/__pycache__/private.py"
        path = self.root / name
        path.parent.mkdir()
        path.write_text("# private\n")
        with (self.root / "RELEASE_MANIFEST.sha256").open("a") as stream:
            stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
        with self.assertRaisesRegex(ValueError, "Excluded directory"):
            check_release(self.root)

    def test_extracted_portable_imports(self) -> None:
        """Exercise startup imports with no project source outside the payload."""
        owned = {p.relative_to(self.root).as_posix() for p in manifest_release_files(self.root)}
        extraction = self.root / "extracted"
        for name in portable_inputs(self.root, owned):
            target = extraction / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, target)
        code = """
import importlib, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
def forbid_acquisition(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'}:
        raise RuntimeError('Acquisition forbidden during import smoke test')
sys.addaudithook(forbid_acquisition)
import app, setup_assistant, install_body_dependencies, install_face_dependencies
from lic_dependencies.profile import ROOT, PROFILE
assert ROOT == root == setup_assistant.PROJECT_ROOT == setup_assistant.DEPENDENCY_ROOT
assert PROFILE['profile'] == 'windows-nvidia-cu130'
for path in (root / 'lic_dependencies').glob('*.py'):
    name = 'lic_dependencies' if path.stem == '__init__' else 'lic_dependencies.' + path.stem
    assert Path(importlib.import_module(name).__file__).resolve().is_relative_to(root)
assert (root / 'constraints-lic.txt').is_file() and (root / 'constraints-nvidia.txt').is_file()
assert not (root / 'venv').exists()
assert callable(app.main) and callable(setup_assistant.main)
"""
        state = str(self.root / "isolated-user")
        environment = {**os.environ, "APPDATA": state, "LOCALAPPDATA": state,
                       "USERPROFILE": state, "HOME": state, "HF_HOME": state,
                       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(extraction)],
                                cwd=extraction, env=environment, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
