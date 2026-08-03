"""Windows GUI smoke test for v0.22.0 / Milestone 10 Phase 1C.

Run after installing the normal application dependencies:

    python -X dev -m tests.test_v0220_gui

The test verifies mode-aware menus, the non-button curation edge marker,
configurable bounded pages, current-page selection semantics, focus-aware
Ctrl+A handling, and chronological Undo/Redo across selection and catalog
actions.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder


def _top_level_menu_labels(menu: tk.Menu) -> list[str]:
    """Return labels from real menu entries, ignoring Tk synthetic entries.

    Some Tk builds expose a tear-off entry at index zero when a menu inherits
    the toolkit default.  Tear-off entries have no ``-label`` option, so smoke
    tests must not assume that every raw menu index is a labeled command or
    cascade.
    """
    end = menu.index("end")
    if end is None:
        return []
    labels: list[str] = []
    for index in range(end + 1):
        try:
            label = str(menu.entrycget(index, "label"))
        except tk.TclError:
            continue
        if label:
            labels.append(label)
    return labels


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0220_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            source = root_path / "images"
            source.mkdir()
            for index in range(220):
                color = (index % 256, (index * 3) % 256, (index * 7) % 256)
                Image.new("RGB", (96, 128), color).save(
                    source / f"workstation_fixture_{index:06d}.png"
                )

            database = root_path / "catalog" / "dataset_tools.db"
            import_catalog_folder(
                CatalogImportOptions(
                    source_folder=source,
                    target_database=database,
                    mode="create",
                    recursive=True,
                    create_image_set=True,
                    image_set_name="UI fixture",
                )
            )

            root = tk.Tk()
            root.geometry("1450x1020")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            application.notebook.select(application.browser_tab)
            root.update()
            root.update_idletasks()

            browser = application.catalog_browser
            assert not hasattr(browser, "remove_unnecessary_button")
            assert not hasattr(browser, "undo_selection_button")
            assert not hasattr(browser, "curation_handle")
            assert browser.images_per_page == 100
            assert len(browser.cards_by_id) == 100
            assert browser.load_more_button.cget("text") == "Next"

            browser_labels = _top_level_menu_labels(application.menu_bar)
            assert str(application.menu_bar.cget("tearoff")) == "0"
            assert "Selection" in browser_labels
            assert "Filters" in browser_labels
            assert "Browser" in browser_labels
            assert "Settings" in browser_labels

            application.theme_key_var.set("dark_workstation")
            application._apply_theme_setting()
            assert application.settings.appearance_theme == "dark_workstation"
            assert browser.settings.appearance_theme == "dark_workstation"
            assert browser.colors["browser_background"] == "#1A1D21"
            assert browser.canvas.cget("background") == "#1A1D21"

            application.notebook.select(application.analysis_tab)
            root.update()
            analysis_labels = _top_level_menu_labels(application.menu_bar)
            assert "Selection" not in analysis_labels
            assert "Filters" not in analysis_labels
            assert "Browser" not in analysis_labels

            application.notebook.select(application.browser_tab)
            root.update()

            browser.select_current_page()
            assert len(browser.selected_image_ids) == 100
            browser._append_next_card_batch()
            browser.select_current_page()
            assert len(browser.selected_image_ids) == 200
            browser.select_all_results()
            assert len(browser.selected_image_ids) == 220

            # Ctrl+A belongs to the search text while that field has focus.
            browser.clear_selection()
            browser.search_entry.focus_set()
            root.update()
            assert browser._select_all_shortcut(None) is None
            assert not browser.selected_image_ids

            browser.set_images_per_page(50)
            assert browser.images_per_page == 50
            assert len(browser.cards_by_id) == 50
            assert browser.settings.browser_images_per_page == 50

            browser.canvas.focus_set()
            root.update()
            browser.select_current_page()
            assert len(browser.selected_image_ids) == 50
            assert browser._escape_shortcut(None) == "break"
            assert not browser.selected_image_ids
            browser.select_all_results()
            assert len(browser.selected_image_ids) == 220
            assert browser._deselect_all_shortcut(None) == "break"
            assert not browser.selected_image_ids

            assert browser.filter_button.cget("text") == "Filters"

            # Isolate one chronological sequence: catalog edit, then selection.
            browser._history_undo_stack.clear()
            browser._history_redo_stack.clear()
            first_ids = {record.image_id for record in browser.visible_records[:2]}
            browser.selected_image_ids = first_ids
            browser._selection_changed()
            assert browser.edit_service is not None
            assert browser._apply_tag_operation(
                "Add UI smoke tag",
                lambda: browser.edit_service.add_manual_tags(
                    sorted(first_ids),
                    ("ui smoke",),
                ),
            )
            before_deselect = set(browser.selected_image_ids)
            browser.deselect_current_page()
            assert not browser.selected_image_ids

            browser._undo_history()
            assert browser.selected_image_ids == before_deselect
            browser._undo_history()
            refreshed = {
                record.image_id: record
                for record in browser.repository.fetch_records()  # type: ignore[union-attr]
            }
            assert all(
                "ui smoke" not in refreshed[image_id].manual_tags.casefold()
                for image_id in first_ids
            )

            application.dataset_readiness.shutdown()
            browser.shutdown()
            root.destroy()
            root = None
            shutdown_logging()
        finally:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
            try:
                shutdown_logging()
            except NameError:
                pass
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "v0.22.0 GUI smoke test passed: browser-only menus, attached curation "
        "marker, configurable bounded pages, focus-aware selection shortcuts, "
        "and chronological selection/catalog Undo/Redo."
    )


if __name__ == "__main__":
    run()
