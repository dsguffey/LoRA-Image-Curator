"""Windows GUI smoke test for v0.21.0 / Milestone 10 Phase 1B.

Run after installing the normal application dependencies:

    python -X dev test_v0210_gui.py

The test uses a disposable 420-image catalog to verify that the browser never
grows one Tk canvas beyond 100 cards, preserves selection across pages, supports
selection undo, exposes the in-browser curation panel, keeps ordinary search
tag-only, and starts provider cancellation in a safe disabled state.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0210_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        appdata = root_path / "appdata"
        os.environ["APPDATA"] = str(appdata)
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from catalog_browser import CARD_BATCH_SIZE, CARD_PAGE_SIZE

            source = root_path / "images"
            source.mkdir()
            for index in range(420):
                color = (index, (index * 3) % 256, (index * 7) % 256)
                Image.new("RGB", (96, 128), color).save(
                    source / f"Gal_Gadot_interview_{index:06d}.png"
                )

            database = root_path / "catalog" / "dataset_tools.db"
            import_catalog_folder(
                CatalogImportOptions(
                    source_folder=source,
                    target_database=database,
                    mode="create",
                    recursive=True,
                    create_image_set=True,
                    image_set_name="Performance fixture",
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
            assert len(browser.all_records) == 420
            assert len(browser.visible_records) == 420
            assert len(browser.cards_by_id) == CARD_BATCH_SIZE == 100
            assert CARD_PAGE_SIZE == CARD_BATCH_SIZE
            assert browser.load_more_button.instate(("!disabled",))
            assert browser.thumbnail_cache is not None
            assert browser.thumbnail_cache.cache_directory == (
                appdata / "LoRAImageCurator" / "thumbnail_cache"
            )
            assert application.cancel_analysis_button.instate(("disabled",))
            assert not hasattr(browser, "curation_handle")
            assert browser.curation_options.small_face_percent == 0.25

            # A shared video filename no longer makes every image appear to
            # have a matching subject tag.
            browser.search_var.set("gal gadot")
            browser._apply_search()
            assert browser.visible_records == []

            first_image_id = browser.all_records[0].image_id
            assert browser.edit_service is not None
            browser.edit_service.add_manual_tags((first_image_id,), ("gal gadot",))
            browser.refresh(quiet=True)
            browser.search_var.set("gal gadot")
            browser._apply_search()
            assert [record.image_id for record in browser.visible_records] == [
                first_image_id
            ]

            browser.search_var.set("")
            browser._apply_search()
            assert len(browser.cards_by_id) == CARD_BATCH_SIZE
            assert browser.load_more_button.cget("text") == "Next"
            browser._append_next_card_batch()
            assert browser.card_page_index == 1
            assert len(browser.cards_by_id) == CARD_BATCH_SIZE
            assert len(browser.cards_by_id) <= CARD_PAGE_SIZE
            assert browser.previous_page_button.instate(("!disabled",))

            browser.select_all_visible()
            assert len(browser.selected_image_ids) == 100
            browser.clear_selection()
            assert not browser.selected_image_ids
            browser._undo_selection()
            assert len(browser.selected_image_ids) == 100

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
        "v0.21.0 GUI smoke test passed: bounded canvas paging, tag-only search, "
        "selection undo, in-browser curation controls, external preview cache, "
        "and safe provider cancellation controls."
    )


if __name__ == "__main__":
    run()
