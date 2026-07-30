"""Run and persist optional body analysis for existing catalog images.

The runner owns no GUI state.  It uses one local provider instance for the
batch, records provider/model provenance, reuses matching successful results,
and commits one image at a time so cancellation never corrupts the catalog.
"""

from __future__ import annotations

import sqlite3
import time

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from body_analysis import (
    PROVIDER_KEY,
    PROVIDER_LABEL,
    BodyAnalysisOptions,
    BodyAnalysisResult,
    MediaPipeBodyAnalyzer,
    calculate_file_sha256,
)
from catalog import Catalog


ProgressCallback = Callable[[int, int, Path], None]


class BodyAnalysisCancelled(RuntimeError):
    """Raised after a cooperative cancellation request between images."""


@dataclass(slots=True, frozen=True)
class BodyAnalysisSummary:
    requested_images: int
    analyzed_images: int
    reused_images: int
    failed_images: int
    bodies_detected: int
    full_body_images: int


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def analyze_catalog_bodies(
    database_path: Path,
    model_path: Path,
    options: BodyAnalysisOptions,
    *,
    image_ids: Iterable[int] | None = None,
    reuse_stored: bool = True,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
) -> BodyAnalysisSummary:
    """Analyze all requested images with a preferred present file location."""
    database = database_path.expanduser().resolve()
    model = model_path.expanduser().resolve()
    normalized = options.normalized()
    model_sha256 = calculate_file_sha256(model)
    requested_set = (
        {int(image_id) for image_id in image_ids}
        if image_ids is not None
        else None
    )

    # Opening through Catalog applies the additive body-analysis migration.
    with Catalog(database):
        pass

    connection = sqlite3.connect(database, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        model_id = _register_model(
            connection,
            model_path=model,
            model_sha256=model_sha256,
        )
        sources = _source_rows(connection, requested_set)
        analyzed = reused = failed = bodies = full_bodies = 0
        with MediaPipeBodyAnalyzer(model, normalized) as analyzer:
            for index, row in enumerate(sources):
                _wait_if_paused(pause_event, cancel_event)
                source_path = Path(str(row["absolute_path"]))
                if progress_callback is not None:
                    progress_callback(index, len(sources), source_path)
                image_id = int(row["image_id"])
                file_id = int(row["file_id"])

                if reuse_stored and _matching_result_exists(
                    connection,
                    image_id=image_id,
                    model_id=model_id,
                    options=normalized,
                ):
                    reused += 1
                    continue

                started = datetime.now(timezone.utc)
                try:
                    result = analyzer.analyze(source_path)
                except Exception as error:
                    failed += 1
                    _store_error(
                        connection,
                        image_id=image_id,
                        file_id=file_id,
                        model_id=model_id,
                        options=normalized,
                        error=f"{type(error).__name__}: {error}",
                        processing_seconds=(
                            datetime.now(timezone.utc) - started
                        ).total_seconds(),
                    )
                    continue

                analyzed += 1
                bodies += int(result.body_detected)
                full_bodies += int(result.full_body)
                _store_success(
                    connection,
                    image_id=image_id,
                    file_id=file_id,
                    model_id=model_id,
                    options=normalized,
                    result=result,
                    processing_seconds=(
                        datetime.now(timezone.utc) - started
                    ).total_seconds(),
                )

        if progress_callback is not None and sources:
            progress_callback(len(sources), len(sources), Path(str(sources[-1]["absolute_path"])))
        return BodyAnalysisSummary(
            requested_images=len(sources),
            analyzed_images=analyzed,
            reused_images=reused,
            failed_images=failed,
            bodies_detected=bodies,
            full_body_images=full_bodies,
        )
    finally:
        connection.close()


def store_import_body_result(
    connection: sqlite3.Connection,
    *,
    image_id: int,
    file_id: int,
    model_path: Path,
    options: BodyAnalysisOptions,
    result: BodyAnalysisResult,
    model_sha256: str | None = None,
) -> None:
    """Persist a result already calculated while filtering a folder import."""
    fingerprint = model_sha256 or calculate_file_sha256(model_path)
    model_id = _register_model(
        connection,
        model_path=model_path,
        model_sha256=fingerprint,
    )
    _store_success(
        connection,
        image_id=image_id,
        file_id=file_id,
        model_id=model_id,
        options=options.normalized(),
        result=result,
        processing_seconds=0.0,
    )


def _register_model(
    connection: sqlite3.Connection,
    *,
    model_path: Path,
    model_sha256: str,
) -> int:
    now = utc_now_text()
    connection.execute(
        """
        INSERT INTO body_models (
            provider_key, provider_label, provider_version, model_name,
            model_path, model_sha256, landmark_layout, license_label,
            first_seen_at, last_used_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'mediapipe_33', 'Apache-2.0', ?, ?)
        ON CONFLICT(provider_key, model_sha256)
        DO UPDATE SET
            model_path = excluded.model_path,
            last_used_at = excluded.last_used_at
        """,
        (
            PROVIDER_KEY,
            PROVIDER_LABEL,
            _mediapipe_version(),
            model_path.name,
            str(model_path),
            model_sha256,
            now,
            now,
        ),
    )
    row = connection.execute(
        """
        SELECT id FROM body_models
        WHERE provider_key = ? AND model_sha256 = ?
        """,
        (PROVIDER_KEY, model_sha256),
    ).fetchone()
    if row is None:
        raise RuntimeError("Could not register the body-analysis model.")
    connection.commit()
    return int(row[0])


def _source_rows(
    connection: sqlite3.Connection,
    image_ids: set[int] | None,
) -> list[sqlite3.Row]:
    parameters: list[int] = []
    where = ""
    if image_ids is not None:
        if not image_ids:
            return []
        placeholders = ",".join("?" for _ in image_ids)
        where = f"AND images.id IN ({placeholders})"
        parameters.extend(sorted(image_ids))
    return list(
        connection.execute(
            f"""
            SELECT
                images.id AS image_id,
                files.id AS file_id,
                files.absolute_path
            FROM images
            JOIN files ON files.id = (
                SELECT preferred.id
                FROM files AS preferred
                WHERE preferred.image_id = images.id
                  AND preferred.status = 'present'
                ORDER BY preferred.last_seen_at DESC, preferred.id DESC
                LIMIT 1
            )
            WHERE 1 = 1
              {where}
            ORDER BY images.id
            """,
            parameters,
        ).fetchall()
    )


def _matching_result_exists(
    connection: sqlite3.Connection,
    *,
    image_id: int,
    model_id: int,
    options: BodyAnalysisOptions,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM body_image_results
        WHERE image_id = ?
          AND body_model_id = ?
          AND detection_threshold = ?
          AND visibility_threshold = ?
          AND full_body_threshold_percent = ?
          AND status = 'success'
        """,
        (
            image_id,
            model_id,
            options.detection_threshold,
            options.landmark_visibility_threshold,
            options.full_body_threshold_percent,
        ),
    ).fetchone()
    return row is not None


def _store_success(
    connection: sqlite3.Connection,
    *,
    image_id: int,
    file_id: int,
    model_id: int,
    options: BodyAnalysisOptions,
    result: BodyAnalysisResult,
    processing_seconds: float,
) -> None:
    connection.execute(
        """
        INSERT INTO body_image_results (
            image_id, source_file_id, body_model_id, pose_count,
            body_detected, face_visible, full_body_score, full_body,
            classification, landmarks_json, detection_threshold,
            visibility_threshold, full_body_threshold_percent,
            status, error, processing_seconds, analyzed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', '', ?, ?)
        ON CONFLICT(
            image_id, body_model_id, detection_threshold,
            visibility_threshold, full_body_threshold_percent
        )
        DO UPDATE SET
            source_file_id = excluded.source_file_id,
            pose_count = excluded.pose_count,
            body_detected = excluded.body_detected,
            face_visible = excluded.face_visible,
            full_body_score = excluded.full_body_score,
            full_body = excluded.full_body,
            classification = excluded.classification,
            landmarks_json = excluded.landmarks_json,
            status = 'success',
            error = '',
            processing_seconds = excluded.processing_seconds,
            analyzed_at = excluded.analyzed_at
        """,
        (
            image_id,
            file_id,
            model_id,
            result.pose_count,
            int(result.body_detected),
            int(result.face_visible),
            result.full_body_score,
            int(result.full_body),
            result.classification,
            result.landmarks_json,
            options.detection_threshold,
            options.landmark_visibility_threshold,
            options.full_body_threshold_percent,
            max(0.0, float(processing_seconds)),
            utc_now_text(),
        ),
    )
    connection.commit()


def _store_error(
    connection: sqlite3.Connection,
    *,
    image_id: int,
    file_id: int,
    model_id: int,
    options: BodyAnalysisOptions,
    error: str,
    processing_seconds: float,
) -> None:
    empty = BodyAnalysisResult(
        pose_count=0,
        body_detected=False,
        face_visible=False,
        full_body_score=0.0,
        full_body=False,
        classification="analysis_error",
        landmarks_json="[]",
    )
    _store_success(
        connection,
        image_id=image_id,
        file_id=file_id,
        model_id=model_id,
        options=options,
        result=empty,
        processing_seconds=processing_seconds,
    )
    connection.execute(
        """
        UPDATE body_image_results
        SET status = 'error', error = ?
        WHERE image_id = ?
          AND body_model_id = ?
          AND detection_threshold = ?
          AND visibility_threshold = ?
          AND full_body_threshold_percent = ?
        """,
        (
            error,
            image_id,
            model_id,
            options.detection_threshold,
            options.landmark_visibility_threshold,
            options.full_body_threshold_percent,
        ),
    )
    connection.commit()


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise BodyAnalysisCancelled("Body analysis cancelled.")


def _wait_if_paused(
    pause_event: Event | None,
    cancel_event: Event | None,
) -> None:
    """Pause between images while retaining the initialized MediaPipe model."""
    while pause_event is not None and pause_event.is_set():
        _raise_if_cancelled(cancel_event)
        time.sleep(0.10)
    _raise_if_cancelled(cancel_event)


def _mediapipe_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("mediapipe")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
