"""Regressions for the v0.27.11 clean GUI and bounded-overlay correction.

The v0.27.10 golden gate passed on Windows, but Python development mode exposed
three late Tk Variable finalizers and one viewer redraw callback that outlived
its widget. The same run also showed that overlay verification copied the
complete installed directory, including user-managed content. These checks keep
callback ownership explicit and ensure future golden runners use only synthetic
user-data neighbors.
"""

from __future__ import annotations

import hashlib
import tempfile
import zipfile

from pathlib import Path

from app_identity import APP_VERSION
from test_golden_build import _verify_synthetic_overlay


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Require public current-version markers to agree without pinning a patch."""
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_viewer_owns_every_delayed_redraw() -> None:
    """Keep pending image redraws cancellable during a fast viewer close."""
    viewer = _source("image_review_dialog.py")
    assert (
        "self._redraw_after_id = self.after_idle("
        "self._run_scheduled_redraw)"
    ) in viewer
    assert (
        "self._redraw_after_id = self.after("
        "80, self._run_scheduled_redraw)"
    ) in viewer
    assert "def destroy(self) -> None:" in viewer
    assert "self.after_cancel(callback_id)" in viewer
    assert "_resize_after_id" not in viewer


def test_overlay_fixture_never_copies_the_project_tree() -> None:
    """Prove overlay safety with a small archive and synthetic user content."""
    runner = _source("test_golden_build.py")
    assert "copytree(PROJECT_ROOT" not in runner
    assert "synthetic overwrite installation" in runner

    with tempfile.TemporaryDirectory(prefix="v02711_overlay_") as temporary:
        root = Path(temporary)
        archive_path = root / "synthetic-release.zip"
        payload = b'APP_VERSION = "synthetic-current"\n'
        manifest = (
            f"{hashlib.sha256(payload).hexdigest()}  app_identity.py\n"
        ).encode("utf-8")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("app_identity.py", payload)
            archive.writestr("RELEASE_MANIFEST.sha256", manifest)

        overlay = root / "overlay"
        _verify_synthetic_overlay(archive_path, overlay)
        assert (overlay / "app_identity.py").read_bytes() == payload
        assert (overlay / "output" / "dataset_tools.db").is_file()
        assert (
            overlay
            / "venv"
            / "Lib"
            / "site-packages"
            / "invalid_vendor.py"
        ).is_file()


def test_current_release_chains_include_v02711() -> None:
    """Keep this correction in regression, package, and Windows GUI gates."""
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    current_gui = _source("test_v02712_gui.py")

    assert '"test_v02711_regression.py"' in regressions
    assert '"test_v02711_regression.py"' in build
    assert '"test_v02711_gui.py"' in build
    assert "from test_v02711_gui import run as run_v02711" in current_gui


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_viewer_owns_every_delayed_redraw()
    test_overlay_fixture_never_copies_the_project_tree()
    test_current_release_chains_include_v02711()
    print(
        "v0.27.11 regression tests passed: synchronized version metadata, "
        "owned viewer redraw callbacks, main-thread Tk cleanup, synthetic-only "
        "overlay verification, and current release gates."
    )
