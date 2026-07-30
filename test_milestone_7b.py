"""Dependency-free regression tests for unified selection editing and history."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from catalog import SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository
from catalog_edits import BatchEditRequest, CatalogEditService, HISTORY_LIMIT


def _rows(connection: sqlite3.Connection, sql: str, parameters: tuple = ()) -> list[tuple]:
    return [tuple(row) for row in connection.execute(sql, parameters).fetchall()]


def _logical_state(database: Path, image_ids: list[int]) -> dict[str, list[tuple]]:
    """Capture user-owned state without relying on service-private helpers."""
    placeholders = ",".join("?" for _ in image_ids)
    with closing(sqlite3.connect(database)) as connection, connection:
        return {
            "review": _rows(
                connection,
                f"""
                SELECT image_id, status, notes
                FROM image_review_state
                WHERE image_id IN ({placeholders})
                ORDER BY image_id
                """,
                tuple(image_ids),
            ),
            "keywords": _rows(
                connection,
                f"""
                SELECT it.image_id, t.normalized_name, it.source, it.review_status
                FROM image_tags AS it
                JOIN tags AS t ON t.id = it.tag_id
                WHERE it.image_id IN ({placeholders})
                  AND t.category = 'set_keyword'
                  AND LOWER(it.source) = 'manual'
                ORDER BY it.image_id, t.normalized_name
                """,
                tuple(image_ids),
            ),
            "matches": _rows(
                connection,
                f"""
                SELECT fir.image_id, im.id, im.review_status
                FROM identity_matches AS im
                JOIN face_detections AS fd ON fd.id = im.face_detection_id
                JOIN face_image_results AS fir ON fir.id = fd.face_result_id
                WHERE fir.image_id IN ({placeholders})
                ORDER BY fir.image_id, im.id
                """,
                tuple(image_ids),
            ),
        }


def run(source_catalog: Path) -> None:
    """Exercise schema migration, selection edits, undo, redo, and branching."""
    with tempfile.TemporaryDirectory() as temporary:
        test_db = Path(temporary) / "dataset_tools.db"
        shutil.copy2(source_catalog, test_db)

        records = CatalogBrowserRepository(test_db).fetch_records()
        assert len(records) >= 3, "Test catalog needs at least three images"

        # A user-supplied test catalog may already contain earlier history. The
        # regression scenario starts with a clean history stack while preserving
        # all current catalog metadata as its baseline.
        with closing(sqlite3.connect(test_db)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(catalog_edit_operations)"
                )
            }
            assert "discarded_at" in columns
            connection.execute("DELETE FROM catalog_edit_operations")
            provider_counts = {
                "analysis": connection.execute(
                    "SELECT COUNT(*) FROM analysis_results"
                ).fetchone()[0],
                "faces": connection.execute(
                    "SELECT COUNT(*) FROM face_detections"
                ).fetchone()[0],
            }
            connection.commit()

        with_identity = next(
            record for record in records if record.identity_match_id is not None
        )
        without_identity = next(
            record for record in records if record.identity_match_id is None
        )
        third = next(
            record
            for record in records
            if record.image_id not in {with_identity.image_id, without_identity.image_id}
        )
        selected_ids = sorted(
            {with_identity.image_id, without_identity.image_id, third.image_id}
        )
        service = CatalogEditService(test_db)
        # Normalize the selected rows so every requested test edit is guaranteed
        # to change something regardless of what the uploaded catalog contains.
        service.clear_manual_keyword(selected_ids)
        service.set_review_state(selected_ids, "unreviewed")
        if with_identity.identity_match_id is not None:
            service.review_identity_match(with_identity.identity_match_id, "suggested")
        before = _logical_state(test_db, selected_ids)

        first = service.apply_batch_edit(
            selected_ids,
            BatchEditRequest(keyword_action="set", keyword="history_keyword"),
        )
        second = service.apply_batch_edit(
            selected_ids,
            BatchEditRequest(review_status="keep"),
        )
        third_result = service.apply_batch_edit(
            selected_ids,
            BatchEditRequest(identity_status="confirmed"),
        )
        assert first.operation_id and second.operation_id
        assert third_result.identity_skipped_no_suggestion >= 1

        applied = _logical_state(test_db, selected_ids)
        assert applied != before
        assert service.get_last_undoable_operation() is not None
        assert service.get_next_redoable_operation() is None

        undo_identity = service.undo_last_operation()
        assert undo_identity.operation_id == third_result.operation_id
        assert service.get_next_redoable_operation() is not None

        undo_review = service.undo_last_operation()
        assert undo_review.operation_id == second.operation_id
        redo_review = service.redo_next_operation()
        assert redo_review.operation_id == second.operation_id
        redo_identity = service.redo_next_operation()
        assert redo_identity.operation_id == third_result.operation_id
        assert _logical_state(test_db, selected_ids) == applied

        # Undo twice, then create a new branch. Redo must be unavailable and the
        # superseded operations must be marked discarded rather than replayed.
        service.undo_last_operation()
        service.undo_last_operation()
        branch = service.apply_batch_edit(
            [selected_ids[0]],
            BatchEditRequest(keyword_action="set", keyword="new_branch"),
        )
        assert branch.operation_id is not None
        assert service.get_next_redoable_operation() is None

        with closing(sqlite3.connect(test_db)) as connection, connection:
            discarded = connection.execute(
                "SELECT COUNT(*) FROM catalog_edit_operations WHERE discarded_at IS NOT NULL"
            ).fetchone()[0]
            assert discarded >= 1

        # Generate more than the retained history depth. Older operations are
        # discarded, while the newest HISTORY_LIMIT remain available.
        for index in range(HISTORY_LIMIT + 5):
            service.apply_batch_edit(
                [selected_ids[0]],
                BatchEditRequest(keyword_action="set", keyword=f"step_{index}"),
            )
        with closing(sqlite3.connect(test_db)) as connection, connection:
            live_history = connection.execute(
                "SELECT COUNT(*) FROM catalog_edit_operations WHERE discarded_at IS NULL"
            ).fetchone()[0]
            assert live_history <= HISTORY_LIMIT

            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute(
                "SELECT COUNT(*) FROM analysis_results"
            ).fetchone()[0] == provider_counts["analysis"]
            assert connection.execute(
                "SELECT COUNT(*) FROM face_detections"
            ).fetchone()[0] == provider_counts["faces"]

        # Undo still refuses to overwrite an out-of-band change.
        conflict = service.apply_batch_edit(
            selected_ids,
            BatchEditRequest(review_status="reject"),
        )
        assert conflict.operation_id is not None
        service.set_review_state([selected_ids[0]], "review")
        try:
            service.undo_last_operation()
        except RuntimeError as error:
            assert "outside the recorded history" in str(error)
        else:
            raise AssertionError("Undo should refuse to overwrite newer metadata")

        print(
            "Milestone 7B regression tests passed: current schema migration, unified "
            "selection edits, multi-step undo/redo, branch invalidation, history "
            "retention, conflict protection, and provider preservation."
        )


if __name__ == "__main__":
    import sys

    run(Path(sys.argv[1]))
