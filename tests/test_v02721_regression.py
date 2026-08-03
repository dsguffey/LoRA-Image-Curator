"""Security and compatibility contracts for Florence stabilization v0.27.21."""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_REVISION = "26b734a54fdfbf9c398351eedfabb7f27fc470b7"
TRANSFORMERS_VERSION = "4.56.2"


def _project(relative_name: str) -> str:
    """Read one current release file for a dependency-free contract check."""
    return (PROJECT_ROOT / relative_name).read_text(encoding="utf-8")


def test_release_identity_and_dependency_pin_are_synchronized() -> None:
    """Keep the focused security release and native dependency line exact."""
    assert APP_VERSION == "0.28.0"
    assert "Version 0.28.0" in _project("VERSION.txt")
    assert 'version = "0.28.0"' in _project("pyproject.toml")
    assert f"transformers=={TRANSFORMERS_VERSION}" in _project("requirements.txt")
    assert (
        f'"transformers=={TRANSFORMERS_VERSION}"'
        in _project("pyproject.toml")
    )


def test_florence_loader_cannot_execute_repository_code_or_pickle_weights() -> None:
    """Require native code, an immutable snapshot, and safetensors weights."""
    source = _project("florence_analyzer.py")
    assert "trust_remote_code=True" not in source
    assert source.count("trust_remote_code=False") == 2
    assert f'MODEL_REVISION = "{MODEL_REVISION}"' in source
    assert source.count("revision=MODEL_REVISION") == 2
    assert "use_safetensors=True" in source
    assert 'startswith("transformers.models.florence2")' in source
    assert "Install Base Dependencies.bat" in source


def test_native_input_and_setup_readiness_boundaries_are_explicit() -> None:
    """Protect canonical native generation and established-venv repair UX."""
    florence = _project("florence_analyzer.py")
    setup = _project("setup_assistant.py")
    assert 'if "attention_mask" in inputs:' in florence
    assert "**inputs," in florence
    assert f'"transformers": "{TRANSFORMERS_VERSION}"' in setup
    assert "UPDATE REQUIRED" in setup
    assert "exact_versions_match" in setup


def test_security_disclosure_and_release_gates_cover_the_change() -> None:
    """Keep public claims, tests, packaging, and hosted checks synchronized."""
    security = _project("SECURITY.md")
    notices = _project("MODEL_LICENSES.txt")
    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    golden = _project("tests/test_golden_build.py")
    assert "does not enable `trust_remote_code`" in " ".join(security.split())
    assert MODEL_REVISION in notices
    assert '"tests/test_v02721_regression.py"' in regressions
    assert '"tests/test_v02721_regression.py"' in builder
    assert '"tests/test_v02721_gui.py"' in builder
    assert 'GUI_ENTRYPOINT = "tests/test_v0280_gui.py"' in golden


if __name__ == "__main__":
    test_release_identity_and_dependency_pin_are_synchronized()
    test_florence_loader_cannot_execute_repository_code_or_pickle_weights()
    test_native_input_and_setup_readiness_boundaries_are_explicit()
    test_security_disclosure_and_release_gates_cover_the_change()
    print(
        "v0.27.21 regression tests passed: Florence uses pinned native "
        "Transformers code, safetensors weights, and synchronized setup gates."
    )
