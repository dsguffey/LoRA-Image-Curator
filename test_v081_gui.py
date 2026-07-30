"""Tkinter smoke test for unified selection editing and drag selection.

This test needs a graphical display. On Linux CI it can be run with:

    xvfb-run -a python test_v081_gui.py path/to/dataset_tools.db

It works on a temporary copy and never modifies the supplied catalog.
"""

from __future__ import annotations

import shutil
import tempfile
import tkinter as tk

from pathlib import Path
from types import SimpleNamespace

from catalog_browser import CatalogBrowserFrame


def run(source_catalog: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "dataset_tools.db"
        shutil.copy2(source_catalog, database)

        root = tk.Tk()
        root.geometry("1400x900")
        browser = CatalogBrowserFrame(root, initial_catalog_path=database)
        browser.pack(fill="both", expand=True)
        browser.filter_var.set("All images")
        browser.sort_var.set("Filename (A–Z)")
        browser._apply_search()
        root.update()
        root.update_idletasks()

        assert len(browser.visible_records) >= 4
        first_four = [record.image_id for record in browser.visible_records[:4]]
        browser.selected_image_ids = set(first_four)
        browser._selection_changed(first_four[-1])
        root.update_idletasks()

        assert browser.detail_filename_var.get() == "Selection Review"
        assert browser.detail_preview_label.cget("text").startswith("4 images selected")
        assert "disabled" in browser.open_image_button.state()

        cards = [browser.cards_by_id[image_id] for image_id in first_four[:2]]
        left = min(card.outer.winfo_rootx() for card in cards) - 3
        top = min(card.outer.winfo_rooty() for card in cards) - 3
        right = max(
            card.outer.winfo_rootx() + card.outer.winfo_width() for card in cards
        ) + 3
        bottom = max(
            card.outer.winfo_rooty() + card.outer.winfo_height() for card in cards
        ) + 3

        browser._on_grid_drag_start(
            SimpleNamespace(x_root=left, y_root=top, state=0)
        )
        browser._on_grid_drag_motion(
            SimpleNamespace(x_root=right, y_root=bottom, state=0)
        )
        assert len(browser.selected_image_ids) >= 2
        assert browser._drag_border_windows
        browser._on_grid_drag_end(
            SimpleNamespace(x_root=right, y_root=bottom, state=0)
        )
        assert not browser._drag_border_windows

        browser.shutdown()
        root.destroy()

        print(
            "Unified-selection GUI smoke test passed: multi-selection summary, unified "
            "editor state, drag-box selection, and marquee cleanup."
        )


if __name__ == "__main__":
    import sys

    run(Path(sys.argv[1]))
