"""Regressions for the v0.27.9 Python 3.14 SQLite resource-safety fix.

The golden build runs every historical test in a temporary directory.  On
Windows, even one unclosed SQLite handle prevents that directory from being
removed.  Python's connection context manager commits or rolls back but does
not close the connection, so maintained tests and application initialization
must make handle ownership explicit.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from app_identity import APP_VERSION
from catalog import Catalog, SCHEMA_VERSION
from tools import audit_project


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Require public version markers to stay synchronized after v0.27.9."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 9)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_audit_rejects_nonclosing_sqlite_contexts() -> None:
    """Prevent transaction-only context managers from leaking file handles."""
    with tempfile.TemporaryDirectory(prefix="v0279_sqlite_audit_") as temporary:
        root = Path(temporary)
        module = root / "sqlite_fixture.py"
        module.write_text(
            '"""Synthetic documented SQLite module."""\n'
            "import sqlite3\n\n"
            "def _read_value(database):\n"
            "    with sqlite3.connect(database) as connection:\n"
            "        return connection.execute('SELECT 1').fetchone()[0]\n",
            encoding="utf-8",
        )
        (root / "RELEASE_MANIFEST.sha256").write_text(
            f"{'0' * 64}  sqlite_fixture.py\n",
            encoding="utf-8",
        )

        original_root = audit_project.PROJECT_ROOT
        audit_project.PROJECT_ROOT = root
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert audit_project.run_audit(quiet=True) == 1
            assert "does not close the database" in output.getvalue()

            module.write_text(
                '"""Synthetic documented SQLite module."""\n'
                "import sqlite3\n"
                "from contextlib import closing\n\n"
                "def _read_value(database):\n"
                "    with closing(sqlite3.connect(database)) as connection, connection:\n"
                "        return connection.execute('SELECT 1').fetchone()[0]\n",
                encoding="utf-8",
            )
            assert audit_project.run_audit(quiet=True) == 0
        finally:
            audit_project.PROJECT_ROOT = original_root


def test_failed_catalog_construction_releases_database() -> None:
    """Release the native handle when migration validation rejects a catalog."""
    with tempfile.TemporaryDirectory(prefix="v0279_catalog_open_") as temporary:
        database = Path(temporary) / "newer_schema.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        try:
            Catalog(database)
        except RuntimeError as error:
            assert "newer LoRA Image Curator version" in str(error)
        else:
            raise AssertionError("A future-schema catalog should be rejected.")

        # This is the decisive Windows assertion: unlink fails with WinError 32
        # if Catalog.__init__ left its connection alive after raising.
        database.unlink()
        assert not database.exists()


def test_current_release_chains_include_v0279() -> None:
    """Keep the fix in regression, package, and cumulative Windows gates."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    gui = _source("test_v0279_gui.py")
    current_gui = _source("test_v02710_gui.py")

    assert "_run_gui_gate(" in runner
    assert '"test_v0279_regression.py"' in regressions
    assert '"test_v0279_regression.py"' in build
    assert '"test_v0279_gui.py"' in build
    assert "from test_v0278_gui import run as run_v0278" in gui
    assert "from test_v0279_gui import run as run_v0279" in current_gui


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_audit_rejects_nonclosing_sqlite_contexts()
    test_failed_catalog_construction_releases_database()
    test_current_release_chains_include_v0279()
    print(
        "v0.27.9 regression tests passed: version consistency, explicit SQLite "
        "closure enforcement, failed-open cleanup, and current release gates."
    )
