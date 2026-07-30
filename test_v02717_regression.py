"""Regressions for the v0.27.17 deterministic Windows GUI gate.

The v0.27.16 source used the same application runtime as the Windows-passing
v0.27.15 build, but its cumulative GUI replay could still finalize objects from
many already-destroyed Tcl interpreters at nondeterministic times. The current
gate isolates the v0.27.10-and-earlier history in a strict child process and
reports source and Python-runtime paths independently.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one current project file for release-boundary assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Keep current metadata synchronized while retaining v0.27.17 history."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 17)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")
    assert "## v0.27.17 — Isolated Windows GUI Gate" in _source("CHANGELOG.md")


def test_legacy_gui_history_uses_a_strict_isolated_process() -> None:
    """Keep old Tk interpreters out of the current lifecycle-test process."""
    gui = _source("test_v02711_gui.py")
    method = gui.split(
        "def _run_inherited_chain_without_unraisable_errors() -> None:", 1
    )[1].split(
        "def _verify_viewer_cancels_pending_redraw() -> None:", 1
    )[0]
    assert "_run_gui_gate(" in method
    assert '"test_v02710_gui.py"' in method
    assert "run_v02710()" not in method
    assert "environment=os.environ.copy()" in method


def test_golden_gate_proves_source_and_runtime_paths() -> None:
    """Distinguish a valid external venv from an invalid external source."""
    golden = _source("test_golden_build.py")
    assert "def _verify_and_report_runtime_paths() -> None:" in golden
    assert "identity_path.parent != PROJECT_ROOT" in golden
    assert 'print(f"Project source: {PROJECT_ROOT}"' in golden
    assert 'print(f"Python runtime: {runtime_path}"' in golden


def test_current_release_chains_include_v02717() -> None:
    """Keep the patch in regression, package, and live-Windows gates."""
    build = _source("tools/build_release.py")
    regressions = _source("tools/run_regressions.py")
    golden = _source("test_golden_build.py")
    gui = _source("test_v02717_gui.py")
    assert '"test_v02717_regression.py"' in build
    assert '"test_v02717_gui.py"' in build
    assert '"test_v02717_regression.py"' in regressions
    assert '"test_v02718_gui.py"' in golden
    assert "from test_v02716_gui import run as run_v02716" in gui


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_legacy_gui_history_uses_a_strict_isolated_process()
    test_golden_gate_proves_source_and_runtime_paths()
    test_current_release_chains_include_v02717()
    print(
        "v0.27.17 regression tests passed: isolated historical GUI replay, "
        "strict stderr handling, explicit runtime/source paths, and current "
        "release gates are synchronized."
    )
