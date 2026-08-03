"""Dependency-light regression coverage for the v0.27.1 workflow polish."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from pathlib import Path

from catalog import Catalog, ImportRunCounts
from provider_coverage import read_catalog_provider_coverage
from video_extraction import (
    VideoExtractionOptions,
    estimate_interval_frame_count,
    run_video_extraction,
)


def _register_fixture_images(database: Path, image_folder: Path) -> list[tuple[int, int]]:
    """Create three present catalog images through the public catalog boundary."""
    records: list[tuple[int, int]] = []
    with Catalog(database) as catalog:
        run_id = catalog.start_import_run(
            input_root=image_folder,
            output_folder=database.parent,
            model_name="fixture",
            transformers_version="0",
            analysis_version=1,
            include_triage=False,
            reuse_stored_analysis=True,
        )
        for index in range(3):
            path = image_folder / f"image_{index}.jpg"
            path.write_bytes(f"fixture-{index}".encode("ascii"))
            registration = catalog.register_file(
                file_path=path,
                input_root=image_folder,
                run_id=run_id,
            )
            records.append((registration.image_id, registration.file_id))
        catalog.finish_import_run(
            run_id,
            status="complete",
            counts=ImportRunCounts(
                discovered_files=3,
                new_unique_images=3,
            ),
        )
    return records


def test_provider_coverage_counts_persisted_catalog_results() -> None:
    """Provider cards must report the active catalog, not the latest UI session."""
    with tempfile.TemporaryDirectory(prefix="lora_v0271_coverage_") as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        image_folder = root / "images"
        image_folder.mkdir()
        records = _register_fixture_images(database, image_folder)
        now = "2026-07-28T12:00:00+00:00"

        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = ON")
        image_1, file_1 = records[0]
        image_2, file_2 = records[1]
        connection.execute(
            """
            INSERT INTO analysis_results(
                image_id, source_file_id, model_name, transformers_version,
                analysis_version, include_triage, caption, object_labels,
                ocr_text, likely_screenshot_or_ui, candidate_recommendation,
                recommendation_reason, triage_status, triage_error,
                processing_seconds, status, error, analyzed_at
            ) VALUES (?, ?, 'florence', '1', 1, 1, 'caption', '', '', 'no',
                      'candidate', '', 'success', '', 0.1, 'success', '', ?)
            """,
            (image_1, file_1, now),
        )
        connection.execute(
            """
            INSERT INTO face_models(
                provider_key, provider_version, model_name, model_fingerprint,
                model_root, embedding_dimension, license_label,
                first_seen_at, last_used_at
            ) VALUES ('insightface', '1', 'fixture', 'abc', '', 4, '', ?, ?)
            """,
            (now, now),
        )
        face_model_id = int(
            connection.execute("SELECT id FROM face_models").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO face_image_results(
                image_id, source_file_id, face_model_id, face_count, status,
                error, processing_seconds, analyzed_at
            ) VALUES (?, ?, ?, 1, 'success', '', 0.1, ?)
            """,
            (image_1, file_1, face_model_id, now),
        )
        connection.execute(
            """
            INSERT INTO face_image_results(
                image_id, source_file_id, face_model_id, face_count, status,
                error, processing_seconds, analyzed_at
            ) VALUES (?, ?, ?, 0, 'error', 'fixture failure', 0.1, ?)
            """,
            (image_2, file_2, face_model_id, now),
        )
        connection.execute(
            """
            INSERT INTO body_models(
                provider_key, provider_label, provider_version, model_name,
                model_path, model_sha256, landmark_layout, license_label,
                first_seen_at, last_used_at
            ) VALUES ('mediapipe_pose', 'MediaPipe', '1', 'fixture.task',
                      'fixture.task', 'def', 'mediapipe_33', '', ?, ?)
            """,
            (now, now),
        )
        body_model_id = int(
            connection.execute("SELECT id FROM body_models").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO body_image_results(
                image_id, source_file_id, body_model_id, pose_count,
                body_detected, face_visible, full_body_score, full_body,
                classification, landmarks_json, detection_threshold,
                visibility_threshold, full_body_threshold_percent, status,
                error, processing_seconds, analyzed_at
            ) VALUES (?, ?, ?, 1, 1, 1, 0.8, 1, 'full_body', '[]',
                      0.5, 0.5, 70, 'success', '', 0.1, ?)
            """,
            (image_1, file_1, body_model_id, now),
        )
        connection.commit()
        connection.close()

        coverage = read_catalog_provider_coverage(database)
        assert coverage.florence.total_images == 3
        assert coverage.florence.checked_images == 1
        assert coverage.florence_triage_successful == 1
        assert coverage.face.checked_images == 2
        assert coverage.face.successful_images == 1
        assert coverage.face.error_images == 1
        assert coverage.body.checked_images == 1
        assert coverage.body.unchecked_images == 2


def _write_fake_ffmpeg(executable: Path) -> None:
    """Write a tiny deterministic process that behaves like image FFmpeg output."""
    executable.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

pattern = sys.argv[-1]
for number in (1, 2):
    path = pathlib.Path(pattern.replace("%06d", f"{number:06d}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"generated-{number}", encoding="utf-8")
print("frame=2", flush=True)
print("progress=end", flush=True)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def test_skip_collision_preserves_existing_numbered_frames() -> None:
    """Safe skip mode must merge missing names without replacing existing bytes."""
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory(prefix="lora_v0271_resume_") as temporary:
        root = Path(temporary)
        executable = root / "fake_ffmpeg"
        source = root / "movie.mkv"
        destination = root / "frames"
        destination.mkdir()
        source.write_bytes(b"video")
        _write_fake_ffmpeg(executable)
        existing = destination / "frame_000001.jpg"
        existing.write_text("keep-me", encoding="utf-8")

        summary = run_video_extraction(
            VideoExtractionOptions(
                ffmpeg_path=executable,
                source_video=source,
                destination_folder=destination,
                interval_seconds=0.5,
                max_frames=2,
                output_format="jpg",
                filename_prefix="frame",
                collision_policy="skip",
            )
        )
        assert existing.read_text(encoding="utf-8") == "keep-me"
        assert (destination / "frame_000002.jpg").read_text(
            encoding="utf-8"
        ) == "generated-2"
        assert summary.output_count == 1
        assert summary.skipped_existing_files == 1


def test_estimate_and_ui_contract() -> None:
    """Protect the full-video estimate and the agreed discoverable controls."""
    assert estimate_interval_frame_count(8476.0, 0.5, 20_000) == 16_952
    project = Path(__file__).resolve().parents[1]
    browser = (project / "catalog_browser.py").read_text(encoding="utf-8")
    dialogs = (project / "browser_workflow_dialogs.py").read_text(encoding="utf-8")
    app = (project / "app.py").read_text(encoding="utf-8")
    assert 'text="First"' in browser
    assert 'text="−10"' in browser
    assert 'text="+10"' in browser
    assert 'text="Last"' in browser
    assert "kind=\"filter\"" in browser
    assert "command=self._clear_and_apply" in dialogs
    assert 'text="Run / Restart Florence"' in app
    assert 'text="Run / Restart Face"' in app
    assert 'label="Run Florence Caption & Triage…"' in app
    assert 'label="Run Face Detection & Identity…"' in app


def main() -> None:
    test_provider_coverage_counts_persisted_catalog_results()
    test_skip_collision_preserves_existing_numbered_frames()
    test_estimate_and_ui_contract()
    print(
        "v0.27.1 regression tests passed: provider coverage, deterministic "
        "skip/merge extraction, full-video estimates, filter history, and "
        "large-catalog navigation."
    )


if __name__ == "__main__":
    main()
