"""Slim Portable Source distribution contracts for v0.28.2."""

from __future__ import annotations

import json
import tempfile
import zipfile

from pathlib import Path

from app_identity import APP_VERSION
from tools.build_portable_source import build_archive, portable_source_members
from tools.compile_project import manifest_release_files


ROOT = Path(__file__).resolve().parents[1]


def _project(relative_name: str) -> str:
    """Read one release file for synchronized cross-file assertions."""
    return (ROOT / relative_name).read_text(encoding="utf-8")


def _portable_policy() -> dict[str, object]:
    """Load the exact policy exercised by the real builder."""
    return json.loads(_project("portable_source_payload_policy.json"))


def test_release_identity_is_synchronized() -> None:
    assert APP_VERSION == "0.28.4"
    assert "Version 0.28.4" in _project("VERSION.txt")
    assert 'version = "0.28.4"' in _project("pyproject.toml")
    assert '"version": "0.28.4"' in _project("provider_registry.json")
    assert "v0.28.2" in _project("README.md")
    assert "v0.28.2" in _project("README.txt")


def test_three_release_artifacts_cannot_be_confused() -> None:
    source_policy = _portable_policy()
    future_runtime_policy = json.loads(_project("portable_payload_policy.json"))
    source_builder = _project("tools/build_release.py")
    names = {
        "LoRA_Image_Curator_Source_v{version}.zip",
        str(source_policy["artifact_name_template"]),
        str(future_runtime_policy["artifact_name_template"]),
    }
    assert len(names) == 3
    assert "LoRA_Image_Curator_Source_v{version}.zip" in source_builder
    assert "does not bundle Python" in str(source_policy["runtime_rule"])
    assert "clean private runtime" in str(future_runtime_policy["runtime_rule"])


def test_portable_selection_covers_every_runtime_module_only_from_manifest() -> None:
    signed_root_python = {
        path.relative_to(ROOT).as_posix()
        for path in manifest_release_files(ROOT)
        if path.parent == ROOT and path.suffix.casefold() == ".py"
    }
    selected = {name for name, _path in portable_source_members()}
    assert signed_root_python <= selected
    assert all("/" not in name for name in selected)
    for required in _portable_policy()["required_archive_files"]:
        if required != "RELEASE_MANIFEST.sha256":
            assert required in selected


def test_real_portable_build_is_deterministic_slim_and_self_verifying() -> None:
    policy = _portable_policy()
    with tempfile.TemporaryDirectory(prefix="lora_portable_source_v0282_") as temp:
        root = Path(temp)
        first = root / "first.zip"
        second = root / "second.zip"
        build_archive(first)
        build_archive(second)
        assert first.read_bytes() == second.read_bytes()

        with zipfile.ZipFile(second) as archive:
            assert archive.testzip() is None
            names = archive.namelist()
            assert "RELEASE_MANIFEST.sha256" in names
            assert len(names) < 100
            assert not any("/" in name for name in names)
            assert not set(policy["excluded_repository_files"]) & set(names)
            assert not any(name.startswith(("tests/", "tools/", "docs/")) for name in names)
            assert not any(name.casefold().endswith(".zip") for name in names)
            assert "README.txt" in names
            assert "PORTABLE_README.txt" not in names


def test_portable_readme_states_the_real_setup_and_download_boundary() -> None:
    readme = _project("PORTABLE_README.txt")
    assert "not yet a self-contained executable" in readme
    assert "Python, a virtual environment, provider models, and FFmpeg are not bundled" in readme
    assert '"Setup and Launch LoRA Image Curator.bat"' in readme
    assert "Large model downloads are not started" in readme
    assert "FFmpeg is optional and user-installed" in readme
    assert "Tools > Open Setup & Repair" in readme


def test_portable_runtime_messages_do_not_name_excluded_helper_launchers() -> None:
    excluded_helpers = {
        "Install Base Dependencies.bat",
        "Install Body Analysis Dependencies.bat",
        "Install Face Analysis Dependencies.bat",
        "Check Face Analysis Setup.bat",
    }
    for archive_name, source_path in portable_source_members():
        if not archive_name.casefold().endswith(".py"):
            continue
        source = source_path.read_text(encoding="utf-8")
        assert not excluded_helpers & {
            helper for helper in excluded_helpers if helper in source
        }, f"{archive_name} points users to an excluded helper launcher"


def test_release_gates_include_v0282_and_both_archive_types() -> None:
    regressions = _project("tools/run_regressions.py")
    source_builder = _project("tools/build_release.py")
    golden = _project("tests/test_golden_build.py")
    workflow = _project(".github/workflows/repository-checks.yml")
    assert '"tests/test_v0282_regression.py"' in regressions
    assert '"tests/test_v0282_regression.py"' in source_builder
    assert '"tests/test_v0282_gui.py"' in source_builder
    assert "tools/build_portable_source.py" in source_builder
    assert "build_portable_source.py" in golden
    assert 'GUI_ENTRYPOINT = "tests/test_v0284_gui.py"' in golden
    assert "tests.test_v0284_regression" in workflow


if __name__ == "__main__":
    test_release_identity_is_synchronized()
    test_three_release_artifacts_cannot_be_confused()
    test_portable_selection_covers_every_runtime_module_only_from_manifest()
    test_real_portable_build_is_deterministic_slim_and_self_verifying()
    test_portable_readme_states_the_real_setup_and_download_boundary()
    test_portable_runtime_messages_do_not_name_excluded_helper_launchers()
    test_release_gates_include_v0282_and_both_archive_types()
    print(
        "v0.28.2 regression tests passed: the Portable Source archive is "
        "deterministic, manifest-bounded, setup-complete, and free of "
        "repository-only or user/runtime data."
    )
