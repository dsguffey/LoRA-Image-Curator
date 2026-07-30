"""Windows GUI smoke test for v0.27.2 large-catalog safety controls."""

from __future__ import annotations

import os
import re
import tempfile
import tkinter as tk

from pathlib import Path

from settings_dialog import SettingsDialog
from test_v0271_gui import run as run_v0271
from ui_theme import AppTheme


def _menu_labels(menu: tk.Menu) -> tuple[str, ...]:
    end = menu.index("end")
    if end is None:
        return ()
    return tuple(
        str(menu.entrycget(index, "label"))
        for index in range(int(end) + 1)
        if str(menu.type(index)) != "separator"
    )


def _verify_theme_contract() -> None:
    """Catch invalid application palette references without requiring Tk."""
    app_source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    referenced_fields = set(
        re.findall(r"\bself\.theme\.([A-Za-z_][A-Za-z0-9_]*)", app_source)
    )
    declared_fields = set(AppTheme.__dataclass_fields__)
    missing_fields = sorted(referenced_fields - declared_fields)
    assert not missing_fields, (
        "app.py references undefined AppTheme fields: "
        + ", ".join(missing_fields)
    )


def _verify_v0272_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0272_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        settings_dialog: SettingsDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1200x720")
            application = DatasetToolsApp(root)
            root.update()

            assert application.analysis_canvas.winfo_exists()
            assert str(application.analysis_canvas.cget("scrollregion")).strip()
            assert application.input_folder_count_var.get().startswith(
                "Images found:"
            )
            assert application.pause_analysis_button.cget("text") == "Pause Run"
            assert application.run_florence_button.cget("text") == (
                "Run / Restart Florence"
            )
            assert application.run_face_analysis_button.cget("text") == (
                "Run / Restart Face"
            )
            assert application.run_body_analysis_button.cget("text") == (
                "Run / Restart Body"
            )
            assert application.florence_provider_device_var.get().startswith(
                "Device:"
            )
            assert application.face_provider_device_var.get().startswith("Device:")
            assert application.body_provider_device_var.get().startswith("Device:")

            application.notebook.select(application.browser_tab)
            root.update()
            assert _menu_labels(application.selected_images_menu) == (
                "Quarantine Selected…",
                "Restore Selected from Quarantine…",
                "Send Selected Files to Recycle Bin…",
                "Remove Selected Records from Catalog…",
            )

            settings_dialog = SettingsDialog(
                root,
                settings=application.settings,
                on_save=lambda _settings: None,
                initial_section="paths",
            )
            root.update()
            assert settings_dialog.delete_catalog_record_var.get() is False
            settings_dialog.destroy()
            settings_dialog = None

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
    print(
        "GUI smoke test note: several temporary app windows will open and "
        "close while historical interface checkpoints run. This is expected."
    )
    _verify_theme_contract()
    run_v0271()
    _verify_v0272_widgets()
    print(
        "v0.27.2 GUI smoke test passed: scrollable analysis, folder counts, "
        "provider device/run controls, Browser selected-image actions, and "
        "conservative record-removal settings."
    )


if __name__ == "__main__":
    run()
