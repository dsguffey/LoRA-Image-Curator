"""Tkinter smoke test for v0.16.0 Finalize & Export.

Run on Windows with ``python -X dev test_v0160_gui.py`` or on Linux with
``xvfb-run -a python -X dev test_v0160_gui.py``. The test opens the real main
window, verifies the renamed tab and scope handoff card, then opens the shared
export dialog with readiness context and checks clean logging shutdown.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from pathlib import Path

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0160_gui_") as temporary:
        root_path = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root_path / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging
            from export_dialog import DatasetExportDialog, PROFILE_LABELS

            source = root_path / "images"
            source.mkdir()
            for filename, color in (
                ("first.png", "#6F85A3"),
                ("second.png", "#A36F85"),
            ):
                Image.new("RGB", (800, 1000), color).save(source / filename)

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

            root = tk.Tk()
            root.geometry("1450x980")
            application = DatasetToolsApp(root)
            application._activate_catalog(database, load=True)
            application.notebook.select(application.readiness_tab)
            root.update()
            root.update_idletasks()

            assert (
                application.notebook.tab(application.readiness_tab, "text")
                == "Finalize & Export"
            )
            frame = application.dataset_readiness
            assert frame._current_report is not None
            assert frame._export_scope_button is not None
            assert frame._export_scope_button.cget("text") == "Export Training Data…"
            assert str(frame._export_scope_button.cget("state")) == "normal"
            assert frame._handoff_scope_var is not None
            assert frame._handoff_scope_var.get() == "All catalog images"
            assert frame._handoff_profile_var is not None
            assert frame._handoff_profile_var.get() == "LoRA target: Flux Character LoRA"

            captured: dict[str, object] = {}

            def capture_scope(image_ids, scope_label, report, profile_key) -> None:
                captured.update(
                    image_ids=image_ids,
                    scope_label=scope_label,
                    report=report,
                    profile_key=profile_key,
                )

            frame.export_scope = capture_scope
            frame._export_current_scope()
            assert captured["image_ids"] == [1, 2]
            assert captured["scope_label"] == "All catalog images"
            assert captured["profile_key"] == "flux_character_lora"

            dialog = DatasetExportDialog(
                root,
                database_path=database,
                image_ids=[1, 2],
                settings=application.catalog_browser.settings,
                scope_label='Image set "Imported"',
                readiness_report=frame._current_report,
                initial_profile_key="flux_lora",
            )
            root.update_idletasks()
            assert dialog.title() == "Export Training Handoff"
            assert dialog.readme_var.get()
            assert "SD 1.5 LoRA" in PROFILE_LABELS
            assert "General / Other LoRA" in PROFILE_LABELS
            assert 'Scope: Image set "Imported"' in dialog.preflight_var.get()
            assert "Readiness:" in dialog.preflight_var.get()
            dialog.destroy()

            frame.shutdown()
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
        "v0.16.0 GUI smoke test passed: Finalize & Export scope handoff, "
        "pre-export readiness warnings, all LoRA presets, README output, and "
        "clean log shutdown."
    )


if __name__ == "__main__":
    run()
