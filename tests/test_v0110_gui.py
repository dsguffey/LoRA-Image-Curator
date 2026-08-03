"""Tkinter smoke test for the v0.11.1 Dataset Readiness controls.

Run on Windows with ``python -m tests.test_v0110_gui`` or on Linux with
``xvfb-run -a python -m tests.test_v0110_gui``.
"""

from __future__ import annotations

import tempfile
import tkinter as tk

from pathlib import Path
from tkinter import ttk

from PIL import Image

from catalog_browser import CatalogBrowserFrame
from readiness_frame import DatasetReadinessFrame
from test_milestone_7d import _seed_catalog


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root_path = Path(temporary)
        database, _image_ids, _sources = _seed_catalog(root_path)
        for source in _sources[:2]:
            Image.new("RGB", (64, 64), "#808080").save(source, format="JPEG")
        root = tk.Tk()
        root.geometry("1450x980")
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)
        browser_tab = ttk.Frame(notebook)
        readiness_tab = ttk.Frame(notebook)
        notebook.add(browser_tab, text="Catalog Browser")
        notebook.add(readiness_tab, text="Dataset Readiness")
        browser = CatalogBrowserFrame(browser_tab, initial_catalog_path=database)
        browser.pack(fill="both", expand=True)
        readiness = DatasetReadinessFrame(
            readiness_tab,
            show_query=lambda query: browser.apply_external_query(query),
        )
        readiness.pack(fill="both", expand=True)
        readiness.set_records(browser.all_records, str(database))
        root.update()
        root.update_idletasks()

        assert readiness.run_button.cget("text") == "Run Quality Analysis"
        assert readiness.profile_var.get() == "Flux Character LoRA"
        readiness.profile_var.set("SDXL Character LoRA")
        readiness._on_profile_changed()
        assert readiness._current_profile_key() == "sdxl_character_lora"
        readiness.duplicate_similarity_var.set(98.4)
        readiness._on_duplicate_slider("98.4")
        readiness._commit_duplicate_slider()
        assert round(readiness.duplicate_similarity_var.get()) == 98
        assert readiness.duplicate_percent_var.get() == "Moderate similarity"
        assert not hasattr(browser, "training_preview_frame")
        # Catalog lifecycle controls moved to the Dataset Tools tab in v0.13.0;
        # the browser continues to own only image/set/review workflows.
        assert not hasattr(browser, "delete_catalog_button")

        readiness.shutdown()
        browser.shutdown()
        root.destroy()

    print("v0.11.1 GUI smoke test passed: quality controls and LoRA profiles.")


if __name__ == "__main__":
    run()
