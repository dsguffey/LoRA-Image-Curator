"""Tkinter smoke test for v0.12.0 image sets and targeted readiness refresh.

Run on Windows with ``python -X dev -m tests.test_v0120_gui`` or on Linux with
``xvfb-run -a python -X dev -m tests.test_v0120_gui``. The valid JPEG fixtures keep
thumbnail-worker output useful: any Pillow traceback now represents a real
regression rather than deliberately invalid test bytes.
"""

from __future__ import annotations

import tempfile
import tkinter as tk

from pathlib import Path
from tkinter import ttk

from PIL import Image

from catalog_browser import CatalogBrowserFrame
from image_sets import ImageSetRepository
from readiness_frame import DatasetReadinessFrame
from test_milestone_7d import _seed_catalog


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root_path = Path(temporary)
        database, image_ids, sources = _seed_catalog(root_path)
        for source in sources[:2]:
            Image.new("RGB", (80, 80), "#7389A6").save(source, format="JPEG")
        saved_set = ImageSetRepository(database).create_set(
            "Training Candidates", image_ids[:2]
        )

        root = tk.Tk()
        root.geometry("1500x980")
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

        assert "Set: Training Candidates" in readiness.image_set_combo.cget("values")
        readiness.image_set_var.set("Set: Training Candidates")
        readiness._on_image_set_changed()
        assert readiness._selected_image_set_id == saved_set.set_id
        assert readiness._current_report is not None
        assert readiness._current_report.total_images == 2

        composition = next(
            child
            for child in readiness.content.winfo_children()
            if child.winfo_class() == "TLabelframe"
            and child.cget("text") == "Dataset Composition"
        )
        original_composition_id = str(composition)

        readiness.blur_threshold_var.set("250")
        readiness._on_blur_changed()
        root.update_idletasks()
        assert composition.winfo_exists()
        assert str(composition) == original_composition_id

        previous_report = readiness._current_report
        readiness.duplicate_similarity_var.set(99.2)
        readiness._on_duplicate_slider("99.2")
        assert readiness._current_report is previous_report
        readiness._commit_duplicate_slider()
        root.update_idletasks()
        assert readiness.duplicate_similarity_var.get() == 99
        assert composition.winfo_exists()
        assert str(composition) == original_composition_id

        browser.apply_external_query('set:"Training Candidates"')
        assert len(browser.visible_records) == 2
        assert browser.command_state()["has_catalog"]

        readiness.shutdown()
        browser.shutdown()
        root.destroy()

    print(
        "v0.12.0 GUI smoke test passed: image-set scope, set search, and "
        "release-only targeted readiness refresh."
    )


if __name__ == "__main__":
    run()
