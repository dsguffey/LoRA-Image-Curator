"""Persistent, versioned SQLite storage for LoRA Image Curator.

Why SQLite?
-----------
CSV remains useful for reports and exports, but it becomes awkward once the
application needs to preserve relationships such as:

- one image appearing at more than one file path
- multiple analysis results for one image
- automatic and manual tags
- accepted or rejected tag suggestions
- review state
- file-action history
- future duplicate groups and face embeddings

SQLite provides those relationships in one local database file without
requiring the user to install or administer a database server.

Current schema
--------------
The version-10 catalog retains the original separation of an image's *content* from its *location*:

    images
        One record per unique SHA-256 file hash.

    files
        One record per path where that content has been seen.

This matters because two folders can contain byte-for-byte copies of the same
image. They should share analysis results while still retaining distinct file
locations for future copy, quarantine, and delete operations.

The schema also stores face-provider results and creates general tables for:

- tag definitions
- image/tag assignments
- review state
- file-action history
- versioned analysis results
- face detections and embeddings
- identity reference profiles and reviewable matches
- provider-owned structured tag suggestions tied to analysis results
- user-owned AI-tag exclusions that survive provider reruns
- durable selection-edit snapshots used by multi-step undo and redo
- explicitly named saved searches created by deliberate user action
- explicitly named image sets and their catalog-local memberships

The GUI does not expose all of those features yet. Creating the structure now
prevents later features from requiring a disruptive database redesign.

Schema versioning
-----------------
SQLite's ``PRAGMA user_version`` stores the catalog schema version.

A future LoRA Image Curator release can add migration functions such as:

    version 1 -> version 2
    version 2 -> version 3
    version 3 -> version 4
    version 4 -> version 5

without requiring the user to delete the catalog and start over.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app_identity import CATALOG_APPLICATION_ID
from image_discovery import is_legacy_thumbnail_cache_path


CATALOG_FILENAME = "dataset_tools.db"
SCHEMA_VERSION = 14
HASH_CHUNK_SIZE = 4 * 1024 * 1024


# =============================================================================
# Public data structures
# =============================================================================

@dataclass(slots=True)
class FileRegistration:
    """
    Describe how one discovered file affected the catalog.

    ``action`` is one of:

    - ``new_image``
    - ``new_location_existing_image``
    - ``unchanged_file``
    - ``changed_file_content``
    - ``changed_file_metadata``
    """

    image_id: int
    file_id: int
    content_sha256: str
    action: str


@dataclass(slots=True)
class CatalogSummary:
    """High-level counts suitable for the GUI's catalog summary panel."""

    unique_images: int
    file_locations: int
    present_file_locations: int
    missing_file_locations: int
    defined_tags: int
    tag_assignments: int


@dataclass(slots=True)
class ImportRunCounts:
    """Counts recorded for one import/analysis run."""

    discovered_files: int = 0
    new_unique_images: int = 0
    new_locations_existing_images: int = 0
    unchanged_files: int = 0
    changed_files: int = 0
    missing_files_marked: int = 0
    reused_analyses: int = 0
    generated_analyses: int = 0
    failed_analyses: int = 0


# =============================================================================
# Small utility functions
# =============================================================================

def utc_now_text() -> str:
    """
    Return a stable, timezone-aware timestamp for database storage.

    ISO-8601 text is readable during troubleshooting and sorts correctly.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_path_key(path: Path) -> str:
    """
    Return the comparison key used to identify a file path.

    ``os.path.normcase`` makes the key case-insensitive on Windows while
    preserving normal case-sensitive behavior on platforms that use it.
    """
    resolved = path.expanduser().resolve()
    return os.path.normcase(os.path.normpath(str(resolved)))


def calculate_sha256(file_path: Path) -> str:
    """
    Calculate a file's SHA-256 content hash without loading it all into memory.
    """
    digest = hashlib.sha256()

    with file_path.open("rb") as source_file:
        while True:
            chunk = source_file.read(HASH_CHUNK_SIZE)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# =============================================================================
# Catalog class
# =============================================================================

class Catalog:
    """
    Manage one LoRA Image Curator SQLite catalog.

    A Catalog object owns one SQLite connection and should be used from only
    one thread. The current application creates it inside the background
    analysis thread, which satisfies that rule.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
        )
        self.connection.row_factory = sqlite3.Row

        # Construction can fail while configuring SQLite or applying a schema
        # migration.  Because ``__init__`` never returns in that case, callers
        # cannot enter the Catalog context manager and close the connection for
        # us.  Close here so a failed open never leaves the catalog locked on
        # Windows or produces a delayed ResourceWarning on modern Python.
        try:
            self._configure_connection()
            self._ensure_schema()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        """
        Checkpoint the write-ahead log and close the database connection.
        """
        try:
            self.connection.commit()
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self.connection.close()

    # =========================================================================
    # Connection and migration handling
    # =========================================================================

    def _configure_connection(self) -> None:
        """
        Enable SQLite features useful for a desktop catalog.

        WAL (write-ahead logging) improves resilience and permits readers while
        a write transaction is active. Foreign keys protect relationships.
        """
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")

    def _get_schema_version(self) -> int:
        row = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()

        return int(row[0])

    def _set_schema_version(self, version: int) -> None:
        self.connection.execute(
            f"PRAGMA user_version = {int(version)}"
        )

    def _ensure_schema(self) -> None:
        """
        Create or migrate the database to the version this release expects.
        """
        current_version = self._get_schema_version()

        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                "This catalog was created by a newer LoRA Image Curator version.\n\n"
                f"Catalog schema: {current_version}\n"
                f"Supported schema: {SCHEMA_VERSION}\n\n"
                "Open it with the newer application rather than risking data "
                "loss."
            )

        while current_version < SCHEMA_VERSION:
            if current_version == 0:
                self._migrate_0_to_1()
                current_version = 1
            elif current_version == 1:
                self._migrate_1_to_2()
                current_version = 2
            elif current_version == 2:
                self._migrate_2_to_3()
                current_version = 3
            elif current_version == 3:
                self._migrate_3_to_4()
                current_version = 4
            elif current_version == 4:
                self._migrate_4_to_5()
                current_version = 5
            elif current_version == 5:
                self._migrate_5_to_6()
                current_version = 6
            elif current_version == 6:
                self._migrate_6_to_7()
                current_version = 7
            elif current_version == 7:
                self._migrate_7_to_8()
                current_version = 8
            elif current_version == 8:
                self._migrate_8_to_9()
                current_version = 9
            elif current_version == 9:
                self._migrate_9_to_10()
                current_version = 10
            elif current_version == 10:
                self._migrate_10_to_11()
                current_version = 11
            elif current_version == 11:
                self._migrate_11_to_12()
                current_version = 12
            elif current_version == 12:
                self._migrate_12_to_13()
                current_version = 13
            elif current_version == 13:
                self._migrate_13_to_14()
                current_version = 14
            else:
                raise RuntimeError(
                    "LoRA Image Curator does not know how to migrate catalog schema "
                    f"{current_version}."
                )

    def _migrate_0_to_1(self) -> None:
        """
        Create the initial catalog schema.

        ``executescript`` is wrapped in a transaction so a failed initial
        creation cannot leave a misleading version number.
        """
        schema_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE import_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL
                CHECK(status IN ('running', 'complete', 'failed')),
            input_root TEXT NOT NULL,
            input_root_key TEXT NOT NULL,
            output_folder TEXT NOT NULL,
            model_name TEXT NOT NULL,
            transformers_version TEXT NOT NULL,
            analysis_version INTEGER NOT NULL,
            include_triage INTEGER NOT NULL
                CHECK(include_triage IN (0, 1)),
            reuse_stored_analysis INTEGER NOT NULL
                CHECK(reuse_stored_analysis IN (0, 1)),
            discovered_files INTEGER NOT NULL DEFAULT 0,
            new_unique_images INTEGER NOT NULL DEFAULT 0,
            new_locations_existing_images INTEGER NOT NULL DEFAULT 0,
            unchanged_files INTEGER NOT NULL DEFAULT 0,
            changed_files INTEGER NOT NULL DEFAULT 0,
            missing_files_marked INTEGER NOT NULL DEFAULT 0,
            reused_analyses INTEGER NOT NULL DEFAULT 0,
            generated_analyses INTEGER NOT NULL DEFAULT 0,
            failed_analyses INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE images (
            id INTEGER PRIMARY KEY,
            content_sha256 TEXT NOT NULL UNIQUE,
            byte_size INTEGER NOT NULL,
            width INTEGER,
            height INTEGER,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE RESTRICT,
            path_key TEXT NOT NULL UNIQUE,
            absolute_path TEXT NOT NULL,
            input_root TEXT NOT NULL,
            input_root_key TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            modified_time_ns INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'present'
                CHECK(status IN (
                    'present',
                    'missing',
                    'quarantined',
                    'deleted'
                )),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_seen_run_id INTEGER
                REFERENCES import_runs(id) ON DELETE SET NULL
        );

        CREATE INDEX idx_files_image_id
            ON files(image_id);

        CREATE INDEX idx_files_input_root_status
            ON files(input_root_key, status);

        CREATE TABLE analysis_results (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE CASCADE,
            source_file_id INTEGER
                REFERENCES files(id) ON DELETE SET NULL,
            model_name TEXT NOT NULL,
            transformers_version TEXT NOT NULL,
            analysis_version INTEGER NOT NULL,
            include_triage INTEGER NOT NULL
                CHECK(include_triage IN (0, 1)),
            caption TEXT NOT NULL,
            detected_object_count INTEGER,
            object_labels TEXT NOT NULL DEFAULT '',
            person_count INTEGER,
            ocr_region_count INTEGER,
            ocr_character_count INTEGER,
            ocr_text TEXT NOT NULL DEFAULT '',
            likely_screenshot_or_ui TEXT NOT NULL,
            candidate_recommendation TEXT NOT NULL,
            recommendation_reason TEXT NOT NULL,
            triage_status TEXT NOT NULL,
            triage_error TEXT NOT NULL DEFAULT '',
            processing_seconds REAL NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('success', 'error')),
            error TEXT NOT NULL DEFAULT '',
            analyzed_at TEXT NOT NULL,
            UNIQUE(
                image_id,
                model_name,
                transformers_version,
                analysis_version,
                include_triage
            )
        );

        CREATE INDEX idx_analysis_image
            ON analysis_results(image_id);

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(normalized_name, category)
        );

        CREATE INDEX idx_tags_normalized_name
            ON tags(normalized_name);

        CREATE TABLE image_tags (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL
                REFERENCES tags(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            confidence REAL,
            review_status TEXT NOT NULL DEFAULT 'suggested'
                CHECK(review_status IN (
                    'suggested',
                    'confirmed',
                    'rejected'
                )),
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(image_id, tag_id, source)
        );

        CREATE INDEX idx_image_tags_image
            ON image_tags(image_id);

        CREATE INDEX idx_image_tags_tag_review
            ON image_tags(tag_id, review_status);

        CREATE TABLE image_review_state (
            image_id INTEGER PRIMARY KEY
                REFERENCES images(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'unreviewed'
                CHECK(status IN (
                    'unreviewed',
                    'keep',
                    'review',
                    'reject',
                    'quarantined'
                )),
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE file_actions (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL
                REFERENCES files(id) ON DELETE RESTRICT,
            action_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            target_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            performed_at TEXT NOT NULL
        );

        INSERT INTO catalog_metadata(key, value)
        VALUES
            ('application', '__CATALOG_APPLICATION_ID__'),
            ('created_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 1;

        COMMIT;
        """.replace("__CATALOG_APPLICATION_ID__", CATALOG_APPLICATION_ID)

        try:
            self.connection.executescript(schema_sql)
            self.connection.commit()

        except Exception:
            self.connection.rollback()
            raise

    def _migrate_1_to_2(self) -> None:
        """
        Add modular face-analysis and identity-reference storage.

        Version 2 deliberately stores face detections separately from Florence
        results.  Face models produce structured records (bounding boxes,
        landmarks, and embeddings) that need to be queried and reviewed at a
        finer level than one result row per image.

        The migration is additive: no version-1 tables or rows are rewritten.
        Existing catalogs therefore retain every image, file location, caption,
        tag, and import-run record.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE face_models (
            id INTEGER PRIMARY KEY,
            provider_key TEXT NOT NULL,
            provider_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_fingerprint TEXT NOT NULL,
            model_root TEXT NOT NULL DEFAULT '',
            embedding_dimension INTEGER NOT NULL,
            license_label TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            UNIQUE(provider_key, model_name, model_fingerprint)
        );

        CREATE TABLE face_analysis_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL
                CHECK(status IN ('running', 'complete', 'failed')),
            input_root TEXT NOT NULL,
            input_root_key TEXT NOT NULL,
            face_model_id INTEGER NOT NULL
                REFERENCES face_models(id) ON DELETE RESTRICT,
            identity_name TEXT NOT NULL DEFAULT '',
            similarity_threshold REAL NOT NULL,
            reuse_stored_analysis INTEGER NOT NULL
                CHECK(reuse_stored_analysis IN (0, 1)),
            execution_provider TEXT NOT NULL DEFAULT '',
            discovered_files INTEGER NOT NULL DEFAULT 0,
            generated_images INTEGER NOT NULL DEFAULT 0,
            reused_images INTEGER NOT NULL DEFAULT 0,
            failed_images INTEGER NOT NULL DEFAULT 0,
            faces_detected INTEGER NOT NULL DEFAULT 0,
            suggestions_created INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE face_image_results (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE CASCADE,
            source_file_id INTEGER
                REFERENCES files(id) ON DELETE SET NULL,
            face_model_id INTEGER NOT NULL
                REFERENCES face_models(id) ON DELETE RESTRICT,
            face_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL
                CHECK(status IN ('success', 'error')),
            error TEXT NOT NULL DEFAULT '',
            processing_seconds REAL NOT NULL DEFAULT 0,
            analyzed_at TEXT NOT NULL,
            UNIQUE(image_id, face_model_id)
        );

        CREATE INDEX idx_face_image_results_image
            ON face_image_results(image_id);

        CREATE TABLE face_detections (
            id INTEGER PRIMARY KEY,
            face_result_id INTEGER NOT NULL
                REFERENCES face_image_results(id) ON DELETE CASCADE,
            face_index INTEGER NOT NULL,
            bbox_x1 REAL NOT NULL,
            bbox_y1 REAL NOT NULL,
            bbox_x2 REAL NOT NULL,
            bbox_y2 REAL NOT NULL,
            detection_score REAL NOT NULL,
            landmarks_json TEXT NOT NULL DEFAULT '[]',
            embedding BLOB NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            embedding_norm REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(face_result_id, face_index)
        );

        CREATE INDEX idx_face_detections_result
            ON face_detections(face_result_id);

        CREATE TABLE identities (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE identity_profiles (
            id INTEGER PRIMARY KEY,
            identity_id INTEGER NOT NULL
                REFERENCES identities(id) ON DELETE CASCADE,
            face_model_id INTEGER NOT NULL
                REFERENCES face_models(id) ON DELETE RESTRICT,
            profile_embedding BLOB NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            reference_count INTEGER NOT NULL,
            reference_details_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(identity_id, face_model_id)
        );

        CREATE TABLE identity_matches (
            id INTEGER PRIMARY KEY,
            face_detection_id INTEGER NOT NULL
                REFERENCES face_detections(id) ON DELETE CASCADE,
            identity_profile_id INTEGER NOT NULL
                REFERENCES identity_profiles(id) ON DELETE CASCADE,
            similarity REAL NOT NULL,
            threshold REAL NOT NULL,
            is_suggested INTEGER NOT NULL
                CHECK(is_suggested IN (0, 1)),
            review_status TEXT NOT NULL DEFAULT 'suggested'
                CHECK(review_status IN (
                    'suggested',
                    'confirmed',
                    'rejected'
                )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(face_detection_id, identity_profile_id)
        );

        CREATE INDEX idx_identity_matches_profile_similarity
            ON identity_matches(identity_profile_id, similarity DESC);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_2_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 2;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


    def _migrate_2_to_3(self) -> None:
        """
        Add durable batch-edit history for safe undo.

        Milestone 7B is the first release that can modify many catalog images
        with one command.  A batch operation therefore stores a compact JSON
        snapshot of the user-owned metadata immediately before and after the
        transaction.  The application can restore the latest operation without
        touching provider analysis, source images, or unrelated catalog rows.

        This migration is additive.  Existing review decisions, manual tags,
        face matches, and provider results are unchanged.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE catalog_edit_operations (
            id INTEGER PRIMARY KEY,
            operation_type TEXT NOT NULL,
            description TEXT NOT NULL,
            affected_image_count INTEGER NOT NULL,
            before_state_json TEXT NOT NULL,
            after_state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            undone_at TEXT
        );

        CREATE INDEX idx_catalog_edit_operations_undo
            ON catalog_edit_operations(undone_at, id DESC);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_3_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 3;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate_3_to_4(self) -> None:
        """
        Extend catalog edit history from one-step batch undo to a real stack.

        Version 0.8.1 allows all selection edits—single-image or multi-image—to
        participate in undo and redo.  ``discarded_at`` distinguishes a valid
        redo branch from historical operations that were superseded by a new
        edit.  Existing already-undone v3 operations are marked discarded
        during migration because v3 did not preserve enough branch information
        to redo them safely.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        ALTER TABLE catalog_edit_operations
            ADD COLUMN discarded_at TEXT;

        UPDATE catalog_edit_operations
        SET discarded_at = COALESCE(undone_at, CURRENT_TIMESTAMP)
        WHERE undone_at IS NOT NULL;

        CREATE INDEX idx_catalog_edit_operations_history
            ON catalog_edit_operations(discarded_at, undone_at, id DESC);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_4_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 4;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate_4_to_5(self) -> None:
        """
        Separate provider-generated tags from the user's curation layer.

        Florence captions remain untouched in ``analysis_results``. Structured
        object labels are materialized as provider-owned suggestions tied to
        the exact analysis row that produced them. User exclusions live in a
        separate table, so rerunning or replacing an analysis cannot silently
        erase a deliberate curation decision.

        Existing manual keywords, identity decisions, and edit history remain
        unchanged. The migration also backfills tag suggestions from every
        successful analysis already present in the catalog.
        """
        try:
            self.connection.executescript(
                """
                BEGIN IMMEDIATE;

                CREATE TABLE analysis_tag_suggestions (
                    id INTEGER PRIMARY KEY,
                    analysis_result_id INTEGER NOT NULL
                        REFERENCES analysis_results(id) ON DELETE CASCADE,
                    image_id INTEGER NOT NULL
                        REFERENCES images(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL
                        REFERENCES tags(id) ON DELETE CASCADE,
                    provider_source TEXT NOT NULL,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(analysis_result_id, tag_id, provider_source)
                );

                CREATE INDEX idx_analysis_tag_suggestions_image
                    ON analysis_tag_suggestions(image_id, analysis_result_id);

                CREATE INDEX idx_analysis_tag_suggestions_tag
                    ON analysis_tag_suggestions(tag_id, image_id);

                CREATE TABLE image_tag_exclusions (
                    image_id INTEGER NOT NULL
                        REFERENCES images(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL
                        REFERENCES tags(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(image_id, tag_id)
                );

                CREATE INDEX idx_image_tag_exclusions_tag
                    ON image_tag_exclusions(tag_id, image_id);
                """
            )

            rows = self.connection.execute(
                """
                SELECT id, image_id, object_labels, analyzed_at
                FROM analysis_results
                WHERE status = 'success'
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                self._sync_analysis_object_tags(
                    analysis_result_id=int(row["id"]),
                    image_id=int(row["image_id"]),
                    object_labels=str(row["object_labels"] or ""),
                    timestamp=str(row["analyzed_at"] or utc_now_text()),
                )

            self.connection.execute(
                """
                INSERT OR REPLACE INTO catalog_metadata(key, value)
                VALUES ('schema_5_migrated_at', CURRENT_TIMESTAMP)
                """
            )
            self._set_schema_version(5)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


    def _migrate_5_to_6(self) -> None:
        """
        Add non-destructive dataset-export audit history.

        Export runs are deliberately separate from ``catalog_edit_operations``.
        An export creates new files outside the catalog and therefore is not an
        undoable metadata edit. The history records profile choices, aggregate
        counts, and per-image outcomes so a partial or cancelled export remains
        understandable without changing review state, tags, provider output, or
        source-file locations.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE export_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL
                CHECK(status IN (
                    'running',
                    'complete',
                    'partial',
                    'cancelled',
                    'failed'
                )),
            destination_path TEXT NOT NULL,
            profile_key TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            copy_images INTEGER NOT NULL
                CHECK(copy_images IN (0, 1)),
            create_sidecars INTEGER NOT NULL
                CHECK(create_sidecars IN (0, 1)),
            create_manifest INTEGER NOT NULL
                CHECK(create_manifest IN (0, 1)),
            collision_policy TEXT NOT NULL
                CHECK(collision_policy IN ('rename', 'skip')),
            requested_image_count INTEGER NOT NULL,
            exported_image_count INTEGER NOT NULL DEFAULT 0,
            skipped_image_count INTEGER NOT NULL DEFAULT 0,
            failed_image_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX idx_export_runs_started_at
            ON export_runs(started_at DESC, id DESC);

        CREATE TABLE export_run_items (
            id INTEGER PRIMARY KEY,
            export_run_id INTEGER NOT NULL
                REFERENCES export_runs(id) ON DELETE CASCADE,
            image_id INTEGER
                REFERENCES images(id) ON DELETE SET NULL,
            source_path TEXT NOT NULL DEFAULT '',
            exported_image_path TEXT NOT NULL DEFAULT '',
            sidecar_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL
                CHECK(status IN ('exported', 'skipped', 'error')),
            error_message TEXT NOT NULL DEFAULT '',
            UNIQUE(export_run_id, image_id)
        );

        CREATE INDEX idx_export_run_items_run
            ON export_run_items(export_run_id, id);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_6_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 6;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


    def _migrate_6_to_7(self) -> None:
        """Add explicitly named, catalog-local saved searches.

        Saved searches contain only query text and are created solely through a
        deliberate Save Search action.  Automatic search history remains an
        optional application preference rather than catalog data.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE saved_searches (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            query_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_saved_searches_name
            ON saved_searches(name COLLATE NOCASE);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_7_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 7;

        COMMIT;
        """
        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


    def _migrate_7_to_8(self) -> None:
        """Add replaceable, per-image local quality-analysis results.

        Quality measurements are durable because recalculating them requires
        reopening and decoding every source image.  They remain separate from
        manual review and provider captions: a future algorithm can replace a
        result without rewriting user decisions or source files.

        The image table already stores a unique SHA-256 content identity.
        Exact copies therefore remain one image record with several file
        locations, while ``perceptual_hash`` supports advisory near-duplicate
        comparisons between different image records.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE image_quality_results (
            image_id INTEGER PRIMARY KEY
                REFERENCES images(id) ON DELETE CASCADE,
            source_file_id INTEGER
                REFERENCES files(id) ON DELETE SET NULL,
            algorithm_version INTEGER NOT NULL,
            sharpness_score REAL,
            perceptual_hash TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL
                CHECK(status IN ('success', 'error')),
            error TEXT NOT NULL DEFAULT '',
            analyzed_at TEXT NOT NULL
        );

        CREATE INDEX idx_image_quality_status
            ON image_quality_results(status, algorithm_version);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_8_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 8;

        COMMIT;
        """
        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate_8_to_9(self) -> None:
        """Add deliberately saved, catalog-local image sets.

        A named set is persistent because the user explicitly creates it. The
        browser's incidental current selection and the readiness page's active
        scope remain session state. Memberships reference catalog images rather
        than file paths, so a moved file does not silently fall out of a set.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE image_sets (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE image_set_members (
            image_set_id INTEGER NOT NULL
                REFERENCES image_sets(id) ON DELETE CASCADE,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE CASCADE,
            added_at TEXT NOT NULL,
            PRIMARY KEY(image_set_id, image_id)
        );

        CREATE INDEX idx_image_set_members_image
            ON image_set_members(image_id, image_set_id);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_9_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 9;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate_9_to_10(self) -> None:
        """Repair the v0.19 preview-cache feedback loop.

        Version 0.19 stored WebP previews beside the catalog. When that folder
        was also the selected image source, the recursive provider scan could
        catalog those previews and the browser could then make previews of the
        previews. This migration removes only file records matching the exact
        LoRA Image Curator cache signature, followed by image rows that no longer
        have any real file location. It never deletes files from disk.

        Runs left ``running`` by a forced close are also finalized so the
        catalog does not permanently claim that abandoned work is active.
        """
        cache_rows = self.connection.execute(
            "SELECT id, relative_path FROM files"
        ).fetchall()
        cache_file_ids = [
            int(row["id"])
            for row in cache_rows
            if is_legacy_thumbnail_cache_path(str(row["relative_path"]))
        ]
        migrated_at = utc_now_text()

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if cache_file_ids:
                self.connection.executemany(
                    "DELETE FROM files WHERE id = ?",
                    ((file_id,) for file_id in cache_file_ids),
                )
                self.connection.execute(
                    """
                    DELETE FROM images
                    WHERE NOT EXISTS (
                        SELECT 1 FROM files WHERE files.image_id = images.id
                    )
                    """
                )

            self.connection.execute(
                """
                UPDATE import_runs
                SET status = 'failed',
                    completed_at = COALESCE(completed_at, ?),
                    error_message = CASE
                        WHEN error_message = ''
                        THEN 'Run interrupted before completion.'
                        ELSE error_message
                    END
                WHERE status = 'running'
                """,
                (migrated_at,),
            )
            self.connection.execute(
                """
                UPDATE face_analysis_runs
                SET status = 'failed',
                    completed_at = COALESCE(completed_at, ?),
                    error_message = CASE
                        WHEN error_message = ''
                        THEN 'Run interrupted before completion.'
                        ELSE error_message
                    END
                WHERE status = 'running'
                """,
                (migrated_at,),
            )
            self.connection.execute(
                """
                UPDATE export_runs
                SET status = 'cancelled',
                    completed_at = COALESCE(completed_at, ?),
                    error_message = CASE
                        WHEN error_message = ''
                        THEN 'Export interrupted before completion.'
                        ELSE error_message
                    END
                WHERE status = 'running'
                """,
                (migrated_at,),
            )
            self.connection.execute(
                """
                INSERT OR REPLACE INTO catalog_metadata(key, value)
                VALUES ('legacy_thumbnail_records_removed', ?)
                """,
                (str(len(cache_file_ids)),),
            )
            self.connection.execute(
                """
                INSERT OR REPLACE INTO catalog_metadata(key, value)
                VALUES ('schema_10_migrated_at', ?)
                """,
                (migrated_at,),
            )
            self._set_schema_version(10)
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def _migrate_10_to_11(self) -> None:
        """Add provider-neutral cached body/pose evidence.

        The migration stores model provenance and normalized results without
        changing any existing image, file, face, quality, selection, or export
        record.  Body analysis is optional; existing catalogs therefore gain
        empty tables and remain immediately usable when MediaPipe is absent.
        """
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE IF NOT EXISTS body_models (
            id INTEGER PRIMARY KEY,
            provider_key TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            provider_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_path TEXT NOT NULL,
            model_sha256 TEXT NOT NULL,
            landmark_layout TEXT NOT NULL,
            license_label TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            UNIQUE(provider_key, model_sha256)
        );

        CREATE TABLE IF NOT EXISTS body_image_results (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL
                REFERENCES images(id) ON DELETE CASCADE,
            source_file_id INTEGER
                REFERENCES files(id) ON DELETE SET NULL,
            body_model_id INTEGER NOT NULL
                REFERENCES body_models(id) ON DELETE RESTRICT,
            pose_count INTEGER NOT NULL DEFAULT 0,
            body_detected INTEGER NOT NULL
                CHECK(body_detected IN (0, 1)),
            face_visible INTEGER NOT NULL
                CHECK(face_visible IN (0, 1)),
            full_body_score REAL NOT NULL,
            full_body INTEGER NOT NULL
                CHECK(full_body IN (0, 1)),
            classification TEXT NOT NULL,
            landmarks_json TEXT NOT NULL DEFAULT '[]',
            detection_threshold REAL NOT NULL,
            visibility_threshold REAL NOT NULL,
            full_body_threshold_percent INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('success', 'error')),
            error TEXT NOT NULL DEFAULT '',
            processing_seconds REAL NOT NULL DEFAULT 0,
            analyzed_at TEXT NOT NULL,
            UNIQUE(
                image_id,
                body_model_id,
                detection_threshold,
                visibility_threshold,
                full_body_threshold_percent
            )
        );

        CREATE INDEX IF NOT EXISTS idx_body_image_results_image
            ON body_image_results(image_id, analyzed_at DESC);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_11_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 11;

        COMMIT;
        """

        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def _migrate_11_to_12(self) -> None:
        """Store optional source-video metadata against exact file locations."""
        migration_sql = """
        BEGIN IMMEDIATE;

        CREATE TABLE IF NOT EXISTS file_video_origins (
            file_id INTEGER PRIMARY KEY
                REFERENCES files(id) ON DELETE CASCADE,
            source_video TEXT NOT NULL,
            sampling_mode TEXT NOT NULL,
            timestamp_seconds REAL,
            frame_number INTEGER,
            interval_seconds REAL,
            recorded_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_video_origins_source_time
            ON file_video_origins(source_video, timestamp_seconds);

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_12_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 12;

        COMMIT;
        """
        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def _migrate_12_to_13(self) -> None:
        """Retain Florence OCR rectangles for spatial text-overlay review."""
        migration_sql = """
        BEGIN IMMEDIATE;

        ALTER TABLE analysis_results
        ADD COLUMN ocr_regions_json TEXT NOT NULL DEFAULT '[]';

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_13_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 13;

        COMMIT;
        """
        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def _migrate_13_to_14(self) -> None:
        """Cache obvious local overlay/bar candidates with quality results."""
        migration_sql = """
        BEGIN IMMEDIATE;

        ALTER TABLE image_quality_results
        ADD COLUMN overlay_regions_json TEXT NOT NULL DEFAULT '[]';

        INSERT OR REPLACE INTO catalog_metadata(key, value)
        VALUES ('schema_14_migrated_at', CURRENT_TIMESTAMP);

        PRAGMA user_version = 14;

        COMMIT;
        """
        try:
            self.connection.executescript(migration_sql)
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise


    # =========================================================================
    # Import-run tracking
    # =========================================================================

    def start_import_run(
        self,
        *,
        input_root: Path,
        output_folder: Path,
        model_name: str,
        transformers_version: str,
        analysis_version: int,
        include_triage: bool,
        reuse_stored_analysis: bool,
    ) -> int:
        """Create a run record and return its database ID."""
        now = utc_now_text()

        cursor = self.connection.execute(
            """
            INSERT INTO import_runs (
                started_at,
                status,
                input_root,
                input_root_key,
                output_folder,
                model_name,
                transformers_version,
                analysis_version,
                include_triage,
                reuse_stored_analysis
            )
            VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                str(input_root),
                normalize_path_key(input_root),
                str(output_folder),
                model_name,
                transformers_version,
                analysis_version,
                int(include_triage),
                int(reuse_stored_analysis),
            ),
        )
        self.connection.commit()

        return int(cursor.lastrowid)

    def finish_import_run(
        self,
        run_id: int,
        *,
        status: str,
        counts: ImportRunCounts,
        error_message: str = "",
    ) -> None:
        """Finalize an import-run record."""
        if status not in {"complete", "failed"}:
            raise ValueError(
                f"Invalid final run status: {status}"
            )

        self.connection.execute(
            """
            UPDATE import_runs
            SET
                completed_at = ?,
                status = ?,
                discovered_files = ?,
                new_unique_images = ?,
                new_locations_existing_images = ?,
                unchanged_files = ?,
                changed_files = ?,
                missing_files_marked = ?,
                reused_analyses = ?,
                generated_analyses = ?,
                failed_analyses = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                utc_now_text(),
                status,
                counts.discovered_files,
                counts.new_unique_images,
                counts.new_locations_existing_images,
                counts.unchanged_files,
                counts.changed_files,
                counts.missing_files_marked,
                counts.reused_analyses,
                counts.generated_analyses,
                counts.failed_analyses,
                error_message,
                run_id,
            ),
        )
        self.connection.commit()

    # =========================================================================
    # File and image registration
    # =========================================================================

    def register_file(
        self,
        *,
        file_path: Path,
        input_root: Path,
        run_id: int,
    ) -> FileRegistration:
        """
        Insert or update one file and return its catalog identity.

        Fast path
        ---------
        When path, byte size, and nanosecond modification time are unchanged,
        the existing content hash is reused without reading the full file.

        Changed path
        ------------
        If size or modification time changed, the file is hashed again. The new
        hash may point to:

        - a brand-new unique image, or
        - content that already exists elsewhere in the catalog.
        """
        resolved_file = file_path.expanduser().resolve()
        resolved_root = input_root.expanduser().resolve()

        stat_result = resolved_file.stat()
        byte_size = int(stat_result.st_size)
        modified_time_ns = int(stat_result.st_mtime_ns)

        path_key = normalize_path_key(resolved_file)
        input_root_key = normalize_path_key(resolved_root)
        relative_path = str(resolved_file.relative_to(resolved_root))
        now = utc_now_text()

        existing_file = self.connection.execute(
            """
            SELECT
                f.id AS file_id,
                f.image_id,
                f.byte_size,
                f.modified_time_ns,
                i.content_sha256
            FROM files AS f
            JOIN images AS i
                ON i.id = f.image_id
            WHERE f.path_key = ?
            """,
            (path_key,),
        ).fetchone()

        if (
            existing_file is not None
            and int(existing_file["byte_size"]) == byte_size
            and int(existing_file["modified_time_ns"]) == modified_time_ns
        ):
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE files
                    SET
                        absolute_path = ?,
                        input_root = ?,
                        input_root_key = ?,
                        relative_path = ?,
                        status = 'present',
                        last_seen_at = ?,
                        last_seen_run_id = ?
                    WHERE id = ?
                    """,
                    (
                        str(resolved_file),
                        str(resolved_root),
                        input_root_key,
                        relative_path,
                        now,
                        run_id,
                        int(existing_file["file_id"]),
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE images
                    SET last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        int(existing_file["image_id"]),
                    ),
                )

            return FileRegistration(
                image_id=int(existing_file["image_id"]),
                file_id=int(existing_file["file_id"]),
                content_sha256=str(existing_file["content_sha256"]),
                action="unchanged_file",
            )

        content_sha256 = calculate_sha256(resolved_file)

        existing_image = self.connection.execute(
            """
            SELECT id
            FROM images
            WHERE content_sha256 = ?
            """,
            (content_sha256,),
        ).fetchone()

        with self.connection:
            if existing_image is None:
                image_cursor = self.connection.execute(
                    """
                    INSERT INTO images (
                        content_sha256,
                        byte_size,
                        first_seen_at,
                        last_seen_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        content_sha256,
                        byte_size,
                        now,
                        now,
                    ),
                )
                image_id = int(image_cursor.lastrowid)
                content_was_new = True

            else:
                image_id = int(existing_image["id"])
                content_was_new = False

                self.connection.execute(
                    """
                    UPDATE images
                    SET last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        image_id,
                    ),
                )

            if existing_file is None:
                file_cursor = self.connection.execute(
                    """
                    INSERT INTO files (
                        image_id,
                        path_key,
                        absolute_path,
                        input_root,
                        input_root_key,
                        relative_path,
                        byte_size,
                        modified_time_ns,
                        status,
                        first_seen_at,
                        last_seen_at,
                        last_seen_run_id
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, ?
                    )
                    """,
                    (
                        image_id,
                        path_key,
                        str(resolved_file),
                        str(resolved_root),
                        input_root_key,
                        relative_path,
                        byte_size,
                        modified_time_ns,
                        now,
                        now,
                        run_id,
                    ),
                )
                file_id = int(file_cursor.lastrowid)

                action = (
                    "new_image"
                    if content_was_new
                    else "new_location_existing_image"
                )

            else:
                file_id = int(existing_file["file_id"])
                previous_image_id = int(existing_file["image_id"])

                self.connection.execute(
                    """
                    UPDATE files
                    SET
                        image_id = ?,
                        absolute_path = ?,
                        input_root = ?,
                        input_root_key = ?,
                        relative_path = ?,
                        byte_size = ?,
                        modified_time_ns = ?,
                        status = 'present',
                        last_seen_at = ?,
                        last_seen_run_id = ?
                    WHERE id = ?
                    """,
                    (
                        image_id,
                        str(resolved_file),
                        str(resolved_root),
                        input_root_key,
                        relative_path,
                        byte_size,
                        modified_time_ns,
                        now,
                        run_id,
                        file_id,
                    ),
                )

                action = (
                    "changed_file_content"
                    if previous_image_id != image_id
                    else "changed_file_metadata"
                )

        return FileRegistration(
            image_id=image_id,
            file_id=file_id,
            content_sha256=content_sha256,
            action=action,
        )

    def update_image_dimensions(
        self,
        image_id: int,
        width: int | None,
        height: int | None,
    ) -> None:
        """
        Save dimensions when analysis successfully opens the image.

        Existing non-null dimensions are retained when a later operation has no
        value to provide.
        """
        self.connection.execute(
            """
            UPDATE images
            SET
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                width,
                height,
                utc_now_text(),
                image_id,
            ),
        )
        self.connection.commit()

    def mark_unseen_files_missing(
        self,
        *,
        input_root: Path,
        run_id: int,
    ) -> int:
        """
        Mark files formerly cataloged under this exact input root as missing.

        This changes only database state. It never deletes or moves a file.
        """
        cursor = self.connection.execute(
            """
            UPDATE files
            SET status = 'missing'
            WHERE
                input_root_key = ?
                AND status = 'present'
                AND (
                    last_seen_run_id IS NULL
                    OR last_seen_run_id != ?
                )
            """,
            (
                normalize_path_key(input_root),
                run_id,
            ),
        )
        self.connection.commit()

        return int(cursor.rowcount)

    # =========================================================================
    # Analysis-result storage and reuse
    # =========================================================================

    def get_reusable_analysis(
        self,
        *,
        image_id: int,
        model_name: str,
        transformers_version: str,
        analysis_version: int,
        requested_triage: bool,
    ) -> sqlite3.Row | None:
        """
        Return a compatible successful analysis result, when one exists.

        A triage-enabled result contains everything required by caption-only
        mode, so it may be reused for either request. A caption-only result
        cannot satisfy a request for object detection and OCR.
        """
        return self.connection.execute(
            """
            SELECT
                ar.*,
                i.width AS stored_width,
                i.height AS stored_height
            FROM analysis_results AS ar
            JOIN images AS i
                ON i.id = ar.image_id
            WHERE
                ar.image_id = ?
                AND ar.model_name = ?
                AND ar.transformers_version = ?
                AND ar.analysis_version = ?
                AND ar.status = 'success'
                AND (
                    ar.include_triage = 1
                    OR ? = 0
                )
            ORDER BY
                ar.include_triage DESC,
                ar.analyzed_at DESC
            LIMIT 1
            """,
            (
                image_id,
                model_name,
                transformers_version,
                analysis_version,
                int(requested_triage),
            ),
        ).fetchone()

    def store_successful_analysis(
        self,
        *,
        image_id: int,
        source_file_id: int,
        model_name: str,
        transformers_version: str,
        analysis_version: int,
        include_triage: bool,
        result: Mapping[str, Any],
    ) -> None:
        """
        Insert or update one successful versioned analysis result.

        Failed attempts do not overwrite a previously successful result.
        """
        self.connection.execute(
            """
            INSERT INTO analysis_results (
                image_id,
                source_file_id,
                model_name,
                transformers_version,
                analysis_version,
                include_triage,
                caption,
                detected_object_count,
                object_labels,
                person_count,
                ocr_region_count,
                ocr_character_count,
                ocr_text,
                ocr_regions_json,
                likely_screenshot_or_ui,
                candidate_recommendation,
                recommendation_reason,
                triage_status,
                triage_error,
                processing_seconds,
                status,
                error,
                analyzed_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'success', '', ?
            )
            ON CONFLICT (
                image_id,
                model_name,
                transformers_version,
                analysis_version,
                include_triage
            )
            DO UPDATE SET
                source_file_id = excluded.source_file_id,
                caption = excluded.caption,
                detected_object_count = excluded.detected_object_count,
                object_labels = excluded.object_labels,
                person_count = excluded.person_count,
                ocr_region_count = excluded.ocr_region_count,
                ocr_character_count = excluded.ocr_character_count,
                ocr_text = excluded.ocr_text,
                ocr_regions_json = excluded.ocr_regions_json,
                likely_screenshot_or_ui = excluded.likely_screenshot_or_ui,
                candidate_recommendation = excluded.candidate_recommendation,
                recommendation_reason = excluded.recommendation_reason,
                triage_status = excluded.triage_status,
                triage_error = excluded.triage_error,
                processing_seconds = excluded.processing_seconds,
                status = 'success',
                error = '',
                analyzed_at = excluded.analyzed_at
            """,
            (
                image_id,
                source_file_id,
                model_name,
                transformers_version,
                analysis_version,
                int(include_triage),
                str(result["caption"]),
                result["detected_object_count"],
                str(result["object_labels"]),
                result["person_count"],
                result["ocr_region_count"],
                result["ocr_character_count"],
                str(result["ocr_text"]),
                str(result.get("ocr_regions_json", "[]")),
                str(result["likely_screenshot_or_ui"]),
                str(result["candidate_recommendation"]),
                str(result["recommendation_reason"]),
                str(result["triage_status"]),
                str(result["triage_error"]),
                float(result["processing_seconds"]),
                utc_now_text(),
            ),
        )

        analysis_row = self.connection.execute(
            """
            SELECT id
            FROM analysis_results
            WHERE image_id = ?
              AND model_name = ?
              AND transformers_version = ?
              AND analysis_version = ?
              AND include_triage = ?
            """,
            (
                image_id,
                model_name,
                transformers_version,
                analysis_version,
                int(include_triage),
            ),
        ).fetchone()
        if analysis_row is None:
            raise RuntimeError("The stored analysis result could not be retrieved")

        self._sync_analysis_object_tags(
            analysis_result_id=int(analysis_row["id"]),
            image_id=image_id,
            object_labels=str(result["object_labels"]),
            timestamp=utc_now_text(),
        )
        self.connection.commit()

    def _sync_analysis_object_tags(
        self,
        *,
        analysis_result_id: int,
        image_id: int,
        object_labels: str,
        timestamp: str,
    ) -> None:
        """
        Replace the derived object-tag suggestions for one analysis result.

        The raw ``object_labels`` string remains the provider's authoritative
        output. This method creates normalized, deduplicated rows that the GUI
        can search and curate without ever rewriting that raw result.
        """
        labels: list[str] = []
        seen: set[str] = set()
        for raw_label in object_labels.split("|"):
            label = " ".join(raw_label.split()).strip()
            normalized = label.casefold()
            if not label or normalized in seen:
                continue
            seen.add(normalized)
            labels.append(label)

        self.connection.execute(
            "DELETE FROM analysis_tag_suggestions WHERE analysis_result_id = ?",
            (analysis_result_id,),
        )

        for label in labels:
            normalized = label.casefold()
            self.connection.execute(
                """
                INSERT INTO tags(name, normalized_name, category, created_at)
                VALUES (?, ?, 'ai_object', ?)
                ON CONFLICT(normalized_name, category) DO NOTHING
                """,
                (label, normalized, timestamp),
            )
            tag_row = self.connection.execute(
                """
                SELECT id
                FROM tags
                WHERE normalized_name = ? AND category = 'ai_object'
                """,
                (normalized,),
            ).fetchone()
            if tag_row is None:
                raise RuntimeError(f"AI tag could not be created: {label}")

            self.connection.execute(
                """
                INSERT INTO analysis_tag_suggestions(
                    analysis_result_id,
                    image_id,
                    tag_id,
                    provider_source,
                    confidence,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, 'florence:object_detection', NULL, ?, ?)
                """,
                (
                    analysis_result_id,
                    image_id,
                    int(tag_row["id"]),
                    timestamp,
                    timestamp,
                ),
            )

    # =========================================================================
    # Modular face-analysis and identity-reference storage
    # =========================================================================

    def get_file_record(self, file_path: Path) -> sqlite3.Row | None:
        """Return the catalog image/file IDs associated with one path."""
        return self.connection.execute(
            """
            SELECT
                f.id AS file_id,
                f.image_id,
                f.absolute_path,
                f.relative_path,
                i.content_sha256
            FROM files AS f
            JOIN images AS i
                ON i.id = f.image_id
            WHERE f.path_key = ?
            """,
            (normalize_path_key(file_path),),
        ).fetchone()

    def store_file_video_origin(
        self,
        *,
        file_id: int,
        source_video: str,
        sampling_mode: str,
        timestamp_seconds: float | None,
        frame_number: int | None,
        interval_seconds: float | None,
    ) -> None:
        """Associate one registered frame with its source clip and position."""
        self.connection.execute(
            """
            INSERT INTO file_video_origins (
                file_id, source_video, sampling_mode, timestamp_seconds,
                frame_number, interval_seconds, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id)
            DO UPDATE SET
                source_video = excluded.source_video,
                sampling_mode = excluded.sampling_mode,
                timestamp_seconds = excluded.timestamp_seconds,
                frame_number = excluded.frame_number,
                interval_seconds = excluded.interval_seconds,
                recorded_at = excluded.recorded_at
            """,
            (
                int(file_id),
                str(source_video),
                str(sampling_mode),
                (
                    float(timestamp_seconds)
                    if timestamp_seconds is not None
                    else None
                ),
                int(frame_number) if frame_number is not None else None,
                (
                    float(interval_seconds)
                    if interval_seconds is not None
                    else None
                ),
                utc_now_text(),
            ),
        )
        self.connection.commit()

    def register_face_model(
        self,
        *,
        provider_key: str,
        provider_version: str,
        model_name: str,
        model_fingerprint: str,
        model_root: str,
        embedding_dimension: int,
        license_label: str,
    ) -> int:
        """Register the exact face-model configuration used for inference."""
        now = utc_now_text()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO face_models (
                    provider_key,
                    provider_version,
                    model_name,
                    model_fingerprint,
                    model_root,
                    embedding_dimension,
                    license_label,
                    first_seen_at,
                    last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_key, model_name, model_fingerprint)
                DO UPDATE SET
                    provider_version = excluded.provider_version,
                    model_root = excluded.model_root,
                    embedding_dimension = excluded.embedding_dimension,
                    license_label = excluded.license_label,
                    last_used_at = excluded.last_used_at
                """,
                (
                    provider_key,
                    provider_version,
                    model_name,
                    model_fingerprint,
                    model_root,
                    embedding_dimension,
                    license_label,
                    now,
                    now,
                ),
            )

        row = self.connection.execute(
            """
            SELECT id
            FROM face_models
            WHERE
                provider_key = ?
                AND model_name = ?
                AND model_fingerprint = ?
            """,
            (provider_key, model_name, model_fingerprint),
        ).fetchone()

        if row is None:
            raise RuntimeError("The face model could not be registered.")

        return int(row["id"])

    def start_face_analysis_run(
        self,
        *,
        input_root: Path,
        face_model_id: int,
        identity_name: str,
        similarity_threshold: float,
        reuse_stored_analysis: bool,
        execution_provider: str,
        discovered_files: int,
    ) -> int:
        """Create an auditable record for one face-analysis pass."""
        cursor = self.connection.execute(
            """
            INSERT INTO face_analysis_runs (
                started_at,
                status,
                input_root,
                input_root_key,
                face_model_id,
                identity_name,
                similarity_threshold,
                reuse_stored_analysis,
                execution_provider,
                discovered_files
            )
            VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                str(input_root.expanduser().resolve()),
                normalize_path_key(input_root),
                face_model_id,
                identity_name,
                float(similarity_threshold),
                int(reuse_stored_analysis),
                execution_provider,
                int(discovered_files),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_face_analysis_run(
        self,
        run_id: int,
        *,
        status: str,
        generated_images: int,
        reused_images: int,
        failed_images: int,
        faces_detected: int,
        suggestions_created: int,
        error_message: str = "",
    ) -> None:
        """Finish a face-analysis run with counts useful for diagnostics."""
        if status not in {"complete", "failed"}:
            raise ValueError(f"Invalid face-analysis status: {status}")

        self.connection.execute(
            """
            UPDATE face_analysis_runs
            SET
                completed_at = ?,
                status = ?,
                generated_images = ?,
                reused_images = ?,
                failed_images = ?,
                faces_detected = ?,
                suggestions_created = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                utc_now_text(),
                status,
                int(generated_images),
                int(reused_images),
                int(failed_images),
                int(faces_detected),
                int(suggestions_created),
                error_message,
                run_id,
            ),
        )
        self.connection.commit()

    def get_reusable_face_result(
        self,
        *,
        image_id: int,
        face_model_id: int,
    ) -> sqlite3.Row | None:
        """Return a successful stored face result for an exact model."""
        return self.connection.execute(
            """
            SELECT *
            FROM face_image_results
            WHERE
                image_id = ?
                AND face_model_id = ?
                AND status = 'success'
            """,
            (image_id, face_model_id),
        ).fetchone()

    def get_face_detections(
        self,
        face_result_id: int,
    ) -> list[sqlite3.Row]:
        """Return stored faces in stable face-index order."""
        return list(
            self.connection.execute(
                """
                SELECT *
                FROM face_detections
                WHERE face_result_id = ?
                ORDER BY face_index
                """,
                (face_result_id,),
            ).fetchall()
        )

    def store_face_result(
        self,
        *,
        image_id: int,
        source_file_id: int,
        face_model_id: int,
        status: str,
        error: str,
        processing_seconds: float,
        detections: list[Mapping[str, Any]],
    ) -> tuple[int, list[int]]:
        """
        Replace the stored face result for one image/model pair atomically.

        Replacing child rows rather than trying to match faces across runs keeps
        the data deterministic.  Any unreviewed identity matches attached to an
        older detection disappear through foreign-key cascade.
        """
        if status not in {"success", "error"}:
            raise ValueError(f"Invalid face result status: {status}")

        now = utc_now_text()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO face_image_results (
                    image_id,
                    source_file_id,
                    face_model_id,
                    face_count,
                    status,
                    error,
                    processing_seconds,
                    analyzed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id, face_model_id)
                DO UPDATE SET
                    source_file_id = excluded.source_file_id,
                    face_count = excluded.face_count,
                    status = excluded.status,
                    error = excluded.error,
                    processing_seconds = excluded.processing_seconds,
                    analyzed_at = excluded.analyzed_at
                """,
                (
                    image_id,
                    source_file_id,
                    face_model_id,
                    len(detections),
                    status,
                    error,
                    float(processing_seconds),
                    now,
                ),
            )

            row = self.connection.execute(
                """
                SELECT id
                FROM face_image_results
                WHERE image_id = ? AND face_model_id = ?
                """,
                (image_id, face_model_id),
            ).fetchone()

            if row is None:
                raise RuntimeError("The face result could not be stored.")

            face_result_id = int(row["id"])

            self.connection.execute(
                "DELETE FROM face_detections WHERE face_result_id = ?",
                (face_result_id,),
            )

            detection_ids: list[int] = []

            for detection in detections:
                cursor = self.connection.execute(
                    """
                    INSERT INTO face_detections (
                        face_result_id,
                        face_index,
                        bbox_x1,
                        bbox_y1,
                        bbox_x2,
                        bbox_y2,
                        detection_score,
                        landmarks_json,
                        embedding,
                        embedding_dimension,
                        embedding_norm,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        face_result_id,
                        int(detection["face_index"]),
                        float(detection["bbox_x1"]),
                        float(detection["bbox_y1"]),
                        float(detection["bbox_x2"]),
                        float(detection["bbox_y2"]),
                        float(detection["detection_score"]),
                        str(detection.get("landmarks_json", "[]")),
                        sqlite3.Binary(bytes(detection["embedding"])),
                        int(detection["embedding_dimension"]),
                        float(detection["embedding_norm"]),
                        now,
                    ),
                )
                detection_ids.append(int(cursor.lastrowid))

        return face_result_id, detection_ids

    def get_or_create_identity(
        self,
        name: str,
        notes: str = "",
    ) -> int:
        """Return a stable identity ID for an arbitrary user-supplied name."""
        cleaned_name = " ".join(name.split())

        if not cleaned_name:
            raise ValueError("An identity name cannot be empty.")

        normalized_name = cleaned_name.casefold()
        now = utc_now_text()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO identities (
                    name, normalized_name, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(normalized_name)
                DO UPDATE SET
                    name = excluded.name,
                    notes = CASE
                        WHEN excluded.notes != '' THEN excluded.notes
                        ELSE identities.notes
                    END,
                    updated_at = excluded.updated_at
                """,
                (cleaned_name, normalized_name, notes, now, now),
            )

        row = self.connection.execute(
            "SELECT id FROM identities WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()

        if row is None:
            raise RuntimeError("The identity could not be stored.")

        return int(row["id"])

    def upsert_identity_profile(
        self,
        *,
        identity_id: int,
        face_model_id: int,
        profile_embedding: bytes,
        embedding_dimension: int,
        reference_count: int,
        reference_details_json: str,
    ) -> int:
        """Store the normalized mean embedding for one identity/model pair."""
        now = utc_now_text()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO identity_profiles (
                    identity_id,
                    face_model_id,
                    profile_embedding,
                    embedding_dimension,
                    reference_count,
                    reference_details_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_id, face_model_id)
                DO UPDATE SET
                    profile_embedding = excluded.profile_embedding,
                    embedding_dimension = excluded.embedding_dimension,
                    reference_count = excluded.reference_count,
                    reference_details_json = excluded.reference_details_json,
                    updated_at = excluded.updated_at
                """,
                (
                    identity_id,
                    face_model_id,
                    sqlite3.Binary(profile_embedding),
                    int(embedding_dimension),
                    int(reference_count),
                    reference_details_json,
                    now,
                    now,
                ),
            )

        row = self.connection.execute(
            """
            SELECT id
            FROM identity_profiles
            WHERE identity_id = ? AND face_model_id = ?
            """,
            (identity_id, face_model_id),
        ).fetchone()

        if row is None:
            raise RuntimeError("The identity profile could not be stored.")

        return int(row["id"])

    def upsert_identity_match(
        self,
        *,
        face_detection_id: int,
        identity_profile_id: int,
        similarity: float,
        threshold: float,
        is_suggested: bool,
    ) -> None:
        """Store a face/profile comparison without overwriting user review."""
        now = utc_now_text()

        self.connection.execute(
            """
            INSERT INTO identity_matches (
                face_detection_id,
                identity_profile_id,
                similarity,
                threshold,
                is_suggested,
                review_status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'suggested', ?, ?)
            ON CONFLICT(face_detection_id, identity_profile_id)
            DO UPDATE SET
                similarity = excluded.similarity,
                threshold = excluded.threshold,
                is_suggested = excluded.is_suggested,
                updated_at = excluded.updated_at
            """,
            (
                face_detection_id,
                identity_profile_id,
                float(similarity),
                float(threshold),
                int(is_suggested),
                now,
                now,
            ),
        )
        self.connection.commit()

    def remove_suggested_tag_assignment(
        self,
        *,
        image_id: int,
        tag_id: int,
        source: str,
    ) -> None:
        """Remove only an unreviewed machine suggestion, never a user decision."""
        self.connection.execute(
            """
            DELETE FROM image_tags
            WHERE
                image_id = ?
                AND tag_id = ?
                AND source = ?
                AND review_status = 'suggested'
            """,
            (image_id, tag_id, source),
        )
        self.connection.commit()

    # =========================================================================
    # General-purpose tags prepared for future GUI releases
    # =========================================================================

    def get_or_create_tag(
        self,
        name: str,
        category: str = "",
    ) -> int:
        """
        Return a tag ID, creating the tag when necessary.

        This method is not yet exposed in the GUI, but the schema and API are
        ready for future manual tags, InsightFace suggestions, clothing tags,
        object tags, scenery tags, and arbitrary user-created labels.
        """
        cleaned_name = " ".join(name.split())

        if not cleaned_name:
            raise ValueError("A tag name cannot be empty.")

        cleaned_category = " ".join(category.split())
        normalized_name = cleaned_name.casefold()
        now = utc_now_text()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tags (
                    name,
                    normalized_name,
                    category,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(normalized_name, category)
                DO NOTHING
                """,
                (
                    cleaned_name,
                    normalized_name,
                    cleaned_category,
                    now,
                ),
            )

        row = self.connection.execute(
            """
            SELECT id
            FROM tags
            WHERE
                normalized_name = ?
                AND category = ?
            """,
            (
                normalized_name,
                cleaned_category,
            ),
        ).fetchone()

        if row is None:
            raise RuntimeError("The tag could not be created or retrieved.")

        return int(row["id"])

    def assign_tag(
        self,
        *,
        image_id: int,
        tag_id: int,
        source: str,
        confidence: float | None = None,
        review_status: str = "suggested",
        notes: str = "",
    ) -> None:
        """
        Add or update a tag assignment while preserving its source.

        A future InsightFace assignment and a manual assignment may coexist
        because the uniqueness rule includes ``source``.
        """
        if review_status not in {
            "suggested",
            "confirmed",
            "rejected",
        }:
            raise ValueError(
                f"Invalid tag review status: {review_status}"
            )

        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(
                "Tag confidence must be between 0 and 1."
            )

        cleaned_source = " ".join(source.split())

        if not cleaned_source:
            raise ValueError("A tag source cannot be empty.")

        now = utc_now_text()

        self.connection.execute(
            """
            INSERT INTO image_tags (
                image_id,
                tag_id,
                source,
                confidence,
                review_status,
                notes,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id, tag_id, source)
            DO UPDATE SET
                confidence = excluded.confidence,
                review_status = CASE
                    WHEN
                        image_tags.review_status IN ('confirmed', 'rejected')
                        AND excluded.review_status = 'suggested'
                    THEN image_tags.review_status
                    ELSE excluded.review_status
                END,
                notes = CASE
                    WHEN
                        image_tags.review_status IN ('confirmed', 'rejected')
                        AND excluded.review_status = 'suggested'
                    THEN image_tags.notes
                    ELSE excluded.notes
                END,
                updated_at = excluded.updated_at
            """,
            (
                image_id,
                tag_id,
                cleaned_source,
                confidence,
                review_status,
                notes,
                now,
                now,
            ),
        )
        self.connection.commit()

    # =========================================================================
    # Summary and troubleshooting helpers
    # =========================================================================

    def get_summary(self) -> CatalogSummary:
        """Return current catalog totals."""
        row = self.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM images) AS unique_images,
                (SELECT COUNT(*) FROM files) AS file_locations,
                (
                    SELECT COUNT(*)
                    FROM files
                    WHERE status = 'present'
                ) AS present_file_locations,
                (
                    SELECT COUNT(*)
                    FROM files
                    WHERE status = 'missing'
                ) AS missing_file_locations,
                (SELECT COUNT(*) FROM tags) AS defined_tags,
                (
                    (SELECT COUNT(*) FROM image_tags)
                    + (SELECT COUNT(*) FROM analysis_tag_suggestions)
                ) AS tag_assignments
            """
        ).fetchone()

        return CatalogSummary(
            unique_images=int(row["unique_images"]),
            file_locations=int(row["file_locations"]),
            present_file_locations=int(row["present_file_locations"]),
            missing_file_locations=int(row["missing_file_locations"]),
            defined_tags=int(row["defined_tags"]),
            tag_assignments=int(row["tag_assignments"]),
        )
