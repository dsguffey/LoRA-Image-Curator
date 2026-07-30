"""
dataset_export.py

Non-destructive dataset assembly for LoRA Image Curator.

The export subsystem deliberately sits outside catalog editing and provider
analysis. It reads the permanent catalog, derives training text from immutable
provider output plus user-owned curation, and copies selected files into a new
folder. Source images are never moved, renamed, deleted, or overwritten.

The module is split into four layers:

* ``DatasetExportRepository`` reads structured export records and writes a
  lightweight audit history to the catalog.
* ``build_export_plan`` resolves destination names before any files are written.
* ``execute_export`` performs collision-safe staged copies, sidecars, manifest,
  cancellation, and error reporting.
* Small immutable dataclasses make the GUI and regression tests share the same
  behavior rather than reimplementing export rules in widgets.

Exports are intentionally not undoable catalog edits. They create new files in a
user-selected destination and record what happened, but they never alter review
state, tags, analyses, or source-file records.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import threading
import uuid

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from catalog import Catalog
from training_text import (
    TrainingTextLayers,
    TrainingTextProfile,
    build_training_text,
)


COLLISION_RENAME = "rename"
COLLISION_SKIP = "skip"
VALID_COLLISION_POLICIES = {COLLISION_RENAME, COLLISION_SKIP}


@dataclass(slots=True, frozen=True)
class ExportImageRecord:
    """All catalog data needed to export one selected image."""

    image_id: int
    source_path: Path | None
    filename: str
    content_sha256: str
    review_status: str
    suggested_identity: str
    identity_review_status: str
    excluded_ai_tags: tuple[str, ...]
    layers: TrainingTextLayers


@dataclass(slots=True, frozen=True)
class ExportOptions:
    """One deliberate export request from the GUI or a regression test."""

    destination: Path
    profile: TrainingTextProfile
    copy_images: bool = True
    create_sidecars: bool = True
    create_manifest: bool = True
    create_readme: bool = True
    collision_policy: str = COLLISION_RENAME
    handoff_scope: str = ""
    handoff_notes: tuple[str, ...] = ()

    def validated(self) -> "ExportOptions":
        """Return a normalized copy or raise a user-facing ``ValueError``."""
        destination = self.destination.expanduser().resolve()
        collision_policy = self.collision_policy.strip().casefold()
        if collision_policy not in VALID_COLLISION_POLICIES:
            raise ValueError(
                "Collision policy must be 'rename' or 'skip'."
            )
        if not (
            self.copy_images
            or self.create_sidecars
            or self.create_manifest
            or self.create_readme
        ):
            raise ValueError(
                "Choose at least one output: copied images, TXT sidecars, a CSV "
                "manifest, or a training-handoff README."
            )
        if not any(
            (
                self.profile.include_trigger,
                self.profile.include_manual_tags,
                self.profile.include_ai_tags,
                self.profile.include_raw_caption,
            )
        ) and self.create_sidecars:
            raise ValueError(
                "The selected profile contains no training-text layers. "
                "Enable at least one Custom profile option or turn off sidecars."
            )
        return ExportOptions(
            destination=destination,
            profile=self.profile,
            copy_images=bool(self.copy_images),
            create_sidecars=bool(self.create_sidecars),
            create_manifest=bool(self.create_manifest),
            create_readme=bool(self.create_readme),
            collision_policy=collision_policy,
            handoff_scope=" ".join(str(self.handoff_scope).split()).strip(),
            handoff_notes=tuple(
                note
                for note in (
                    " ".join(str(value).split()).strip()
                    for value in self.handoff_notes
                )
                if note
            ),
        )


@dataclass(slots=True, frozen=True)
class ExportPlanItem:
    """Resolved destination paths for one image before export starts."""

    record: ExportImageRecord
    training_text: str
    image_path: Path | None
    sidecar_path: Path | None
    planned_status: str = "planned"  # planned or skipped
    reason: str = ""


@dataclass(slots=True, frozen=True)
class ExportPlan:
    """Complete, reviewable export plan with no filesystem mutations."""

    options: ExportOptions
    items: tuple[ExportPlanItem, ...]
    manifest_path: Path | None
    readme_path: Path | None

    @property
    def requested_count(self) -> int:
        return len(self.items)

    @property
    def planned_count(self) -> int:
        return sum(item.planned_status == "planned" for item in self.items)

    @property
    def skipped_count(self) -> int:
        return self.requested_count - self.planned_count

    @property
    def image_file_count(self) -> int:
        return sum(item.image_path is not None for item in self.items if item.planned_status == "planned")

    @property
    def sidecar_file_count(self) -> int:
        return sum(item.sidecar_path is not None for item in self.items if item.planned_status == "planned")


@dataclass(slots=True, frozen=True)
class ExportProgress:
    """One thread-safe progress notification sent to the GUI."""

    processed_count: int
    total_count: int
    filename: str
    message: str


@dataclass(slots=True, frozen=True)
class ExportError:
    """One item-level failure suitable for UI display and CSV reporting."""

    image_id: int
    filename: str
    source_path: str
    message: str


@dataclass(slots=True, frozen=True)
class ExportResult:
    """Final outcome returned by ``execute_export``."""

    run_id: int | None
    status: str
    requested_count: int
    exported_count: int
    skipped_count: int
    failed_count: int
    cancelled: bool
    manifest_path: Path | None
    readme_path: Path | None
    error_report_path: Path | None
    errors: tuple[ExportError, ...] = field(default_factory=tuple)


class ExportCancellationToken:
    """Cooperative cancellation shared between the GUI and worker thread."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class DatasetExportRepository:
    """Read export layers and append export audit history for one catalog."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    def fetch_records(self, image_ids: Iterable[int]) -> list[ExportImageRecord]:
        """Return structured export records in the caller's image-ID order."""
        ordered_ids = _normalize_ids_preserving_order(image_ids)
        if not ordered_ids:
            return []
        if not self.database_path.exists():
            raise FileNotFoundError(f"Catalog not found: {self.database_path}")

        # Catalog opening applies supported additive migrations, including the
        # export-history schema introduced by v0.9.0.
        with Catalog(self.database_path):
            pass

        connection = _connect(self.database_path)
        placeholders = ",".join("?" for _ in ordered_ids)
        try:
            rows = connection.execute(
                f"""
                WITH preferred_files AS (
                    SELECT
                        f.image_id,
                        f.absolute_path,
                        f.status,
                        ROW_NUMBER() OVER (
                            PARTITION BY f.image_id
                            ORDER BY
                                CASE f.status WHEN 'present' THEN 0 ELSE 1 END,
                                f.last_seen_at DESC,
                                f.id DESC
                        ) AS preference_rank
                    FROM files AS f
                ),
                chosen_caption AS (
                    SELECT ar.image_id, ar.caption
                    FROM analysis_results AS ar
                    WHERE ar.status = 'success'
                      AND ar.id = (
                          SELECT ar2.id
                          FROM analysis_results AS ar2
                          WHERE ar2.image_id = ar.image_id
                            AND ar2.status = 'success'
                          ORDER BY ar2.analyzed_at DESC, ar2.id DESC
                          LIMIT 1
                      )
                ),
                best_identity AS (
                    SELECT
                        fir.image_id,
                        identities.name AS suggested_identity,
                        im.review_status AS identity_review_status
                    FROM identity_matches AS im
                    JOIN identity_profiles AS ip
                        ON ip.id = im.identity_profile_id
                    JOIN identities
                        ON identities.id = ip.identity_id
                    JOIN face_detections AS fd
                        ON fd.id = im.face_detection_id
                    JOIN face_image_results AS fir
                        ON fir.id = fd.face_result_id
                    WHERE im.id = (
                        SELECT im2.id
                        FROM identity_matches AS im2
                        JOIN face_detections AS fd2
                            ON fd2.id = im2.face_detection_id
                        JOIN face_image_results AS fir2
                            ON fir2.id = fd2.face_result_id
                        WHERE fir2.image_id = fir.image_id
                          AND im2.is_suggested = 1
                        ORDER BY im2.similarity DESC, im2.id DESC
                        LIMIT 1
                    )
                )
                SELECT
                    images.id AS image_id,
                    images.content_sha256,
                    preferred_files.absolute_path,
                    preferred_files.status AS file_status,
                    COALESCE(image_review_state.status, 'unreviewed') AS review_status,
                    COALESCE(chosen_caption.caption, '') AS raw_caption,
                    COALESCE(best_identity.suggested_identity, '') AS suggested_identity,
                    COALESCE(best_identity.identity_review_status, '') AS identity_review_status
                FROM images
                LEFT JOIN preferred_files
                    ON preferred_files.image_id = images.id
                   AND preferred_files.preference_rank = 1
                LEFT JOIN chosen_caption
                    ON chosen_caption.image_id = images.id
                LEFT JOIN image_review_state
                    ON image_review_state.image_id = images.id
                LEFT JOIN best_identity
                    ON best_identity.image_id = images.id
                WHERE images.id IN ({placeholders})
                """,
                ordered_ids,
            ).fetchall()

            base_by_id = {int(row["image_id"]): row for row in rows}
            missing_ids = [image_id for image_id in ordered_ids if image_id not in base_by_id]
            if missing_ids:
                raise ValueError(
                    "The catalog no longer contains selected image IDs: "
                    + ", ".join(str(value) for value in missing_ids[:10])
                )

            manual_keywords: dict[int, str] = {}
            manual_tags: dict[int, list[str]] = {image_id: [] for image_id in ordered_ids}
            active_ai_tags: dict[int, list[str]] = {image_id: [] for image_id in ordered_ids}
            excluded_ai_tags: dict[int, list[str]] = {image_id: [] for image_id in ordered_ids}

            manual_rows = connection.execute(
                f"""
                SELECT it.image_id, t.name, t.category
                FROM image_tags AS it
                JOIN tags AS t ON t.id = it.tag_id
                WHERE it.image_id IN ({placeholders})
                  AND LOWER(it.source) IN ('manual', 'user', 'user_manual')
                  AND it.review_status <> 'rejected'
                  AND t.category IN ('manual_keyword', 'manual_tag')
                ORDER BY it.image_id, t.normalized_name, it.id
                """,
                ordered_ids,
            ).fetchall()
            for row in manual_rows:
                image_id = int(row["image_id"])
                name = str(row["name"] or "").strip()
                if not name:
                    continue
                if str(row["category"]) == "manual_keyword":
                    manual_keywords.setdefault(image_id, name)
                else:
                    manual_tags[image_id].append(name)

            ai_rows = connection.execute(
                f"""
                WITH chosen_tag_analysis AS (
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
                SELECT
                    ats.image_id,
                    t.name,
                    CASE WHEN exclusions.tag_id IS NULL THEN 0 ELSE 1 END AS excluded
                FROM analysis_tag_suggestions AS ats
                JOIN chosen_tag_analysis AS chosen
                    ON chosen.id = ats.analysis_result_id
                JOIN tags AS t
                    ON t.id = ats.tag_id
                LEFT JOIN image_tag_exclusions AS exclusions
                    ON exclusions.image_id = ats.image_id
                   AND exclusions.tag_id = ats.tag_id
                WHERE ats.image_id IN ({placeholders})
                ORDER BY ats.image_id, t.normalized_name
                """,
                ordered_ids,
            ).fetchall()
            for row in ai_rows:
                image_id = int(row["image_id"])
                name = str(row["name"] or "").strip()
                if not name:
                    continue
                target = excluded_ai_tags if bool(row["excluded"]) else active_ai_tags
                target[image_id].append(name)

            records: list[ExportImageRecord] = []
            for image_id in ordered_ids:
                row = base_by_id[image_id]
                raw_path = str(row["absolute_path"] or "").strip()
                source_path = Path(raw_path) if raw_path else None
                filename = source_path.name if source_path is not None else f"image_{image_id}"
                records.append(
                    ExportImageRecord(
                        image_id=image_id,
                        source_path=source_path,
                        filename=filename,
                        content_sha256=str(row["content_sha256"] or ""),
                        review_status=str(row["review_status"] or "unreviewed"),
                        suggested_identity=str(row["suggested_identity"] or ""),
                        identity_review_status=str(row["identity_review_status"] or ""),
                        excluded_ai_tags=tuple(excluded_ai_tags[image_id]),
                        layers=TrainingTextLayers(
                            trigger_keyword=manual_keywords.get(image_id, ""),
                            manual_tags=tuple(manual_tags[image_id]),
                            active_ai_tags=tuple(active_ai_tags[image_id]),
                            raw_caption=str(row["raw_caption"] or ""),
                        ),
                    )
                )
            return records
        finally:
            connection.close()

    def start_export_run(self, plan: ExportPlan) -> int:
        """Record a running export and return its catalog audit ID."""
        connection = _connect(self.database_path)
        try:
            cursor = connection.execute(
                """
                INSERT INTO export_runs(
                    started_at,
                    status,
                    destination_path,
                    profile_key,
                    profile_json,
                    copy_images,
                    create_sidecars,
                    create_manifest,
                    collision_policy,
                    requested_image_count,
                    exported_image_count,
                    skipped_image_count,
                    failed_image_count,
                    error_message
                )
                VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '')
                """,
                (
                    _utc_now_text(),
                    str(plan.options.destination),
                    plan.options.profile.key,
                    json.dumps(asdict(plan.options.profile), ensure_ascii=False, sort_keys=True),
                    int(plan.options.copy_images),
                    int(plan.options.create_sidecars),
                    int(plan.options.create_manifest),
                    plan.options.collision_policy,
                    plan.requested_count,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def record_export_item(
        self,
        *,
        run_id: int,
        item: ExportPlanItem,
        status: str,
        error_message: str = "",
    ) -> None:
        """Append or update one item outcome without touching catalog metadata."""
        connection = _connect(self.database_path)
        try:
            connection.execute(
                """
                INSERT INTO export_run_items(
                    export_run_id,
                    image_id,
                    source_path,
                    exported_image_path,
                    sidecar_path,
                    status,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(export_run_id, image_id) DO UPDATE SET
                    source_path = excluded.source_path,
                    exported_image_path = excluded.exported_image_path,
                    sidecar_path = excluded.sidecar_path,
                    status = excluded.status,
                    error_message = excluded.error_message
                """,
                (
                    run_id,
                    item.record.image_id,
                    str(item.record.source_path or ""),
                    str(item.image_path or ""),
                    str(item.sidecar_path or ""),
                    status,
                    error_message,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def finish_export_run(
        self,
        *,
        run_id: int,
        status: str,
        exported_count: int,
        skipped_count: int,
        failed_count: int,
        error_message: str = "",
    ) -> None:
        """Finalize aggregate counts for a completed, partial, or cancelled run."""
        connection = _connect(self.database_path)
        try:
            connection.execute(
                """
                UPDATE export_runs
                SET completed_at = ?,
                    status = ?,
                    exported_image_count = ?,
                    skipped_image_count = ?,
                    failed_image_count = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    _utc_now_text(),
                    status,
                    exported_count,
                    skipped_count,
                    failed_count,
                    error_message,
                    run_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()


def build_export_plan(
    records: Sequence[ExportImageRecord],
    options: ExportOptions,
) -> ExportPlan:
    """
    Resolve every output path without creating folders or writing files.

    The planner prevents both existing-destination collisions and collisions
    within the current selection. ``rename`` appends ``_2``, ``_3``, and so on;
    ``skip`` records an explicit skip reason. No policy overwrites existing data.
    """
    normalized_options = options.validated()
    destination = normalized_options.destination
    reserved: set[str] = set()
    items: list[ExportPlanItem] = []

    for record in records:
        training_text = build_training_text(record.layers, normalized_options.profile)
        source = record.source_path
        needs_source = normalized_options.copy_images or normalized_options.create_sidecars
        if needs_source and (source is None or not source.exists() or not source.is_file()):
            items.append(
                ExportPlanItem(
                    record=record,
                    training_text=training_text,
                    image_path=None,
                    sidecar_path=None,
                    planned_status="skipped",
                    reason="Source image is missing or unavailable.",
                )
            )
            continue

        source_name = _safe_source_name(record)
        stem = Path(source_name).stem or f"image_{record.image_id}"
        suffix = Path(source_name).suffix
        if not suffix and normalized_options.copy_images:
            suffix = ".img"

        candidate_index = 1
        planned_item: ExportPlanItem | None = None
        while planned_item is None:
            candidate_stem = stem if candidate_index == 1 else f"{stem}_{candidate_index}"
            image_path = (
                destination / f"{candidate_stem}{suffix}"
                if normalized_options.copy_images
                else None
            )
            sidecar_path = (
                destination / f"{candidate_stem}.txt"
                if normalized_options.create_sidecars
                else None
            )
            targets = [path for path in (image_path, sidecar_path) if path is not None]
            collides = any(_path_key(path) in reserved or path.exists() for path in targets)
            if not collides:
                for path in targets:
                    reserved.add(_path_key(path))
                planned_item = ExportPlanItem(
                    record=record,
                    training_text=training_text,
                    image_path=image_path,
                    sidecar_path=sidecar_path,
                )
                continue

            if normalized_options.collision_policy == COLLISION_SKIP:
                planned_item = ExportPlanItem(
                    record=record,
                    training_text=training_text,
                    image_path=image_path,
                    sidecar_path=sidecar_path,
                    planned_status="skipped",
                    reason="Destination filename already exists or is reserved by this export.",
                )
                continue
            candidate_index += 1

        items.append(planned_item)

    manifest_path = None
    if normalized_options.create_manifest:
        manifest_path = _allocate_standalone_path(
            destination,
            "manifest.csv",
            reserved,
        )

    readme_path = None
    if normalized_options.create_readme:
        readme_path = _allocate_standalone_path(
            destination,
            "README.txt",
            reserved,
        )

    return ExportPlan(
        options=normalized_options,
        items=tuple(items),
        manifest_path=manifest_path,
        readme_path=readme_path,
    )


def execute_export(
    plan: ExportPlan,
    *,
    repository: DatasetExportRepository | None = None,
    cancellation: ExportCancellationToken | None = None,
    progress_callback: Callable[[ExportProgress], None] | None = None,
) -> ExportResult:
    """
    Execute a reviewed plan using staged writes and cooperative cancellation.

    Each image/sidecar pair is staged under temporary names before promotion.
    Existing files are never replaced. A failure is isolated to the current
    item, reported in ``export_errors.csv``, and does not roll back already
    completed copies. The manifest records every requested image, including
    skipped and failed items, so partial exports remain auditable.
    """
    cancellation = cancellation or ExportCancellationToken()
    destination = plan.options.destination
    destination.mkdir(parents=True, exist_ok=True)

    run_id: int | None = None
    if repository is not None:
        run_id = repository.start_export_run(plan)

    exported_count = 0
    skipped_count = 0
    failed_count = 0
    errors: list[ExportError] = []
    outcome_by_image: dict[int, tuple[str, str]] = {}
    processed_count = 0

    try:
        for item in plan.items:
            if cancellation.cancelled:
                break

            if item.planned_status == "skipped":
                skipped_count += 1
                processed_count += 1
                outcome_by_image[item.record.image_id] = ("skipped", item.reason)
                if repository is not None and run_id is not None:
                    repository.record_export_item(
                        run_id=run_id,
                        item=item,
                        status="skipped",
                        error_message=item.reason,
                    )
                _notify_progress(
                    progress_callback,
                    ExportProgress(
                        processed_count,
                        plan.requested_count,
                        item.record.filename,
                        item.reason,
                    ),
                )
                continue

            try:
                _export_one_item(item, plan.options)
            except Exception as error:  # Per-item isolation is intentional.
                message = f"{type(error).__name__}: {error}"
                failed_count += 1
                errors.append(
                    ExportError(
                        image_id=item.record.image_id,
                        filename=item.record.filename,
                        source_path=str(item.record.source_path or ""),
                        message=message,
                    )
                )
                outcome_by_image[item.record.image_id] = ("error", message)
                item_status = "error"
            else:
                exported_count += 1
                outcome_by_image[item.record.image_id] = ("exported", "")
                item_status = "exported"
                message = "Exported"

            processed_count += 1
            if repository is not None and run_id is not None:
                repository.record_export_item(
                    run_id=run_id,
                    item=item,
                    status=item_status,
                    error_message=("" if item_status == "exported" else message),
                )
            _notify_progress(
                progress_callback,
                ExportProgress(
                    processed_count,
                    plan.requested_count,
                    item.record.filename,
                    message,
                ),
            )

        cancelled = cancellation.cancelled
        if cancelled:
            # Items not reached after cancellation are recorded as skipped in
            # the manifest/history rather than falsely counted as failures.
            for item in plan.items[processed_count:]:
                skipped_count += 1
                reason = "Export cancelled before this image was processed."
                outcome_by_image[item.record.image_id] = ("skipped", reason)
                if repository is not None and run_id is not None:
                    repository.record_export_item(
                        run_id=run_id,
                        item=item,
                        status="skipped",
                        error_message=reason,
                    )

        manifest_path = None
        if plan.options.create_manifest and plan.manifest_path is not None:
            _write_manifest(plan, outcome_by_image)
            manifest_path = plan.manifest_path

        error_report_path = None
        if errors:
            reserved = {
                _path_key(path)
                for item in plan.items
                for path in (item.image_path, item.sidecar_path)
                if path is not None
            }
            if plan.manifest_path is not None:
                reserved.add(_path_key(plan.manifest_path))
            if plan.readme_path is not None:
                reserved.add(_path_key(plan.readme_path))
            error_report_path = _allocate_standalone_path(
                destination,
                "export_errors.csv",
                reserved,
            )
            _write_error_report(error_report_path, errors)

        if cancelled:
            final_status = "cancelled"
        elif failed_count or skipped_count:
            final_status = "partial"
        else:
            final_status = "complete"

        readme_path = None
        if plan.options.create_readme and plan.readme_path is not None:
            _write_handoff_readme(
                plan,
                status=final_status,
                exported_count=exported_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
            )
            readme_path = plan.readme_path

        if repository is not None and run_id is not None:
            repository.finish_export_run(
                run_id=run_id,
                status=final_status,
                exported_count=exported_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                error_message=(
                    f"{failed_count} item(s) failed; see error report."
                    if failed_count
                    else ""
                ),
            )

        return ExportResult(
            run_id=run_id,
            status=final_status,
            requested_count=plan.requested_count,
            exported_count=exported_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            cancelled=cancelled,
            manifest_path=manifest_path,
            readme_path=readme_path,
            error_report_path=error_report_path,
            errors=tuple(errors),
        )
    except Exception as error:
        if repository is not None and run_id is not None:
            try:
                repository.finish_export_run(
                    run_id=run_id,
                    status="failed",
                    exported_count=exported_count,
                    skipped_count=skipped_count,
                    failed_count=failed_count,
                    error_message=f"{type(error).__name__}: {error}",
                )
            except Exception:
                pass
        raise


def format_export_preview(plan: ExportPlan, *, sample_limit: int = 12) -> str:
    """Return a compact human-readable plan for the Preview button."""
    lines = [
        f"Destination: {plan.options.destination}",
        f"Profile: {plan.options.profile.label}",
        f"Selected images: {plan.requested_count:,}",
        f"Images to copy: {plan.image_file_count:,}",
        f"TXT sidecars: {plan.sidecar_file_count:,}",
        f"Planned skips: {plan.skipped_count:,}",
        f"Manifest: {plan.manifest_path.name if plan.manifest_path else 'No'}",
        f"Training handoff README: {plan.readme_path.name if plan.readme_path else 'No'}",
        "",
        "Sample outputs:",
    ]
    for item in plan.items[:sample_limit]:
        if item.planned_status == "skipped":
            lines.append(f"• {item.record.filename} — SKIP: {item.reason}")
            continue
        targets = [path.name for path in (item.image_path, item.sidecar_path) if path]
        lines.append(f"• {item.record.filename} → {', '.join(targets) or 'manifest only'}")
    if len(plan.items) > sample_limit:
        lines.append(f"…and {len(plan.items) - sample_limit:,} more")

    sample = next((item for item in plan.items if item.training_text), None)
    lines.extend(
        [
            "",
            "Example training text:",
            sample.training_text if sample is not None else "(empty)",
        ]
    )
    return "\n".join(lines)


def _export_one_item(item: ExportPlanItem, options: ExportOptions) -> None:
    """Stage and atomically promote one image/sidecar pair."""
    staged: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    try:
        if options.copy_images and item.image_path is not None:
            source = item.record.source_path
            if source is None:
                raise FileNotFoundError("No source image path is stored.")
            _assert_target_unused(item.image_path)
            temporary = _temporary_sibling(item.image_path)
            shutil.copy2(source, temporary)
            staged.append((temporary, item.image_path))

        if options.create_sidecars and item.sidecar_path is not None:
            _assert_target_unused(item.sidecar_path)
            temporary = _temporary_sibling(item.sidecar_path)
            with temporary.open("w", encoding="utf-8", newline="\n") as sidecar:
                sidecar.write(item.training_text)
                sidecar.write("\n")
            staged.append((temporary, item.sidecar_path))

        for temporary, target in staged:
            # ``replace`` is safe here because the target was checked again just
            # before staging. A race that creates the target between checks is
            # extremely unlikely in a local desktop workflow; refuse it rather
            # than overwrite by checking once more.
            _assert_target_unused(target)
            temporary.rename(target)
            promoted.append(target)
    except Exception:
        for temporary, _target in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        for target in promoted:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _write_manifest(
    plan: ExportPlan,
    outcomes: dict[int, tuple[str, str]],
) -> None:
    """Write a UTF-8-with-BOM audit manifest that opens cleanly in Excel."""
    assert plan.manifest_path is not None
    _assert_target_unused(plan.manifest_path)
    temporary = _temporary_sibling(plan.manifest_path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                (
                    "image_id",
                    "status",
                    "error",
                    "exported_filename",
                    "sidecar_filename",
                    "source_path",
                    "content_sha256",
                    "profile",
                    "training_text",
                    "set_keyword",
                    "manual_tags",
                    "active_ai_tags",
                    "excluded_ai_tags",
                    "raw_caption",
                    "review_status",
                    "suggested_identity",
                    "identity_review_status",
                )
            )
            for item in plan.items:
                status, error = outcomes.get(
                    item.record.image_id,
                    (item.planned_status, item.reason),
                )
                layers = item.record.layers
                writer.writerow(
                    (
                        item.record.image_id,
                        status,
                        error,
                        item.image_path.name if item.image_path else "",
                        item.sidecar_path.name if item.sidecar_path else "",
                        str(item.record.source_path or ""),
                        item.record.content_sha256,
                        plan.options.profile.label,
                        item.training_text,
                        layers.trigger_keyword,
                        " | ".join(layers.manual_tags),
                        " | ".join(layers.active_ai_tags),
                        " | ".join(item.record.excluded_ai_tags),
                        layers.raw_caption,
                        item.record.review_status,
                        item.record.suggested_identity,
                        item.record.identity_review_status,
                    )
                )
        temporary.rename(plan.manifest_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_error_report(path: Path, errors: Sequence[ExportError]) -> None:
    """Write item failures separately so the completion dialog stays compact."""
    _assert_target_unused(path)
    temporary = _temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("image_id", "filename", "source_path", "error"))
            for error in errors:
                writer.writerow(
                    (error.image_id, error.filename, error.source_path, error.message)
                )
        temporary.rename(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_handoff_readme(
    plan: ExportPlan,
    *,
    status: str,
    exported_count: int,
    skipped_count: int,
    failed_count: int,
) -> None:
    """Write a compact human-readable record beside the training files.

    The README intentionally documents what LoRA Image Curator actually exported. It
    does not invent optimizer, learning-rate, epoch, or trainer settings that
    cannot be inferred safely from the catalog.
    """
    assert plan.readme_path is not None
    _assert_target_unused(plan.readme_path)
    temporary = _temporary_sibling(plan.readme_path)
    options = plan.options
    scope = options.handoff_scope or "Browser selection"
    notes = options.handoff_notes or (
        "No Dataset Readiness summary was attached to this export.",
    )
    lines = [
        "LORA IMAGE CURATOR TRAINING HANDOFF",
        "==============================",
        "",
        f"Created: {_utc_now_text()}",
        f"Scope: {scope}",
        f"Training text profile: {options.profile.label}",
        f"Export status: {status}",
        f"Requested catalog images: {plan.requested_count}",
        f"Successfully processed catalog images: {exported_count}",
        f"Skipped catalog images: {skipped_count}",
        f"Failed catalog images: {failed_count}",
        "",
        "OUTPUTS",
        "-------",
        f"Copied images: {'Yes' if options.copy_images else 'No'}",
        f"Same-name TXT sidecars: {'Yes' if options.create_sidecars else 'No'}",
        f"CSV manifest: {'Yes' if options.create_manifest else 'No'}",
        "",
        "PRE-EXPORT NOTES",
        "----------------",
        *(f"- {note}" for note in notes),
        "",
        "IMPORTANT",
        "---------",
        (
            "LoRA Image Curator copied export data non-destructively. It did not move, "
            "delete, resize, convert, or rewrite the source images."
        ),
        (
            "This file records dataset preparation only. Choose trainer-specific "
            "network, optimizer, learning-rate, epoch, and resolution settings in "
            "the training tool you actually use."
        ),
        "",
    ]
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines))
        temporary.rename(plan.readme_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _allocate_standalone_path(
    destination: Path,
    filename: str,
    reserved: set[str],
) -> Path:
    """Allocate a collision-safe manifest/error-report filename."""
    requested = Path(filename)
    index = 1
    while True:
        stem = requested.stem if index == 1 else f"{requested.stem}_{index}"
        candidate = destination / f"{stem}{requested.suffix}"
        key = _path_key(candidate)
        if key not in reserved and not candidate.exists():
            reserved.add(key)
            return candidate
        index += 1


def _safe_source_name(record: ExportImageRecord) -> str:
    """Return a usable basename without inventing path hierarchy."""
    name = Path(record.filename).name.strip()
    if not name or name in {".", ".."}:
        return f"image_{record.image_id}.img"
    return name


def _temporary_sibling(target: Path) -> Path:
    """Create a unique hidden staging filename beside the final destination."""
    return target.with_name(f".{target.name}.datasettools-{uuid.uuid4().hex}.tmp")


def _assert_target_unused(path: Path) -> None:
    if path.exists():
        raise FileExistsError(
            f"LoRA Image Curator will not overwrite an existing export file: {path.name}"
        )


def _path_key(path: Path) -> str:
    """Use case-insensitive reservation on Windows and normal paths elsewhere."""
    value = os.path.abspath(str(path))
    return os.path.normcase(value)


def _normalize_ids_preserving_order(image_ids: Iterable[int]) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for raw_value in image_ids:
        value = int(raw_value)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _notify_progress(
    callback: Callable[[ExportProgress], None] | None,
    progress: ExportProgress,
) -> None:
    if callback is None:
        return
    try:
        callback(progress)
    except Exception:
        # Progress UI failure must not corrupt an otherwise valid export.
        pass
