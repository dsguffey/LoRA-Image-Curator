"""Regressions for the v0.27.7 bounded golden-build compilation fix."""

from __future__ import annotations

import tempfile

from pathlib import Path

from app_identity import APP_VERSION
from tools.compile_project import compile_project_python, manifest_python_files


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")


def test_v0277_release_remains_in_maintained_history() -> None:
    """Retain the bounded-compilation fix after later patch releases."""
    changelog = _source("CHANGELOG.md")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")

    assert "## v0.27.7 — Bounded Golden-Build Compilation" in changelog
    assert '"test_v0277_regression.py"' in regressions
    assert '"test_v0277_regression.py"' in build
    assert '"test_v0277_gui.py"' in build


def test_compile_gate_ignores_unmanifested_virtual_environment() -> None:
    """Prove corrupt third-party source cannot enter the project-owned gate."""
    with tempfile.TemporaryDirectory(prefix="v0277_compile_scope_") as temporary:
        root = Path(temporary)
        project_source = root / "project_source.py"
        project_source.write_text("VALUE = 27\n", encoding="utf-8")
        third_party_source = root / "venv" / "Lib" / "site-packages" / "broken.py"
        third_party_source.parent.mkdir(parents=True)
        third_party_source.write_text("VALUE = ∂ç\n", encoding="utf-8")
        (root / "RELEASE_MANIFEST.sha256").write_text(
            f"{'0' * 64}  project_source.py\n"
            f"{'0' * 64}  README.md\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text("fixture\n", encoding="utf-8")

        assert manifest_python_files(root) == (project_source,)
        assert compile_project_python(root) == 1


def test_golden_runner_uses_bounded_compilation_and_current_gui() -> None:
    """Keep the Windows gate on project inventory and the current GUI chain."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    current_gui = f'test_v{APP_VERSION.replace(".", "")}_gui.py'

    assert '"-m", "tools.compile_project"' in runner
    assert f'"{current_gui}"' in runner
    assert '"test_v0277_regression.py"' in regressions
    assert '"tools/compile_project.py"' in build
    assert '"test_v0277_regression.py"' in build
    assert '"test_v0277_gui.py"' in build


if __name__ == "__main__":
    test_v0277_release_remains_in_maintained_history()
    test_compile_gate_ignores_unmanifested_virtual_environment()
    test_golden_runner_uses_bounded_compilation_and_current_gui()
    print(
        "v0.27.7 regression tests passed: maintained historical coverage, "
        "manifested-only compilation, virtual-environment exclusion, and "
        "current release gates."
    )
