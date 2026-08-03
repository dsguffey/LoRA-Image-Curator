"""Dependency-free regression tests for v0.8.2 tag curation."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from catalog import Catalog, SCHEMA_VERSION
from catalog_browser import CatalogBrowserFrame, CatalogBrowserRepository, parse_manual_tag_input
from catalog_edits import CatalogEditService
from training_text import TrainingTextLayers, build_tag_training_text


def _snapshot_raw_analysis(database: Path) -> list[tuple]:
    with closing(sqlite3.connect(database)) as connection, connection:
        return connection.execute(
            """
            SELECT id, image_id, caption, object_labels, status
            FROM analysis_results
            ORDER BY id
            """
        ).fetchall()


def _find_common_ai_pair(database: Path) -> tuple[list[int], str]:
    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute(
            """
            WITH chosen_analysis AS (
                SELECT ar.id, ar.image_id
                FROM analysis_results AS ar
                WHERE ar.status = 'success'
                  AND ar.include_triage = 1
                  AND ar.id = (
                    SELECT ar2.id
                    FROM analysis_results AS ar2
                    WHERE ar2.image_id = ar.image_id
                      AND ar2.status = 'success'
                      AND ar2.include_triage = 1
                    ORDER BY ar2.analyzed_at DESC, ar2.id DESC
                    LIMIT 1
                )
            )
            SELECT t.name, GROUP_CONCAT(DISTINCT ats.image_id), COUNT(DISTINCT ats.image_id)
            FROM analysis_tag_suggestions AS ats
            JOIN chosen_analysis AS ca ON ca.id = ats.analysis_result_id
            JOIN tags AS t ON t.id = ats.tag_id
            GROUP BY t.normalized_name
            HAVING COUNT(DISTINCT ats.image_id) >= 2
            ORDER BY COUNT(DISTINCT ats.image_id) DESC, t.normalized_name
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise AssertionError("Test catalog needs two images sharing an AI object tag")
    image_ids = [int(value) for value in str(row[1]).split(",")[:2]]
    return sorted(image_ids), str(row[0])


def run(source_catalog: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "dataset_tools.db"
        shutil.copy2(source_catalog, database)

        # Opening through the repository performs the additive v5 migration and
        # backfills deduplicated AI suggestions from raw Florence object labels.
        repository = CatalogBrowserRepository(database)
        records = repository.fetch_records()
        assert records

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute(
                "SELECT COUNT(*) FROM analysis_tag_suggestions"
            ).fetchone()[0] > 0
            duplicate = connection.execute(
                """
                SELECT analysis_result_id, tag_id, provider_source, COUNT(*)
                FROM analysis_tag_suggestions
                GROUP BY analysis_result_id, tag_id, provider_source
                HAVING COUNT(*) > 1
                """
            ).fetchone()
            assert duplicate is None
            connection.execute("DELETE FROM catalog_edit_operations")
            connection.commit()

        selected_ids, shared_ai_tag = _find_common_ai_pair(database)
        service = CatalogEditService(database)
        raw_before = _snapshot_raw_analysis(database)

        # Parser and service both deduplicate case-insensitively. Adding a
        # manual version of an existing AI concept produces one effective chip,
        # not a blue/orange duplicate.
        parsed = parse_manual_tag_input(
            f"{shared_ai_tag}, studio_lighting; Studio_Lighting\n"
        )
        assert parsed == [shared_ai_tag, "studio_lighting"]
        added = service.add_manual_tags(selected_ids, parsed)
        assert added.changed_image_count == len(selected_ids)
        assert added.changed_assignment_count == len(selected_ids) * 2
        assert not service.add_manual_tags(selected_ids, parsed).changed_anything

        common = repository.fetch_common_tags(selected_ids)
        matching = [tag for tag in common if tag.normalized_name == shared_ai_tag.casefold()]
        assert len(matching) == 1
        assert matching[0].kind == "manual"
        assert any(tag.normalized_name == "studio_lighting" for tag in common)

        # A tag present on only part of a batch is deliberately omitted. Users
        # must add it normally to make the batch uniform.
        service.add_manual_tags([selected_ids[0]], ["partial_only"])
        assert any(
            tag.normalized_name == "partial_only"
            for tag in repository.fetch_common_tags([selected_ids[0]])
        )
        assert not any(
            tag.normalized_name == "partial_only"
            for tag in repository.fetch_common_tags(selected_ids)
        )

        # Removing the manual assertion reveals the still-preserved AI tag.
        removed = service.remove_manual_tags(selected_ids, [shared_ai_tag])
        assert removed.changed_image_count == len(selected_ids)
        matching = [
            tag
            for tag in repository.fetch_common_tags(selected_ids)
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert len(matching) == 1 and matching[0].kind == "ai_active"

        excluded = service.set_ai_tag_excluded(
            selected_ids, shared_ai_tag, excluded=True
        )
        assert excluded.changed_image_count == len(selected_ids)
        matching = [
            tag
            for tag in repository.fetch_common_tags(selected_ids)
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert len(matching) == 1 and matching[0].kind == "ai_excluded"

        # One click is one history operation even when hundreds of assignments
        # would be involved. Undo/redo restores the complete selection state.
        service.undo_last_operation()
        matching = [
            tag
            for tag in repository.fetch_common_tags(selected_ids)
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert matching[0].kind == "ai_active"
        service.redo_next_operation()
        matching = [
            tag
            for tag in repository.fetch_common_tags(selected_ids)
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert matching[0].kind == "ai_excluded"

        # Provider output is never rewritten by curation. Exclusions are also
        # independent user data and survive re-materializing the same analysis.
        assert _snapshot_raw_analysis(database) == raw_before
        with Catalog(database) as catalog:
            row = catalog.connection.execute(
                """
                SELECT *
                FROM analysis_results
                WHERE image_id = ?
                  AND status = 'success'
                  AND include_triage = 1
                ORDER BY analyzed_at DESC, id DESC
                LIMIT 1
                """,
                (selected_ids[0],),
            ).fetchone()
            assert row is not None and row["source_file_id"] is not None
            catalog.store_successful_analysis(
                image_id=int(row["image_id"]),
                source_file_id=int(row["source_file_id"]),
                model_name=str(row["model_name"]),
                transformers_version=str(row["transformers_version"]),
                analysis_version=int(row["analysis_version"]),
                include_triage=bool(row["include_triage"]),
                result={
                    "caption": str(row["caption"]),
                    "detected_object_count": row["detected_object_count"],
                    "object_labels": str(row["object_labels"]),
                    "person_count": row["person_count"],
                    "ocr_region_count": row["ocr_region_count"],
                    "ocr_character_count": row["ocr_character_count"],
                    "ocr_text": str(row["ocr_text"]),
                    "likely_screenshot_or_ui": str(row["likely_screenshot_or_ui"]),
                    "candidate_recommendation": str(row["candidate_recommendation"]),
                    "recommendation_reason": str(row["recommendation_reason"]),
                    "triage_status": str(row["triage_status"]),
                    "triage_error": str(row["triage_error"]),
                    "processing_seconds": float(row["processing_seconds"]),
                },
            )
        matching = [
            tag
            for tag in repository.fetch_common_tags([selected_ids[0]])
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert matching[0].kind == "ai_excluded"

        # A newer caption-only result must not hide object tags produced by the
        # latest successful triage analysis. Caption and tag providers can run
        # independently, so the browser intentionally chooses them separately.
        with Catalog(database) as catalog:
            source_row = catalog.connection.execute(
                "SELECT source_file_id FROM analysis_results WHERE image_id = ? "
                "AND source_file_id IS NOT NULL LIMIT 1",
                (selected_ids[0],),
            ).fetchone()
            assert source_row is not None
            catalog.store_successful_analysis(
                image_id=selected_ids[0],
                source_file_id=int(source_row["source_file_id"]),
                model_name="caption-only-regression-test",
                transformers_version="test",
                analysis_version=1,
                include_triage=False,
                result={
                    "caption": "A newer caption without object detection.",
                    "detected_object_count": None,
                    "object_labels": "",
                    "person_count": None,
                    "ocr_region_count": None,
                    "ocr_character_count": None,
                    "ocr_text": "",
                    "likely_screenshot_or_ui": "",
                    "candidate_recommendation": "",
                    "recommendation_reason": "",
                    "triage_status": "not_requested",
                    "triage_error": "",
                    "processing_seconds": 0.0,
                },
            )
        matching = [
            tag
            for tag in repository.fetch_common_tags([selected_ids[0]])
            if tag.normalized_name == shared_ai_tag.casefold()
        ]
        assert matching[0].kind == "ai_excluded"

        # Final training text is derived on demand in deterministic priority
        # order, with a manual assertion suppressing an equivalent AI duplicate.
        text = build_tag_training_text(
            TrainingTextLayers(
                trigger_keyword="subject_token",
                manual_tags=("studio_lighting", shared_ai_tag),
                active_ai_tags=(shared_ai_tag.upper(), "woman"),
            )
        )
        assert text == f"subject_token, studio_lighting, {shared_ai_tag}, woman"

        refreshed = {record.image_id: record for record in repository.fetch_records()}
        record = refreshed[selected_ids[0]]
        assert CatalogBrowserFrame._record_matches_search(record, "manual:studio_lighting")
        assert CatalogBrowserFrame._record_matches_search(
            record, f"excluded:{shared_ai_tag.replace(' ', '_')}"
        )
        assert not CatalogBrowserFrame._record_matches_search(
            record, f"NOT excluded:{shared_ai_tag.replace(' ', '_')}"
        )

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        print(
            "v0.8.2 tests passed: schema 5 AI-tag materialization, common-only "
            "batch chips, duplicate-free manual tags, exclusions, undo/redo, "
            "provider preservation, derived training text, and tag search."
        )


if __name__ == "__main__":
    import sys

    run(Path(sys.argv[1]))
