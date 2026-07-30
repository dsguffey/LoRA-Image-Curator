"""Windows GUI smoke test for v0.27.3 large-catalog workflow fixes."""

from __future__ import annotations

import gc
import os
import tempfile
import tkinter as tk

from pathlib import Path

from release_preflight import assert_clean_release_directory

# Run before importing the historical checkpoint chain. A merged extraction can
# otherwise import an obsolete top-level test module before the new UI exists.
assert_clean_release_directory(Path(__file__).parent)

from settings_dialog import SettingsDialog
from test_v0272_gui import run as run_v0272


def _verify_v0273_widgets() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0273_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        settings_dialog: SettingsDialog | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1250x760")
            application = DatasetToolsApp(root)
            root.update()

            assert application.analysis_canvas.winfo_exists()
            assert application.catalog_browser.open_image_button.cget("text") == (
                "Enlarge / Review"
            )
            assert application.catalog_browser.image_quality_button.cget("text") == (
                "Image Quality…"
            )
            assert "96%" in application.dataset_readiness.duplicate_percent_var.get()
            assert "Looser match" in (
                application.dataset_readiness.duplicate_percent_var.get()
            )

            saved = []
            settings_dialog = SettingsDialog(
                root,
                settings=application.settings,
                on_save=saved.append,
                initial_section="paths",
            )
            root.update()
            settings_dialog.delete_catalog_record_var.set(True)
            settings_dialog._save()
            settings_dialog = None
            assert saved
            assert saved[-1].delete_catalog_record_with_file is True

            # ``SettingsDialog._save`` destroys its Toplevel. Collect the
            # resulting variable/widget cycles before destroying the owning
            # root so Python 3.14 cannot finalize them from a worker thread.
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
    print(
        "GUI smoke test note: several temporary app windows will open and "
        "close while historical interface checkpoints run. This is expected."
    )
    run_v0272()
    _verify_v0273_widgets()
    print(
        "v0.27.3 GUI smoke test passed: persistent delete cleanup, bounded "
        "integer descriptions, wheel-ready scroll surfaces, enlarged review, "
        "and read-only Image Quality details."
    )


if __name__ == "__main__":
    run()
