"""Tkinter smoke test for v0.13.0 catalog-management placement.

Run on Windows with ``python -X dev -m tests.test_v0130_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0130_gui``.  The test opens the real main
window briefly, verifies the tab ownership/state wiring, then closes it.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder
from readiness_frame import ALL_CATALOG_IMAGES_LABEL


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0130_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        try:
            # app.py configures logging at import time, so import it only after
            # redirecting APPDATA into this disposable test directory.
            from app import DatasetToolsApp

            source = root_path / "images"
            source.mkdir()
            Image.new("RGB", (72, 96), "#6F85A3").save(source / "photo.jpg")
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

            root = tk.Tk()
            root.geometry("1450x980")
            application = DatasetToolsApp(root)
            root.update()
            root.update_idletasks()

            assert application.new_empty_catalog_button.cget("text") == "New Empty Catalog…"
            assert application.create_catalog_from_folder_button.cget("text") == "Create from Folder…"
            assert application.open_catalog_button.cget("text") == "Open Catalog…"
            assert application.import_catalog_folder_button.cget("text") == "Import Folder…"
            assert application.delete_catalog_button.cget("text") == "Delete Catalog…"
            assert not hasattr(application.catalog_browser, "delete_catalog_button")

            application._activate_catalog(database, load=True)
            root.update_idletasks()
            assert str(application.import_catalog_folder_button.cget("state")) == "normal"
            assert str(application.delete_catalog_button.cget("state")) == "normal"
            assert application.catalog_browser.catalog_path == database.resolve()
            assert application.dataset_readiness.image_set_var.get() == ALL_CATALOG_IMAGES_LABEL

            application.dataset_readiness.shutdown()
            application.catalog_browser.shutdown()
            root.destroy()
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "v0.13.0 GUI smoke test passed: SQLite Catalog owns lifecycle/import "
        "controls and readiness defaults to All catalog images."
    )


if __name__ == "__main__":
    run()
