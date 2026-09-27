"""Current cumulative Windows GUI smoke entry point for v0.28.4."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import tkinter as tk

from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from browser_workflow import BrowserFilterState
from catalog_lifecycle import create_catalog_database
from browser_workflow_dialogs import BrowserFiltersDialog
from caption_tagging import CaptionTaggingDialog
from settings_dialog import SettingsDialog
from settings_manager import AppSettings
from tests.test_florence_caption_search import (
    _catalog_snapshot,
    create_caption_search_catalog,
)
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

                # The modal Add Tags workflow must keep explicit image targets,
                # stage caption words, and write only on Finished.
                browser.browser_filter_state = BrowserFilterState()
                browser._reload_filter_image_set_scope()
                browser._apply_search()
                browser.selected_image_ids = {1}
                browser._selection_changed()
                root.deiconify()
                root.update_idletasks()

                def run_tagging(action):
                    errors = []
                    expected_ids = tuple(sorted(browser.selected_image_ids))

                    def interact():
                        dialog = next(
                            child for child in browser.winfo_children()
                            if isinstance(child, CaptionTaggingDialog)
                        )
                        try:
                            assert dialog.target_ids == expected_ids
                            assert f"{len(expected_ids)} images selected" in dialog.title() or f"{len(expected_ids)} image" in str(
                                [child.cget("text") for child in _descendants(dialog)
                                 if isinstance(child, ttk.Label)]
                            )
                            assert browser._tagging_target_ids == expected_ids
                            action(dialog)
                        except BaseException as error:
                            errors.append(error)
                            dialog._cancel()

                    root.after(30, interact)
                    browser._add_manual_tags()
                    if errors:
                        raise errors[0]
                    assert browser._tagging_target_ids is None
                    assert browser.selected_image_ids == set(expected_ids)

                def one_image_action(dialog):
                    wall = dialog.model.word_at(dialog.model.caption.index("wall"))
                    dialog.model.click(wall)
                    dialog._finished()

                run_tagging(one_image_action)
                browser.selected_image_ids = {1, 2}
                browser._selection_changed()
                before_tagging = _catalog_snapshot(caption_catalog)

                def cancel_action(dialog):
                    dialog.update_idletasks()
                    offset = dialog.model.caption.index("beard") + 2
                    box = dialog.caption_text.bbox(f"1.0+{offset}c")
                    assert box is not None
                    event = SimpleNamespace(x=box[0] + 2, y=box[1] + 2, state=0)
                    dialog._on_press(event)
                    dialog._on_release(event)
                    assert dialog.model.pending_tags() == ["beard"]
                    dialog._on_press(event)
                    dialog._on_release(event)
                    assert dialog.model.pending_tags() == []
                    dialog._on_press(event)
                    dialog._on_release(event)
                    browser.clear_selection()
                    assert browser.selected_image_ids == {1, 2}
                    assert "Finish or cancel Tagging Mode" in browser.edit_status_var.get()
                    dialog._cancel()

                run_tagging(cancel_action)
                assert _catalog_snapshot(caption_catalog) == before_tagging

                def escape_action(dialog):
                    dialog.model.click(dialog.model.word_at(dialog.model.caption.index("beard")))
                    dialog.manual_entry.focus_set()
                    dialog.manual_entry.event_generate("<Escape>")
                    assert not dialog.winfo_exists()

                run_tagging(escape_action)
                assert _catalog_snapshot(caption_catalog) == before_tagging

                def finished_action(dialog):
                    caption = dialog.model.caption
                    beard = dialog.model.word_at(caption.index("beard") + 2)
                    black = dialog.model.word_at(caption.index("black") + 2)
                    jacket = dialog.model.word_at(caption.index("jacket") + 2)
                    assert None not in (beard, black, jacket)
                    dialog.model.click(beard)
                    dialog.update_idletasks()
                    start = dialog.caption_text.bbox(f"1.0+{caption.index('black') + 2}c")
                    end = dialog.caption_text.bbox(f"1.0+{caption.index('jacket') + 2}c")
                    assert start is not None and end is not None
                    start_event = SimpleNamespace(x=start[0] + 2, y=start[1] + 2, state=0)
                    end_event = SimpleNamespace(x=end[0] + 2, y=end[1] + 2, state=0)
                    dialog._on_press(start_event)
                    dialog._on_motion(end_event)
                    dialog._on_release(end_event)
                    assert dialog.model.pending_tags() == ["beard", "black jacket"]
                    assert "Pending tags (2) for 2 images" in dialog.pending_var.get()
                    dialog._finished()

                run_tagging(finished_action)
                assert {tag.normalized_name for tag in browser._displayed_selection_tags
                        if tag.kind == "manual"} >= {"beard", "black jacket"}
                assert "wall" not in {tag.normalized_name for tag in browser._displayed_selection_tags}
                with closing(sqlite3.connect(caption_catalog)) as connection:
                    added = connection.execute(
                        "SELECT it.image_id, t.normalized_name FROM image_tags it "
                        "JOIN tags t ON t.id = it.tag_id "
                        "WHERE t.category = 'manual_tag' "
                        "AND t.normalized_name IN ('beard', 'black jacket') "
                        "ORDER BY it.image_id, t.normalized_name"
                    ).fetchall()
                    assert added == [
                        (1, "beard"), (1, "black jacket"),
                        (2, "beard"), (2, "black jacket"),
                    ]
                    assert connection.execute(
                        "SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                        "WHERE t.category='manual_tag' AND t.normalized_name='wall'"
                    ).fetchall() == [(1,)]

                def control_finished_action(dialog):
                    caption = dialog.model.caption
                    dialog.update_idletasks()

                    def point(word):
                        box = dialog.caption_text.bbox(
                            f"1.0+{caption.index(word) + 1}c"
                        )
                        assert box is not None
                        return box[0] + 2, box[1] + 2

                    def click(word, *, control=False):
                        x, y = point(word)
                        state = 0x0004 if control else 0
                        dialog.caption_text.event_generate(
                            "<ButtonPress-1>", x=x, y=y, state=state
                        )
                        assert dialog._press_control is control
                        dialog.caption_text.event_generate(
                            "<ButtonRelease-1>", x=x, y=y, state=state
                        )

                    assert "Text" in dialog.caption_text.bindtags()
                    click("black")
                    assert dialog.model.pending_tags() == ["black"]
                    click("jacket", control=True)
                    assert dialog.model.pending_tags() == ["black jacket"]
                    click("wall", control=True)
                    assert dialog.model.pending_tags() == ["black jacket wall"]
                    click("jacket", control=True)
                    assert dialog.model.pending_tags() == ["black wall"]
                    assert len(dialog.model.candidates) == 1

                    start_x, start_y = point("brick")
                    end_x, end_y = point("wall")
                    dialog.caption_text.event_generate(
                        "<ButtonPress-1>", x=start_x, y=start_y, state=0x0004
                    )
                    assert dialog._press_control
                    dialog.caption_text.event_generate(
                        "<B1-Motion>", x=end_x, y=end_y, state=0x0104
                    )
                    dialog.caption_text.event_generate(
                        "<ButtonRelease-1>", x=end_x, y=end_y, state=0x0004
                    )
                    assert dialog.model.pending_tags() == ["black wall brick"]
                    click("beard")
                    assert dialog.model.pending_tags() == ["black wall brick", "beard"]
                    dialog._finished()

                run_tagging(control_finished_action)
                with closing(sqlite3.connect(caption_catalog)) as connection:
                    assert connection.execute(
                        "SELECT it.image_id, t.normalized_name FROM image_tags it "
                        "JOIN tags t ON t.id=it.tag_id "
                        "WHERE t.category='manual_tag' "
                        "AND t.normalized_name='black wall brick' "
                        "ORDER BY it.image_id"
                    ).fetchall() == [(1, "black wall brick"), (2, "black wall brick")]
                browser.search_var.set("black jacket")
                browser._apply_search()
                assert {record.image_id for record in browser.visible_records} == {1, 2}
            finally:
                if application is not None:
                    application._finish_close()
                else:
                    root.destroy()

    mode = "cumulative" if include_history else "focused"
    print(
        f"v0.28.4 {mode} GUI smoke test passed: File menu catalog/export "
        "commands, named and extensionless catalog handling, independent report "
        "folder, Florence caption search and Tagging Mode batch controls, primary "
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
