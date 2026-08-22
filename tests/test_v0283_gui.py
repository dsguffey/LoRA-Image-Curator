"""Current cumulative Windows GUI smoke entry point for v0.28.3."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from catalog import Catalog
from test_v0282_gui import run as run_v0282


def run() -> None:
    """Replay the GUI and verify the detection-only completion warning."""
    run_v0282()
    with tempfile.TemporaryDirectory(prefix="lora_v0283_gui_") as temporary:
        root_path = Path(temporary)
        database = root_path / "dataset_tools.db"
        report = root_path / "face_results.csv"
        with Catalog(database):
            pass
        report.write_text("status,face_count\nsuccess,1\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "APPDATA": temporary,
                "LORA_IMAGE_CURATOR_TEST_MODE": "1",
            },
        ):
            from app import DatasetToolsApp

            tk_root = tk.Tk()
            tk_root.withdraw()
            application = DatasetToolsApp(tk_root)
            summary = SimpleNamespace(
                total_images=1,
                total_seconds=0.1,
                output_csv=report,
                catalog_database=database,
                faces_detected=1,
                suggestions_created=0,
                reused_images=0,
                generated_images=1,
                failed_images=0,
                identity_matching_enabled=False,
                identity_profile_warning=(
                    "No usable face was found in the identity reference folder."
                ),
            )
            with (
                patch("app.messagebox.showwarning") as warning,
                patch("app.messagebox.showinfo") as information,
            ):
                application._handle_face_completion(summary)
                warning.assert_called_once()
                information.assert_not_called()
                assert "Face detection completed" in warning.call_args.args[1]
            assert "identity matching was skipped" in (
                application.progress_warning_var.get()
            )
            assert application.summary_vars["faces_detected"].get() == "1"
            assert application.summary_vars["identity_suggestions"].get() == "0"
            application._finish_close()

    print(
        "v0.28.3 cumulative GUI smoke test passed: a missing identity profile "
        "finishes as a visible detection-only warning instead of a provider "
        "failure."
    )


if __name__ == "__main__":
    run()
