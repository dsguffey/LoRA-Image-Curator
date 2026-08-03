"""Tkinter smoke test for v0.14.0 grouped similarity review.

Run on Windows with ``python -X dev -m tests.test_v0140_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0140_gui``.  The test opens the real main
window, verifies the renamed catalog actions and grouped duplicate layout, then
closes it without touching any user catalog.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder
from quality_analysis import analyze_catalog_quality


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0140_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        try:
            from app import DatasetToolsApp

            source = root_path / "images"
            source.mkdir()
            # Solid-color images have distinct bytes/content identities but the
            # same 64-bit difference hash, producing one deterministic group.
            for filename, color in (
                ("first.png", "#6F85A3"),
                ("second.png", "#A36F85"),
                ("third.png", "#85A36F"),
            ):
                Image.new("RGB", (72, 96), color).save(source / filename)

            database = root_path / "catalog" / "dataset_tools.db"
            import_catalog_folder(
                CatalogImportOptions(
                    source_folder=source,
                    target_database=database,
                    mode="create",
                    recursive=True,
                    create_image_set=True,
                    image_set_name="Imported",
                )
            )
            analyze_catalog_quality(database)

            root = tk.Tk()
            root.geometry("1450x980")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            root.update()
            root.update_idletasks()

            assert application.create_catalog_from_folder_button.cget("text") == "Create from Images…"
            assert application.import_catalog_folder_button.cget("text") == "Add Images…"

            browser = application.catalog_browser
            browser.selected_image_ids = {1}
            browser._select_saved_image_set((2, 3))
            assert browser.selected_image_ids == {1, 2, 3}

            browser.apply_external_query("duplicate:100", remember=False)
            root.update_idletasks()
            assert browser.duplicate_review_threshold == 100
            assert browser.duplicate_review_clusters == ((1, 2, 3),)
            assert len(browser.duplicate_group_frames) == 1
            assert len(browser.cards_by_id) == 3

            browser.apply_external_query("quality:analyzed", remember=False)
            root.update_idletasks()
            assert browser.duplicate_review_threshold is None
            assert not browser.duplicate_group_frames
            assert len(browser.cards_by_id) == 3

            application.dataset_readiness.shutdown()
            browser.shutdown()
            root.destroy()
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "v0.14.0 GUI smoke test passed: catalog labels, additive set selection, "
        "grouped similarity review, and ordinary-grid restoration."
    )


if __name__ == "__main__":
    run()
