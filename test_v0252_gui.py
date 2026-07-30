"""Windows GUI smoke test for the v0.25.3 browser-pruning workflow."""

from __future__ import annotations

import gc
import os
import tempfile
import tkinter as tk

from pathlib import Path

from browser_workflow import BrowserFilterState, READINESS_ISSUE_LABELS
from browser_workflow_dialogs import BrowserFiltersDialog, KeywordSelectionDialog
from image_sets import ImageSetSummary
from test_v0250_gui import run as run_v0250


def _verify_v0252_widgets() -> None:
    """Build the real widgets and exercise dialog result collection."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0252_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1280x850")
            application = DatasetToolsApp(root)
            application.notebook.select(application.browser_tab)
            root.update()
            browser = application.catalog_browser

            assert browser.filter_button.cget("text") == "Filters"
            assert not hasattr(browser, "clear_filters_button")
            assert not hasattr(browser, "curation_handle")

            summary = ImageSetSummary(
                set_id=9,
                name="Interview Pruned",
                image_count=43,
                created_at="",
                updated_at="",
            )
            filter_dialog = BrowserFiltersDialog(
                browser,
                initial_state=BrowserFilterState(),
                image_sets=(summary,),
            )
            filter_dialog.image_set_var.set("Interview Pruned (43)")
            filter_dialog.issue_vars["Blur"].set(True)
            filter_dialog.issue_vars["Possible Duplicates"].set(True)
            filter_dialog._apply()
            assert filter_dialog.result is not None
            assert filter_dialog.result.image_set_id == 9
            assert filter_dialog.result.readiness_issues == frozenset(
                {"Blur", "Possible Duplicates"}
            )
            # The dialog destroys its Tk window when Apply is invoked. Drop
            # the Python owner and collect its variable/widget cycles while
            # this root's Tcl interpreter is still alive and on the main
            # thread. Python 3.14 otherwise may finalize a StringVar later on
            # a short-lived provider worker and emit an unraisable exception.
            filter_dialog = None

            keyword_dialog = KeywordSelectionDialog(browser, action="deselect")
            keyword_dialog.keyword_var.set("close-up, interview")
            keyword_dialog.match_var.set("All keywords")
            keyword_dialog._apply()
            assert keyword_dialog.result == (("close-up", "interview"), True)
            keyword_dialog = None
            gc.collect()

            assert len(READINESS_ISSUE_LABELS) == 11
            application._finish_close()
            root = None
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


def run() -> None:
    run_v0250()
    _verify_v0252_widgets()
    source = Path(__file__).with_name("image_set_dialog.py").read_text(
        encoding="utf-8"
    )
    assert 'text="Update Image Set"' in source
    assert 'text="Select Image Set in Browser"' in source
    print(
        "v0.25.3 GUI smoke test passed: unified filters, all readiness checks, "
        "keyword selection, threshold help, and image-set workflow controls."
    )


if __name__ == "__main__":
    run()
