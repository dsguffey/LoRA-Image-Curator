"""Metadata-only folder import for LoRA Image Curator catalogs.

Milestone 8D separates *cataloging files* from running an AI provider.  A user
can therefore create or extend a catalog from a folder immediately, inspect the
thumbnails/readiness data that are available, and decide later whether Florence
or face analysis is worth running.

Safety and ownership decisions
------------------------------

* Source images are opened read-only and are never moved, renamed, or changed.
* Create, merge, and replace operations are built in a staging database.  The
  requested catalog is replaced only after the complete import succeeds.
  Cancellation or an unexpected error therefore leaves the original catalog
  untouched instead of publishing a partially imported state.
* SHA-256 remains the catalog's content identity.  Byte-identical files map to
  one image record, while each known location remains traceable for missing-file
  checks and exact-copy reporting.
* The optional image set is persistent only because the user explicitly asks
  for it as part of the import.
"""

from __future__ import annotations

import os
import sqlite3

from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Literal
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from body_analysis import (
    BodyAnalysisOptions,
    MediaPipeBodyAnalyzer,
    calculate_file_sha256,
    inspect_body_setup,
)
from body_analysis_runner import store_import_body_result
from catalog import Catalog, ImportRunCounts
from catalog_lifecycle import validate_catalog_database
from image_discovery import discover_supported_images
from image_sets import (
    MAX_IMAGE_SET_NAME_LENGTH,
    ImageSetRepository,
    normalize_image_set_name,
)
from video_origin import VideoOriginManifestCache


ImportMode = Literal["create", "merge", "replace"]
ProgressCallback = Callable[[int, int, Path], None]


class CatalogImportCancelled(RuntimeError):
    """Raised when the user cancels before the staged catalog is published."""


@dataclass(slots=True, frozen=True)
class CatalogImportOptions:
    """All deliberate choices for one folder-to-catalog operation."""

    source_folder: Path
    target_database: Path
    mode: ImportMode
    recursive: bool = True
    create_image_set: bool = True
    image_set_name: str = ""
    overwrite_existing: bool = False
    skip_without_body: bool = False
    skip_without_face: bool = False
    body_model_path: str = ""
    body_detection_threshold: float = 0.50
    body_landmark_visibility_threshold: float = 0.50
    body_full_body_threshold_percent: int = 70


@dataclass(slots=True, frozen=True)
class CatalogImportFailure:
    """One supported-extension file that could not be cataloged."""

    file_path: Path
    error: str


@dataclass(slots=True, frozen=True)
class CatalogImportSummary:
    """User-facing outcome of a successfully published staged import."""

    source_folder: Path
    target_database: Path
    mode: ImportMode
    recursive: bool
    discovered_files: int
    cataloged_files: int
    new_unique_images: int
    exact_duplicate_files: int
    unchanged_files: int
    changed_files: int
    skipped_without_body: int
    skipped_without_face: int
    duplicate_sha256_values: tuple[str, ...]
    failed_files: tuple[CatalogImportFailure, ...]
    image_set_name: str
    image_set_image_count: int


def discover_image_files(source_folder: Path, *, recursive: bool) -> list[Path]:
    """Return supported images in deterministic path order.

    Folder traversal intentionally ignores sidecars, databases, cache files,
    and every other unsupported extension.  A non-recursive import examines
    only direct children of the chosen folder.
    """
    return discover_supported_images(source_folder, recursive=recursive)


def default_image_set_name(source_folder: Path) -> str:
    """Return a useful set name even for a filesystem root folder."""
    source = source_folder.expanduser().resolve()
    candidate = source.name.strip() or "Imported images"
    return candidate[:MAX_IMAGE_SET_NAME_LENGTH].rstrip()


def import_catalog_folder(
    options: CatalogImportOptions,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> CatalogImportSummary:
    """Import a folder through a private staging catalog, then publish it.

    ``create`` refuses an existing target unless the GUI has recorded an
    explicit overwrite confirmation in ``overwrite_existing``. ``merge`` begins
    from a consistent SQLite backup of the current catalog. ``replace`` begins
    with a new empty catalog and therefore removes the prior catalog's owned
    metadata, quality data, image sets, and history when the staged result is
    published.
    """
    source = options.source_folder.expanduser().resolve()
    target = options.target_database.expanduser().resolve()
    _validate_options(options, source=source, target=target)
    image_paths = discover_image_files(source, recursive=options.recursive)

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.import-{uuid4().hex}.tmp")
    imported_image_ids: set[int] = set()
    duplicate_hashes: set[str] = set()
    failures: list[CatalogImportFailure] = []
    skipped_without_body = 0
    skipped_without_face = 0
    cataloged_files = 0
    counts = ImportRunCounts(discovered_files=len(image_paths))
    body_options = BodyAnalysisOptions(
        detection_threshold=options.body_detection_threshold,
        landmark_visibility_threshold=(
            options.body_landmark_visibility_threshold
        ),
        full_body_threshold_percent=options.body_full_body_threshold_percent,
    ).normalized()
    body_model = (
        Path(options.body_model_path).expanduser().resolve()
        if options.body_model_path
        else None
    )
    body_model_sha256 = (
        calculate_file_sha256(body_model)
        if body_model is not None
        and (options.skip_without_body or options.skip_without_face)
        else None
    )
    video_origins = VideoOriginManifestCache()

    try:
        if options.mode == "merge":
            _backup_catalog(target, staging)

        analyzer_context = (
            MediaPipeBodyAnalyzer(body_model, body_options)
            if (options.skip_without_body or options.skip_without_face)
            and body_model is not None
            else None
        )
        with Catalog(staging) as catalog, (
            analyzer_context
            if analyzer_context is not None
            else _NullBodyAnalyzer()
        ) as body_analyzer:
            run_id = catalog.start_import_run(
                input_root=source,
                output_folder=target.parent,
                model_name="LoRA Image Curator folder import",
                transformers_version="not applicable",
                analysis_version=0,
                include_triage=False,
                reuse_stored_analysis=False,
            )

            for index, image_path in enumerate(image_paths, start=1):
                _raise_if_cancelled(cancel_event)
                if progress_callback is not None:
                    progress_callback(index - 1, len(image_paths), image_path)

                try:
                    width, height = _read_image_dimensions(image_path)
                    body_result = (
                        body_analyzer.analyze(image_path)
                        if analyzer_context is not None
                        else None
                    )
                    if (
                        options.skip_without_body
                        and body_result is not None
                        and not body_result.body_detected
                    ):
                        skipped_without_body += 1
                        continue
                    if (
                        options.skip_without_face
                        and body_result is not None
                        and not body_result.face_visible
                    ):
                        skipped_without_face += 1
                        continue
                    registration = catalog.register_file(
                        file_path=image_path,
                        input_root=source,
                        run_id=run_id,
                    )
                    video_origin = video_origins.origin_for(image_path)
                    if video_origin is not None:
                        catalog.store_file_video_origin(
                            file_id=registration.file_id,
                            source_video=video_origin.source_video,
                            sampling_mode=video_origin.sampling_mode,
                            timestamp_seconds=video_origin.timestamp_seconds,
                            frame_number=video_origin.frame_number,
                            interval_seconds=video_origin.interval_seconds,
                        )
                    catalog.update_image_dimensions(
                        registration.image_id,
                        width,
                        height,
                    )
                    if (
                        body_result is not None
                        and body_model is not None
                    ):
                        store_import_body_result(
                            catalog.connection,
                            image_id=registration.image_id,
                            file_id=registration.file_id,
                            model_path=body_model,
                            options=body_options,
                            result=body_result,
                            model_sha256=body_model_sha256,
                        )
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    SyntaxError,
                    sqlite3.Error,
                    UnidentifiedImageError,
                    Image.DecompressionBombError,
                ) as error:
                    failures.append(
                        CatalogImportFailure(
                            file_path=image_path,
                            error=f"{type(error).__name__}: {error}",
                        )
                    )
                    counts.failed_analyses += 1
                    continue

                imported_image_ids.add(registration.image_id)
                cataloged_files += 1
                if registration.action == "new_image":
                    counts.new_unique_images += 1
                elif registration.action == "new_location_existing_image":
                    counts.new_locations_existing_images += 1
                    duplicate_hashes.add(registration.content_sha256)
                elif registration.action == "unchanged_file":
                    counts.unchanged_files += 1
                else:
                    counts.changed_files += 1

            catalog.finish_import_run(
                run_id,
                status="complete",
                counts=counts,
                error_message=(
                    f"{len(failures)} file(s) could not be cataloged"
                    if failures
                    else ""
                ),
            )

        _raise_if_cancelled(cancel_event)

        image_set_name = ""
        image_set_count = 0
        if options.create_image_set and imported_image_ids:
            repository = ImageSetRepository(staging)
            requested_name = options.image_set_name.strip() or default_image_set_name(source)
            image_set_name = _available_image_set_name(repository, requested_name)
            created_set = repository.create_set(image_set_name, imported_image_ids)
            image_set_count = created_set.image_count

        _checkpoint_catalog(staging)
        _raise_if_cancelled(cancel_event)
        _publish_staging_catalog(staging, target)

        return CatalogImportSummary(
            source_folder=source,
            target_database=target,
            mode=options.mode,
            recursive=options.recursive,
            discovered_files=len(image_paths),
            cataloged_files=cataloged_files,
            new_unique_images=counts.new_unique_images,
            exact_duplicate_files=counts.new_locations_existing_images,
            unchanged_files=counts.unchanged_files,
            changed_files=counts.changed_files,
            skipped_without_body=skipped_without_body,
            skipped_without_face=skipped_without_face,
            duplicate_sha256_values=tuple(sorted(duplicate_hashes)),
            failed_files=tuple(failures),
            image_set_name=image_set_name,
            image_set_image_count=image_set_count,
        )
    finally:
        _remove_database_artifacts(staging)


def format_import_summary(summary: CatalogImportSummary) -> str:
    """Format a compact completion message with factual duplicate details."""
    action = {
        "create": "Created catalog",
        "merge": "Merged folder into catalog",
        "replace": "Replaced catalog contents",
    }[summary.mode]
    lines = [
        action,
        "",
        f"Supported image files found: {summary.discovered_files:,}",
        f"Files cataloged: {summary.cataloged_files:,}",
        f"New unique images: {summary.new_unique_images:,}",
        (
            "Exact SHA-256 duplicates skipped as additional images: "
            f"{summary.exact_duplicate_files:,}"
        ),
        f"Unchanged known files: {summary.unchanged_files:,}",
        f"Changed known files: {summary.changed_files:,}",
        f"Skipped — no body/pose evidence: {summary.skipped_without_body:,}",
        f"Skipped — no visible-face pose evidence: {summary.skipped_without_face:,}",
        f"Files that could not be cataloged: {len(summary.failed_files):,}",
    ]

    if summary.image_set_name:
        lines.extend(
            (
                "",
                f'Image set: "{summary.image_set_name}" '
                f"({summary.image_set_image_count:,} images)",
            )
        )

    if summary.duplicate_sha256_values:
        lines.extend(("", "Duplicate SHA-256 values:"))
        lines.extend(summary.duplicate_sha256_values)

    if summary.failed_files:
        lines.extend(("", "Files not cataloged:"))
        for failure in summary.failed_files:
            lines.append(f"{failure.file_path}: {failure.error}")

    return "\n".join(lines)


def _validate_options(
    options: CatalogImportOptions,
    *,
    source: Path,
    target: Path,
) -> None:
    if options.mode not in {"create", "merge", "replace"}:
        raise ValueError(f"Unsupported catalog import mode: {options.mode}")
    if not source.exists() or not source.is_dir():
        raise NotADirectoryError(f"Image folder not found: {source}")
    if options.mode == "create" and target.exists():
        if not options.overwrite_existing:
            raise FileExistsError(f"Catalog already exists: {target}")
        # Confirmation belongs to the GUI, but the backend still validates
        # that the existing file is actually a LoRA Image Curator catalog before
        # permitting its atomic replacement.
        validate_catalog_database(target)
    if options.mode in {"merge", "replace"}:
        validate_catalog_database(target)
    if options.skip_without_body or options.skip_without_face:
        if not options.body_model_path:
            raise ValueError(
                "Import filtering requires a configured body-analysis model."
            )
        status = inspect_body_setup(
            Path(options.body_model_path),
            perform_runtime_check=True,
        )
        if not status.ready:
            raise RuntimeError(
                "\n".join(status.notes)
                or "The body-analysis provider/model is not ready."
            )


def _read_image_dimensions(image_path: Path) -> tuple[int, int]:
    """Validate that Pillow can decode the file header and obtain dimensions."""
    with Image.open(image_path) as image:
        width, height = image.size
        image.verify()
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    return int(width), int(height)


def _backup_catalog(source: Path, destination: Path) -> None:
    """Create a consistent SQLite staging copy, including committed WAL data."""
    validate_catalog_database(source)
    with closing(sqlite3.connect(source, timeout=30.0)) as source_connection:
        with closing(sqlite3.connect(destination, timeout=30.0)) as destination_connection:
            source_connection.backup(destination_connection)


def _checkpoint_catalog(database_path: Path) -> None:
    """Fold staged WAL frames into the main database before atomic publication."""
    with closing(sqlite3.connect(database_path, timeout=30.0)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _publish_staging_catalog(staging: Path, target: Path) -> None:
    """Atomically publish the verified staging file at the requested path."""
    validate_catalog_database(staging)
    if target.exists():
        # Fold every committed frame into the original main file before its
        # sidecars are removed. If the subsequent atomic replace fails, the
        # original remains a complete, valid database in DELETE journal mode.
        with closing(sqlite3.connect(target, timeout=30.0)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
        for suffix in ("-wal", "-shm"):
            with suppress(FileNotFoundError):
                Path(str(target) + suffix).unlink()
    os.replace(staging, target)


def _remove_database_artifacts(database_path: Path) -> None:
    """Remove only the uniquely named staging database and its SQLite sidecars."""
    for candidate in (
        database_path,
        Path(str(database_path) + "-wal"),
        Path(str(database_path) + "-shm"),
    ):
        with suppress(FileNotFoundError):
            candidate.unlink()


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CatalogImportCancelled("Catalog import cancelled.")


def _available_image_set_name(
    repository: ImageSetRepository,
    requested_name: str,
) -> str:
    """Avoid overwriting an existing deliberate set during a merge import."""
    base_name = normalize_image_set_name(requested_name)
    existing = {item.name.casefold() for item in repository.list_sets()}
    if base_name.casefold() not in existing:
        return base_name
    counter = 2
    while True:
        suffix = f" ({counter})"
        prefix = base_name[: MAX_IMAGE_SET_NAME_LENGTH - len(suffix)].rstrip()
        candidate = f"{prefix}{suffix}"
        if candidate.casefold() not in existing:
            return candidate
        counter += 1


class _NullBodyAnalyzer:
    """Context-compatible no-op used when import filtering is disabled."""

    def __enter__(self) -> "_NullBodyAnalyzer":
        return self

    def __exit__(self, *_exception: object) -> None:
        return None
