"""Explicit provider-download and shared setup contracts for v0.28.1."""

from __future__ import annotations

import os
import tempfile

from pathlib import Path
from unittest.mock import patch

from app_identity import APP_VERSION
from provider_registry import get_component
from provider_setup import inspect_florence_cache


ROOT = Path(__file__).resolve().parents[1]


def _project(relative_name: str) -> str:
    return (ROOT / relative_name).read_text(encoding="utf-8")


def test_release_identity_is_synchronized() -> None:
    assert APP_VERSION == "0.28.2"
    assert "Version 0.28.2" in _project("VERSION.txt")
    assert 'version = "0.28.2"' in _project("pyproject.toml")
    assert get_component("florence_model")["approx_download_bytes"] == 1_540_000_000
    assert get_component("insightface_buffalo_l")["approx_download_bytes"] == 326_000_000
    assert get_component("mediapipe_pose_full_v1")["approx_download_bytes"] == 9_398_198


def test_florence_cache_inspection_is_offline_and_revision_exact() -> None:
    source = _project("provider_setup.py")
    assert "urlopen" not in source
    assert "requests" not in source
    assert "snapshot_download" not in source

    revision = str(get_component("florence_model")["revision"])
    repository = "models--florence-community--Florence-2-large-ft"
    with tempfile.TemporaryDirectory(prefix="lora_florence_cache_v0281_") as temp:
        cache = Path(temp) / "hub"
        with patch.dict(os.environ, {"HF_HUB_CACHE": str(cache)}, clear=False):
            missing = inspect_florence_cache()
            assert not missing.model_ready
            snapshot = cache / repository / "snapshots" / revision
            snapshot.mkdir(parents=True)
            for name in (
                "config.json",
                "preprocessor_config.json",
                "tokenizer_config.json",
                "tokenizer.json",
                "model.safetensors",
            ):
                (snapshot / name).write_bytes(b"synthetic-present-file")
            ready = inspect_florence_cache()
            assert ready.model_ready
            assert ready.snapshot_path == snapshot.resolve()


def test_florence_loader_requires_per_run_download_authority() -> None:
    source = _project("florence_analyzer.py")
    assert "allow_model_download: bool = False" in source
    assert source.count("local_files_only=not allow_model_download") == 2
    assert source.count("trust_remote_code=False") == 2
    assert "use_safetensors=True" in source


def test_gui_preflights_name_every_download_before_network_authority() -> None:
    application = _project("app.py")
    assert '"Download Florence-2 model?"' in application
    assert 'get_component("florence_model")' in application
    assert '"Download InsightFace buffalo_l?"' in application
    assert 'get_component("insightface_buffalo_l")' in application
    assert '"Download MediaPipe Pose model?"' in application
    assert 'get_component("mediapipe_pose_full_v1")' in application
    for detail in ("Publisher:", "Download:", "Source:"):
        assert application.count(detail) >= 3
    assert "ProviderDownloadDialog" in application
    assert "download_model(model_path)" in application


def test_package_repair_reuses_the_existing_setup_assistant() -> None:
    application = _project("app.py")
    assert 'label="Open Setup & Repair…"' in application
    assert '"Setup and Launch LoRA Image Curator.bat"' in application
    assert "self._finish_close()" in application
    assert "_offer_setup_for_missing_packages" in application
    face_start = application.split("def _start_face_analysis", 1)[1].split(
        "def _face_analysis_worker", 1
    )[0]
    assert "Install Face Analysis Dependencies.bat" not in face_start


def test_release_gates_include_v0281() -> None:
    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    workflow = _project(".github/workflows/repository-checks.yml")
    golden = _project("tests/test_golden_build.py")
    assert '"tests/test_v0281_regression.py"' in regressions
    assert '"tests/test_v0281_regression.py"' in builder
    assert '"tests/test_v0281_gui.py"' in builder
    assert "tests.test_v0282_regression" in workflow
    assert 'GUI_ENTRYPOINT = "tests/test_v0282_gui.py"' in golden


if __name__ == "__main__":
    test_release_identity_is_synchronized()
    test_florence_cache_inspection_is_offline_and_revision_exact()
    test_florence_loader_requires_per_run_download_authority()
    test_gui_preflights_name_every_download_before_network_authority()
    test_package_repair_reuses_the_existing_setup_assistant()
    test_release_gates_include_v0281()
    print(
        "v0.28.1 regression tests passed: provider checks stay offline, model "
        "downloads require explicit per-run approval, setup actions reuse the "
        "established assistant, and release endpoints are synchronized."
    )
