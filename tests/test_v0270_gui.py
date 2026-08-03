"""Windows GUI smoke test for the v0.27.0 browser workflow reorganization."""

from __future__ import annotations

import gc
import os
import tempfile
import tkinter as tk

from pathlib import Path

from browser_workflow import BrowserFilterState
from browser_workflow_dialogs import BrowserFiltersDialog, CurationOptionsDialog
from test_v0260_gui import run as run_v0260


def _verify_v0270_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0270_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        filter_dialog: BrowserFiltersDialog | None = None
        curation_dialog: CurationOptionsDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1400x960")
            application = DatasetToolsApp(root)
            application.notebook.select(application.browser_tab)
            root.update()
            browser = application.catalog_browser

            assert browser.filter_button.cget("text") == "Filters"
            assert browser.filter_button.cget("style") == "TButton"
            assert not hasattr(browser, "curation_handle")
            # This historical workflow check runs against the current
            # application. v0.27.2 made restart semantics explicit on every
            # provider card, so the inherited assertion must use the current
            # user-facing label rather than the v0.27.0 wording.
            assert application.run_body_analysis_button.cget("text") == (
                "Run / Restart Body"
            )

            browser.browser_filter_state = BrowserFilterState(
                face_state="Has face",
                body_state="Full body",
            )
            browser._update_filter_button_state()
            assert browser.filter_button.cget("text") == "Filters On"
            assert browser.filter_button.cget("style") == "Active.TButton"

            filter_dialog = BrowserFiltersDialog(
                browser,
                initial_state=browser.browser_filter_state,
                image_sets=(),
                initial_section="body",
            )
            root.update()
            assert filter_dialog.notebook.tab(
                filter_dialog.notebook.select(), "text"
            ) == "Body / Pose"
            filter_dialog._clear_controls()
            filter_dialog._apply()
            assert filter_dialog.result is not None
            assert filter_dialog.result.is_active() is False
            filter_dialog = None

            curation_dialog = CurationOptionsDialog(
                browser,
                initial=browser.curation_options,
            )
            root.update()
            assert curation_dialog.title() == "Remove Unnecessary Images"
            curation_dialog.destroy()
            curation_dialog = None

            # Collect destroyed dialog cycles before the application removes
            # this root's Tcl interpreter. This keeps Tk Variable finalizers on
            # the main thread under Python 3.14's development-mode checks.
            gc.collect()
            application._finish_close()
            root = None
        finally:
            for dialog in (filter_dialog, curation_dialog):
                if dialog is not None:
                    try:
                        dialog.destroy()
                    except tk.TclError:
                        pass
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
    run_v0260()
    _verify_v0270_widgets()
    print(
        "v0.27.0 GUI smoke test passed: reorganized filters, conspicuous active "
        "state, separate curation, and Analysis-tab body workflow."
    )


if __name__ == "__main__":
    run()
