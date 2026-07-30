"""Tkinter smoke test for v0.17.0 Video Source Import.

Run on Windows with ``python -X dev test_v0170_gui.py`` or on Linux with
``xvfb-run -a python -X dev test_v0170_gui.py``.  FFmpeg is represented by a
validated fixture status; the smoke test never requires or invokes a real
FFmpeg installation.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0170_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from video_extraction import FFmpegStatus, VideoExtractionSummary
            from video_extraction_dialog import (
                VideoExtractionDialog,
                VideoExtractionReportDialog,
                VideoSourceResult,
            )

            source = root_path / "images"
            source.mkdir()
            Image.new("RGB", (800, 1000), "#6F85A3").save(source / "first.png")
            database = root_path / "catalog" / "dataset_tools.db"
            import_catalog_folder(
                CatalogImportOptions(
                    source_folder=source,
                    target_database=database,
                    mode="create",
                    recursive=True,
                    create_image_set=True,
                    image_set_name="Imported",
                )
            )

            fake_ffmpeg = root_path / "ffmpeg.exe"
            fake_ffmpeg.write_bytes(b"fixture")
            ffmpeg_status = FFmpegStatus(
                available=True,
                executable=fake_ffmpeg.resolve(),
                source="manual",
                version_line="ffmpeg version 8.0-test",
                error="",
            )

            root = tk.Tk()
            root.geometry("1450x1020")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            root.update()
            root.update_idletasks()

            assert (
                application.notebook.tab(application.analysis_tab, "text")
                == "Analyze & Update Catalog"
            )
            assert application.video_source_button.cget("text") == (
                "Extract Frames from Video…"
            )
            assert "FFmpeg" in application.video_ffmpeg_status_var.get()

            with patch(
                "video_extraction_dialog.discover_ffmpeg",
                return_value=ffmpeg_status,
            ):
                dialog = VideoExtractionDialog(
                    root,
                    settings=application.settings,
                    current_catalog=database,
                    on_settings_saved=application._save_video_settings,
                )
            root.update_idletasks()
            assert dialog.title() == "Extract Frames from Video"
            assert dialog.ffmpeg_path_var.get() == str(fake_ffmpeg.resolve())
            assert dialog.start_button.instate(("!disabled",))
            assert dialog._post_action() == "merge"
            assert dialog.catalog_target_var.get() == str(database.resolve())
            assert dialog.create_set_var.get()

            dialog.sampling_var.set("Scene changes")
            dialog._update_sampling_fields()
            assert str(dialog.interval_entry.cget("state")) == "disabled"
            assert str(dialog.scene_entry.cget("state")) == "normal"

            dialog.post_action_var.set("Save frames only")
            dialog._update_post_action_fields()
            assert dialog._post_action() == "save"
            assert str(dialog.create_set_check.cget("state")) == "disabled"
            assert not dialog.run_analysis_var.get()
            dialog.destroy()

            extracted = root_path / "frames" / "scene_000001.jpg"
            extracted.parent.mkdir()
            Image.new("RGB", (640, 480), "#557799").save(extracted)
            extraction = VideoExtractionSummary(
                source_video=root_path / "source movie.mkv",
                destination_folder=extracted.parent,
                output_files=(extracted,),
                sampling_description="One frame every 2 seconds",
                output_format="jpg",
                maximum_frames=500,
                command=(
                    str(fake_ffmpeg),
                    "-i",
                    str(root_path / "source movie.mkv"),
                    str(extracted.parent / "scene_%06d.jpg"),
                ),
                elapsed_seconds=1.25,
            )
            report = VideoExtractionReportDialog(
                root,
                VideoSourceResult(
                    extraction=extraction,
                    catalog_import=None,
                    run_analysis_requested=False,
                ),
            )
            root.update_idletasks()
            text_widgets = [
                widget
                for widget in report.winfo_children()[0].winfo_children()
                if isinstance(widget, tk.Text)
            ]
            assert len(text_widgets) == 1
            report_text = text_widgets[0].get("1.0", "end")
            assert "Frames written: 1" in report_text
            assert "Exact FFmpeg command:" in report_text
            assert "Multi-person action frames remain review-needed" in report_text
            report.destroy()

            application.dataset_readiness.shutdown()
            application.catalog_browser.shutdown()
            root.destroy()
            root = None
            shutdown_logging()
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

    print(
        "v0.17.0 GUI smoke test passed: Video Sources placement, external FFmpeg "
        "status/manual-path workflow, interval/scene controls, catalog handoff "
        "choices, complete extraction report, and clean log shutdown."
    )


if __name__ == "__main__":
    run()
