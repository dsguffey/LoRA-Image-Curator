"""Windows GUI smoke test for the v0.27.1 provider and browser polish."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from browser_workflow import BrowserFilterState
from browser_workflow_dialogs import BrowserFiltersDialog
from settings_dialog import SettingsDialog
from test_v0270_gui import run as run_v0270


def _verify_v0271_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0271_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        filter_dialog: BrowserFiltersDialog | None = None
        settings_dialog: SettingsDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1400x960")
            application = DatasetToolsApp(root)
            application.notebook.select(application.browser_tab)
            root.update()
            browser = application.catalog_browser

            assert browser.filter_button.winfo_parent() == browser.sort_combo.winfo_parent()
            assert browser.first_page_button.cget("text") == "First"
            assert browser.back_ten_pages_button.cget("text") == "−10"
            assert browser.previous_page_button.cget("text") == "Prev"
            assert browser.load_more_button.cget("text") == "Next"
            assert browser.forward_ten_pages_button.cget("text") == "+10"
            assert browser.last_page_button.cget("text") == "Last"
            assert application.run_florence_button.cget("text") == (
                "Run / Restart Florence"
            )
            assert application.run_face_analysis_button.cget("text") == (
                "Run / Restart Face"
            )
            application.run_face_analysis_var.set(False)
            application._toggle_face_controls()
            assert all(
                str(widget.cget("state")) == "normal"
                for widget in application.face_setting_widgets
            )
            assert application.run_body_analysis_button.cget("text") == (
                "Run / Restart Body"
            )
            assert "No catalog selected" in (
                application.florence_provider_status_var.get()
            )

            filter_dialog = BrowserFiltersDialog(
                browser,
                initial_state=BrowserFilterState(face_state="Has face"),
                image_sets=(),
            )
            root.update()
            filter_dialog._clear_and_apply()
            assert filter_dialog.result is not None
            assert filter_dialog.result.is_active() is False
            filter_dialog = None

            settings_dialog = SettingsDialog(
                root,
                settings=application.settings,
                on_save=lambda _settings: None,
                initial_section="analysis",
            )
            root.update()
            assert settings_dialog.reuse_analysis_var.get() == (
                application.settings.reuse_stored_analysis
            )
            settings_dialog.destroy()
            settings_dialog = None

            application._finish_close()
            root = None
        finally:
            for dialog in (filter_dialog, settings_dialog):
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
    run_v0270()
    _verify_v0271_widgets()
    print(
        "v0.27.1 GUI smoke test passed: adjacent Sort/Filters controls, "
        "immediate clear, provider Run controls/status, settings-owned run "
        "options, and large-catalog navigation."
    )


if __name__ == "__main__":
    run()
