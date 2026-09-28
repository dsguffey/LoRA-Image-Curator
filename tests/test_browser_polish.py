"""Focused word search and face-status contracts for Browser Polish Pass."""

from __future__ import annotations

import tempfile

from pathlib import Path
from types import SimpleNamespace

from advanced_search import record_matches_query
from browser_workflow import BrowserFilterState, apply_browser_filter_state
from catalog_browser import (
    CatalogBrowserRepository,
    face_detection_status,
    florence_recommendation_display,
)
from image_sets import ImageSetRepository
from tests.test_florence_caption_search import create_caption_search_catalog


def _record(**changes: str) -> SimpleNamespace:
    values = dict(
        tags="", manual_tags="", manual_keyword="", ai_tags_active="",
        ai_tags_excluded="", caption="", ocr_text="", search_blob="",
        filename="", relative_path="", absolute_path="", image_set_names="",
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_complete_words_and_contiguous_phrases() -> None:
    man = _record(caption="A (man), standing outdoors.")
    woman = _record(caption="A woman standing outdoors.")
    human = _record(caption="A human standing outdoors.")
    assert record_matches_query(man, "man")
    assert record_matches_query(man, "MAN")
    assert record_matches_query(man, "(man)")
    assert record_matches_query(man, "man,")
    assert not record_matches_query(woman, "man")
    assert not record_matches_query(human, "man")
    assert record_matches_query(woman, "woman")

    phrase = _record(caption="A man with a white,\nbeard.")
    separated = _record(caption="A white shirt and a long beard.")
    assert record_matches_query(phrase, "white beard")
    assert record_matches_query(phrase, "WHITE   BEARD")
    assert not record_matches_query(separated, "white beard")
    assert record_matches_query(separated, "white AND beard")
    assert record_matches_query(separated, "white, beard")
    assert not record_matches_query(_record(caption="white shirt"), "white, beard")
    assert record_matches_query(phrase, '"white, beard"')

    compound = _record(caption="A long-haired man wears a t-shirt; man's coat.")
    assert record_matches_query(compound, "long-haired")
    assert record_matches_query(compound, "t-shirt")
    assert record_matches_query(compound, "man's")
    assert not record_matches_query(_record(caption="long-haired"), "haired")
    assert not record_matches_query(_record(caption="man's"), "man")
    assert not record_matches_query(_record(caption="t-shirt"), "shirt")
    assert not record_matches_query(_record(caption="t-shirt"), "t shirt")


def test_all_ordinary_metadata_uses_same_word_rule() -> None:
    record = _record(
        tags="white beard, manual",
        manual_tags="white beard, manual",
        manual_keyword="gal_gadot",
        ai_tags_active="woman outdoors",
        ai_tags_excluded="human silhouette",
        caption="A long-haired subject.",
        ocr_text="OPEN, studio",
        filename="man_only.png",
        relative_path="man_only.png",
        absolute_path="C:/synthetic/man_only.png",
    )
    for field, query in (
        ("manual", "beard"), ("trigger", "gal gadot"),
        ("ai", "woman"), ("excluded", "human"),
        ("caption", "long-haired"), ("ocr", "studio"),
    ):
        assert record_matches_query(record, query)
        assert record_matches_query(record, f"{field}:{query.replace(' ', '_')}")
    assert record_matches_query(record, "white beard")
    assert record_matches_query(record, "gal gadot")
    assert not record_matches_query(
        _record(manual_tags="white", caption="beard"), "white beard"
    )
    assert not record_matches_query(
        _record(manual_tags="white", ai_tags_active="beard"),
        'tag:"white beard"',
    )
    assert not record_matches_query(record, "man")
    assert not record_matches_query(_record(manual_tags="manual"), "man")
    assert record_matches_query(record, "filename:man")


def test_catalog_scope_and_counts_remain_read_only() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_browser_polish_") as temporary:
        database, image_set_id = create_caption_search_catalog(Path(temporary))
        records = CatalogBrowserRepository(database).fetch_records()
        assert {r.image_id for r in records if record_matches_query(r, "man")} == {1}
        assert {r.image_id for r in records if record_matches_query(r, "woman")} == {2}
        scoped = apply_browser_filter_state(
            records,
            BrowserFilterState(image_set_id=image_set_id, image_set_name="Caption scope"),
            image_set_ids=ImageSetRepository(database).get_image_ids(image_set_id),
        ).records
        assert {r.image_id for r in scoped if record_matches_query(r, "man")} == {1}
        assert not any(record_matches_query(r, "woman") for r in scoped)


def test_face_status_distinguishes_provider_and_stored_result() -> None:
    missing = SimpleNamespace(face_analysis_available=False, face_count=0)
    no_face = SimpleNamespace(face_analysis_available=True, face_count=0)
    positive = SimpleNamespace(face_analysis_available=True, face_count=2)
    assert face_detection_status(missing, provider_available=False) == (
        "Face detection not installed"
    )
    assert face_detection_status(missing, provider_available=True) == "Not analyzed"
    assert face_detection_status(no_face, provider_available=True) == "No person detected"
    assert face_detection_status(positive, provider_available=True) == "2 faces detected"
    reason = "no person detected; object detection can miss people"
    assert florence_recommendation_display(reason) == (
        "Florence object detection found no person; object detection can miss people"
    )


def run() -> None:
    test_complete_words_and_contiguous_phrases()
    test_all_ordinary_metadata_uses_same_word_rule()
    test_catalog_scope_and_counts_remain_read_only()
    test_face_status_distinguishes_provider_and_stored_result()
    print("Browser Polish focused contracts passed.")


if __name__ == "__main__":
    run()
