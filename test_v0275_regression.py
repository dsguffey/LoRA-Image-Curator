"""Focused v0.27.5 regression for enlarged-review deletion.

The viewer must expose one unobtrusive trash action, hand off exactly its
current record to the Browser, and then close so it cannot retain a deleted
record. The Browser remains the sole owner of file-action policy.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")


def test_enlarged_view_delegates_exactly_one_image() -> None:
    """Protect the viewer-to-Browser deletion boundary."""
    browser = _source("catalog_browser.py")
    review = _source("image_review_dialog.py")

    assert "on_delete: Callable[[object], None] | None = None" in review
    assert 'text="🗑"' in review
    assert "def _delete_current" in review
    assert "self._on_delete(record)" in review
    assert "self.destroy()" in review
    assert "on_delete=self._delete_review_record" in browser
    assert "self.selected_image_ids = {record.image_id}" in browser
    assert "self.delete_selected_to_trash()" in browser

    called: list[int] = []
    destroyed: list[bool] = []
    fake_dialog = SimpleNamespace(
        current_record=SimpleNamespace(image_id=17),
        _on_delete=lambda record: called.append(int(record.image_id)),
        destroy=lambda: destroyed.append(True),
    )
    from image_review_dialog import ImageReviewDialog

    ImageReviewDialog._delete_current(fake_dialog)
    assert called == [17]
    assert destroyed == [True]


if __name__ == "__main__":
    test_enlarged_view_delegates_exactly_one_image()
    print(
        "v0.27.5 regression tests passed: enlarged review delegates exactly "
        "one current image to the Browser deletion policy and closes cleanly."
    )
