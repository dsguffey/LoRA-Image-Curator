"""Dependency-free regression tests for Milestone 7A manual review."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from catalog_browser import CatalogBrowserRepository
from catalog_edits import CatalogEditService


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def run(source_catalog: Path) -> None:
    """Exercise every 7A write path against a disposable catalog copy."""
    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        test_db = temporary_path / "dataset_tools.db"
        shutil.copy2(source_catalog, test_db)

        original_records = CatalogBrowserRepository(test_db).fetch_records()
        assert original_records, "Test catalog must contain at least one image"

        identity_record = next(
            (
                record
                for record in original_records
                if record.identity_match_id is not None
            ),
            None,
        )
        assert identity_record is not None, "Test catalog needs one identity suggestion"

        chosen = original_records[0]
        untouched = original_records[-1]
        if untouched.image_id == chosen.image_id and len(original_records) > 1:
            untouched = original_records[1]

        with closing(sqlite3.connect(test_db)) as connection, connection:
            original_analysis_count = _count(connection, "analysis_results")
            original_face_count = _count(connection, "face_detections")

        service = CatalogEditService(test_db)

        # The backup API must produce a standalone, healthy SQLite file.
        backup = service.create_backup(temporary_path / "before_edits.db")
        assert backup.exists()
        with closing(sqlite3.connect(backup)) as connection, connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert _count(connection, "images") == len(original_records)

        # Review state is image-specific and persists through repository reloads.
        service.set_review_state([chosen.image_id], "keep")
        with closing(sqlite3.connect(test_db)) as connection, connection:
            row = connection.execute(
                "SELECT status FROM image_review_state WHERE image_id = ?",
                (chosen.image_id,),
            ).fetchone()
            assert row == ("keep",)
            if untouched.image_id != chosen.image_id:
                assert connection.execute(
                    "SELECT 1 FROM image_review_state WHERE image_id = ?",
                    (untouched.image_id,),
                ).fetchone() is None

        # Replacing a manual keyword must remove the prior manual assignment,
        # not accumulate stale set keywords on the image.
        service.replace_manual_keyword([chosen.image_id], "first_keyword")
        service.replace_manual_keyword([chosen.image_id], "second keyword")
        with closing(sqlite3.connect(test_db)) as connection, connection:
            assignments = connection.execute(
                """
                SELECT t.name, t.category, it.source, it.review_status
                FROM image_tags AS it
                JOIN tags AS t ON t.id = it.tag_id
                WHERE it.image_id = ?
                  AND t.category = 'set_keyword'
                  AND LOWER(it.source) = 'manual'
                """,
                (chosen.image_id,),
            ).fetchall()
            assert assignments == [
                ("second keyword", "set_keyword", "manual", "confirmed")
            ]

        reopened = {
            record.image_id: record
            for record in CatalogBrowserRepository(test_db).fetch_records()
        }
        assert reopened[chosen.image_id].manual_keyword == "second keyword"
        assert reopened[chosen.image_id].has_manual_metadata

        service.clear_manual_keyword([chosen.image_id])
        reopened = {
            record.image_id: record
            for record in CatalogBrowserRepository(test_db).fetch_records()
        }
        assert reopened[chosen.image_id].manual_keyword == ""

        # Identity review must keep the detailed face-match record and its
        # searchable provider tag synchronized, while preserving the original
        # suggestion so Reset remains possible.
        match_id = int(identity_record.identity_match_id)
        service.review_identity_match(match_id, "confirmed")
        with closing(sqlite3.connect(test_db)) as connection, connection:
            assert connection.execute(
                "SELECT review_status FROM identity_matches WHERE id = ?",
                (match_id,),
            ).fetchone() == ("confirmed",)
            tag_states = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT it.review_status
                    FROM image_tags AS it
                    JOIN tags AS t ON t.id = it.tag_id
                    WHERE it.image_id = ?
                      AND t.category = 'identity'
                      AND LOWER(it.source) LIKE 'face:%'
                    """,
                    (identity_record.image_id,),
                )
            }
            assert "confirmed" in tag_states

        confirmed = {
            record.image_id: record
            for record in CatalogBrowserRepository(test_db).fetch_records()
        }[identity_record.image_id]
        assert confirmed.identity_review_status == "confirmed"
        assert confirmed.has_manual_metadata

        service.review_identity_match(match_id, "rejected")
        rejected = {
            record.image_id: record
            for record in CatalogBrowserRepository(test_db).fetch_records()
        }[identity_record.image_id]
        assert rejected.identity_review_status == "rejected"
        assert rejected.identity_match_id == match_id
        assert rejected.suggested_identity == identity_record.suggested_identity

        service.review_identity_match(match_id, "suggested")
        reset = {
            record.image_id: record
            for record in CatalogBrowserRepository(test_db).fetch_records()
        }[identity_record.image_id]
        assert reset.identity_review_status == "suggested"

        # Resetting the image decision to unreviewed should remove the now-empty
        # review row rather than leaving meaningless state behind.
        service.set_review_state([chosen.image_id], "unreviewed")
        with closing(sqlite3.connect(test_db)) as connection, connection:
            assert connection.execute(
                "SELECT 1 FROM image_review_state WHERE image_id = ?",
                (chosen.image_id,),
            ).fetchone() is None

            assert _count(connection, "analysis_results") == original_analysis_count
            assert _count(connection, "face_detections") == original_face_count
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        print(
            "Milestone 7A tests passed: backup, review state, manual keyword, "
            "identity review, persistence, and database integrity."
        )


if __name__ == "__main__":
    import sys

    run(Path(sys.argv[1]))
