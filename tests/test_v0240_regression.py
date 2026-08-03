"""Dependency-light regressions for the v0.24.0 usability polish pass.

This suite intentionally avoids creating a Tk window.  It can therefore run in
headless release environments while the Windows GUI smoke test remains the
final platform-specific verification.
"""

from __future__ import annotations

import inspect
import tempfile

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from catalog_browser import CatalogBrowserFrame, DecodedThumbnailCache
from video_extraction_dialog import VideoExtractionDialog


class _BindingRecorder:
    """Minimal widget-shaped recorder for testing event ownership headlessly."""

    def __init__(self) -> None:
        self.bindings: list[tuple[str, object, str | None]] = []

    def bind(
        self,
        sequence: str,
        callback: object,
        add: str | None = None,
    ) -> None:
        self.bindings.append((sequence, callback, add))


class _PageShortcutHarness:
    """Small controller-shaped object for shortcut policy tests."""

    def __init__(self) -> None:
        self.card_page_index = 0
        self.images_per_page = 100
        self.visible_records = [object()] * 250
        self.duplicate_review_clusters: tuple[tuple[int, ...], ...] = ()
        self._last_page_shortcut_at = 0.0
        self.forward_calls = 0
        self.backward_calls = 0

    @staticmethod
    def winfo_ismapped() -> bool:
        return True

    def _append_next_card_batch(self) -> None:
        self.forward_calls += 1
        self.card_page_index += 1

    def _show_previous_card_page(self) -> None:
        self.backward_calls += 1
        self.card_page_index -= 1


def test_decoded_thumbnail_lru() -> None:
    """Decoded previews are reused and least-recently-used entries are bounded."""
    with tempfile.TemporaryDirectory(prefix="dataset_tools_v0240_cache_") as temp:
        folder = Path(temp)
        paths = [folder / f"preview_{index}.png" for index in range(3)]
        for index, path in enumerate(paths):
            Image.new("RGB", (20 + index, 20 + index), (index * 40, 0, 0)).save(path)

        cache = DecodedThumbnailCache(max_items=2)
        created: list[object] = []

        def fake_photo_image(_image: Image.Image) -> object:
            photo = object()
            created.append(photo)
            return photo

        with patch("catalog_browser.ImageTk.PhotoImage", side_effect=fake_photo_image):
            first = cache.get_or_load(paths[0])
            assert first is not None
            assert cache.get_or_load(paths[0]) is first
            assert len(created) == 1

            second = cache.get_or_load(paths[1])
            assert second is not None
            assert cache.get_if_cached(paths[0]) is first
            cache.get_or_load(paths[2])
            assert len(cache) == 2
            assert cache.get_if_cached(paths[1]) is None
            assert cache.get_if_cached(paths[0]) is first


def test_alt_page_shortcut_guard() -> None:
    """Held-key repeats and final-page events are consumed without oscillation."""
    binding_source = inspect.getsource(CatalogBrowserFrame._bind_shortcuts)
    assert 'toplevel.bind("<Alt-Right>"' in binding_source
    assert 'bind_all("<Alt-Right>"' not in binding_source

    harness = _PageShortcutHarness()
    assert CatalogBrowserFrame._next_page_shortcut(harness, None) == "break"
    assert harness.card_page_index == 1
    assert harness.forward_calls == 1

    # An immediate key-repeat is swallowed rather than delegated to Tk menus.
    assert CatalogBrowserFrame._next_page_shortcut(harness, None) == "break"
    assert harness.card_page_index == 1
    assert harness.forward_calls == 1

    # Boundary events are also consumed.
    harness.card_page_index = 2
    harness._last_page_shortcut_at = 0.0
    assert CatalogBrowserFrame._next_page_shortcut(harness, None) == "break"
    assert harness.card_page_index == 2
    assert harness.forward_calls == 1

    harness._last_page_shortcut_at = 0.0
    assert CatalogBrowserFrame._previous_page_shortcut(harness, None) == "break"
    assert harness.card_page_index == 1
    assert harness.backward_calls == 1

def test_dialog_discovery_is_deferred() -> None:
    """Dialog construction must not synchronously invoke external FFmpeg."""
    constructor = inspect.getsource(VideoExtractionDialog.__init__)
    build_index = constructor.index("self._build_interface()")
    schedule_index = constructor.index("self._ffmpeg_discovery_after_id = self.after")
    assert build_index < schedule_index
    assert "discover_ffmpeg(" not in constructor


def test_help_icon_coverage() -> None:
    """Keep the requested help affordance and its event ownership intact."""
    from ui_helpers import HelpIcon, Tooltip

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert 'help_attribute="face_reference_folder_help"' in source
    assert "Reference folder:" in source
    assert "HelpIcon" in source
    help_icon_source = inspect.getsource(HelpIcon)
    assert "class HelpIcon" in help_icon_source
    assert "click_to_pin=True" not in help_icon_source
    assert "show_on_focus=False" in help_icon_source
    assert "dismiss_on_click=False" in help_icon_source
    assert "takefocus=False" in help_icon_source
    for sequence in ("<Enter>", "<Leave>"):
        expected = f'self.bind("{sequence}", self._redraw, add="+")'
        assert expected in help_icon_source
    assert 'self.bind("<FocusIn>", self._redraw' not in help_icon_source
    assert 'self.bind("<FocusOut>", self._redraw' not in help_icon_source

    recorder = _BindingRecorder()
    Tooltip(recorder, "Recorded help.")  # type: ignore[arg-type]
    registered = {
        sequence: add
        for sequence, _callback, add in recorder.bindings
    }
    assert registered["<Enter>"] == "+"
    assert registered["<Leave>"] == "+"
    assert registered["<FocusIn>"] == "+"
    assert registered["<FocusOut>"] == "+"
    assert registered["<ButtonPress>"] == "+"
    assert registered["<Destroy>"] == "+"


def run() -> None:
    test_decoded_thumbnail_lru()
    test_alt_page_shortcut_guard()
    test_dialog_discovery_is_deferred()
    test_help_icon_coverage()
    print(
        "v0.24.3 regression passed: decoded thumbnail LRU, guarded Alt paging, "
        "deferred FFmpeg discovery, and hover-only help bindings."
    )


if __name__ == "__main__":
    run()
