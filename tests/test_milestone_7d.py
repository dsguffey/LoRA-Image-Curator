"""Dependency-free regression tests for v0.9.0 dataset export and assembly."""

from __future__ import annotations

import csv
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from catalog import Catalog, SCHEMA_VERSION
from dataset_export import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    DatasetExportRepository,
    ExportCancellationToken,
    ExportOptions,
    build_export_plan,
    execute_export,
)
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    build_training_text,
    custom_training_profile,
)


NOW = "2026-07-22T00:00:00+00:00"


def _seed_catalog(root: Path) -> tuple[Path, list[int], list[Path]]:
    database = root / "dataset_tools.db"
    source_a = root / "source_a"
    source_b = root / "source_b"
    source_a.mkdir()
    source_b.mkdir()

    first = source_a / "photo.jpg"
    second = source_b / "photo.jpg"
    missing = root / "missing" / "photo.jpg"
    first.write_bytes(b"first-image-bytes")
    second.write_bytes(b"second-image-bytes")

    with Catalog(database):
        pass

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for image_id, content_hash in ((1, "a" * 64), (2, "b" * 64), (3, "c" * 64)):
            connection.execute(
                """
                INSERT INTO images(
                    id, content_sha256, byte_size, width, height,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, 640, 480, ?, ?)
                """,
                (image_id, content_hash, 100 + image_id, NOW, NOW),
            )

        for file_id, image_id, path, status in (
            (1, 1, first, "present"),
            (2, 2, second, "present"),
            (3, 3, missing, "missing"),
        ):
            connection.execute(
                """
                INSERT INTO files(
                    id, image_id, path_key, absolute_path, input_root,
                    input_root_key, relative_path, byte_size, modified_time_ns,
                    status, first_seen_at, last_seen_at, last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 100, 1, ?, ?, ?, NULL)
                """,
                (
                    file_id,
                    image_id,
                    str(path).casefold(),
                    str(path),
                    str(path.parent),
                    str(path.parent).casefold(),
                    path.name,
                    status,
                    NOW,
                    NOW,
                ),
            )

        for image_id in (1, 2, 3):
            connection.execute(
                """
                INSERT INTO analysis_results(
                    id, image_id, source_file_id, model_name,
                    transformers_version, analysis_version, include_triage,
                    caption, detected_object_count, object_labels, person_count,
                    ocr_region_count, ocr_character_count, ocr_text,
                    likely_screenshot_or_ui, candidate_recommendation,
                    recommendation_reason, triage_status, triage_error,
                    processing_seconds, status, error, analyzed_at
                ) VALUES (
                    ?, ?, ?, 'test-provider', 'test', 1, 1,
                    ?, 3, 'person, red_dress, outdoors', 1,
                    0, 0, '', 'no', 'keep', 'test', 'complete', '',
                    0.1, 'success', '', ?
                )
                """,
                (
                    image_id,
                    image_id,
                    image_id,
                    f"Natural caption for image {image_id}.",
                    NOW,
                ),
            )

        tags = (
            (1, "subject_token", "subject_token", "manual_keyword"),
            (2, "red_dress", "red_dress", "manual_tag"),
            (3, "person", "person", "provider_object"),
            (4, "red_dress", "red_dress", "provider_object"),
            (5, "outdoors", "outdoors", "provider_object"),
        )
        connection.executemany(
            "INSERT INTO tags(id, name, normalized_name, category, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(*row, NOW) for row in tags],
        )

        for image_id in (1, 2, 3):
            connection.execute(
                """
                INSERT INTO image_tags(
                    image_id, tag_id, source, confidence, review_status,
                    notes, created_at, updated_at
                ) VALUES (?, 1, 'manual', NULL, 'confirmed', '', ?, ?)
                """,
                (image_id, NOW, NOW),
            )
        for image_id in (1, 2):
            connection.execute(
                """
                INSERT INTO image_tags(
                    image_id, tag_id, source, confidence, review_status,
                    notes, created_at, updated_at
                ) VALUES (?, 2, 'manual', NULL, 'confirmed', '', ?, ?)
                """,
                (image_id, NOW, NOW),
            )

        for image_id in (1, 2, 3):
            for tag_id in (3, 4, 5):
                connection.execute(
                    """
                    INSERT INTO analysis_tag_suggestions(
                        analysis_result_id, image_id, tag_id, provider_source,
                        confidence, created_at, updated_at
                    ) VALUES (?, ?, ?, 'florence_object_detection', NULL, ?, ?)
                    """,
                    (image_id, image_id, tag_id, NOW, NOW),
                )
        connection.execute(
            "INSERT INTO image_tag_exclusions(image_id, tag_id, created_at, updated_at) "
            "VALUES (1, 5, ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO image_review_state(image_id, status, notes, updated_at) "
            "VALUES (1, 'keep', '', ?)",
            (NOW,),
        )
        connection.commit()
    finally:
        connection.close()

    return database, [1, 2, 3], [first, second, missing]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database, image_ids, sources = _seed_catalog(root)
        repository = DatasetExportRepository(database)
        records = repository.fetch_records(image_ids)

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert SCHEMA_VERSION >= 12
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='export_runs'"
            ).fetchone()[0] == 1

        first = records[0]
        assert first.layers.trigger_keyword == "subject_token"
        assert first.layers.manual_tags == ("red_dress",)
        assert first.layers.active_ai_tags == ("person", "red_dress")
        assert first.excluded_ai_tags == ("outdoors",)

        # Built-in and Custom profiles derive text without mutating the catalog.
        flux = BUILTIN_TRAINING_PROFILES["flux_lora"]
        sdxl = BUILTIN_TRAINING_PROFILES["sdxl_lora"]
        caption = BUILTIN_TRAINING_PROFILES["caption_dataset"]
        custom = custom_training_profile(
            include_trigger=True,
            include_manual_tags=False,
            include_ai_tags=False,
            include_raw_caption=True,
        )
        assert build_training_text(first.layers, flux) == "subject_token, red_dress, person"
        assert build_training_text(first.layers, sdxl) == "subject_token, red_dress"
        assert build_training_text(first.layers, caption) == "Natural caption for image 1."
        assert build_training_text(first.layers, custom) == (
            "subject_token; Natural caption for image 1."
        )

        # Existing and intra-selection filename collisions are safely renamed.
        destination = root / "export_rename"
        destination.mkdir()
        (destination / "photo.jpg").write_bytes(b"preexisting")
        plan = build_export_plan(
            records,
            ExportOptions(
                destination=destination,
                profile=flux,
                copy_images=True,
                create_sidecars=True,
                create_manifest=True,
                collision_policy=COLLISION_RENAME,
            ),
        )
        assert plan.requested_count == 3
        assert plan.planned_count == 2
        assert plan.skipped_count == 1  # Missing source.
        assert plan.items[0].image_path.name == "photo_2.jpg"
        assert plan.items[1].image_path.name == "photo_3.jpg"
        assert plan.items[0].sidecar_path.name == "photo_2.txt"

        result = execute_export(plan, repository=repository)
        assert result.status == "partial"
        assert result.exported_count == 2
        assert result.skipped_count == 1
        assert result.failed_count == 0
        assert (destination / "photo.jpg").read_bytes() == b"preexisting"
        assert (destination / "photo_2.jpg").read_bytes() == sources[0].read_bytes()
        assert (destination / "photo_3.jpg").read_bytes() == sources[1].read_bytes()
        assert (destination / "photo_2.txt").read_text(encoding="utf-8").strip() == (
            "subject_token, red_dress, person"
        )
        assert result.manifest_path is not None and result.manifest_path.exists()
        manifest_rows = _read_csv(result.manifest_path)
        assert len(manifest_rows) == 3
        assert manifest_rows[0]["status"] == "exported"
        assert manifest_rows[2]["status"] == "skipped"

        with closing(sqlite3.connect(database)) as connection, connection:
            run_row = connection.execute(
                "SELECT status, exported_image_count, skipped_image_count, failed_image_count "
                "FROM export_runs WHERE id = ?",
                (result.run_id,),
            ).fetchone()
            assert run_row == ("partial", 2, 1, 0)
            assert connection.execute(
                "SELECT COUNT(*) FROM export_run_items WHERE export_run_id = ?",
                (result.run_id,),
            ).fetchone()[0] == 3

        # Skip policy never overwrites or renames an existing target.
        skip_destination = root / "export_skip"
        skip_destination.mkdir()
        (skip_destination / "photo.jpg").write_bytes(b"keep-me")
        skip_plan = build_export_plan(
            records[:1],
            ExportOptions(
                destination=skip_destination,
                profile=flux,
                collision_policy=COLLISION_SKIP,
            ),
        )
        assert skip_plan.items[0].planned_status == "skipped"
        skip_result = execute_export(skip_plan, repository=repository)
        assert skip_result.exported_count == 0 and skip_result.skipped_count == 1
        assert (skip_destination / "photo.jpg").read_bytes() == b"keep-me"

        # A source that disappears after preview becomes an isolated item error,
        # produces an error report, and leaves already exported items intact.
        failure_destination = root / "export_failure"
        failure_plan = build_export_plan(
            records[:2],
            ExportOptions(destination=failure_destination, profile=flux),
        )
        sources[1].unlink()
        failure_result = execute_export(failure_plan, repository=repository)
        assert failure_result.status == "partial"
        assert failure_result.exported_count == 1
        assert failure_result.failed_count == 1
        assert failure_result.error_report_path is not None
        assert _read_csv(failure_result.error_report_path)[0]["image_id"] == "2"

        # Cancellation stops before the next item and records untouched items as
        # skipped rather than errors. One user action still yields one run.
        sources[1].write_bytes(b"second-image-bytes")
        cancel_destination = root / "export_cancel"
        cancel_plan = build_export_plan(
            records[:2],
            ExportOptions(destination=cancel_destination, profile=flux),
        )
        token = ExportCancellationToken()

        def cancel_after_first(progress) -> None:
            if progress.processed_count == 1:
                token.cancel()

        cancel_result = execute_export(
            cancel_plan,
            repository=repository,
            cancellation=token,
            progress_callback=cancel_after_first,
        )
        assert cancel_result.status == "cancelled"
        assert cancel_result.exported_count == 1
        assert cancel_result.skipped_count == 1
        assert cancel_result.failed_count == 0

        # Source data and curated catalog layers are unchanged by every export.
        assert sources[0].read_bytes() == b"first-image-bytes"
        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("SELECT COUNT(*) FROM catalog_edit_operations").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM export_runs").fetchone()[0] == 4

        print(
            "v0.9.0 tests passed: schema 6 export history, profile-based training "
            "text, collision-safe copy/sidecars, manifests, missing files, item "
            "errors, cancellation, source preservation, and database integrity."
        )


if __name__ == "__main__":
    run()
