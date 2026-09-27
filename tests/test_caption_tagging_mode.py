"""Focused word-boundary and durable batch-tag contracts for Caption Tagging Mode."""

from __future__ import annotations

import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from advanced_search import record_matches_query
from caption_tagging import CaptionTagSelection
from catalog_browser import CatalogBrowserRepository
from catalog_edits import CatalogEditService
from tests.test_florence_caption_search import create_caption_search_catalog


def test_caption_word_gestures_keep_explicit_tag_boundaries() -> None:
    caption = "A man wearing a black leather jacket outdoors; well-lit."
    selection = CaptionTagSelection(caption)
    word = lambda text: selection.word_at(caption.index(text) + 1)
    assert selection.word_at(caption.index("black") + 2) == word("black")
    assert selection.word_at(caption.index(";")) == word("outdoors")
    assert selection.word_at(caption.index(" ")) is None

    selection.click(word("black"))
    assert selection.pending_tags() == ["black"]
    selection.click(word("jacket"), control=True)
    assert selection.pending_tags() == ["black jacket"]
    selection.click(word("outdoors"), control=True)
    assert selection.pending_tags() == ["black jacket outdoors"]
    assert len(selection.candidates) == 1
    selection.click(word("jacket"), control=True)
    assert selection.pending_tags() == ["black outdoors"]
    selection.click(word("leather"), control=True)
    assert selection.pending_tags() == ["black outdoors leather"]
    assert selection.candidates[0] == [word("black"), word("outdoors"), word("leather")]
    selection.drag(word("wearing"), selection.word_at(caption.index("a black")), control=True)
    assert selection.pending_tags() == ["black outdoors leather wearing a"]
    assert len(selection.candidates) == 1
    selection.click(word("man"))
    assert selection.pending_tags() == ["black outdoors leather wearing a", "man"]
    selection.click(word("man"))
    assert selection.pending_tags() == ["black outdoors leather wearing a"]

    shift = CaptionTagSelection(caption)
    shift.click(word("black"))
    shift.click(word("jacket"), shift=True)
    assert shift.pending_tags() == ["black leather jacket"]
    shift.click(word("leather"))
    assert shift.pending_tags() == []

    dragged = CaptionTagSelection(caption)
    dragged.drag(word("black"), word("jacket"))
    assert dragged.pending_tags() == ["black leather jacket"]
    assert "well-lit" in [word.text for word in selection.words]

    duplicate = CaptionTagSelection("beard, beard")
    duplicate.click(0)
    duplicate.click(1)
    assert duplicate.pending_tags() == ["beard"]


def test_manual_batch_is_additive_transactional_and_preserves_caption_state() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_caption_tagging_") as temporary:
        database, image_set_id = create_caption_search_catalog(Path(temporary))
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE analysis_results SET caption = ? WHERE image_id = 1",
                ("A man with a white beard wearing a black jacket.",),
            )
            before_analysis = tuple(connection.execute(
                "SELECT image_id, caption FROM analysis_results ORDER BY image_id"
            ))
            before_reviews = tuple(connection.execute(
                "SELECT * FROM image_review_state ORDER BY image_id"
            ))
            before_sets = tuple(connection.execute(
                "SELECT * FROM image_set_members ORDER BY image_set_id, image_id"
            ))

        selected_ids = (1, 2)
        result = CatalogEditService(database).add_manual_tags(
            selected_ids, ["beard", "white beard", "WHITE  BEARD"]
        )
        assert result.changed_image_count == 2
        assert result.changed_assignment_count == 3  # image 2 already had beard
        assert not CatalogEditService(database).add_manual_tags(
            selected_ids, ["beard", "white beard"]
        ).changed_anything

        with closing(sqlite3.connect(database)) as connection:
            assignments = tuple(connection.execute(
                "SELECT it.image_id, t.normalized_name FROM image_tags AS it "
                "JOIN tags AS t ON t.id = it.tag_id "
                "WHERE t.category = 'manual_tag' AND it.source = 'manual' "
                "ORDER BY it.image_id, t.normalized_name"
            ))
            assert assignments == (
                (1, "beard"), (1, "red_dress"), (1, "white beard"),
                (2, "beard"), (2, "red_dress"), (2, "white beard"),
            )
            assert tuple(connection.execute(
                "SELECT image_id, caption FROM analysis_results ORDER BY image_id"
            )) == before_analysis
            assert tuple(connection.execute(
                "SELECT * FROM image_review_state ORDER BY image_id"
            )) == before_reviews
            assert tuple(connection.execute(
                "SELECT * FROM image_set_members ORDER BY image_set_id, image_id"
            )) == before_sets

        repository = CatalogBrowserRepository(database)
        common = repository.fetch_common_tags(selected_ids)
        assert {tag.normalized_name for tag in common if tag.kind == "manual"} == {
            "beard", "red_dress", "white beard"
        }
        records = repository.fetch_records()
        assert {record.image_id for record in records if record_matches_query(record, "white beard")} == {1, 2}
        assert 3 not in {record.image_id for record in records if record_matches_query(record, "beard")}
        assert image_set_id > 0


def run() -> None:
    test_caption_word_gestures_keep_explicit_tag_boundaries()
    test_manual_batch_is_additive_transactional_and_preserves_caption_state()
    print("Caption Tagging Mode focused contracts passed.")


if __name__ == "__main__":
    run()
