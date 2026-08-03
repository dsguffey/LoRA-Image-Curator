"""Regressions for the v0.27.8 installed-output-safe golden audit.

The supported release workflow overlays source files into an existing
``DatasetTools`` installation. That directory legitimately contains the
user-managed ``output`` folder, including the active catalog and timestamped
database backups. Release validation must ignore that runtime folder while
remaining strict about forbidden artifacts accidentally placed in source.
"""

from __future__ import annotations

import contextlib
import io
import tempfile

from pathlib import Path

from app_identity import APP_VERSION
from tools import audit_project, build_release


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def _write_manifest(root: Path, *relative_names: str) -> None:
    """Declare the synthetic files owned by one installed release fixture."""
    (root / "RELEASE_MANIFEST.sha256").write_text(
        "".join(f"{'0' * 64}  {name}\n" for name in relative_names),
        encoding="utf-8",
    )


def test_public_version_metadata_remains_consistent() -> None:
    """Retain synchronized metadata after v0.27.8 becomes historical."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 8)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_audit_ignores_output_but_rejects_source_database() -> None:
    """Keep user catalogs outside the audit without weakening source checks."""
    with tempfile.TemporaryDirectory(prefix="v0278_audit_scope_") as temporary:
        root = Path(temporary)
        (root / "project_source.py").write_text(
            '"""Synthetic documented project module."""\n',
            encoding="utf-8",
        )
        output_database = root / "output" / "dataset_tools.db"
        output_database.parent.mkdir()
        output_database.write_bytes(b"private synthetic runtime catalog")
        _write_manifest(root, "project_source.py")

        original_root = audit_project.PROJECT_ROOT
        audit_project.PROJECT_ROOT = root
        try:
            assert audit_project.run_audit(quiet=True) == 0
            (root / "accidentally_shipped.db").write_bytes(b"forbidden")
            _write_manifest(root, "project_source.py", "accidentally_shipped.db")
            with contextlib.redirect_stdout(io.StringIO()):
                assert audit_project.run_audit(quiet=True) == 1
        finally:
            audit_project.PROJECT_ROOT = original_root


def test_release_inventory_excludes_complete_output_folder() -> None:
    """Prevent text reports in runtime output from entering an archive."""
    with tempfile.TemporaryDirectory(prefix="v0278_release_scope_") as temporary:
        root = Path(temporary)
        public_file = root / "README.md"
        public_file.write_text("public\n", encoding="utf-8")
        runtime_report = root / "output" / "provider_report.txt"
        runtime_report.parent.mkdir()
        runtime_report.write_text("private runtime report\n", encoding="utf-8")
        _write_manifest(root, "README.md")

        original_root = build_release.PROJECT_ROOT
        build_release.PROJECT_ROOT = root
        try:
            assert build_release.release_files() == [public_file]
        finally:
            build_release.PROJECT_ROOT = original_root


def test_maintained_release_chains_include_v0278() -> None:
    """Keep the correction in maintained history after later patch releases."""
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")

    assert '"tests/test_v0278_regression.py"' in regressions
    assert '"tests/test_v0278_regression.py"' in build
    assert '"tests/test_v0278_gui.py"' in build
    assert "manifest_release_files(PROJECT_ROOT)" in _source(
        "tools/audit_project.py"
    )
    assert "manifest_release_files(PROJECT_ROOT)" in build


if __name__ == "__main__":
    test_public_version_metadata_remains_consistent()
    test_audit_ignores_output_but_rejects_source_database()
    test_release_inventory_excludes_complete_output_folder()
    test_maintained_release_chains_include_v0278()
    print(
        "v0.27.8 regression tests passed: version consistency, runtime-output "
        "exclusion, retained source-artifact rejection, and current release "
        "gates."
    )
