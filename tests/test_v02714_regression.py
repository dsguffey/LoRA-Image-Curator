"""Regressions for the v0.27.14 Git-ready workflow clarification update."""

from __future__ import annotations

import os
import tempfile

from pathlib import Path

from app_identity import APP_VERSION
from image_discovery import discover_supported_images
from settings_manager import AppSettings, load_settings, save_settings


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_current_version_and_launchers_are_consistent() -> None:
    """Keep every public version marker, including Windows launchers, aligned."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 14)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")
    for launcher in (
        "Run LoRA Image Curator.bat",
        "Run LoRA Image Curator - Diagnostic.bat",
        "Check Face Analysis Setup.bat",
        "Install Face Analysis Dependencies.bat",
    ):
        text = _source(launcher)
        assert f"v{APP_VERSION}" in text, launcher
        assert "v0.27.7" not in text, launcher


def test_subfolder_defaults_round_trip_independently() -> None:
    """Provider and catalog scopes default on and remain independently editable."""
    defaults = AppSettings()
    assert defaults.catalog_import_include_subfolders is True
    assert defaults.caption_include_subfolders is True
    assert defaults.face_include_subfolders is True
    assert defaults.face_reference_include_subfolders is True

    with tempfile.TemporaryDirectory(prefix="lora_curator_v02714_settings_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = temporary
        try:
            save_settings(
                AppSettings(
                    catalog_import_include_subfolders=False,
                    caption_include_subfolders=True,
                    face_include_subfolders=False,
                    face_reference_include_subfolders=True,
                )
            )
            loaded = load_settings()
            assert loaded.catalog_import_include_subfolders is False
            assert loaded.caption_include_subfolders is True
            assert loaded.face_include_subfolders is False
            assert loaded.face_reference_include_subfolders is True
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata


def test_discovery_and_provider_contracts_honor_subfolder_scope() -> None:
    """Central discovery and both provider adapters retain explicit scope."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v02714_images_") as temporary:
        root = Path(temporary)
        nested = root / "nested"
        nested.mkdir()
        (root / "root.jpg").write_bytes(b"not decoded during discovery")
        (nested / "nested.png").write_bytes(b"not decoded during discovery")

        assert [
            path.name
            for path in discover_supported_images(root, recursive=False)
        ] == [
            "root.jpg"
        ]
        assert {
            path.name
            for path in discover_supported_images(root, recursive=True)
        } == {
            "root.jpg",
            "nested.png",
        }
    florence = _source("florence_analyzer.py")
    face = _source("face_analyzer.py")
    pipeline = _source("analysis_pipeline.py")
    assert "discover_supported_images(input_folder, recursive=recursive)" in florence
    assert "discover_supported_images(folder, recursive=recursive)" in face
    assert "recursive=face_recursive" in pipeline
    assert "reference_recursive=face_reference_recursive" in pipeline


def test_ui_contracts_explain_progress_and_scroll_ownership() -> None:
    """Protect the temporary green markers, shared heading, and details-wheel fix."""
    app = _source("app.py")
    browser = _source("catalog_browser.py")
    settings = _source("settings_dialog.py")
    theme = _source("ui_theme.py")

    assert app.count("● Running — progress below") == 2
    assert "● Running — progress in dialog" in app
    assert 'self._set_running_provider("florence")' in app
    assert 'self._set_running_provider("face")' in app
    assert "Current work: Image Captioning / Florence-2" in app
    assert "Current work: Face Scanning / InsightFace" in app
    assert 'style.configure("Running.TLabel"' in theme
    assert "register_mousewheel_region(self.tag_text, details_canvas)" in browser
    assert "register_mousewheel_region(self.detail_text, details_canvas)" in browser
    for label in (
        "Catalog & Paths…",
        "Image Captioning…",
        "Face Scanning…",
        "Body / Pose Scanning…",
        "Video Extraction…",
        "Privacy & Diagnostics…",
    ):
        assert label in app
    assert 'notebook.add(captioning, text="Image Captioning")' in settings
    assert 'notebook.add(face, text="Face Scanning")' in settings
    assert "Provider diagnostic" in settings
    assert "App issue:" in settings
    assert "Provider/tool issue:" in settings


def test_current_release_chains_include_v02714() -> None:
    """Keep this update inside automated, packaging, and Windows GUI gates."""
    assert '"tests/test_v02714_regression.py"' in _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    assert '"tests/test_v02714_regression.py"' in build
    assert '"tests/test_v02714_gui.py"' in build
    assert '"tests/test_v0282_gui.py"' in _source("test_golden_build.py")
    assert (
        "from test_v02714_gui import run as run_v02714"
        in _source("test_v02715_gui.py")
    )
    assert (
        "from test_v02715_gui import run as run_v02715"
        in _source("test_v02716_gui.py")
    )


if __name__ == "__main__":
    test_current_version_and_launchers_are_consistent()
    test_subfolder_defaults_round_trip_independently()
    test_discovery_and_provider_contracts_honor_subfolder_scope()
    test_ui_contracts_explain_progress_and_scroll_ownership()
    test_current_release_chains_include_v02714()
    print(
        "v0.27.14 regression tests passed: independent subfolder scopes, "
        "functional Settings organization, temporary provider progress markers, "
        "details-wheel routing, provider diagnostics, and synchronized launchers."
    )
