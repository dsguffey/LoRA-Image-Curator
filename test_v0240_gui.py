"""Windows GUI smoke test for the v0.24.0 usability polish pass.

Run after installing the normal application dependencies:

    python -X dev test_v0240_gui.py

The established v0.23.0 smoke test runs first. This focused extension then
verifies that help icons construct, repaint, and display real help text through
their supported hover interaction. It also verifies that the video dialog
exists before its deferred FFmpeg probe begins.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from settings_manager import AppSettings
from test_v0230_gui import run as run_v0230
from ui_helpers import HelpIcon
from video_extraction_dialog import VideoExtractionDialog


def _verify_help_icon_interaction(root: tk.Tk) -> None:
    """Exercise the user-visible event path rather than inspecting icon presence.

    This probe deliberately sends real Tk events.  The original smoke test only
    checked the widget type and theme, which allowed later visual bindings to
    replace the tooltip's hover binding without failing the test.
    """
    host = tk.Toplevel(root)
    host.title("Dataset Tools help-icon smoke probe")
    help_icon = HelpIcon(
        host,
        "Smoke-test contextual help is visible.",
        delay_ms=0,
    )
    help_icon.pack(padx=20, pady=20)
    root.update()

    help_icon.event_generate("<Enter>")
    root.update()
    assert help_icon.tooltip.is_visible
    assert help_icon.tooltip._window is not None
    labels = help_icon.tooltip._window.winfo_children()
    assert labels
    assert labels[0].cget("text") == "Smoke-test contextual help is visible."

    # A help icon is a hover affordance, not a button. Clicking it must not pin,
    # dismiss, or otherwise change the already-visible hover tip.
    help_icon.event_generate("<Button-1>", x=8, y=8)
    root.update()
    assert help_icon.tooltip.is_visible

    help_icon.event_generate("<Leave>")
    root.update()
    assert not help_icon.tooltip.is_visible
    host.destroy()


def run() -> None:
    run_v0230()

    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0240_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        dialog: VideoExtractionDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1450x1020")
            application = DatasetToolsApp(root)
            root.update_idletasks()

            assert isinstance(application.face_reference_folder_help, HelpIcon)
            assert "reference images" in (
                application.face_reference_folder_help.help_text.casefold()
            )
            assert isinstance(application.catalog_browser.search_help, HelpIcon)

            application.theme_key_var.set("dark_workstation")
            application._apply_theme_setting()
            root.update_idletasks()
            assert (
                application.face_reference_folder_help.cget("background")
                == "#282D33"
            )
            _verify_help_icon_interaction(root)

            dialog = VideoExtractionDialog(
                root,
                settings=AppSettings(),
                current_catalog=None,
            )
            assert dialog.winfo_exists()
            assert dialog.ffmpeg_status_var.get() == "Checking FFmpeg…"
            assert dialog._ffmpeg_discovery_thread is None
            dialog._request_close()
            dialog = None

            # Use the real application close path. It owns and cancels the
            # app/menu/browser ``after()`` callbacks before destroying Tk.
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

    print(
        "v0.24.3 GUI smoke test passed: legacy GUI coverage, visible hover "
        "help, and immediately constructed video extraction dialog."
    )


if __name__ == "__main__":
    run()
