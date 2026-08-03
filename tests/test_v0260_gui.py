"""Windows GUI smoke test for v0.26.0 body/file-action settings and wiring."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from test_v0252_gui import run as run_v0252


def _verify_v0260_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0260_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        dialog = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from settings_dialog import SettingsDialog

            root = tk.Tk()
            root.geometry("1280x850")
            application = DatasetToolsApp(root)
            application.notebook.select(application.browser_tab)
            root.update()

            browser = application.catalog_browser
            assert browser.quarantine_button.cget("text") == "Quarantine Selected"
            assert browser.restore_quarantine_button.cget("text") == "Restore Selected"

            dialog = SettingsDialog(
                root,
                settings=application.settings,
                on_save=lambda _settings: None,
            )
            root.update()
            assert dialog.telemetry_var.get() is False
            # v0.27.2 superseded the old "always confirm Recycle Bin" checkbox
            # with confirmation based on the active browser page size. The
            # persisted value remains only for settings-file compatibility.
            assert application.settings.confirm_trash_deletion is True
            assert not hasattr(dialog, "confirm_trash_var")
            assert 60 <= dialog.full_body_percent_var.get() <= 100
            assert dialog.body_model_var.get().endswith(
                "pose_landmarker_full.task"
            )
            dialog.destroy()
            dialog = None

            application._finish_close()
            root = None
        finally:
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
    run_v0252()
    _verify_v0260_widgets()
    app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    browser_source = (Path(__file__).resolve().parents[1] / "catalog_browser.py").read_text(
        encoding="utf-8"
    )
    assert 'label="Run Body / Pose Analysis…"' in app_source
    assert 'label="Privacy & Third-Party Products"' in app_source
    assert 'self.bind_all("<Delete>"' in browser_source
    print(
        "v0.26.0 GUI smoke test passed: settings/privacy defaults, body-analysis "
        "wiring, quarantine controls, and Delete-to-Recycle-Bin shortcut."
    )


if __name__ == "__main__":
    run()
