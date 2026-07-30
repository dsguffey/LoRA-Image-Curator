"""Tkinter smoke test for v0.8.2 common-only interactive tag chips.

Run on Linux with:

    xvfb-run -a python test_v082_gui.py path/to/dataset_tools.db
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import tkinter as tk

from contextlib import closing
from pathlib import Path

from catalog_browser import CatalogBrowserFrame, CatalogBrowserRepository


def _find_pair(repository: CatalogBrowserRepository, image_ids: list[int]) -> tuple[list[int], str]:
    for left_index, left in enumerate(image_ids):
        for right in image_ids[left_index + 1 :]:
            common = repository.fetch_common_tags([left, right])
            ai = next((tag for tag in common if tag.kind == "ai_active"), None)
            if ai is not None:
                return [left, right], ai.name
    raise AssertionError("Test catalog needs two images sharing an active AI tag")


def run(source_catalog: Path, screenshot_path: Path | None = None) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "dataset_tools.db"
        shutil.copy2(source_catalog, database)
        repository = CatalogBrowserRepository(database)
        records = repository.fetch_records()
        pair, shared_tag = _find_pair(repository, [record.image_id for record in records])

        root = tk.Tk()
        root.geometry("1400x960")
        browser = CatalogBrowserFrame(root, initial_catalog_path=database)
        browser.pack(fill="both", expand=True)
        browser.selected_image_ids = set(pair)
        browser._selection_changed(pair[-1])
        root.update()
        root.update_idletasks()

        assert browser.detail_filename_var.get() == "Selection Review"
        assert any(
            tag.name == shared_tag and tag.kind == "ai_active"
            for tag in browser._displayed_selection_tags
        )
        assert "disabled" not in browser.add_tags_button.state()

        # A partial manual tag must stay hidden in the batch panel.
        assert browser.edit_service is not None
        browser.edit_service.add_manual_tags([pair[0]], ["partial_gui_tag"])
        browser.refresh(quiet=True)
        root.update_idletasks()
        assert not any(
            tag.normalized_name == "partial_gui_tag"
            for tag in browser._displayed_selection_tags
        )

        # Adding it normally across the selection makes it a common orange chip.
        browser._apply_tag_operation(
            'Add manual tag "partial_gui_tag"',
            lambda: browser.edit_service.add_manual_tags(pair, ["partial_gui_tag"]),
        )
        root.update()
        assert any(
            tag.normalized_name == "partial_gui_tag" and tag.kind == "manual"
            for tag in browser._displayed_selection_tags
        )

        if screenshot_path is not None:
            from PIL import ImageGrab

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            x = root.winfo_rootx()
            y = root.winfo_rooty()
            ImageGrab.grab(
                bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())
            ).save(screenshot_path)

        # Tear down Tk before asking TemporaryDirectory to remove the copied
        # database. On Windows, an unclosed sqlite3 connection keeps the file
        # locked and caused WinError 32 even though every GUI assertion passed.
        browser.shutdown()
        root.destroy()

        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        print("v0.8.2 GUI smoke test passed: common-only AI/manual tag chips and batch add.")


if __name__ == "__main__":
    import sys

    source = Path(sys.argv[1])
    screenshot = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    run(source, screenshot)
