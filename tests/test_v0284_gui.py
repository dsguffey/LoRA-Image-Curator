"""Current cumulative Windows GUI smoke entry point for v0.28.4."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import tkinter as tk

from dataclasses import replace
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from browser_workflow import BrowserFilterState
from catalog_lifecycle import create_catalog_database
from browser_workflow_dialogs import BrowserFiltersDialog
from settings_dialog import SettingsDialog
from settings_manager import AppSettings
from tests.test_florence_caption_search import create_caption_search_catalog
from test_v0283_gui import run as run_v0283


def _menu_labels(menu: tk.Menu) -> tuple[str, ...]:
    end = menu.index("end")
    if end is None:
        return ()
    return tuple(
        str(menu.entrycget(index, "label"))
        for index in range(end + 1)
        if str(menu.type(index)) != "separator"
    )


def _descendants(widget: tk.Misc) -> tuple[tk.Misc, ...]:
    found: list[tk.Misc] = []
    for child in widget.winfo_children():
        found.append(child)
        found.extend(_descendants(child))
    return tuple(found)


def run(*, include_history: bool = True) -> None:
    """Replay the GUI chain and verify the pre-feedback workflow controls."""
    if include_history:
        run_v0283()
    with tempfile.TemporaryDirectory(prefix="lora_v0284_gui_") as temporary:
        with patch_environment(temporary):
            from app import DatasetToolsApp

            root = tk.Tk()
            root.withdraw()
            application: DatasetToolsApp | None = None
            try:
                application = DatasetToolsApp(root)
                labels = _menu_labels(application.file_menu)
                assert "New Empty Catalog…" in labels
                assert "Create from Images…" in labels
                assert "Open Catalog…" in labels
                assert "Add Images…" in labels
                assert "Delete Catalog…" in labels
                assert "Export Training Data…" in labels
                assert application.start_button.cget("text") == (
                    "Update Catalog & Run All Analysis"
                )
                assert application.run_quality_analysis_var.get() is True
                assert application.run_quality_analysis_button.cget("text") == (
                    "Run Quality Analysis"
                )
                assert application.reanalyze_quality_checkbutton.cget("text") == (
                    "Reanalyze cached images"
                )
                assert application.florence_analysis_progress.cget("maximum") == 100
                assert application.face_analysis_progress.cget("maximum") == 100
                assert application.body_analysis_progress.cget("maximum") == 100
                assert application.dataset_readiness.run_button.winfo_manager() == ""
                assert application.dataset_readiness.cancel_button.winfo_manager() == ""
                named_catalog = Path(temporary) / "FaceCatalog.db"
                with patch("app.filedialog.asksaveasfilename", return_value=str(named_catalog.with_suffix(""))):
                    application._create_empty_catalog()
                assert named_catalog.exists()
                assert application._current_catalog_path() == named_catalog
                assert not (Path(temporary) / "dataset_tools.db").exists()

                legacy_catalog = create_catalog_database(Path(temporary) / "LegacyCatalog")
                with patch("app.filedialog.askopenfilename", return_value=str(legacy_catalog)):
                    application._open_catalog()
                assert application._current_catalog_path() == legacy_catalog
                assert not (Path(temporary) / "LegacyCatalog.db").exists()

                reports = Path(temporary) / "reports"
                reports.mkdir()
                with patch("app.filedialog.askdirectory", return_value=str(reports)):
                    application._choose_output_folder()
                assert application.output_folder_var.get() == str(reports)
                assert application._current_catalog_path() == legacy_catalog
                profile_combo = application.dataset_readiness.profile_combo
                assert isinstance(profile_combo, ttk.Combobox)
                assert str(profile_combo.cget("state")) in {
                    "readonly",
                    "disabled",
                }

                filters = BrowserFiltersDialog(
                    root,
                    initial_state=BrowserFilterState(),
                    image_sets=(),
                    initial_section="filter_settings",
                )
                widgets = _descendants(filters)
                assert any(isinstance(widget, ttk.Spinbox) for widget in widgets)
                assert "Prominent Overlay" in filters.issue_vars
                filters.profile_var.set("SDXL Character LoRA")
                filters.blur_threshold_var.set("125")
                filters.duplicate_similarity_var.set(98)
                filters.overlay_coverage_var.set(12)
                filters.overlay_spatial_mode_var.set("Face and Body")
                filters._apply()
                assert filters.result is not None
                assert filters.result.profile_key == "sdxl_character_lora"
                assert filters.result.blur_threshold == 125
                assert filters.result.duplicate_similarity_percent == 98
                assert filters.result.overlay_coverage_threshold_percent == 12
                assert filters.result.overlay_spatial_mode == "both"

                saved_settings: list[AppSettings] = []
                settings_dialog = SettingsDialog(
                    root,
                    settings=AppSettings(),
                    on_save=saved_settings.append,
                    initial_section="filter_settings",
                )
                assert settings_dialog.overlay_spatial_mode_var.get() == (
                    "Face or Body"
                )
                settings_dialog.overlay_spatial_mode_var.set("Body")
                settings_dialog._save()
                assert saved_settings
                assert saved_settings[0].overlay_spatial_mode == "body"

                application._finish_close()
                application = None
                root = tk.Tk()
                root.withdraw()
                application = DatasetToolsApp(root)
                assert application._current_catalog_path() == legacy_catalog
                assert application.output_folder_var.get() == str(reports)

                caption_root = Path(temporary) / "caption-search"
                caption_root.mkdir()
                caption_catalog, caption_set_id = create_caption_search_catalog(caption_root)
                browser = application.catalog_browser
                browser.set_catalog_path(caption_catalog, load=True, quiet=True)
                browser.images_per_page = 1
                browser.search_var.set("beard")
                browser._apply_search()
                assert {record.image_id for record in browser.visible_records} == {1, 2}
                assert browser.results_var.get() == "2 of 3 images"
                assert browser._page_count() == 2

                browser.search_var.set("BLACK JACKET")
                browser._apply_search()
                assert [record.image_id for record in browser.visible_records] == [1]
                assert browser.results_var.get() == "1 of 3 images"

                browser.browser_filter_state = replace(
                    browser.browser_filter_state,
                    image_set_id=caption_set_id,
                    image_set_name="Caption scope",
                )
                browser._reload_filter_image_set_scope()
                browser.search_var.set("beard")
                browser._apply_search()
                assert [record.image_id for record in browser.visible_records] == [1]
                assert browser.results_var.get() == "1 of 3 images"
            finally:
                if application is not None:
                    application._finish_close()
                else:
                    root.destroy()

    mode = "cumulative" if include_history else "focused"
    print(
        f"v0.28.4 {mode} GUI smoke test passed: File menu catalog/export "
        "commands, named and extensionless catalog handling, independent report "
        "folder, Florence caption search and scoped result counts, primary "
        "Analyze quality controls, status-only Finalize, "
        "editable Filters, Finalize target, and Prominent Overlay are visible."
    )


class patch_environment:
    """Small local environment context without adding another test dependency."""

    def __init__(self, appdata: str) -> None:
        self.appdata = appdata
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in {
            "APPDATA": self.appdata,
            "LORA_IMAGE_CURATOR_TEST_MODE": "1",
        }.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *_args) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Run only the v0.28.4 GUI checks without replaying older milestones.",
    )
    arguments = parser.parse_args()
    run(include_history=not arguments.latest_only)
