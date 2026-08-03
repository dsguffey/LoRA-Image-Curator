"""Regressions for the v0.27.18 professional repository review.

This release changes public presentation and repository automation without
changing application behavior. These checks keep the first-visitor README,
privacy-aware contribution flow, dependency-free automation, and signed
release boundary synchronized.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    """Read one current project file for release-boundary assertions."""
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Require public version markers to identify the reviewed candidate."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 18)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert "## v0.27.18 — Professional Repository Review" in _source(
        "CHANGELOG.md"
    )


def test_readme_serves_a_first_time_repository_visitor() -> None:
    """Keep clean-checkout guidance and honest status ahead of upgrade detail."""
    readme = _source("README.md")
    normalized = " ".join(readme.split())
    assert "The application prepares image datasets; it does not train a LoRA" in readme
    assert "## Engineering highlights" in readme
    assert "## Installation from a clean checkout" in readme
    assert "### Upgrading an existing installation" in readme
    assert readme.index("## Installation from a clean checkout") < readme.index(
        "### Upgrading an existing installation"
    )
    assert "v0.27.17 passed the complete" in readme
    assert (
        "Linux/macOS portability is not yet a supported release claim"
        in normalized
    )


def test_public_contribution_flow_is_privacy_aware() -> None:
    """Require valid template metadata and private-artifact warnings."""
    bug = _source(".github/ISSUE_TEMPLATE/bug_report.md")
    feature = _source(".github/ISSUE_TEMPLATE/feature_request.md")
    pull_request = _source(".github/pull_request_template.md")
    normalized_pull_request = " ".join(pull_request.split())
    for template in (bug, feature):
        assert template.startswith("---\nname:")
        assert "labels:" in template.split("---", 2)[1]
    for phrase in ("private images", "catalogs", "credentials", "personal paths"):
        assert phrase in normalized_pull_request
    assert "python -X dev -m tests.test_golden_build" in pull_request
    ignore = _source(".gitignore")
    for pattern in ("*.png", "*.mp4", ".env.*", "!docs/assets/**"):
        assert pattern in ignore


def test_dependency_free_repository_workflow_is_bounded() -> None:
    """Keep hosted automation useful without downloading models or user data."""
    workflow = _source(".github/workflows/repository-checks.yml")
    assert "contents: read" in workflow
    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert '"3.11"' in workflow
    assert '"3.14"' in workflow
    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow
    assert "python -m tools.compile_project" in workflow
    assert "python tools/audit_project.py" in workflow
    assert "python -X dev -m tests.test_v02720_regression" in workflow
    assert "pip install" not in workflow


def test_current_release_chains_include_v02718() -> None:
    """Keep this review in regression, package, and live-Windows gates."""
    build = _source("tools/build_release.py")
    regressions = _source("tools/run_regressions.py")
    golden = _source("test_golden_build.py")
    gui = _source("test_v02718_gui.py")
    for member in (
        '"tests/test_v02718_regression.py"',
        '"tests/test_v02718_gui.py"',
        '".github/pull_request_template.md"',
        '".github/workflows/repository-checks.yml"',
    ):
        assert member in build
    assert '"tests/test_v02718_regression.py"' in regressions
    assert '"tests/test_v02720_gui.py"' in golden
    assert "from test_v02717_gui import run as run_v02717" in gui


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_readme_serves_a_first_time_repository_visitor()
    test_public_contribution_flow_is_privacy_aware()
    test_dependency_free_repository_workflow_is_bounded()
    test_current_release_chains_include_v02718()
    print(
        "v0.27.18 regression tests passed: first-visitor README, privacy-aware "
        "templates, dependency-free repository automation, and release gates "
        "are synchronized."
    )
