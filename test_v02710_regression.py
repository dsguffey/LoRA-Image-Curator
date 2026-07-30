"""Regressions for the v0.27.10 manifest-bounded release audit.

LoRA Image Curator is updated in place, so its installation directory can
contain private archives and user-managed folders that are not application
source. Compilation, auditing, and release packaging must share the signed
manifest as their ownership boundary while remaining strict about every file
that the project actually ships.
"""

from __future__ import annotations

import contextlib
import io
import tempfile

from pathlib import Path

from app_identity import APP_VERSION
from tools import audit_project, build_release


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def _write_manifest(root: Path, *relative_names: str) -> None:
    """Declare the synthetic files owned by one installed release fixture."""
    (root / "RELEASE_MANIFEST.sha256").write_text(
        "".join(f"{'0' * 64}  {name}\n" for name in relative_names),
        encoding="utf-8",
    )


def test_current_version_is_consistent() -> None:
    """Require synchronized metadata at or beyond the v0.27.10 boundary."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 10)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_audit_and_builder_ignore_unmanifested_local_archives() -> None:
    """Ignore arbitrary local folders while auditing every shipped source."""
    with tempfile.TemporaryDirectory(prefix="v02710_inventory_") as temporary:
        root = Path(temporary)
        project_source = root / "project_source.py"
        project_source.write_text(
            '"""Synthetic documented project module."""\n',
            encoding="utf-8",
        )
        archived_source = (
            root / "Old Files to be trashed" / "test_milestone_7a.py"
        )
        archived_source.parent.mkdir()
        archived_source.write_text(
            "import sqlite3\n"
            "with sqlite3.connect('archived.db') as connection:\n"
            "    connection.execute('SELECT 1')\n",
            encoding="utf-8",
        )
        _write_manifest(root, "project_source.py")

        original_audit_root = audit_project.PROJECT_ROOT
        original_build_root = build_release.PROJECT_ROOT
        audit_project.PROJECT_ROOT = root
        build_release.PROJECT_ROOT = root
        try:
            assert audit_project.run_audit(quiet=True) == 0
            assert build_release.release_files() == [project_source]

            project_source.write_text(
                '"""Synthetic documented project module."""\n'
                "import sqlite3\n"
                "with sqlite3.connect('owned.db') as connection:\n"
                "    connection.execute('SELECT 1')\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert audit_project.run_audit(quiet=True) == 1
            assert "does not close the database" in output.getvalue()
        finally:
            audit_project.PROJECT_ROOT = original_audit_root
            build_release.PROJECT_ROOT = original_build_root


def test_current_release_chains_include_v02710() -> None:
    """Keep the correction in regression, package, and Windows GUI gates."""
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    gui = _source("test_v02710_gui.py")

    assert '"test_v02710_regression.py"' in regressions
    assert '"test_v02710_regression.py"' in build
    assert '"test_v02710_gui.py"' in build
    assert "from test_v0279_gui import run as run_v0279" in gui


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_audit_and_builder_ignore_unmanifested_local_archives()
    test_current_release_chains_include_v02710()
    print(
        "v0.27.10 regression tests passed: synchronized version metadata, "
        "manifest-bounded audit/package scope, strict shipped-source auditing, "
        "and current release gates."
    )
