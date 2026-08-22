"""Provider-provenance and portable-boundary contracts for v0.28.0.

The tests remain dependency-free and offline. They validate the release-owned
registry, exact model identity, verified download design, one-time disclosure,
smart launcher, slim portable policy, deterministic SPDX generation, and
release synchronization without installing or downloading any provider.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile

from pathlib import Path
from unittest.mock import patch

from app_identity import APP_VERSION
from install_body_dependencies import (
    MODEL_DOWNLOAD_HOSTS,
    MODEL_SHA256,
    MODEL_SIZE_BYTES,
    MODEL_URL,
    download_model,
)
from provider_registry import get_component, load_provider_registry, notice_version
from settings_manager import AppSettings, load_settings, save_settings
from third_party_notice import (
    notice_is_required,
    record_notice_acknowledgement,
    show_first_launch_notice,
)
from tools.generate_sbom import build_sbom


ROOT = Path(__file__).resolve().parents[1]


def _project(relative_name: str) -> str:
    """Read one release file for cross-file assertions."""
    return (ROOT / relative_name).read_text(encoding="utf-8")


def test_release_identity_registry_and_provider_pins_are_synchronized() -> None:
    """Keep product, Florence, PyTorch, and MediaPipe identities in one release."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 28, 0)
    assert f"Version {APP_VERSION}" in _project("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _project("pyproject.toml")
    registry = load_provider_registry()
    assert registry["application"]["version"] == APP_VERSION
    assert registry["notice_version"] == "2026-08-03.2"

    florence = get_component("florence_model")
    assert florence["artifact"] == "florence-community/Florence-2-large-ft"
    assert florence["revision"] == "26b734a54fdfbf9c398351eedfabb7f27fc470b7"
    assert florence["bundled"] is False
    assert "trust_remote_code=False" in _project("florence_analyzer.py")

    nvidia = get_component("pytorch_nvidia")
    assert nvidia["tested_version"] == "torch 2.13.0; torchvision 0.28.0"
    assert nvidia["source_url"] == "https://download.pytorch.org/whl/cu130"

    pose = get_component("mediapipe_pose_full_v1")
    assert pose["tested_version"] == "1"
    assert pose["sha256"] == MODEL_SHA256
    assert pose["approx_download_bytes"] == MODEL_SIZE_BYTES


def test_mediapipe_download_is_versioned_verified_and_atomic() -> None:
    """Forbid moving URLs and publish only fully verified model bytes."""
    assert "/float16/1/" in MODEL_URL
    assert "/latest/" not in MODEL_URL
    assert MODEL_DOWNLOAD_HOSTS == frozenset({"storage.googleapis.com"})
    assert MODEL_SIZE_BYTES == 9_398_198
    assert MODEL_SHA256 == (
        "5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1"
    )
    source = inspect.getsource(download_model)
    assert 'suffix=".task.partial"' in source
    assert "MODEL_DOWNLOAD_HOSTS" in source
    assert source.index("byte_count != MODEL_SIZE_BYTES") < source.index(
        "os.replace(temporary, destination)"
    )
    assert source.index("digest.hexdigest() != MODEL_SHA256") < source.index(
        "os.replace(temporary, destination)"
    )


def test_notice_records_only_its_version_after_ok() -> None:
    """Keep disclosure state local, versioned, minimal, and backward compatible."""
    settings = AppSettings()
    assert notice_is_required(settings)
    settings.remember_paths = False
    with tempfile.TemporaryDirectory(prefix="lora_notice_v0280_") as temporary:
        with patch.dict(os.environ, {"APPDATA": temporary}):
            save_settings(settings)
            before = json.loads(
                (Path(temporary) / "LoRAImageCurator" / "settings.json").read_text(
                    encoding="utf-8"
                )
            )
            assert before["third_party_notice_version"] == ""
            record_notice_acknowledgement(settings)
            loaded = load_settings()
            assert loaded.third_party_notice_version == notice_version()
            assert loaded.remember_paths is False
            stored = json.loads(
                (Path(temporary) / "LoRAImageCurator" / "settings.json").read_text(
                    encoding="utf-8"
                )
            )
            assert "third_party_notice_timestamp" not in stored
            assert "user_identity" not in stored


def test_notice_wording_buttons_and_shutdown_persistence_are_synchronized() -> None:
    """Keep one clear acknowledgment action and prevent shutdown from erasing it."""
    notice = _project("third_party_notice.py")
    application = _project("app.py")
    assert (
        "LoRA Image Curator does not collect telemetry data, but some "
        "third-party tools it's using may. The default settings for them "
        "are set to telemetry off."
    ) in notice.replace('"\n            "', "")
    assert 'text="OK"' in notice
    assert 'text="Continue"' not in notice
    assert 'text="Exit"' not in notice
    assert "third_party_notice_version=(" in application
    assert "browser_settings.third_party_notice_version" in application


def test_first_launch_notice_maps_before_becoming_modal() -> None:
    """Prevent a withdrawn Windows root from hiding its first-launch notice."""
    source = inspect.getsource(show_first_launch_notice)
    assert "window.transient(root)" not in source
    assert source.index("window.wait_visibility()") < source.index(
        "window.grab_set()"
    )
    assert source.index("window.grab_set()") < source.index(
        "root.wait_window(window)"
    )


def test_source_and_portable_inventories_cannot_be_confused() -> None:
    """Require a slim end-user payload and a separately named source archive."""
    policy = json.loads(_project("portable_payload_policy.json"))
    assert policy["artifact_name_template"].startswith(
        "LoRA_Image_Curator_Portable_Windows_x64_"
    )
    assert policy["source_artifact_name_template"].startswith(
        "LoRA_Image_Curator_Source_"
    )
    excluded_directories = set(policy["excluded_directories"])
    assert {"tests", "tools", "docs", ".github", "venv", "output", "models"} <= (
        excluded_directories
    )
    excluded_files = set(policy["excluded_source_only_files"])
    assert {
        "CONTRIBUTING.md",
        "GIT_READY_CHECKLIST.md",
        "setup_assistant.py",
        "requirements.txt",
    } <= excluded_files
    required = set(policy["required_user_files"])
    assert {
        "LICENSE",
        "provider_registry.json",
        "SBOM.spdx.json",
        "THIRD_PARTY_NOTICE.md",
    } <= required
    builder = _project("tools/build_release.py")
    assert 'default_name = f"LoRA_Image_Curator_Source_v{version}.zip"' in builder


def test_checked_in_sbom_is_generated_from_the_registry() -> None:
    """Prevent human-readable notices and machine inventory from drifting."""
    checked_in = json.loads(_project("SBOM.spdx.json"))
    assert checked_in == build_sbom()
    package_names = {package["name"] for package in checked_in["packages"]}
    for component in load_provider_registry()["components"]:
        assert component["artifact"] in package_names
    assert checked_in["spdxVersion"] == "SPDX-2.3"
    assert checked_in["documentDescribes"] == ["SPDXRef-Application"]


def test_smart_launcher_and_release_gates_cover_the_foundation() -> None:
    """Route unhealthy environments to setup and keep every release gate current."""
    assistant = _project("setup_assistant.py")
    launcher = _project("Run LoRA Image Curator.bat")
    assert "def smart_launch_application()" in assistant
    assert "will not silently fall back to " in assistant
    assert '"CPU. Use the tested NVIDIA repair' in assistant
    assert '"--smart-launch"' in assistant
    assert '"setup_assistant.py" --smart-launch' in launcher
    assert 'call "Setup and Launch LoRA Image Curator.bat"' in launcher
    assert 'print("Checking required packages...", flush=True)' in assistant
    assert 'print("Checking graphics runtime...", flush=True)' in assistant
    assert "return launch_application(setup_verified=True)" in assistant

    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    workflow = _project(".github/workflows/repository-checks.yml")
    golden = _project("tests/test_golden_build.py")
    assert '"tests/test_v0280_regression.py"' in regressions
    assert '"tests/test_v0280_regression.py"' in builder
    assert '"tests/test_v0280_gui.py"' in builder
    assert "tests.test_v0284_regression" in workflow
    assert 'GUI_ENTRYPOINT = "tests/test_v0284_gui.py"' in golden


if __name__ == "__main__":
    test_release_identity_registry_and_provider_pins_are_synchronized()
    test_mediapipe_download_is_versioned_verified_and_atomic()
    test_notice_records_only_its_version_after_ok()
    test_notice_wording_buttons_and_shutdown_persistence_are_synchronized()
    test_first_launch_notice_maps_before_becoming_modal()
    test_source_and_portable_inventories_cannot_be_confused()
    test_checked_in_sbom_is_generated_from_the_registry()
    test_smart_launcher_and_release_gates_cover_the_foundation()
    print(
        "v0.28.0 regression tests passed: provider provenance, verified model "
        "download, notice persistence, slim portable policy, SPDX inventory, "
        "smart launch, and release gates are synchronized."
    )
