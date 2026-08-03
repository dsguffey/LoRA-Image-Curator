"""Dependency-light regression tests for Milestone 8E similarity review.

The tests keep clustering and selection policy independent of Tk display
availability.  The companion GUI smoke test verifies the real widgets on a
Windows desktop or under Xvfb.
"""

from __future__ import annotations

from types import SimpleNamespace

from advanced_search import duplicate_review_threshold
from catalog_browser import CatalogBrowserFrame
from dataset_readiness import build_readiness_report
from quality_analysis import duplicate_candidate_clusters


def _record(image_id: int, perceptual_hash: str) -> SimpleNamespace:
    """Return the smallest complete readiness/browser-style record fixture."""
    return SimpleNamespace(
        image_id=image_id,
        perceptual_hash=perceptual_hash,
        review_status="keep",
        file_status="present",
        manual_keyword="person",
        manual_tags="portrait",
        ai_tags_active="",
        ai_tags_excluded="",
        suggested_identity="",
        identity_review_status="",
        face_count=1,
        width=1024,
        height=1024,
        quality_status="success",
        sharpness_score=200.0,
        file_location_count=1,
        nearest_duplicate_similarity=None,
    )


def test_connected_duplicate_clusters() -> None:
    """Overlapping pairs must become one comparison group, not mixed rows."""
    records = (
        _record(1, "0000000000000000"),
        _record(2, "0000000000000001"),
        _record(3, "0000000000000003"),
        _record(4, "ffffffffffffffff"),
    )
    assert duplicate_candidate_clusters(records, 98) == ((1, 2, 3),)
    assert duplicate_candidate_clusters(records, 99) == ()

    report = build_readiness_report(records, duplicate_similarity_percent=98)
    issue = next(item for item in report.issues if item.label == "Possible Duplicates")
    assert issue.count == 3


def test_grouped_review_activation() -> None:
    """Only positive, conjunctive perceptual searches switch presentation."""
    assert duplicate_review_threshold("duplicate:98") == 98
    assert duplicate_review_threshold('set:"Candidates" AND (duplicate:possible)') == 96
    assert duplicate_review_threshold("duplicate:exact") is None
    assert duplicate_review_threshold("NOT (duplicate:98)") is None
    assert duplicate_review_threshold("duplicate:98 OR review:unreviewed") is None
    assert duplicate_review_threshold("woman red_dress") is None


def test_saved_set_selection_is_additive() -> None:
    """Loading a set must preserve earlier transient browser selections."""

    class ValueRecorder:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    browser = SimpleNamespace(
        selected_image_ids={1, 2},
        records_by_id={1: object(), 2: object(), 3: object(), 4: object()},
        anchor_image_id=None,
        edit_status_var=ValueRecorder(),
        selection_changed=False,
        recorded_selection_before=None,
    )

    def selection_changed() -> None:
        browser.selection_changed = True

    browser._selection_changed = selection_changed
    browser._record_selection_change = (
        lambda before, _description: setattr(
            browser, "recorded_selection_before", before
        )
    )
    CatalogBrowserFrame._select_saved_image_set(browser, (2, 3, 99))
    assert browser.selected_image_ids == {1, 2, 3}
    assert browser.selection_changed
    assert browser.recorded_selection_before == {1, 2}
    assert "Added 1 image" in browser.edit_status_var.value


def run() -> None:
    test_connected_duplicate_clusters()
    test_grouped_review_activation()
    test_saved_set_selection_is_additive()
    print(
        "Milestone 8E tests passed: connected similarity clusters, scoped review "
        "activation, readiness counts, and additive image-set selection."
    )


if __name__ == "__main__":
    run()
