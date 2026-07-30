"""Dependency-light regression for the v0.25.3 pruning-workflow release."""

from __future__ import annotations

import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from browser_workflow import (
    READINESS_ISSUE_LABELS,
    BrowserFilterState,
    apply_browser_filter_state,
    parse_keyword_terms,
    record_matches_keyword_terms,
)
from catalog import Catalog
from catalog_browser import CatalogBrowserFrame
from image_sets import ImageSetRepository


def _record(image_id: int, **overrides) -> SimpleNamespace:
    """Build a complete readiness/browser fixture with explicit defaults."""
    values = {
        "image_id": image_id,
        "search_blob": "portrait, studio, person",
        "manual_tags": "portrait, studio",
        "ai_tags_active": "person",
        "ai_tags_excluded": "",
        "manual_keyword": "test_person",
        "caption": "A studio portrait.",
        "review_status": "keep",
        "file_status": "present",
        "suggested_identity": "",
        "identity_review_status": "",
        "face_count": 1,
        "width": 1024,
        "height": 1024,
        "quality_status": "success",
        "sharpness_score": 200.0,
        "perceptual_hash": f"{image_id:016x}",
        "file_location_count": 1,
        "nearest_duplicate_similarity": None,
        "has_manual_metadata": True,
        "ocr_text": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unified_readiness_filters() -> None:
    """Every dashboard check must be available inside one image-set scope."""
    duplicate_a = _record(
        1,
        search_blob="close-up, interview, test_person",
        manual_tags="close-up, interview",
        perceptual_hash="0000000000000000",
    )
    weak = _record(
        2,
        search_blob="interview, computer foreground",
        manual_tags="",
        ai_tags_active="",
        manual_keyword="",
        caption="",
        review_status="unreviewed",
        file_status="missing",
        suggested_identity="Test Person",
        identity_review_status="suggested",
        face_count=2,
        width=320,
        height=480,
        quality_status="",
        sharpness_score=None,
        perceptual_hash="ffffffffffffffff",
        has_manual_metadata=False,
    )
    duplicate_blur = _record(
        3,
        search_blob="close-up, interview, test_person",
        manual_tags="close-up, interview",
        sharpness_score=50.0,
        perceptual_hash="0000000000000001",
    )
    outside_scope = _record(
        4,
        manual_keyword="",
        caption="",
        review_status="unreviewed",
        file_status="missing",
    )
    records = (duplicate_a, weak, duplicate_blur, outside_scope)

    state = BrowserFilterState(
        image_set_id=7,
        image_set_name="Interview Pruned",
        readiness_issues=frozenset(READINESS_ISSUE_LABELS),
        readiness_match="any",
        blur_threshold=100.0,
        duplicate_similarity_percent=98,
    )
    result = apply_browser_filter_state(
        records,
        state,
        image_set_ids=(1, 2, 3),
    )
    assert {record.image_id for record in result.records} == {1, 2, 3}
    assert 4 not in {
        image_id
        for image_ids in result.issue_image_ids.values()
        for image_id in image_ids
    }
    assert result.issue_image_ids["Possible Duplicates"] == frozenset({1, 3})
    assert result.issue_image_ids["Blur"] == frozenset({3})
    assert result.issue_image_ids["Missing Trigger Keyword"] == frozenset({2})
    assert result.issue_image_ids["No Training Text"] == frozenset({2})

    all_state = BrowserFilterState(
        image_set_id=7,
        image_set_name="Interview Pruned",
        readiness_issues=frozenset({"Missing Trigger Keyword", "Unreviewed"}),
        readiness_match="all",
    )
    all_result = apply_browser_filter_state(
        records,
        all_state,
        image_set_ids=(1, 2, 3),
    )
    assert [record.image_id for record in all_result.records] == [2]


def test_result_wide_multi_keyword_matching() -> None:
    """Comma-separated terms support useful Any and All selection semantics."""
    terms = parse_keyword_terms(" close-up, interview,\ncomputer foreground, close-up ")
    assert terms == ("close-up", "interview", "computer foreground")
    close_interview = _record(
        1,
        search_blob="close-up\ninterview\ntest_person",
    )
    computer_interview = _record(
        2,
        search_blob="bust shot\ninterview\ncomputer foreground",
    )
    assert record_matches_keyword_terms(close_interview, terms)
    assert record_matches_keyword_terms(computer_interview, terms)
    assert not record_matches_keyword_terms(
        computer_interview,
        ("close-up", "interview"),
        match_all=True,
    )
    assert record_matches_keyword_terms(
        close_interview,
        ("close-up", "interview"),
        match_all=True,
    )


def test_exact_image_set_replacement() -> None:
    """Update Image Set replaces membership atomically, including empty sets."""
    with tempfile.TemporaryDirectory(prefix="lora_curator_v0252_sets_") as temporary:
        database = Path(temporary) / "catalog.db"
        with Catalog(database):
            pass
        with closing(sqlite3.connect(database)) as connection, connection:
            for image_id in (1, 2, 3):
                connection.execute(
                    """
                    INSERT INTO images(
                        id, content_sha256, byte_size, width, height,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, 1000, 1024, 1024, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (image_id, f"{image_id:064x}"),
                )

        repository = ImageSetRepository(database)
        created = repository.create_set("Candidates", (1, 2))
        updated = repository.replace_images(created.set_id, (2, 3))
        assert updated.image_count == 2
        assert repository.get_image_ids(created.set_id) == (2, 3)
        emptied = repository.replace_images(created.set_id, ())
        assert emptied.image_count == 0
        assert repository.get_image_ids(created.set_id) == ()


def test_select_image_set_replaces_browser_selection() -> None:
    """The live image-set callback follows the progressive-pruning workflow."""

    class ValueRecorder:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    browser = SimpleNamespace(
        selected_image_ids={1, 4},
        records_by_id={1: object(), 2: object(), 3: object(), 4: object()},
        anchor_image_id=None,
        edit_status_var=ValueRecorder(),
        recorded_before=None,
        changed=False,
    )
    browser._record_selection_change = (
        lambda before, _description: setattr(browser, "recorded_before", before)
    )
    browser._selection_changed = lambda: setattr(browser, "changed", True)

    CatalogBrowserFrame._replace_selection_with_saved_image_set(
        browser,
        (2, 3, 99),
    )
    assert browser.selected_image_ids == {2, 3}
    assert browser.recorded_before == {1, 4}
    assert browser.changed
    assert "Selected 2 images" in browser.edit_status_var.value


def test_global_selection_shortcut_contract() -> None:
    """Primary shortcuts favor all results while page-only actions stay explicit."""
    source = Path(__file__).with_name("catalog_browser.py").read_text(
        encoding="utf-8"
    )
    app_source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert 'bind_all("<Control-a>", self._select_all_results_shortcut' in source
    assert 'bind_all("<Control-Shift-a>", self._select_all_shortcut' in source
    assert 'bind_all("<Control-i>", self._invert_all_shortcut' in source
    assert "self.clear_selection()" in source[
        source.index("def _escape_shortcut"):
        source.index("def _undo_shortcut")
    ]
    assert 'label="Select by Keyword…"' in app_source
    assert 'label="Deselect by Keyword…"' in app_source
    assert 'label="Select by Image Set…"' in app_source


def run() -> None:
    test_unified_readiness_filters()
    test_result_wide_multi_keyword_matching()
    test_exact_image_set_replacement()
    test_select_image_set_replaces_browser_selection()
    test_global_selection_shortcut_contract()
    print(
        "v0.25.3 regression passed: unified readiness/image-set filters, "
        "multi-keyword selection, exact set updates, and result-wide defaults."
    )


if __name__ == "__main__":
    run()
