"""Dependency-light regression tests for Milestone 8B image quality.

The test creates synthetic images and temporary catalogs only. It exercises
schema migration, cached sharpness/perceptual hashes, adjustable duplicate
matching, profile-specific readiness, cancellation, and source preservation.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from contextlib import closing
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from advanced_search import record_matches_query
from catalog import Catalog, SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository
from catalog_lifecycle import create_catalog_database, delete_catalog_database
from dataset_readiness import build_readiness_report
from quality_analysis import (
    QualityCancellationToken,
    analyze_catalog_quality,
    measure_image_quality,
    nearest_duplicate_candidates,
    perceptual_hash_similarity,
)
from settings_manager import AppSettings, load_settings, save_settings


NOW = "2026-07-22T00:00:00+00:00"


def _make_images(root: Path) -> list[Path]:
    """Create sharp, recompressed/resized, and soft synthetic fixtures."""
    sharp = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(sharp)
    for offset in range(0, 160, 10):
        draw.line((offset, 0, 159 - offset, 159), fill="black", width=3)
        draw.line((0, offset, 159, 159 - offset), fill="navy", width=2)
    first = root / "sharp.png"
    sharp.save(first)

    second = root / "recompressed.jpg"
    sharp.resize((240, 240), Image.Resampling.LANCZOS).save(second, quality=72)

    soft = sharp.filter(ImageFilter.GaussianBlur(radius=8))
    third = root / "soft.png"
    soft.save(third)
    return [first, second, third]


def _seed_catalog(root: Path, sources: list[Path]) -> Path:
    database = root / "dataset_tools.db"
    with Catalog(database):
        pass
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for image_id, source in enumerate(sources, start=1):
            with Image.open(source) as image:
                width, height = image.size
            connection.execute(
                """
                INSERT INTO images(
                    id, content_sha256, byte_size, width, height,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    f"{image_id:064x}",
                    source.stat().st_size,
                    width,
                    height,
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO files(
                    id, image_id, path_key, absolute_path, input_root,
                    input_root_key, relative_path, byte_size, modified_time_ns,
                    status, first_seen_at, last_seen_at, last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, NULL)
                """,
                (
                    image_id,
                    image_id,
                    str(source).casefold(),
                    str(source),
                    str(root),
                    str(root).casefold(),
                    source.name,
                    source.stat().st_size,
                    source.stat().st_mtime_ns,
                    NOW,
                    NOW,
                ),
            )

        # The first image has a second path with identical SHA-256 content.
        exact_copy = root / "sharp-copy.png"
        exact_copy.write_bytes(sources[0].read_bytes())
        connection.execute(
            """
            INSERT INTO files(
                id, image_id, path_key, absolute_path, input_root,
                input_root_key, relative_path, byte_size, modified_time_ns,
                status, first_seen_at, last_seen_at, last_seen_run_id
            ) VALUES (10, 1, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, NULL)
            """,
            (
                str(exact_copy).casefold(),
                str(exact_copy),
                str(root),
                str(root).casefold(),
                exact_copy.name,
                exact_copy.stat().st_size,
                exact_copy.stat().st_mtime_ns,
                NOW,
                NOW,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return database


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sources = _make_images(root)
        originals = {path: path.read_bytes() for path in sources}
        database = _seed_catalog(root, sources)

        first_measurement = measure_image_quality(sources[0])
        second_measurement = measure_image_quality(sources[1])
        assert first_measurement.sharpness_score > 0
        assert len(first_measurement.perceptual_hash) == 16
        assert perceptual_hash_similarity(
            first_measurement.perceptual_hash,
            second_measurement.perceptual_hash,
        ) >= 80

        progress = []
        summary = analyze_catalog_quality(database, progress_callback=progress.append)
        assert summary.total_images == 3
        assert summary.analyzed_images == 3
        assert summary.reused_images == 0
        assert summary.failed_images == 0
        assert len(progress) == 3

        cached = analyze_catalog_quality(database)
        assert cached.analyzed_images == 0
        assert cached.reused_images == 3

        records = CatalogBrowserRepository(database).fetch_records()
        assert all(record.quality_status == "success" for record in records)
        nearest = nearest_duplicate_candidates(records)
        records = [
            replace(
                record,
                nearest_duplicate_image_id=(
                    nearest[record.image_id][0]
                    if record.image_id in nearest
                    else None
                ),
                nearest_duplicate_similarity=(
                    nearest[record.image_id][1]
                    if record.image_id in nearest
                    else None
                ),
            )
            for record in records
        ]
        exact_record = next(record for record in records if record.image_id == 1)
        assert exact_record.file_location_count == 2
        assert record_matches_query(exact_record, "duplicate:exact")
        assert any(record_matches_query(record, "duplicate:80") for record in records)
        assert all(record_matches_query(record, "quality:analyzed") for record in records)

        threshold = (
            min(record.sharpness_score for record in records if record.sharpness_score is not None)
            + 0.01
        )
        assert any(record_matches_query(record, f"blur:{threshold}") for record in records)

        flux = build_readiness_report(records, profile_key="flux_character_lora")
        sd15 = build_readiness_report(records, profile_key="sd15_character_lora")
        assert flux.profile.label == "Flux Character LoRA"
        assert sd15.profile.label == "SD 1.5 Character LoRA"
        assert next(issue for issue in flux.issues if issue.label == "Low Resolution").count == 3
        assert next(issue for issue in sd15.issues if issue.label == "Low Resolution").count == 3
        # Exact file locations remain searchable catalog metadata, but the
        # redundant readiness row was retired in 9B. Similarity Match is the
        # single duplicate-review control in Finalize & Export.
        assert all(issue.label != "Exact Copies" for issue in flux.issues)
        assert next(issue for issue in flux.issues if issue.label == "Possible Duplicates").deduction == 0

        cancelled_token = QualityCancellationToken()
        cancelled_token.cancel()
        cancelled = analyze_catalog_quality(
            database,
            reanalyze_all=True,
            cancellation=cancelled_token,
        )
        assert cancelled.cancelled
        assert cancelled.analyzed_images == 0

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
            assert connection.execute(
                "SELECT COUNT(*) FROM image_quality_results WHERE status='success'"
            ).fetchone()[0] == 3
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # Recreate a version-7-shaped catalog and confirm the additive migration.
        migration_database = root / "migration.db"
        with Catalog(migration_database):
            pass
        with closing(sqlite3.connect(migration_database)) as connection, connection:
            connection.execute("DROP TABLE image_set_members")
            connection.execute("DROP TABLE image_sets")
            connection.execute("DROP TABLE image_quality_results")
            connection.execute("PRAGMA user_version = 7")
            connection.commit()
        with Catalog(migration_database):
            pass
        with closing(sqlite3.connect(migration_database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='image_quality_results'"
            ).fetchone()[0] == 1

        assert all(path.read_bytes() == content for path, content in originals.items())

        lifecycle_database = root / "delete-me.db"
        create_catalog_database(lifecycle_database)
        retained_source = root / "retained-source.png"
        retained_source.write_bytes(sources[0].read_bytes())
        removed = delete_catalog_database(lifecycle_database)
        assert removed == (lifecycle_database.resolve(),)
        assert not lifecycle_database.exists()
        assert retained_source.exists()

        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root / "appdata")
        try:
            save_settings(
                AppSettings(
                    readiness_profile_key="sdxl_character_lora",
                    quality_blur_threshold=135.0,
                    quality_duplicate_similarity_percent=98,
                )
            )
            loaded = load_settings()
            assert loaded.readiness_profile_key == "sdxl_character_lora"
            assert loaded.quality_blur_threshold == 135.0
            assert loaded.quality_duplicate_similarity_percent == 98
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "Milestone 8B tests passed: schema 8 quality cache, blur measurement, "
        "adjustable near-duplicate matching, reuse, cancellation, LoRA profiles, "
        "settings, catalog lifecycle boundaries, source preservation, and SQLite integrity."
    )


if __name__ == "__main__":
    run()
