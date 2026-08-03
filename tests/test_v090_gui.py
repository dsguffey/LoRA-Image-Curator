"""Tkinter smoke test for the retained v0.9.0 export workflow.

The test creates its own temporary catalog and source images, so it does not
modify or depend on the user's real catalog.

Run on Windows with:

    python -m tests.test_v090_gui

Optionally save a screenshot:

    python -m tests.test_v090_gui screenshot.png

Run on Linux with:

    xvfb-run -a python -m tests.test_v090_gui
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import tkinter as tk

from contextlib import closing
from pathlib import Path

from catalog_browser import CatalogBrowserFrame
from export_dialog import DatasetExportDialog, ExportProgressDialog
from test_milestone_7d import _seed_catalog


def run(screenshot_path: Path | None = None) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root_path = Path(temporary)
        database, image_ids, _sources = _seed_catalog(root_path)

        root = tk.Tk()
        root.geometry("1400x960")
        browser = CatalogBrowserFrame(root, initial_catalog_path=database)
        browser.pack(fill="both", expand=True)
        browser.selected_image_ids = set(image_ids[:2])
        browser._selection_changed(image_ids[1])
        root.update()
        root.update_idletasks()

        assert browser.detail_filename_var.get() == "Selection Review"
        assert browser.command_state()["has_selection"]
        # Milestone 8B deliberately moved training preview ownership out of the
        # browser. The export dialog remains the authoritative preview surface.
        assert not hasattr(browser, "training_preview_frame")

        dialog = DatasetExportDialog(
            browser,
            database_path=database,
            image_ids=image_ids[:2],
            settings=browser.settings,
        )
        dialog.destination_var.set(str(root_path / "gui_export"))
        dialog.update()
        dialog.update_idletasks()
        plan = dialog._build_plan()
        assert plan is not None
        assert plan.requested_count == 2
        assert plan.planned_count == 2
        assert plan.image_file_count == 2
        assert plan.sidecar_file_count == 2
        assert "subject_token" in dialog.sample_var.get()

        # Exercise the worker-thread progress dialog rather than testing only
        # static controls. The temporary sources make this safe and repeatable.
        progress = ExportProgressDialog(
            dialog,
            plan=plan,
            repository=dialog.repository,
        )
        deadline = time.monotonic() + 10.0
        while progress.result is None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert progress.result is not None
        assert progress.result.status == "complete"
        assert progress.result.exported_count == 2
        progress.grab_release()
        progress.destroy()

        if screenshot_path is not None:
            from PIL import ImageGrab

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            x = dialog.winfo_rootx()
            y = dialog.winfo_rooty()
            ImageGrab.grab(
                bbox=(x, y, x + dialog.winfo_width(), y + dialog.winfo_height())
            ).save(screenshot_path)

        dialog.grab_release()
        dialog.destroy()
        browser.shutdown()
        root.destroy()

        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        print(
            "v0.9.0 GUI smoke test passed: Export Selected, profile preview, "
            "dialog planning, background export progress, copied-image counts, "
            "and sidecar counts."
        )


if __name__ == "__main__":
    import sys

    screenshot = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run(screenshot)
