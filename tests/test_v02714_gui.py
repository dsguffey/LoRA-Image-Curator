"""Current cumulative Windows GUI smoke entry point for v0.27.14."""

from __future__ import annotations

import gc
import os
import tempfile
import tkinter as tk

from pathlib import Path

from settings_dialog import SettingsDialog
from test_v02713_gui import run as run_v02713


def _tab_labels(notebook: object) -> tuple[str, ...]:
    return tuple(str(notebook.tab(tab, "text")) for tab in notebook.tabs())


def _verify_v02714_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v02714_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        settings_dialog: SettingsDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1280x800")
            application = DatasetToolsApp(root)
            root.update()

            assert not application.florence_running_label.winfo_ismapped()
            assert not application.face_running_label.winfo_ismapped()
            assert not application.body_running_label.winfo_ismapped()
            application._set_running_provider("florence")
            root.update_idletasks()
            assert application.florence_running_label.winfo_ismapped()
            assert not application.face_running_label.winfo_ismapped()
            assert application.current_work_var.get() == (
                "Current work: Image Captioning / Florence-2"
            )
            application._set_running_provider(None)
            root.update_idletasks()
            assert not application.florence_running_label.winfo_ismapped()

            settings_dialog = SettingsDialog(
                root,
                settings=application.settings,
                on_save=lambda _settings: None,
                initial_section="captioning",
            )
            root.update()
            labels = _tab_labels(settings_dialog.notebook)
            assert "Catalog & Paths" in labels
            assert "Image Captioning" in labels
            assert "Face Scanning" in labels
            assert "Body / Pose" in labels
            assert "Video" in labels
            assert "Privacy & Diagnostics" in labels
            assert settings_dialog.caption_subfolders_var.get() is True
            assert settings_dialog.face_subfolders_var.get() is True
            settings_dialog.destroy()
            settings_dialog = None

            gc.collect()
            application._finish_close()
            root = None
        finally:
            if settings_dialog is not None:
                try:
                    settings_dialog.destroy()
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
    run_v02713()
    _verify_v02714_widgets()
    print(
        "v0.27.14 cumulative GUI smoke test passed: temporary green provider "
        "markers, shared progress ownership, function-organized Settings, and "
        "subfolder defaults remained visible and Tk-safe."
    )


if __name__ == "__main__":
    run()
