"""Windows GUI smoke test for v0.27.4 settings and viewer refinements."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

from PIL import Image

from browser_workflow import BrowserFilterState
from browser_workflow_dialogs import BrowserFiltersDialog
from image_review_dialog import ImageReviewDialog
from settings_dialog import SettingsDialog
from settings_manager import AppSettings
from test_v0273_gui import run as run_v0273


def _descendants(widget: tk.Misc) -> tuple[tk.Misc, ...]:
    found: list[tk.Misc] = []
    for child in widget.winfo_children():
        found.append(child)
        found.extend(_descendants(child))
    return tuple(found)


def _tab_labels(notebook: object) -> tuple[str, ...]:
    tabs = notebook.tabs()
    return tuple(str(notebook.tab(tab, "text")) for tab in tabs)


def _verify_v0274_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0274_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        settings_dialog: SettingsDialog | None = None
        filters_dialog: BrowserFiltersDialog | None = None
        review_dialog: ImageReviewDialog | None = None
        try:
            root = tk.Tk()
            root.geometry("1000x720")
            settings_dialog = SettingsDialog(
                root,
                settings=AppSettings(),
                on_save=lambda _settings: None,
                initial_section="paths",
            )
            root.update()
            assert settings_dialog.save_button.winfo_viewable()
            assert settings_dialog.save_button.cget("text") == "Save"
            assert "Filter Settings" in _tab_labels(settings_dialog.notebook)
            settings_dialog.destroy()
            settings_dialog = None

            filters_dialog = BrowserFiltersDialog(
                root,
                initial_state=BrowserFilterState(),
                image_sets=(),
                initial_section="filter_settings",
            )
            root.update()
            assert "Filter Settings" in _tab_labels(filters_dialog.notebook)
            visible_check_text = {
                str(widget.cget("text"))
                for widget in _descendants(filters_dialog)
                if isinstance(widget, ttk.Checkbutton)
            }
            assert any(
                label.startswith("Possible Duplicates (uses 96%")
                for label in visible_check_text
            )
            filters_dialog.destroy()
            filters_dialog = None

            image_path = Path(temporary) / "viewer.jpg"
            Image.new("RGB", (320, 200), "navy").save(image_path)
            record = SimpleNamespace(
                image_id=1,
                source_path=image_path,
                filename=image_path.name,
                source_video="",
                video_timestamp_seconds=None,
            )
            review_dialog = ImageReviewDialog(
                root,
                records=(record,),
                initial_image_id=1,
            )
            root.update()
            assert review_dialog.control_bar.place_info()
            review_dialog._actual_size()
            assert review_dialog.zoom_var.get() == "100%"
            review_dialog.destroy()
            review_dialog = None
            root.destroy()
            root = None
        finally:
            for dialog in (review_dialog, filters_dialog, settings_dialog):
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
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata


def run() -> None:
    print(
        "GUI smoke test note: several temporary app windows will open and "
        "close while historical interface checkpoints run. This is expected."
    )
    run_v0273()
    _verify_v0274_widgets()
    print(
        "v0.27.4 GUI smoke test passed: persistent Settings footer, Filter "
        "Settings ownership, explicit duplicate checkbox wording, and floating "
        "enlarged-image controls."
    )


if __name__ == "__main__":
    run()
