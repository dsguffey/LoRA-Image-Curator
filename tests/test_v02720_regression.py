"""Focused contracts for the final pre-portable-app source milestone."""

from __future__ import annotations

import re

from pathlib import Path

from app_identity import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent


def _project(relative_name: str) -> str:
    return (PROJECT_ROOT / relative_name).read_text(encoding="utf-8")


def test_version_and_public_history_are_synchronized() -> None:
    """Keep current identity synchronized and retain v0.27.20 history."""
    assert APP_VERSION == "0.28.0"
    assert "Version 0.28.0" in _project("VERSION.txt")
    assert 'version = "0.28.0"' in _project("pyproject.toml")
    assert "v0.28.0" in _project("README.md")
    assert "## v0.27.20" in _project("CHANGELOG.md")


def test_recycle_bin_is_standard_and_body_analysis_remains_optional() -> None:
    """Protect the lightweight safety dependency and heavyweight provider split."""
    base = _project("requirements.txt")
    body = _project("requirements-body.txt")
    metadata = _project("pyproject.toml")
    assistant = _project("setup_assistant.py")
    assert "Send2Trash>=1.8,<3" in base
    assert "Send2Trash" not in body
    assert '"Send2Trash>=1.8,<3"' in metadata
    assert '"mediapipe>=0.10,<1"' in metadata
    assert "Recycle Bin safety" in assistant
    assert "Install optional body/pose analysis" in assistant
    assert not (PROJECT_ROOT / "Install Body and File Action Dependencies.bat").exists()
    body_batch = _project("Install Body Analysis Dependencies.bat")
    assert '"venv\\Scripts\\python.exe" install_body_dependencies.py' in body_batch
    file_actions = _project("file_actions.py")
    assert "Install Base Dependencies.bat" in file_actions
    assert "permanent deletion" in file_actions


def test_tests_are_public_but_no_longer_clutter_the_repository_root() -> None:
    """Require one test directory and synchronized automation references."""
    assert not tuple(PROJECT_ROOT.glob("test_*.py"))
    assert (TEST_ROOT / "test_golden_build.py").is_file()
    workflow = _project(".github/workflows/repository-checks.yml")
    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    readme = _project("README.md")
    assert "tests.test_v0280_regression" in workflow
    assert '"tests/test_v02720_regression.py"' in regressions
    assert '"tests/test_v02723_gui.py"' in builder
    assert "python -X dev -m tests.test_golden_build" in readme
    assert "`tests/`" in readme


def test_new_computer_qa_is_non_mutating_and_documented() -> None:
    """Keep the real Windows QA checkpoint visible and safely repeatable."""
    tool = _project("tools/clean_install_check.py")
    guide = _project("docs/CLEAN_INSTALL_QA.md")
    roadmap = _project("ROADMAP.md")
    assert "No files were changed" in tool
    assert "--phase before-setup" in guide
    assert "--phase after-setup" in guide
    assert "--phase upgrade" in guide
    assert "real new-computer" in roadmap


def test_every_public_batch_file_is_current_and_project_relative() -> None:
    """Prevent stale launcher banners and machine-specific paths."""
    for batch in PROJECT_ROOT.glob("*.bat"):
        text = batch.read_text(encoding="utf-8")
        assert f"v{APP_VERSION}" in text, batch.name
        assert 'cd /d "%~dp0"' in text, batch.name
        assert not re.search(r"(?i)\b[A-Z]:\\", text), batch.name


if __name__ == "__main__":
    test_version_and_public_history_are_synchronized()
    test_recycle_bin_is_standard_and_body_analysis_remains_optional()
    test_tests_are_public_but_no_longer_clutter_the_repository_root()
    test_new_computer_qa_is_non_mutating_and_documented()
    test_every_public_batch_file_is_current_and_project_relative()
    print(
        "v0.27.20 regression tests passed: dependency safety, test layout, "
        "clean-install QA, documentation, and launchers are synchronized."
    )
