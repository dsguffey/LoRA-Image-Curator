"""Tkinter smoke test for v0.15.0 selection culling and clean shutdown.

Run on Windows with ``python -X dev -m tests.test_v0150_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0150_gui``.  The test opens the real main
window, verifies grouped duplicate review and the Remove Unnecessary Images
report/action, then explicitly releases logging before Windows removes the
temporary app-data directory.
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
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0150_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from cull_report_dialog import CullReportDialog

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
            assert browser.filter_button.cget("text") == "Filters"
            assert not hasattr(browser, "curation_handle")
            assert not hasattr(browser, "remove_unnecessary_button")

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

            # The small solid fixtures deliberately meet several removal
            # criteria. Schedule acceptance of the real modal report so the
            # browser method can be exercised without manual interaction.
            def accept_open_report() -> None:
                def descendants(widget: tk.Misc) -> list[tk.Misc]:
                    children: list[tk.Misc] = []
                    for child in widget.winfo_children():
                        children.append(child)
                        children.extend(descendants(child))
                    return children

                reports = [
                    widget
                    for widget in descendants(root)
                    if isinstance(widget, CullReportDialog)
                ]
                if reports:
                    assert len(reports[0].decision_tree.get_children()) == 3
                    reports[0]._apply()
                else:
                    root.after(20, accept_open_report)

            root.after(20, accept_open_report)
            browser._remove_unnecessary_images()
            assert browser.selected_image_ids == set()
            assert "deselected 3" in browser.edit_status_var.get()

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
            # Closing logging here as well makes failed assertions release the
            # Windows file handle before TemporaryDirectory cleanup.
            try:
                shutdown_logging()
            except NameError:
                pass
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "v0.15.0 GUI smoke test passed: grouped review, Remove Unnecessary "
        "Images report/action, Pillow quality analysis, and clean log shutdown."
    )


if __name__ == "__main__":
    run()
