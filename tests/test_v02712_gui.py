"""Current cumulative Windows GUI smoke entry point for v0.27.12."""

from __future__ import annotations

import tempfile
import tkinter as tk

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from image_review_dialog import ImageReviewDialog
from test_v02711_gui import run as run_v02711


def _verify_immediate_redraw_cancels_delayed_timer() -> None:
    """Reproduce the v0.27.11 timer-ID loss before closing the viewer."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v02712_gui_") as temporary:
        image_path = Path(temporary) / "immediate-redraw.png"
        Image.new("RGB", (320, 200), "navy").save(image_path)
        record = SimpleNamespace(
            image_id=2712,
            source_path=image_path,
            filename=image_path.name,
            source_video="",
            video_timestamp_seconds=None,
        )
        root = tk.Tk()
        dialog: ImageReviewDialog | None = None
        try:
            dialog = ImageReviewDialog(
                root,
                records=(record,),
                initial_image_id=record.image_id,
            )
            root.update()

            # Queue the same delayed Configure redraw involved in the Windows
            # report, then use one synchronous viewer action before closing.
            dialog._schedule_redraw(None)  # type: ignore[arg-type]
            callback_id = dialog._redraw_after_id
            assert callback_id is not None
            assert callback_id in root.tk.splitlist(root.tk.call("after", "info"))

            dialog._fit()
            assert dialog._redraw_after_id is None
            assert callback_id not in root.tk.splitlist(
                root.tk.call("after", "info")
            )

            dialog.destroy()
            dialog = None
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
    """Replay the full chain and verify the v0.27.12 redraw boundary."""
    run_v02711()
    _verify_immediate_redraw_cancels_delayed_timer()
    print(
        "v0.27.12 cumulative GUI smoke test passed: immediate redraws retained "
        "timer ownership, all delayed callbacks were cancelled, and all "
        "maintained interface checkpoints completed."
    )


if __name__ == "__main__":
    run()
