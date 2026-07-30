"""Golden-build regressions for v0.27.6 handoff verification.

This release turns the accumulated test history into one maintained release
gate and applies the final single-image deletion clarification: optional
catalog cleanup after one Recycle Bin deletion does not create a database
backup, while multi-image cleanup and explicit catalog-only removal retain
their stronger recovery behavior.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")


def test_v0276_release_remains_in_maintained_history() -> None:
    """Retain the v0.27.6 golden-baseline checks after later patch releases."""
    changelog = _source("CHANGELOG.md")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")

    assert "## v0.27.6 — Golden-Build Verification" in changelog
    assert '"test_v0276_regression.py"' in regressions
    assert '"test_v0276_regression.py"' in build
    assert '"test_v0276_gui.py"' in build


def test_single_delete_skips_only_delete_cleanup_backup() -> None:
    """Lock the narrow no-backup exception without weakening bulk safety."""
    browser = _source("catalog_browser.py")
    delete_method = browser.split(
        "    def delete_selected_to_trash(self) -> None:", 1
    )[1].split(
        "    def remove_selected_from_catalog(self) -> None:", 1
    )[0]
    explicit_remove = browser.split(
        "    def remove_selected_from_catalog(self) -> None:", 1
    )[1].split(
        "    def _create_catalog_removal_backup", 1
    )[0]

    assert "if len(removable_ids) > 1:" in delete_method
    assert "backup = edit_service.create_backup()" in delete_method
    assert "removal = service.remove_catalog_records(" in delete_method
    assert delete_method.index("if len(removable_ids) > 1:") < (
        delete_method.index("backup = edit_service.create_backup()")
    )
    assert "backup = edit_service.create_backup()" in explicit_remove


def test_golden_runner_owns_complete_release_gate() -> None:
    """Keep one discoverable command for automated, GUI, and package checks."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    current_gui = f"test_v{APP_VERSION.replace('.', '')}_gui.py"

    assert "create_golden_fixture" in runner
    assert "tools/run_regressions.py" in runner
    assert current_gui in runner
    assert "tools/build_release.py" in runner
    assert '"test_v0275_regression.py"' in regressions
    assert '"test_v0276_regression.py"' in regressions
    assert '"test_golden_build.py"' in build
    assert '"test_v0276_gui.py"' in build


if __name__ == "__main__":
    test_v0276_release_remains_in_maintained_history()
    test_single_delete_skips_only_delete_cleanup_backup()
    test_golden_runner_owns_complete_release_gate()
    print(
        "v0.27.6 regression tests passed: maintained historical coverage, "
        "narrow single-delete backup exception, and complete golden gate."
    )
