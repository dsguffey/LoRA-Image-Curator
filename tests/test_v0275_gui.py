"""Windows GUI smoke test for the v0.27.5 review-window trash action."""

from __future__ import annotations

import tempfile
import tkinter as tk

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from image_review_dialog import ImageReviewDialog
from test_v0274_gui import run as run_v0274


def _verify_review_delete_control() -> None:
    """Open a real viewer and verify its one-image delete callback."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0275_gui_") as temporary:
        root: tk.Tk | None = None
        dialog: ImageReviewDialog | None = None
        try:
            image_path = Path(temporary) / "review-delete.png"
            Image.new("RGB", (320, 200), "teal").save(image_path)
            record = SimpleNamespace(
                image_id=25,
                source_path=image_path,
                filename=image_path.name,
                source_video="",
                video_timestamp_seconds=None,
            )
            deleted: list[int] = []
            root = tk.Tk()
            root.geometry("900x650")
            dialog = ImageReviewDialog(
                root,
                records=(record,),
                initial_image_id=record.image_id,
                on_delete=lambda current: deleted.append(int(current.image_id)),
            )
            root.update()
            delete_buttons = [
                child
                for child in dialog.control_bar.winfo_children()
                if str(child.cget("text")) == "🗑"
            ]
            assert len(delete_buttons) == 1
            delete_buttons[0].invoke()
            root.update()
            assert deleted == [record.image_id]
            assert not dialog.winfo_exists()
            dialog = None
            root.destroy()
            root = None
        finally:
            if dialog is not None:
                try:
                    dialog.destroy()
                except tk.TclError:
                    pass
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass


def run() -> None:
    """Run the inherited GUI chain and the v0.27.5 viewer check."""
    run_v0274()
    _verify_review_delete_control()
    print(
        "v0.27.5 GUI smoke test passed: the enlarged viewer exposes one trash "
        "control, delegates its current image, and closes afterward."
    )


if __name__ == "__main__":
    run()
