"""Release checksum, source coverage, and local-import regressions."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_release import check_release
from tools.compile_project import manifest_release_files

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lic-release-gate-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for source in [*manifest_release_files(ROOT), ROOT / "RELEASE_MANIFEST.sha256"]:
            target = self.root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def refresh(self, *, omit: str = "") -> None:
        manifest = self.root / "RELEASE_MANIFEST.sha256"
        names = [line.split("  ", 1)[1] for line in manifest.read_text().splitlines()]
        manifest.write_text("".join(f"{hashlib.sha256((self.root / n).read_bytes()).hexdigest()}  {n}\n" for n in names if n != omit), encoding="utf-8", newline="\n")

    def test_complete_source_passes(self) -> None:
        self.refresh()
        self.assertGreater(check_release(self.root)["source_files"], 0)

    def test_stale_hash_fails(self) -> None:
        self.refresh()
        with (self.root / "app.py").open("ab") as stream:
            stream.write(b"\n# tampered\n")
        with self.assertRaisesRegex(ValueError, "Stale release hashes"):
            check_release(self.root)

    def test_manifest_omission_fails(self) -> None:
        self.refresh(omit="app.py")
        with self.assertRaisesRegex(ValueError, "Required source release members omitted"):
            check_release(self.root)

    def test_missing_local_import_fails(self) -> None:
        with (self.root / "setup_assistant.py").open("a") as stream:
            stream.write("\nfrom lic_dependencies.missing_helper import check\n")
        self.refresh()
        with self.assertRaisesRegex(ValueError, "Missing source local import.*missing_helper"):
            check_release(self.root)

    def test_new_tracked_source_omission_fails(self) -> None:
        self.refresh()
        with patch("tools.check_release.tracked_source_files", return_value={"new_runtime.py"}):
            with self.assertRaisesRegex(ValueError, "Tracked source omitted"):
                check_release(self.root)

    def test_generated_directory_fails(self) -> None:
        name = "lic_dependencies/__pycache__/private.py"
        path = self.root / name
        path.parent.mkdir()
        path.write_text("# private\n")
        with (self.root / "RELEASE_MANIFEST.sha256").open("a") as stream:
            stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
        with self.assertRaisesRegex(ValueError, "Excluded directory"):
            check_release(self.root)


if __name__ == "__main__":
    unittest.main()