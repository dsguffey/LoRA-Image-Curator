"""Windows GUI smoke test for LoRA Image Curator v0.25.x.

This extends the verified v0.24.3 interaction smoke test with visible branding
and the InsightFace model-pack Browse affordance. The v0.25.1 assertions also
verify that Alt navigation is installed at a first-priority bind tag rather
than the too-late toplevel stage. It intentionally does not open a file dialog
or load a model.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk

from pathlib import Path

from catalog_browser import ALT_NAVIGATION_BINDTAG
from test_v0240_gui import run as run_v0240


def _verify_first_priority_alt_binding() -> None:
    """Exercise the real focus/bind-tag path used before Windows menu handling."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0251_alt_gui_") as temporary:
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(Path(temporary) / "appdata")
        root: tk.Tk | None = None
        try:
            from app import DatasetToolsApp, shutdown_logging

            root = tk.Tk()
            root.geometry("1200x800")
            application = DatasetToolsApp(root)
            application.notebook.select(application.browser_tab)
            root.update()

            target = application.catalog_browser.search_entry
            target.focus_force()
            root.update()
            tags = tuple(target.bindtags())
            assert tags[0] == ALT_NAVIGATION_BINDTAG
            assert tags.count(ALT_NAVIGATION_BINDTAG) == 1

            if sys.platform == "win32":
                target.event_generate("<KeyPress-Alt_L>")
                root.update()
                assert application.catalog_browser._alt_modifier_held
                target.event_generate("<KeyRelease-Alt_L>")
                root.update()
                assert not application.catalog_browser._alt_modifier_held

            application._finish_close()
            root = None
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


def run() -> None:
    run_v0240()
    _verify_first_priority_alt_binding()

    # Source assertions complement the live focus/bind-tag probe with static
    # branding and command-wiring coverage.
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    browser_source = (
        (Path(__file__).resolve().parents[1] / "catalog_browser.py").read_text(encoding="utf-8")
    )
    assert 'APPLICATION_TITLE = f"{APP_NAME} — LoRA Dataset Workspace"' in source
    assert "command=self._choose_face_model_pack" in source
    assert 'label="About LoRA Image Curator"' in source
    assert 'ALT_NAVIGATION_BINDTAG = "LoRAImageCuratorAltNavigation"' in browser_source
    assert 'toplevel.bind_class(' in browser_source
    assert '"<KeyPress-Alt_L>"' in browser_source
    print(
        "v0.25.1 GUI smoke test passed: v0.24.3 interactions, public branding, "
        "InsightFace model Browse, and first-priority Alt navigation wiring."
    )


if __name__ == "__main__":
    run()
