"""Tkinter smoke test for the v0.10.0 search and readiness interface.

Run on Windows with ``python test_v0100_gui.py`` or on Linux with
``xvfb-run -a python test_v0100_gui.py``.
"""

from __future__ import annotations

import tempfile
import tkinter as tk

from pathlib import Path
from tkinter import ttk

from catalog_browser import CatalogBrowserFrame
from readiness_frame import DatasetReadinessFrame
from test_milestone_7d import _seed_catalog


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root_path = Path(temporary)
        database, _image_ids, _sources = _seed_catalog(root_path)
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

        browser.apply_external_query("review:keep OR file:missing")
        assert len(browser.visible_records) == 2
        assert browser.search_entry.cget("values") == ""
        assert browser.save_keyword_button.cget("text") == "Save Trigger Keyword"
        notebook.select(readiness_tab)
        root.update()
        assert any(
            child.cget("text") == "Dataset Readiness"
            for child in readiness.content.winfo_children()[0].winfo_children()
            if child.winfo_class() == "TLabel"
        )

        browser.shutdown()
        root.destroy()

    print("v0.10.0 GUI smoke test passed: advanced query controls and readiness tab.")


if __name__ == "__main__":
    run()
