"""Search canonical stored Florence captions through ordinary browser search."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from PIL import Image

from advanced_search import record_matches_query
from browser_workflow import BrowserFilterState, apply_browser_filter_state
from catalog_browser import CatalogBrowserRepository
from image_sets import ImageSetRepository
from tests.test_milestone_7d import NOW, _seed_catalog


def create_caption_search_catalog(root: Path) -> tuple[Path, int]:
    """Create a temporary catalog with caption-only, tag-only, and no matches."""
    database, _image_ids, sources = _seed_catalog(root)
    image_paths = (
        root / "source_a" / "caption_beard.png",
        root / "source_b" / "tag_beard.png",
    )
    for source in sources[:2]:
        source.unlink()
    for index, path in enumerate(image_paths):
        with Image.new("RGB", (24, 18), (30 + index * 40, 70, 110)) as image:
            image.save(path, format="PNG")

    filename_only = root / "missing" / "beard_filename_only.png"
    captions = {
        1: "A man with a beard wearing a black jacket beside a brick wall.",
        2: "A woman standing in a quiet field.",
        3: "A distant landscape at sunset.",
    }
    with closing(sqlite3.connect(database)) as connection, connection:
        for image_id, path in ((1, image_paths[0]), (2, image_paths[1])):
            connection.execute(
                "UPDATE images SET content_sha256 = ?, byte_size = ?, width = 24, "
                "height = 18 WHERE id = ?",
                (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, image_id),
            )
            connection.execute(
                "UPDATE files SET path_key = ?, absolute_path = ?, input_root = ?, "
                "input_root_key = ?, relative_path = ?, byte_size = ?, status = 'present' "
                "WHERE image_id = ?",
                (str(path).casefold(), str(path), str(path.parent),
                 str(path.parent).casefold(), path.name, path.stat().st_size, image_id),
            )
        connection.execute(
            "UPDATE files SET path_key = ?, absolute_path = ?, input_root = ?, "
            "input_root_key = ?, relative_path = ? WHERE image_id = 3",
            (str(filename_only).casefold(), str(filename_only), str(filename_only.parent),
             str(filename_only.parent).casefold(), filename_only.name),
        )
        for image_id, caption in captions.items():
            connection.execute(
                "UPDATE analysis_results SET caption = ? WHERE image_id = ?",
                (caption, image_id),
            )
        connection.execute(
            "INSERT INTO tags(id, name, normalized_name, category, created_at) "
            "VALUES (6, 'beard', 'beard', 'manual_tag', ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO image_tags(image_id, tag_id, source, confidence, "
            "review_status, notes, created_at, updated_at) "
            "VALUES (2, 6, 'manual', NULL, 'confirmed', '', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO image_review_state(image_id, status, notes, updated_at) "
            "VALUES (2, 'reject', 'preserve this decision', ?)",
            (NOW,),
        )

    image_set = ImageSetRepository(database).create_set("Caption scope", [1, 3])
    return database, image_set.set_id


def _catalog_snapshot(database: Path) -> tuple[int, dict[str, tuple[tuple, ...]]]:
    tables = (
        "images",
        "files",
        "analysis_results",
        "tags",
        "image_tags",
        "image_tag_exclusions",
        "image_sets",
        "image_set_members",
        "image_review_state",
    )
    with closing(sqlite3.connect(database)) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        contents = {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in tables
        }
    return version, contents


def test_florence_caption_search_is_read_only_and_respects_scope() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_caption_search_") as temporary:
        database, image_set_id = create_caption_search_catalog(Path(temporary))
        before = _catalog_snapshot(database)
        records = CatalogBrowserRepository(database).fetch_records()
        by_id = {record.image_id: record for record in records}
        assert len(records) == 3

        # Image 1 is a caption-only match; image 2 has a matching manual tag
        # without a caption match; image 3 matches only by filename.
        assert "beard" not in by_id[1].tags.casefold()
        assert "beard" in by_id[2].tags.casefold()
        assert "beard" not in by_id[2].caption.casefold()
        assert "beard" in by_id[3].filename.casefold()
        assert "beard" not in by_id[3].caption.casefold()

        def matches(query: str, scoped_records=records) -> list[int]:
            return [record.image_id for record in scoped_records if record_matches_query(record, query)]

        assert set(matches("beard")) == {1, 2}
        assert set(matches("BEARD")) == {1, 2}
        assert matches("black jacket") == [1]
        assert matches("BLACK JACKET") == [1]
        assert matches("brick wall") == [1]
        assert 3 not in matches("beard")

        scoped = apply_browser_filter_state(
            records,
            BrowserFilterState(
                image_set_id=image_set_id,
                image_set_name="Caption scope",
            ),
            image_set_ids=ImageSetRepository(database).get_image_ids(image_set_id),
        ).records
        assert {record.image_id for record in scoped} == {1, 3}
        assert matches("beard", scoped) == [1]
        assert matches("black jacket", scoped) == [1]

        assert _catalog_snapshot(database) == before
        print(
            "Florence caption search regression passed: ordinary terms, phrases, "
            "case folding, filename exclusion, image-set scope, and read-only metadata."
        )


if __name__ == "__main__":
    test_florence_caption_search_is_read_only_and_respects_scope()
