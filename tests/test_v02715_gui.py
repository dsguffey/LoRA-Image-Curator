"""Current cumulative Windows GUI smoke entry point for v0.27.15."""

from __future__ import annotations

import gc
import sys
import tkinter as tk
import weakref

from catalog_browser import CatalogBrowserFrame
from test_v02714_gui import run as run_v02714


def _verify_browser_images_finalize_on_gui_thread() -> None:
    """Release one retained Tk image before destroying its interpreter."""
    captured: list[object] = []
    previous_hook = sys.unraisablehook

    def capture(unraisable: object) -> None:
        captured.append(unraisable)

    root: tk.Tk | None = None
    browser: CatalogBrowserFrame | None = None
    photo: tk.PhotoImage | None = None
    photo_reference: weakref.ReferenceType[tk.PhotoImage] | None = None
    sys.unraisablehook = capture
    try:
        root = tk.Tk()
        browser = CatalogBrowserFrame(root)
        photo = tk.PhotoImage(master=root, width=2, height=2)
        photo_reference = weakref.ref(photo)
        browser.decoded_thumbnail_cache._items[("synthetic", 1, 1)] = photo
        browser._details_preview_photo = photo  # type: ignore[assignment]
        browser.detail_preview_label.configure(image=photo, text="")

        browser.shutdown()
        assert len(browser.decoded_thumbnail_cache) == 0
        assert browser._details_preview_photo is None
        assert browser.on_image_sets_changed is None
        assert browser.on_filter_settings_changed is None
        assert browser.on_command_state_changed is None

        photo = None
        gc.collect()
        assert photo_reference() is None
        root.destroy()
        root = None
        browser = None
        gc.collect()
    finally:
        sys.unraisablehook = previous_hook
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass

    if captured:
        details = "; ".join(
            f"{type(getattr(item, 'exc_value', None)).__name__}: "
            f"{getattr(item, 'exc_value', None)} "
            f"({getattr(item, 'object', None)!r})"
            for item in captured
        )
        raise AssertionError(f"Unraisable GUI cleanup errors: {details}")


def run() -> None:
    """Replay the full GUI chain and verify deterministic image cleanup."""
    run_v02714()
    _verify_browser_images_finalize_on_gui_thread()
    print(
        "v0.27.15 cumulative GUI smoke test passed: thumbnail workers retained "
        "no Tk owner, decoded images finalized on the GUI thread, shutdown "
        "callbacks detached, and all maintained checkpoints completed."
    )


if __name__ == "__main__":
    run()
