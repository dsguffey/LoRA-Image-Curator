"""Focused v0.27.4 regressions for settings and image-viewer clarity.

The patch is primarily graphical, so these dependency-light contracts inspect
the architectural boundaries that matter before the Windows smoke test runs:
the permanent Settings footer, one durable home for shared filter thresholds,
an explicit readiness checkbox for duplicate visibility, thumbnail maximize
controls, a floating enlarged-view toolbar, and flat overwrite-in-place release
packaging.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from release_preflight import unexpected_top_level_python_files


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_settings_owns_shared_filter_values_and_visible_footer() -> None:
    settings = _source("settings_dialog.py")
    app = _source("app.py")
    filters = _source("browser_workflow_dialogs.py")
    readiness = _source("readiness_frame.py")

    assert 'notebook.add(filters, text="Filter Settings")' in settings
    assert 'label="Filter Settings…"' in app
    assert "self.save_button = ttk.Button" in settings
    assert "actions.grid(row=1" in settings
    assert "self.rowconfigure(0, weight=1)" in settings
    assert "readiness_profile_key=profile.key" in settings
    assert "quality_duplicate_similarity_percent=duplicate_similarity" in settings

    assert 'notebook.add(filter_settings, text="Filter Settings")' in filters
    assert "Settings > Filter Settings" in filters
    assert "Possible Duplicates" in filters
    assert "Duplicate similarity is off as a Browser visibility filter" in filters
    # Finalize explains the shared value instead of owning a second threshold
    # slider that could drift from Settings.
    quality_controls = readiness.split(
        "    def _build_quality_controls(self) -> None:", 1
    )[1].split(
        "    def _build_scrolling_content", 1
    )[0]
    assert "duplicate_scale = ttk.Scale" not in quality_controls
    assert "Settings > Filter Settings" in quality_controls


def test_browser_and_enlarged_view_expose_discoverable_controls() -> None:
    browser = _source("catalog_browser.py")
    review = _source("image_review_dialog.py")

    assert "class ExpandImageIcon" in browser
    assert 'Tooltip(self, "Enlarge / review image")' in browser
    assert "relx=1.0" in browser
    assert "rely=1.0" in browser
    assert "self.expand_icon" in browser
    assert "if widget is self.expand_icon" in browser

    assert "self.control_bar = toolbar" in review
    assert "toolbar.place(" in review
    assert 'text="100%"' in review
    assert "def _actual_size" in review
    assert "Return to the Browser (Esc)" in review


def test_enlarged_view_delegates_single_image_deletion() -> None:
    browser = _source("catalog_browser.py")
    review = _source("image_review_dialog.py")

    assert "on_delete: Callable[[object], None] | None = None" in review
    assert 'text="🗑"' in review
    assert "def _delete_current" in review
    assert "self._on_delete(record)" in review
    assert "on_delete=self._delete_review_record" in browser
    assert "self.selected_image_ids = {record.image_id}" in browser
    assert "self.delete_selected_to_trash()" in browser


def test_release_is_flat_and_preflight_supports_in_place_updates() -> None:
    build = _source("tools/build_release.py")
    preflight = _source("release_preflight.py")

    assert 'zip_info("RELEASE_MANIFEST.sha256")' in build
    assert 'f"{root_name}/' not in build
    assert "versioned parent folder" not in build
    assert "Move those named files to a backup folder" in preflight
    assert "new empty folder" not in preflight
    with TemporaryDirectory(prefix="v0274_preflight_") as temporary:
        folder = Path(temporary)
        (folder / "keep.py").write_text("# expected\n", encoding="utf-8")
        (folder / "obsolete.py").write_text("# old\n", encoding="utf-8")
        (folder / "RELEASE_MANIFEST.sha256").write_text(
            f"{'0' * 64}  keep.py\n",
            encoding="utf-8",
        )
        assert unexpected_top_level_python_files(folder) == ("obsolete.py",)


if __name__ == "__main__":
    test_settings_owns_shared_filter_values_and_visible_footer()
    test_browser_and_enlarged_view_expose_discoverable_controls()
    test_enlarged_view_delegates_single_image_deletion()
    test_release_is_flat_and_preflight_supports_in_place_updates()
    print(
        "v0.27.4 regression tests passed: visible Settings footer, centralized "
        "filter interpretation, explicit duplicate on/off guidance, thumbnail "
        "maximize controls, floating image review, and flat update packaging."
    )
