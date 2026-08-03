"""Windows GUI smoke entry point for the v0.27.11 lifecycle boundary.

The long v0.27.10-and-earlier interface history intentionally runs in a child
process. Repeatedly creating and destroying independent ``Tk`` interpreters in
one Python process leaves finalizer timing dependent on garbage collection,
especially on Python 3.14 for Windows. Process isolation gives that historical
chain one deterministic interpreter lifetime while the strict GUI gate still
rejects any stderr diagnostic or non-zero exit status.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from image_review_dialog import ImageReviewDialog
from test_golden_build import _run_gui_gate


def _run_inherited_chain_without_unraisable_errors() -> None:
    """Replay the inherited chain with one isolated Tcl interpreter lifetime."""
    _run_gui_gate(
        [sys.executable, "-X", "dev", "-m", "tests.test_v02710_gui"],
        environment=os.environ.copy(),
    )


def _verify_viewer_cancels_pending_redraw() -> None:
    """Close a viewer with a live redraw timer and verify Tcl has no orphan."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v02711_gui_") as temporary:
        image_path = Path(temporary) / "pending-redraw.png"
        Image.new("RGB", (320, 200), "purple").save(image_path)
        record = SimpleNamespace(
            image_id=2711,
            source_path=image_path,
            filename=image_path.name,
            source_video="",
            video_timestamp_seconds=None,
        )
        root = tk.Tk()
        dialog: ImageReviewDialog | None = None
        callback_id: str | None = None
        try:
            dialog = ImageReviewDialog(
                root,
                records=(record,),
                initial_image_id=record.image_id,
            )
            root.update()
            dialog._schedule_redraw(None)  # type: ignore[arg-type]
            callback_id = dialog._redraw_after_id
            assert callback_id is not None
            assert callback_id in root.tk.splitlist(root.tk.call("after", "info"))

            dialog.destroy()
            dialog = None
            assert callback_id not in root.tk.splitlist(
                root.tk.call("after", "info")
            )
            root.update()
        finally:
            if dialog is not None:
                try:
                    dialog.destroy()
                except tk.TclError:
                    pass
            try:
                root.destroy()
            except tk.TclError:
                pass


def run() -> None:
    """Replay the full chain and verify the v0.27.11 cleanup boundary."""
    _run_inherited_chain_without_unraisable_errors()
    _verify_viewer_cancels_pending_redraw()
    print(
        "v0.27.11 cumulative GUI smoke test passed: historical Tk variables "
        "finalized cleanly, delayed viewer redraws were cancelled, and all "
        "maintained interface checkpoints completed."
    )


if __name__ == "__main__":
    run()
