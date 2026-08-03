"""Tkinter smoke test for v0.18.0 Training Text Validation.

Run on Windows with ``python -X dev -m tests.test_v0180_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0180_gui``. The test opens the real main
window, verifies profile-aware validation in Finalize & Export, follows the
Repeated Training Text result into the Catalog Browser, and checks clean log
shutdown.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_edits import CatalogEditService
from catalog_import import CatalogImportOptions, import_catalog_folder


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0180_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from export_dialog import DatasetExportDialog

            source = root_path / "images"
            source.mkdir()
            for filename, color in (
                ("first.png", "#6F85A3"),
                ("second.png", "#A36F85"),
                ("distinct.png", "#85A36F"),
            ):
                Image.new("RGB", (800, 1000), color).save(source / filename)

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
            edits = CatalogEditService(database)
            edits.set_manual_keyword((1, 2, 3), "test_subject")
            edits.add_manual_tags((1, 2), ("portrait", "studio"))
            edits.add_manual_tags((3,), ("armor", "action pose"))

            root = tk.Tk()
            root.geometry("1450x1020")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            application.notebook.select(application.readiness_tab)
            root.update()
            root.update_idletasks()

            frame = application.dataset_readiness
            assert frame._current_report is not None
            repeated_issue = next(
                issue
                for issue in frame._current_report.issues
                if issue.label == "Repeated Training Text"
            )
            assert repeated_issue.count == 2
            assert repeated_issue.query == "id:1 OR id:2"
            assert frame._issue_buttons["Repeated Training Text"].instate(
                ("!disabled",)
            )

            # The validation result must be actionable without inventing a
            # second caption-search interface.
            frame._issue_buttons["Repeated Training Text"].invoke()
            root.update()
            assert application.notebook.select() == str(application.browser_tab)
            assert application.catalog_browser.search_var.get() == "id:1 OR id:2"
            assert {
                record.image_id
                for record in application.catalog_browser.visible_records
            } == {1, 2}

            application.notebook.select(application.readiness_tab)
            root.update()
            dialog = DatasetExportDialog(
                root,
                database_path=database,
                image_ids=[1, 2, 3],
                settings=application.catalog_browser.settings,
                scope_label="All catalog images",
                readiness_report=frame._current_report,
                initial_profile_key="flux_lora",
            )
            root.update_idletasks()
            assert "Repeated training text: 2 images" in dialog.preflight_var.get()
            assert any(
                warning.endswith("Repeated Training Text")
                for warning in dialog._preflight_warnings()
            )
            dialog.destroy()

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
        "v0.18.0 GUI smoke test passed: profile-aware training-text validation, "
        "exact repeated-text browser results, live export preflight, and clean "
        "log shutdown."
    )


if __name__ == "__main__":
    run()
