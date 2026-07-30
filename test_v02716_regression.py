"""Regressions for the v0.27.16 Git repository readiness pass.

The first public source snapshot needs a slightly different safety boundary
than an in-place local release: it must keep private artifacts out, make issue
reports safe by default, and document exactly how to screen the repository
before publication. These checks keep that public-facing material inside the
signed source inventory instead of depending on memory during packaging.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one project file for public-source assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def test_current_version_identifies_git_readiness_candidate() -> None:
    """Require public metadata to identify the repo-polish release."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 16)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert "v0.27.16" in _source("README.md")
    assert "v0.27.16" in _source("ROADMAP.md")


def test_git_ready_checklist_covers_publication_risks() -> None:
    """Keep the public checklist focused on privacy, status, and verification."""
    checklist = _source("GIT_READY_CHECKLIST.md")
    assert "private catalogs" in checklist
    assert "model weights" in checklist
    assert "python -X dev test_golden_build.py" in checklist
    assert "pre-1.0 active" in checklist
    assert "development project" in checklist
    assert "Re-clone or download the public repository" in checklist


def test_github_issue_templates_warn_against_private_artifacts() -> None:
    """Keep public issue reports from requesting private training material."""
    bug = _source(".github/ISSUE_TEMPLATE/bug_report.md")
    feature = _source(".github/ISSUE_TEMPLATE/feature_request.md")
    for template in (bug, feature):
        assert "private" in template.lower()
        assert "catalog" in template.lower()
        assert "model weights" in template.lower()
        assert "credentials" in template.lower()

    assert "provider/tool limitation" in bug
    assert "third-party provider/tool" in feature


def test_release_inventory_includes_repo_readiness_files() -> None:
    """Ensure packaging, audit, regression, and GUI gates include this pass."""
    build = _source("tools/build_release.py")
    regressions = _source("tools/run_regressions.py")
    golden = _source("test_golden_build.py")
    gui = _source("test_v02716_gui.py")

    assert '".gitattributes"' in build
    assert '".github/ISSUE_TEMPLATE/bug_report.md"' in build
    assert '"GIT_READY_CHECKLIST.md"' in build
    assert '"test_v02716_regression.py"' in regressions
    assert '"test_v02717_gui.py"' in golden
    assert "from test_v02715_gui import run as run_v02715" in gui


if __name__ == "__main__":
    test_current_version_identifies_git_readiness_candidate()
    test_git_ready_checklist_covers_publication_risks()
    test_github_issue_templates_warn_against_private_artifacts()
    test_release_inventory_includes_repo_readiness_files()
    print(
        "v0.27.16 regression tests passed: Git-ready checklist, public issue "
        "templates, release inventory, and version metadata are synchronized."
    )
