"""Tkinter smoke test for v0.19.0 / Milestone 9B.

Run on Windows with ``python -X dev -m tests.test_v0190_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0190_gui``. The smoke test verifies the
Finalize & Export wording, the single duplicate-review control, the visible
LoRA target, confirmed empty-catalog replacement, and clean resource shutdown.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from catalog_edits import CatalogEditService
from catalog_import import CatalogImportOptions, import_catalog_folder


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0190_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            source = root_path / "images"
            source.mkdir()
            Image.new("RGB", (800, 1000), "#6F85A3").save(source / "first.png")
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

            # The first edit creates the session backup that leaked SQLite
            # connections in v0.18.0 when run under ``python -X dev``.
            CatalogEditService(database).set_manual_keyword((1,), "test_subject")

            root = tk.Tk()
            root.geometry("1450x1020")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            application.notebook.select(application.readiness_tab)
            root.update()
            root.update_idletasks()

            frame = application.dataset_readiness
            assert frame._current_report is not None
            assert "Exact Copies" not in frame._issue_buttons
            assert "Possible Duplicates" in frame._issue_buttons
            assert frame._handoff_scope_var is not None
            assert frame._handoff_scope_var.get() == "All catalog images"
            assert frame._handoff_profile_var is not None
            assert (
                frame._handoff_profile_var.get()
                == "LoRA target: Flux Character LoRA"
            )
            assert frame._export_scope_button is not None
            assert (
                frame._export_scope_button.cget("text")
                == "Export Training Data…"
            )

            # A named set remains descriptive without the redundant ``Scope:``
            # prefix inside the already-labeled Training Handoff card.
            frame.image_set_var.set("Set: Imported")
            frame._on_image_set_changed()
            root.update_idletasks()
            assert frame._handoff_scope_var.get() == 'Image set "Imported"'

            # New Empty Catalog must leave the existing database unchanged when
            # confirmation is declined.
            with (
                patch(
                    "app.filedialog.asksaveasfilename",
                    return_value=str(database),
                ),
                patch("app.messagebox.askyesno", return_value=False) as declined,
            ):
                application._create_empty_catalog()
            declined.assert_called_once()
            assert len(application.catalog_browser.all_records) == 1

            # A confirmed replacement empties catalog-owned data and
            # reactivates the same path.
            with (
                patch(
                    "app.filedialog.asksaveasfilename",
                    return_value=str(database),
                ),
                patch("app.messagebox.askyesno", return_value=True) as confirmation,
            ):
                application._create_empty_catalog()
            confirmation.assert_called_once()
            root.update()
            assert application.catalog_browser.catalog_path == database.resolve()
            assert application.catalog_browser.all_records == []

            frame.shutdown()
            application.catalog_browser.shutdown()
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
        "v0.19.0 GUI smoke test passed: clear Finalize & Export wording, visible "
        "LoRA target, one duplicate-review control, confirmed catalog "
        "replacement, and clean SQLite/log shutdown."
    )


if __name__ == "__main__":
    run()
