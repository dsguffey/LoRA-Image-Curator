"""Focused regressions for the v0.27.19 portable source setup milestone.

These checks are dependency-free: they validate the setup/launcher contract,
safe PyTorch command translation, required-versus-optional documentation,
version metadata, and signed release ownership without creating a venv,
installing packages, downloading models, or launching Tk.
"""

from __future__ import annotations

import re

from pathlib import Path

from app_identity import APP_VERSION
from setup_assistant import normalize_official_pytorch_command


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    """Read one current project file for release-boundary assertions."""
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_current_version_retains_v02719_setup_history() -> None:
    """Keep the v0.27.19 setup contract visible after later source cleanup."""
    assert APP_VERSION == "0.27.20"
    assert "Version 0.27.20" in _source("VERSION.txt")
    assert 'version = "0.27.20"' in _source("pyproject.toml")
    assert "v0.27.20" in _source("README.md")
    assert "v0.27.20" in _source("README.txt")
    assert "## v0.27.19 — Portable and Sane Source Setup" in _source(
        "CHANGELOG.md"
    )


def test_checklist_launcher_is_project_relative_and_first_time_safe() -> None:
    """Require one portable front door and actionable legacy wrappers."""
    setup_batch = _source("Setup and Launch LoRA Image Curator.bat")
    assert 'cd /d "%~dp0"' in setup_batch
    assert '"setup_assistant.py"' in setup_batch
    assert '"venv\\Scripts\\python.exe"' in setup_batch
    assert "py -3.11" not in setup_batch
    assert 'py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"' in setup_batch
    assert 'if "!LIC_PROBE!"=="0"' in setup_batch
    assert "https://www.python.org/downloads/windows/" in setup_batch

    install_base = _source("Install Base Dependencies.bat")
    assert "py -3.11" not in install_base
    assert 'if "!LIC_PROBE!"=="0"' in install_base

    normal = _source("Run LoRA Image Curator.bat")
    assert 'call "Setup and Launch LoRA Image Curator.bat"' in normal
    body = _source("Install Body Analysis Dependencies.bat")
    assert '"venv\\Scripts\\python.exe" install_body_dependencies.py' in body
    assert "LIC_PYTHON=python" not in body

    for filename in (
        "Setup and Launch LoRA Image Curator.bat",
        "Install Base Dependencies.bat",
        "Install Face Analysis Dependencies.bat",
        "Install Body Analysis Dependencies.bat",
        "Check Face Analysis Setup.bat",
        "Run LoRA Image Curator.bat",
        "Run LoRA Image Curator - Diagnostic.bat",
    ):
        text = _source(filename)
        assert f"v{APP_VERSION}" in text, filename
        assert not re.search(r"(?i)\b[A-Z]:\\", text), filename


def test_pytorch_selector_command_is_safely_redirected() -> None:
    """Accept official wheel commands while rejecting shell/package expansion."""
    command = (
        "pip3 install torch torchvision --index-url "
        "https://download.pytorch.org/whl/cu128"
    )
    assert normalize_official_pytorch_command(command) == [
        "install",
        "torch",
        "torchvision",
        "--index-url",
        "https://download.pytorch.org/whl/cu128",
    ]

    invalid_commands = (
        "pip install torch requests --index-url https://download.pytorch.org/whl/cpu",
        "pip install torch --index-url https://pypi.org/simple",
        "pip install torch & echo unsafe",
        "curl https://download.pytorch.org/whl/cpu",
    )
    for invalid in invalid_commands:
        try:
            normalize_official_pytorch_command(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe command was accepted: {invalid}")


def test_setup_menu_prints_one_title_then_compact_menu() -> None:
    """Avoid repeatedly redrawing the full setup title after each action."""
    assistant = _source("setup_assistant.py")
    assert 'print(f"\\n{APP_NAME} v{APP_VERSION} — Setup & Launch")' in assistant
    assert '"\\nMAIN MENU\\n"' in assistant
    assert assistant.count("Setup & Launch") == 1


def test_github_documentation_matches_dependency_tiers() -> None:
    """Keep the public README aligned with the guided setup behavior."""
    readme = _source("README.md")
    normalized = " ".join(readme.split())
    for phrase in (
        "source release",
        "users do not activate or administer it manually",
        "Setup and Launch LoRA Image Curator.bat",
        "First-time setup (recommended)",
        "Face analysis, body/pose analysis, and FFmpeg video extraction are optional",
        "CUDA 12 uses `onnxruntime-gpu>=1.21,<1.27`",
        "CUDA 13 uses `onnxruntime-gpu>=1.27,<1.30`",
        "Do not independently install both `onnxruntime` and `onnxruntime-gpu`",
    ):
        assert phrase in normalized
    assert "https://pytorch.org/get-started/locally/" in readme
    assert "https://onnxruntime.ai/docs/execution-providers/" in readme


def test_current_release_chains_include_v02719() -> None:
    """Keep setup files in automation, deterministic packaging, and GUI gates."""
    build = _source("tools/build_release.py")
    regressions = _source("tools/run_regressions.py")
    golden = _source("test_golden_build.py")
    workflow = _source(".github/workflows/repository-checks.yml")
    gui = _source("test_v02719_gui.py")
    for member in (
        '"setup_assistant.py"',
        '"Setup and Launch LoRA Image Curator.bat"',
        '"Install Base Dependencies.bat"',
        '"tests/test_v02719_regression.py"',
        '"tests/test_v02719_gui.py"',
    ):
        assert member in build
    assert '"tests/test_v02719_regression.py"' in regressions
    assert '"tests/test_v02720_gui.py"' in golden
    assert "python -X dev -m tests.test_v02720_regression" in workflow
    assert "from test_v02718_gui import run as run_v02718" in gui


if __name__ == "__main__":
    test_current_version_retains_v02719_setup_history()
    test_checklist_launcher_is_project_relative_and_first_time_safe()
    test_pytorch_selector_command_is_safely_redirected()
    test_setup_menu_prints_one_title_then_compact_menu()
    test_github_documentation_matches_dependency_tiers()
    test_current_release_chains_include_v02719()
    print(
        "v0.27.19 regression tests passed: portable checklist setup, safe "
        "PyTorch routing, dependency tiers, GitHub documentation, and release "
        "gates are synchronized."
    )
