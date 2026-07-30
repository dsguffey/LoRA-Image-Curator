"""
catalog_edits.py

Transactional catalog-editing services for LoRA Image Curator.

The graphical interface delegates every database mutation to this module.  A
Tkinter event handler decides *what the user requested*; this service validates
that request, captures an undo snapshot, writes all requested changes in one
SQLite transaction, and reports exactly what changed.

The boundary is deliberate:

* Provider output remains provider output.
* User decisions remain visibly separate from AI suggestions.
* Re-running Florence or face analysis cannot erase manual work.
* Batch edits either commit completely or do not happen at all.
* Undo restores only user-owned review metadata; it never rewinds analysis or
  touches source image files.

Version 0.8.2 extends the durable selection-edit history to manual tags and
AI-tag exclusions. Single-image and multi-image changes use the same
transactional path, one multi-tag action remains one undo step, Ctrl+Z/Ctrl+Y
can walk several operations, and a new edit after undo safely discards the
obsolete redo branch. The history contains no user accounts or collaboration
metadata; it records only enough local state to reverse catalog operations.
"""

from __future__ import annotations

import json
import sqlite3

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable



VALID_REVIEW_STATES = {"unreviewed", "keep", "review", "reject", "quarantined"}
VALID_IDENTITY_REVIEW_STATES = {"suggested", "confirmed", "rejected"}
VALID_KEYWORD_ACTIONS = {"unchanged", "set", "clear"}
MANUAL_KEYWORD_CATEGORY = "set_keyword"
MANUAL_TAG_CATEGORY = "manual_tag"
AI_TAG_CATEGORY = "ai_object"
MANUAL_TAG_SOURCE = "manual"
HISTORY_LIMIT = 20


@dataclass(slots=True, frozen=True)
class BatchEditRequest:
    """Describe the fields a batch operation should deliberately overwrite."""

    keyword_action: str = "unchanged"
    keyword: str = ""
    review_status: str | None = None
    identity_status: str | None = None

    def normalized(self) -> "BatchEditRequest":
        """Return a validated, whitespace-normalized request."""
        action = self.keyword_action.strip().casefold()
        if action not in VALID_KEYWORD_ACTIONS:
            raise ValueError(f"Unsupported keyword action: {self.keyword_action}")

        clean_keyword = " ".join(self.keyword.split()).strip()
        if action == "set" and not clean_keyword:
            raise ValueError("A Trigger Keyword cannot be blank")

        review_status = self.review_status
        if review_status is not None and review_status not in VALID_REVIEW_STATES:
            raise ValueError(f"Unsupported review state: {review_status}")

        identity_status = self.identity_status
        if (
            identity_status is not None
            and identity_status not in VALID_IDENTITY_REVIEW_STATES
        ):
            raise ValueError(f"Unsupported identity review state: {identity_status}")

        if action == "unchanged" and review_status is None and identity_status is None:
            raise ValueError("Choose at least one batch change")

        return BatchEditRequest(
            keyword_action=action,
            keyword=clean_keyword,
            review_status=review_status,
            identity_status=identity_status,
        )


@dataclass(slots=True, frozen=True)
class BatchEditResult:
    """Summarize the committed effects of one batch transaction."""

    operation_id: int | None
    selected_count: int
    changed_image_count: int
    keyword_changed: int
    review_changed: int
    identity_changed: int
    identity_skipped_no_suggestion: int

    @property
    def changed_anything(self) -> bool:
        return self.operation_id is not None and self.changed_image_count > 0


@dataclass(slots=True, frozen=True)
class TagEditResult:
    """Summarize one committed manual-tag or AI-exclusion operation."""

    operation_id: int | None
    selected_count: int
    changed_image_count: int
    changed_assignment_count: int

    @property
    def changed_anything(self) -> bool:
        return self.operation_id is not None and self.changed_image_count > 0


@dataclass(slots=True, frozen=True)
class HistoryOperation:
    """Small UI-facing description of an undoable or redoable edit."""

    operation_id: int
    description: str
    affected_image_count: int
    created_at: str


@dataclass(slots=True, frozen=True)
class HistoryResult:
    """Describe a successfully undone or redone catalog edit."""

    operation_id: int
    description: str
    affected_image_count: int


class CatalogEditService:
    """Apply explicit user decisions to a LoRA Image Curator catalog atomically."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()


    # ------------------------------------------------------------------
    # Safety and backups
    # ------------------------------------------------------------------

    def create_backup(self, destination: Path | None = None) -> Path:
        """Create a transactionally consistent SQLite backup and return it."""
        if not self.database_path.exists():
            raise FileNotFoundError(self.database_path)

        if destination is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = self.database_path.with_name(
                f"{self.database_path.stem}_backup_{stamp}{self.database_path.suffix}"
            )

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            stem = destination.stem
            suffix = destination.suffix
            counter = 2
            while destination.exists():
                destination = destination.with_name(f"{stem}_{counter}{suffix}")
                counter += 1

        # ``sqlite3.Connection`` commits or rolls back when used as a context
        # manager, but it does not close itself.  Explicit ``closing`` wrappers
        # matter here because the first manual edit can create this backup
        # during a GUI smoke test, and Python's development mode correctly
        # reports either leaked handle as a ResourceWarning.
        with closing(self._connect(read_only=True)) as source:
            with closing(sqlite3.connect(destination, timeout=30.0)) as target:
                source.backup(target)
                target.execute("PRAGMA foreign_keys = ON")
                integrity = target.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise sqlite3.DatabaseError(
                        "The newly created catalog backup failed its integrity check."
                    )

        return destination

    # ------------------------------------------------------------------
    # Image-level review state
    # ------------------------------------------------------------------

    def set_review_state(
        self,
        image_ids: Iterable[int],
        status: str,
        notes: str = "",
    ) -> int:
        """Set one review decision for exactly the supplied catalog image IDs."""
        ids = self._normalize_ids(image_ids)
        if status not in VALID_REVIEW_STATES:
            raise ValueError(f"Unsupported review state: {status}")

        timestamp = self._timestamp()
        clean_notes = notes.strip()

        with closing(self._connect()) as connection, connection:
            self._require_images(connection, ids)

            if status == "unreviewed" and not clean_notes:
                connection.executemany(
                    "DELETE FROM image_review_state WHERE image_id = ?",
                    [(image_id,) for image_id in ids],
                )
            else:
                connection.executemany(
                    """
                    INSERT INTO image_review_state(image_id, status, notes, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(image_id) DO UPDATE SET
                        status = excluded.status,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    [(image_id, status, clean_notes, timestamp) for image_id in ids],
                )

        return len(ids)

    # ------------------------------------------------------------------
    # Manual set keywords
    # ------------------------------------------------------------------

    def replace_manual_keyword(
        self,
        image_ids: Iterable[int],
        keyword: str | None,
    ) -> int:
        """Replace only the user-owned Trigger Keyword for supplied images."""
        ids = self._normalize_ids(image_ids)
        clean_keyword = " ".join((keyword or "").split()).strip()
        timestamp = self._timestamp()

        with closing(self._connect()) as connection, connection:
            self._require_images(connection, ids)
            self._replace_manual_keyword_in_connection(
                connection,
                ids,
                clean_keyword or None,
                timestamp,
            )

        return len(ids)

    def set_manual_keyword(self, image_ids: Iterable[int], keyword: str) -> int:
        """Compatibility wrapper retained for earlier regression tests."""
        if not keyword.strip():
            raise ValueError("Trigger Keyword cannot be blank")
        return self.replace_manual_keyword(image_ids, keyword)

    def clear_manual_keyword(self, image_ids: Iterable[int]) -> int:
        """Remove manual Set keywords without touching provider-created tags."""
        return self.replace_manual_keyword(image_ids, None)

    # ------------------------------------------------------------------
    # Face-identity review
    # ------------------------------------------------------------------

    def review_identity_match(self, match_id: int, status: str) -> None:
        """Confirm, reject, or reset one face-identity suggestion."""
        if status not in VALID_IDENTITY_REVIEW_STATES:
            raise ValueError(f"Unsupported identity review state: {status}")

        with closing(self._connect()) as connection, connection:
            self._review_identity_match_in_connection(
                connection,
                int(match_id),
                status,
                self._timestamp(),
            )

    # ------------------------------------------------------------------
    # Manual tags and AI-tag exclusions
    # ------------------------------------------------------------------

    def add_manual_tags(
        self,
        image_ids: Iterable[int],
        tags: Iterable[str],
    ) -> TagEditResult:
        """
        Add user-authored tags to every selected image without duplicates.

        A manual assertion may coexist with an AI suggestion in storage, but
        the browser and future export builder merge them by normalized name.
        That lets a user deliberately promote an AI concept to a manual tag
        without producing duplicate visible or exported text.
        """
        ids = self._normalize_ids(image_ids)
        cleaned_tags = self._normalize_tag_names(tags)
        if not cleaned_tags:
            raise ValueError("Enter at least one tag")

        timestamp = self._timestamp()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_images(connection, ids)
            before_state = self._capture_user_state(connection, ids)
            changed_ids: set[int] = set()
            changed_assignments = 0

            for name in cleaned_tags:
                normalized = name.casefold()
                connection.execute(
                    """
                    INSERT INTO tags(name, normalized_name, category, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(normalized_name, category) DO NOTHING
                    """,
                    (name, normalized, MANUAL_TAG_CATEGORY, timestamp),
                )
                tag_row = connection.execute(
                    """
                    SELECT id
                    FROM tags
                    WHERE normalized_name = ? AND category = ?
                    """,
                    (normalized, MANUAL_TAG_CATEGORY),
                ).fetchone()
                if tag_row is None:
                    raise RuntimeError(f"Manual tag could not be created: {name}")
                tag_id = int(tag_row["id"])

                existing = {
                    int(row["image_id"])
                    for row in connection.execute(
                        f"""
                        SELECT image_id
                        FROM image_tags
                        WHERE image_id IN ({','.join('?' for _ in ids)})
                          AND tag_id = ?
                          AND LOWER(source) = ?
                          AND review_status = 'confirmed'
                        """,
                        [*ids, tag_id, MANUAL_TAG_SOURCE],
                    )
                }
                targets = [image_id for image_id in ids if image_id not in existing]
                if not targets:
                    continue

                connection.executemany(
                    """
                    INSERT INTO image_tags(
                        image_id, tag_id, source, confidence, review_status,
                        notes, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, 'confirmed', '', ?, ?)
                    ON CONFLICT(image_id, tag_id, source) DO UPDATE SET
                        review_status = 'confirmed',
                        notes = '',
                        updated_at = excluded.updated_at
                    """,
                    [
                        (image_id, tag_id, MANUAL_TAG_SOURCE, timestamp, timestamp)
                        for image_id in targets
                    ],
                )
                changed_ids.update(targets)
                changed_assignments += len(targets)

            return self._finish_tag_edit(
                connection=connection,
                selected_ids=ids,
                before_state=before_state,
                changed_ids=changed_ids,
                changed_assignment_count=changed_assignments,
                description=self._describe_tag_list("Add manual tag", cleaned_tags),
                timestamp=timestamp,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def remove_manual_tags(
        self,
        image_ids: Iterable[int],
        tags: Iterable[str],
    ) -> TagEditResult:
        """Remove named manual tags while leaving matching AI suggestions intact."""
        ids = self._normalize_ids(image_ids)
        cleaned_tags = self._normalize_tag_names(tags)
        if not cleaned_tags:
            raise ValueError("Choose at least one manual tag to remove")

        timestamp = self._timestamp()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_images(connection, ids)
            before_state = self._capture_user_state(connection, ids)
            changed_ids: set[int] = set()
            changed_assignments = 0
            image_placeholders = ",".join("?" for _ in ids)

            for name in cleaned_tags:
                normalized = name.casefold()
                rows = connection.execute(
                    f"""
                    SELECT it.id, it.image_id
                    FROM image_tags AS it
                    JOIN tags AS t ON t.id = it.tag_id
                    WHERE it.image_id IN ({image_placeholders})
                      AND t.normalized_name = ?
                      AND t.category = ?
                      AND LOWER(it.source) = ?
                    """,
                    [*ids, normalized, MANUAL_TAG_CATEGORY, MANUAL_TAG_SOURCE],
                ).fetchall()
                if not rows:
                    continue
                assignment_ids = [int(row["id"]) for row in rows]
                changed_ids.update(int(row["image_id"]) for row in rows)
                changed_assignments += len(rows)
                connection.executemany(
                    "DELETE FROM image_tags WHERE id = ?",
                    [(assignment_id,) for assignment_id in assignment_ids],
                )

            return self._finish_tag_edit(
                connection=connection,
                selected_ids=ids,
                before_state=before_state,
                changed_ids=changed_ids,
                changed_assignment_count=changed_assignments,
                description=self._describe_tag_list("Remove manual tag", cleaned_tags),
                timestamp=timestamp,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_ai_tag_excluded(
        self,
        image_ids: Iterable[int],
        tag: str,
        *,
        excluded: bool,
    ) -> TagEditResult:
        """Exclude or restore one AI tag for every selected image that suggests it."""
        ids = self._normalize_ids(image_ids)
        cleaned = self._normalize_tag_names([tag])
        if len(cleaned) != 1:
            raise ValueError("Choose exactly one AI tag")
        name = cleaned[0]
        normalized = name.casefold()
        timestamp = self._timestamp()

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_images(connection, ids)
            tag_row = connection.execute(
                """
                SELECT id, name
                FROM tags
                WHERE normalized_name = ? AND category = ?
                """,
                (normalized, AI_TAG_CATEGORY),
            ).fetchone()
            if tag_row is None:
                raise KeyError(f"AI tag not found: {name}")
            tag_id = int(tag_row["id"])

            # Only current Florence suggestions are eligible. A stale UI click
            # therefore cannot create an exclusion for an unrelated image.
            current_suggestion_ids = {
                int(row["image_id"])
                for row in connection.execute(
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
                    SELECT DISTINCT ats.image_id
                    FROM analysis_tag_suggestions AS ats
                    JOIN chosen_tag_analysis AS ca
                      ON ca.id = ats.analysis_result_id
                    WHERE ats.image_id IN ({','.join('?' for _ in ids)})
                      AND ats.tag_id = ?
                    """,
                    [*ids, tag_id],
                )
            }
            targets = [image_id for image_id in ids if image_id in current_suggestion_ids]
            before_state = self._capture_user_state(connection, ids)
            changed_ids: set[int] = set()

            if excluded:
                already = {
                    int(row["image_id"])
                    for row in connection.execute(
                        f"""
                        SELECT image_id
                        FROM image_tag_exclusions
                        WHERE image_id IN ({','.join('?' for _ in ids)})
                          AND tag_id = ?
                        """,
                        [*ids, tag_id],
                    )
                }
                changed = [image_id for image_id in targets if image_id not in already]
                connection.executemany(
                    """
                    INSERT INTO image_tag_exclusions(
                        image_id, tag_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(image_id, tag_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                    """,
                    [(image_id, tag_id, timestamp, timestamp) for image_id in changed],
                )
            else:
                changed = [
                    int(row["image_id"])
                    for row in connection.execute(
                        f"""
                        SELECT image_id
                        FROM image_tag_exclusions
                        WHERE image_id IN ({','.join('?' for _ in ids)})
                          AND tag_id = ?
                        """,
                        [*ids, tag_id],
                    )
                    if int(row["image_id"]) in current_suggestion_ids
                ]
                if changed:
                    connection.executemany(
                        "DELETE FROM image_tag_exclusions WHERE image_id = ? AND tag_id = ?",
                        [(image_id, tag_id) for image_id in changed],
                    )

            changed_ids.update(changed)
            verb = "Exclude AI tag" if excluded else "Restore AI tag"
            return self._finish_tag_edit(
                connection=connection,
                selected_ids=ids,
                before_state=before_state,
                changed_ids=changed_ids,
                changed_assignment_count=len(changed),
                description=f'{verb} "{str(tag_row["name"])}"',
                timestamp=timestamp,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Batch editing and durable undo
    # ------------------------------------------------------------------

    def apply_batch_edit(
        self,
        image_ids: Iterable[int],
        request: BatchEditRequest,
    ) -> BatchEditResult:
        """
        Apply explicit user changes to one or many selected images atomically.

        The historical method name remains for compatibility with the v0.8.0
        tests, but the operation is no longer conceptually a separate "batch
        editor."  The same path now powers the details pane for one image or a
        multi-selection.

        Only fields represented by the request are written.  Identity actions
        apply to each image's strongest current suggestion; images without a
        suggestion are counted as skipped rather than treated as failures.
        """
        ids = self._normalize_ids(image_ids)
        normalized = request.normalized()
        timestamp = self._timestamp()

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_images(connection, ids)
            before_state = self._capture_user_state(connection, ids)

            changed_ids: set[int] = set()
            keyword_changed = 0
            review_changed = 0
            identity_changed = 0
            identity_skipped = 0

            if normalized.keyword_action != "unchanged":
                keyword_targets = self._keyword_change_targets(
                    before_state,
                    normalized.keyword_action,
                    normalized.keyword,
                )
                if keyword_targets:
                    keyword_changed = len(keyword_targets)
                    changed_ids.update(keyword_targets)
                    self._replace_manual_keyword_in_connection(
                        connection,
                        keyword_targets,
                        normalized.keyword if normalized.keyword_action == "set" else None,
                        timestamp,
                    )

            if normalized.review_status is not None:
                review_targets = self._review_change_targets(
                    before_state,
                    normalized.review_status,
                )
                if review_targets:
                    review_changed = len(review_targets)
                    changed_ids.update(review_targets)
                    self._set_review_status_preserving_notes(
                        connection,
                        review_targets,
                        normalized.review_status,
                        timestamp,
                    )

            if normalized.identity_status is not None:
                identity_rows = self._strongest_identity_rows(connection, ids)
                identity_skipped = len(ids) - len(identity_rows)
                for row in identity_rows:
                    image_id = int(row["image_id"])
                    if str(row["review_status"]) == normalized.identity_status:
                        continue
                    self._review_identity_match_in_connection(
                        connection,
                        int(row["match_id"]),
                        normalized.identity_status,
                        timestamp,
                    )
                    identity_changed += 1
                    changed_ids.add(image_id)

            if not changed_ids:
                connection.rollback()
                return BatchEditResult(
                    operation_id=None,
                    selected_count=len(ids),
                    changed_image_count=0,
                    keyword_changed=0,
                    review_changed=0,
                    identity_changed=0,
                    identity_skipped_no_suggestion=identity_skipped,
                )

            # Store only images whose user-owned metadata actually changed.
            # This avoids blocking undo merely because an unchanged member of a
            # large selection was edited later for an unrelated reason.
            changed_list = sorted(changed_ids)
            before_changed = self._state_subset(before_state, changed_list)
            after_changed = self._capture_user_state(connection, changed_list)

            # A new edit after one or more undos creates a new history branch.
            # Those old redo candidates remain as audit rows but are explicitly
            # discarded so Ctrl+Y cannot resurrect an obsolete future.
            connection.execute(
                """
                UPDATE catalog_edit_operations
                SET discarded_at = ?
                WHERE undone_at IS NOT NULL AND discarded_at IS NULL
                """,
                (timestamp,),
            )

            description = self._describe_batch_request(normalized)
            cursor = connection.execute(
                """
                INSERT INTO catalog_edit_operations(
                    operation_type,
                    description,
                    affected_image_count,
                    before_state_json,
                    after_state_json,
                    created_at,
                    undone_at,
                    discarded_at
                ) VALUES ('selection_edit', ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    description,
                    len(changed_list),
                    self._canonical_json(before_changed),
                    self._canonical_json(after_changed),
                    timestamp,
                ),
            )
            operation_id = int(cursor.lastrowid)
            self._trim_history(connection, timestamp)
            connection.commit()

            return BatchEditResult(
                operation_id=operation_id,
                selected_count=len(ids),
                changed_image_count=len(changed_list),
                keyword_changed=keyword_changed,
                review_changed=review_changed,
                identity_changed=identity_changed,
                identity_skipped_no_suggestion=identity_skipped,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_last_undoable_operation(self) -> HistoryOperation | None:
        """Return the newest applied edit in the current history branch."""
        with closing(self._connect(read_only=True)) as connection:
            row = connection.execute(
                """
                SELECT id, description, affected_image_count, created_at
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NULL
                  AND discarded_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        return self._history_operation_from_row(row)

    def get_next_redoable_operation(self) -> HistoryOperation | None:
        """Return the next valid operation in the current redo branch."""
        with closing(self._connect(read_only=True)) as connection:
            active_row = connection.execute(
                """
                SELECT MAX(id) AS max_id
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NULL
                  AND discarded_at IS NULL
                """
            ).fetchone()
            active_max = (
                int(active_row["max_id"])
                if active_row is not None and active_row["max_id"] is not None
                else 0
            )
            row = connection.execute(
                """
                SELECT id, description, affected_image_count, created_at
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NOT NULL
                  AND discarded_at IS NULL
                  AND id > ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (active_max,),
            ).fetchone()

        return self._history_operation_from_row(row)

    def undo_last_operation(self) -> HistoryResult:
        """Restore the newest edit's before-state without touching AI output."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NULL
                  AND discarded_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise LookupError("There is no catalog edit to undo")

            before_state = json.loads(str(row["before_state_json"]))
            after_state = json.loads(str(row["after_state_json"]))
            ids = [int(item["image_id"]) for item in after_state["images"]]
            current_state = self._capture_user_state(
                connection, ids, snapshot_version=int(after_state.get("version", 1))
            )

            if self._canonical_json(current_state) != self._canonical_json(after_state):
                raise RuntimeError(
                    "The affected metadata changed outside the recorded history. "
                    "Undo was stopped to avoid overwriting newer work."
                )

            self._restore_user_state(connection, before_state)
            connection.execute(
                "UPDATE catalog_edit_operations SET undone_at = ? WHERE id = ?",
                (self._timestamp(), int(row["id"])),
            )
            connection.commit()
            return HistoryResult(
                operation_id=int(row["id"]),
                description=str(row["description"]),
                affected_image_count=int(row["affected_image_count"]),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def redo_next_operation(self) -> HistoryResult:
        """Reapply the next undone edit when its before-state still matches."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            active_row = connection.execute(
                """
                SELECT MAX(id) AS max_id
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NULL
                  AND discarded_at IS NULL
                """
            ).fetchone()
            active_max = (
                int(active_row["max_id"])
                if active_row is not None and active_row["max_id"] is not None
                else 0
            )
            row = connection.execute(
                """
                SELECT *
                FROM catalog_edit_operations
                WHERE operation_type IN ('batch_edit', 'selection_edit')
                  AND undone_at IS NOT NULL
                  AND discarded_at IS NULL
                  AND id > ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (active_max,),
            ).fetchone()
            if row is None:
                raise LookupError("There is no catalog edit to redo")

            before_state = json.loads(str(row["before_state_json"]))
            after_state = json.loads(str(row["after_state_json"]))
            ids = [int(item["image_id"]) for item in before_state["images"]]
            current_state = self._capture_user_state(
                connection, ids, snapshot_version=int(before_state.get("version", 1))
            )

            if self._canonical_json(current_state) != self._canonical_json(before_state):
                raise RuntimeError(
                    "The affected metadata changed outside the recorded history. "
                    "Redo was stopped to avoid overwriting newer work."
                )

            self._restore_user_state(connection, after_state)
            connection.execute(
                "UPDATE catalog_edit_operations SET undone_at = NULL WHERE id = ?",
                (int(row["id"]),),
            )
            connection.commit()
            return HistoryResult(
                operation_id=int(row["id"]),
                description=str(row["description"]),
                affected_image_count=int(row["affected_image_count"]),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # Compatibility aliases keep older tests and callers readable while the GUI
    # migrates from the phrase "Undo Last Batch" to a general edit history.
    def undo_last_batch_operation(self) -> HistoryResult:
        return self.undo_last_operation()

    @staticmethod
    def _history_operation_from_row(row: sqlite3.Row | None) -> HistoryOperation | None:
        if row is None:
            return None
        return HistoryOperation(
            operation_id=int(row["id"]),
            description=str(row["description"]),
            affected_image_count=int(row["affected_image_count"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _state_subset(state: dict[str, Any], image_ids: list[int]) -> dict[str, Any]:
        wanted = set(image_ids)
        return {
            "version": int(state.get("version", 1)),
            "images": [
                item
                for item in state.get("images", [])
                if int(item["image_id"]) in wanted
            ],
        }

    @staticmethod
    def _trim_history(
        connection: sqlite3.Connection,
        timestamp: str,
    ) -> None:
        """Keep the latest HISTORY_LIMIT operations in the active branch."""
        rows = connection.execute(
            """
            SELECT id
            FROM catalog_edit_operations
            WHERE operation_type IN ('batch_edit', 'selection_edit')
              AND discarded_at IS NULL
            ORDER BY id DESC
            """
        ).fetchall()
        obsolete = [int(row["id"]) for row in rows[HISTORY_LIMIT:]]
        if obsolete:
            connection.executemany(
                "UPDATE catalog_edit_operations SET discarded_at = ? WHERE id = ?",
                [(timestamp, operation_id) for operation_id in obsolete],
            )

    # ------------------------------------------------------------------
    # Batch-state capture and restoration
    # ------------------------------------------------------------------

    def _capture_user_state(
        self,
        connection: sqlite3.Connection,
        image_ids: list[int],
        snapshot_version: int = 2,
    ) -> dict[str, Any]:
        """Capture only user-owned rows that selection editing may mutate."""
        placeholders = ",".join("?" for _ in image_ids)

        review_by_image: dict[int, dict[str, Any]] = {}
        for row in connection.execute(
            f"""
            SELECT image_id, status, notes, updated_at
            FROM image_review_state
            WHERE image_id IN ({placeholders})
            ORDER BY image_id
            """,
            image_ids,
        ):
            review_by_image[int(row["image_id"])] = dict(row)

        keywords_by_image: dict[int, list[dict[str, Any]]] = {
            image_id: [] for image_id in image_ids
        }
        for row in connection.execute(
            f"""
            SELECT
                it.image_id,
                t.name,
                t.normalized_name,
                t.category,
                it.source,
                it.confidence,
                it.review_status,
                it.notes,
                it.created_at,
                it.updated_at
            FROM image_tags AS it
            JOIN tags AS t ON t.id = it.tag_id
            WHERE it.image_id IN ({placeholders})
              AND t.category = ?
              AND LOWER(it.source) = ?
            ORDER BY it.image_id, t.normalized_name, it.id
            """,
            [*image_ids, MANUAL_KEYWORD_CATEGORY, MANUAL_TAG_SOURCE],
        ):
            item = dict(row)
            image_id = int(item.pop("image_id"))
            keywords_by_image[image_id].append(item)

        manual_tags_by_image: dict[int, list[dict[str, Any]]] = {
            image_id: [] for image_id in image_ids
        }
        for row in connection.execute(
            f"""
            SELECT
                it.image_id,
                t.name,
                t.normalized_name,
                t.category,
                it.source,
                it.confidence,
                it.review_status,
                it.notes,
                it.created_at,
                it.updated_at
            FROM image_tags AS it
            JOIN tags AS t ON t.id = it.tag_id
            WHERE it.image_id IN ({placeholders})
              AND t.category = ?
              AND LOWER(it.source) = ?
            ORDER BY it.image_id, t.normalized_name, it.id
            """,
            [*image_ids, MANUAL_TAG_CATEGORY, MANUAL_TAG_SOURCE],
        ):
            item = dict(row)
            image_id = int(item.pop("image_id"))
            manual_tags_by_image[image_id].append(item)

        exclusions_by_image: dict[int, list[dict[str, Any]]] = {
            image_id: [] for image_id in image_ids
        }
        for row in connection.execute(
            f"""
            SELECT
                e.image_id,
                t.name,
                t.normalized_name,
                t.category,
                e.created_at,
                e.updated_at
            FROM image_tag_exclusions AS e
            JOIN tags AS t ON t.id = e.tag_id
            WHERE e.image_id IN ({placeholders})
            ORDER BY e.image_id, t.normalized_name
            """,
            image_ids,
        ):
            item = dict(row)
            image_id = int(item.pop("image_id"))
            exclusions_by_image[image_id].append(item)

        matches_by_image: dict[int, list[dict[str, Any]]] = {
            image_id: [] for image_id in image_ids
        }
        for row in connection.execute(
            f"""
            SELECT
                fir.image_id,
                im.id AS match_id,
                im.review_status,
                im.updated_at
            FROM identity_matches AS im
            JOIN face_detections AS fd ON fd.id = im.face_detection_id
            JOIN face_image_results AS fir ON fir.id = fd.face_result_id
            WHERE fir.image_id IN ({placeholders})
            ORDER BY fir.image_id, im.id
            """,
            image_ids,
        ):
            item = dict(row)
            image_id = int(item.pop("image_id"))
            matches_by_image[image_id].append(item)

        identity_tags_by_image: dict[int, list[dict[str, Any]]] = {
            image_id: [] for image_id in image_ids
        }
        for row in connection.execute(
            f"""
            SELECT
                it.image_id,
                it.id AS image_tag_id,
                it.review_status,
                it.updated_at
            FROM image_tags AS it
            JOIN tags AS t ON t.id = it.tag_id
            WHERE it.image_id IN ({placeholders})
              AND t.category = 'identity'
              AND LOWER(it.source) LIKE 'face:%'
            ORDER BY it.image_id, it.id
            """,
            image_ids,
        ):
            item = dict(row)
            image_id = int(item.pop("image_id"))
            identity_tags_by_image[image_id].append(item)

        images = [
            {
                "image_id": image_id,
                "review_state": review_by_image.get(image_id),
                "manual_keywords": keywords_by_image[image_id],
                "identity_matches": matches_by_image[image_id],
                "identity_tags": identity_tags_by_image[image_id],
                **(
                    {
                        "manual_tags": manual_tags_by_image[image_id],
                        "ai_tag_exclusions": exclusions_by_image[image_id],
                    }
                    if snapshot_version >= 2
                    else {}
                ),
            }
            for image_id in sorted(image_ids)
        ]
        return {"version": 2 if snapshot_version >= 2 else 1, "images": images}

    def _restore_user_state(
        self,
        connection: sqlite3.Connection,
        state: dict[str, Any],
    ) -> None:
        """Restore a prior batch snapshot inside the caller's transaction."""
        images = list(state.get("images", []))
        ids = [int(item["image_id"]) for item in images]
        self._require_images(connection, ids)
        placeholders = ",".join("?" for _ in ids)

        connection.execute(
            f"DELETE FROM image_review_state WHERE image_id IN ({placeholders})",
            ids,
        )
        for item in images:
            review = item.get("review_state")
            if review is not None:
                connection.execute(
                    """
                    INSERT INTO image_review_state(image_id, status, notes, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        int(item["image_id"]),
                        str(review["status"]),
                        str(review["notes"]),
                        str(review["updated_at"]),
                    ),
                )

        connection.execute(
            f"""
            DELETE FROM image_tags
            WHERE image_id IN ({placeholders})
              AND LOWER(source) = ?
              AND tag_id IN (SELECT id FROM tags WHERE category = ?)
            """,
            [*ids, MANUAL_TAG_SOURCE, MANUAL_KEYWORD_CATEGORY],
        )
        for item in images:
            image_id = int(item["image_id"])
            for keyword in item.get("manual_keywords", []):
                connection.execute(
                    """
                    INSERT INTO tags(name, normalized_name, category, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(normalized_name, category) DO NOTHING
                    """,
                    (
                        str(keyword["name"]),
                        str(keyword["normalized_name"]),
                        str(keyword["category"]),
                        str(keyword["created_at"]),
                    ),
                )
                tag_id = connection.execute(
                    "SELECT id FROM tags WHERE normalized_name = ? AND category = ?",
                    (
                        str(keyword["normalized_name"]),
                        str(keyword["category"]),
                    ),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO image_tags(
                        image_id, tag_id, source, confidence, review_status,
                        notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        image_id,
                        int(tag_id),
                        str(keyword["source"]),
                        keyword["confidence"],
                        str(keyword["review_status"]),
                        str(keyword["notes"]),
                        str(keyword["created_at"]),
                        str(keyword["updated_at"]),
                    ),
                )

        if int(state.get("version", 1)) >= 2:
            connection.execute(
                f"""
                DELETE FROM image_tags
                WHERE image_id IN ({placeholders})
                  AND LOWER(source) = ?
                  AND tag_id IN (SELECT id FROM tags WHERE category = ?)
                """,
                [*ids, MANUAL_TAG_SOURCE, MANUAL_TAG_CATEGORY],
            )
            connection.execute(
                f"DELETE FROM image_tag_exclusions WHERE image_id IN ({placeholders})",
                ids,
            )

            for item in images:
                image_id = int(item["image_id"])
                for manual_tag in item.get("manual_tags", []):
                    connection.execute(
                        """
                        INSERT INTO tags(name, normalized_name, category, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(normalized_name, category) DO NOTHING
                        """,
                        (
                            str(manual_tag["name"]),
                            str(manual_tag["normalized_name"]),
                            str(manual_tag["category"]),
                            str(manual_tag["created_at"]),
                        ),
                    )
                    tag_id = connection.execute(
                        "SELECT id FROM tags WHERE normalized_name = ? AND category = ?",
                        (
                            str(manual_tag["normalized_name"]),
                            str(manual_tag["category"]),
                        ),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO image_tags(
                            image_id, tag_id, source, confidence, review_status,
                            notes, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            image_id,
                            int(tag_id),
                            str(manual_tag["source"]),
                            manual_tag["confidence"],
                            str(manual_tag["review_status"]),
                            str(manual_tag["notes"]),
                            str(manual_tag["created_at"]),
                            str(manual_tag["updated_at"]),
                        ),
                    )

                for exclusion in item.get("ai_tag_exclusions", []):
                    connection.execute(
                        """
                        INSERT INTO tags(name, normalized_name, category, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(normalized_name, category) DO NOTHING
                        """,
                        (
                            str(exclusion["name"]),
                            str(exclusion["normalized_name"]),
                            str(exclusion["category"]),
                            str(exclusion["created_at"]),
                        ),
                    )
                    tag_id = connection.execute(
                        "SELECT id FROM tags WHERE normalized_name = ? AND category = ?",
                        (
                            str(exclusion["normalized_name"]),
                            str(exclusion["category"]),
                        ),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO image_tag_exclusions(
                            image_id, tag_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            image_id,
                            int(tag_id),
                            str(exclusion["created_at"]),
                            str(exclusion["updated_at"]),
                        ),
                    )

        for item in images:
            for match in item.get("identity_matches", []):
                cursor = connection.execute(
                    """
                    UPDATE identity_matches
                    SET review_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(match["review_status"]),
                        str(match["updated_at"]),
                        int(match["match_id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Identity match {match['match_id']} no longer exists"
                    )

            for tag in item.get("identity_tags", []):
                cursor = connection.execute(
                    """
                    UPDATE image_tags
                    SET review_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(tag["review_status"]),
                        str(tag["updated_at"]),
                        int(tag["image_tag_id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Identity tag assignment {tag['image_tag_id']} no longer exists"
                    )

    def _finish_tag_edit(
        self,
        *,
        connection: sqlite3.Connection,
        selected_ids: list[int],
        before_state: dict[str, Any],
        changed_ids: set[int],
        changed_assignment_count: int,
        description: str,
        timestamp: str,
    ) -> TagEditResult:
        """Commit one tag action as one durable undo/redo history step."""
        if not changed_ids:
            connection.rollback()
            return TagEditResult(
                operation_id=None,
                selected_count=len(selected_ids),
                changed_image_count=0,
                changed_assignment_count=0,
            )

        changed_list = sorted(changed_ids)
        before_changed = self._state_subset(before_state, changed_list)
        after_changed = self._capture_user_state(connection, changed_list)
        connection.execute(
            """
            UPDATE catalog_edit_operations
            SET discarded_at = ?
            WHERE undone_at IS NOT NULL AND discarded_at IS NULL
            """,
            (timestamp,),
        )
        cursor = connection.execute(
            """
            INSERT INTO catalog_edit_operations(
                operation_type,
                description,
                affected_image_count,
                before_state_json,
                after_state_json,
                created_at,
                undone_at,
                discarded_at
            ) VALUES ('selection_edit', ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                description,
                len(changed_list),
                self._canonical_json(before_changed),
                self._canonical_json(after_changed),
                timestamp,
            ),
        )
        operation_id = int(cursor.lastrowid)
        self._trim_history(connection, timestamp)
        connection.commit()
        return TagEditResult(
            operation_id=operation_id,
            selected_count=len(selected_ids),
            changed_image_count=len(changed_list),
            changed_assignment_count=changed_assignment_count,
        )

    @staticmethod
    def _normalize_tag_names(tags: Iterable[str]) -> list[str]:
        """Normalize whitespace and remove case-insensitive duplicates."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            name = " ".join(str(raw).split()).strip()
            normalized = name.casefold()
            if not name or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(name)
        return cleaned

    @staticmethod
    def _describe_tag_list(action: str, tags: list[str]) -> str:
        quoted = ", ".join(f'"{tag}"' for tag in tags)
        plural = "s" if len(tags) != 1 else ""
        return f"{action}{plural} {quoted}"

    # ------------------------------------------------------------------
    # Internal mutation helpers
    # ------------------------------------------------------------------

    def _replace_manual_keyword_in_connection(
        self,
        connection: sqlite3.Connection,
        image_ids: list[int],
        keyword: str | None,
        timestamp: str,
    ) -> None:
        placeholders = ",".join("?" for _ in image_ids)
        connection.execute(
            f"""
            DELETE FROM image_tags
            WHERE image_id IN ({placeholders})
              AND LOWER(source) = ?
              AND tag_id IN (SELECT id FROM tags WHERE category = ?)
            """,
            [*image_ids, MANUAL_TAG_SOURCE, MANUAL_KEYWORD_CATEGORY],
        )

        if keyword is None:
            return

        normalized = keyword.casefold()
        connection.execute(
            """
            INSERT INTO tags(name, normalized_name, category, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(normalized_name, category) DO NOTHING
            """,
            (keyword, normalized, MANUAL_KEYWORD_CATEGORY, timestamp),
        )
        tag_id_row = connection.execute(
            "SELECT id FROM tags WHERE normalized_name = ? AND category = ?",
            (normalized, MANUAL_KEYWORD_CATEGORY),
        ).fetchone()
        if tag_id_row is None:
            raise RuntimeError("The manual Trigger Keyword could not be created")
        tag_id = int(tag_id_row[0])

        connection.executemany(
            """
            INSERT INTO image_tags(
                image_id, tag_id, source, confidence, review_status,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, 'confirmed', '', ?, ?)
            ON CONFLICT(image_id, tag_id, source) DO UPDATE SET
                review_status = 'confirmed',
                notes = '',
                updated_at = excluded.updated_at
            """,
            [
                (image_id, tag_id, MANUAL_TAG_SOURCE, timestamp, timestamp)
                for image_id in image_ids
            ],
        )

    def _set_review_status_preserving_notes(
        self,
        connection: sqlite3.Connection,
        image_ids: list[int],
        status: str,
        timestamp: str,
    ) -> None:
        for image_id in image_ids:
            existing = connection.execute(
                "SELECT notes FROM image_review_state WHERE image_id = ?",
                (image_id,),
            ).fetchone()
            notes = str(existing["notes"]) if existing is not None else ""

            if status == "unreviewed" and not notes:
                connection.execute(
                    "DELETE FROM image_review_state WHERE image_id = ?",
                    (image_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO image_review_state(image_id, status, notes, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(image_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (image_id, status, notes, timestamp),
                )

    def _review_identity_match_in_connection(
        self,
        connection: sqlite3.Connection,
        match_id: int,
        status: str,
        timestamp: str,
    ) -> None:
        match = connection.execute(
            """
            SELECT
                im.id,
                fir.image_id,
                identities.normalized_name
            FROM identity_matches AS im
            JOIN identity_profiles AS ip ON ip.id = im.identity_profile_id
            JOIN identities ON identities.id = ip.identity_id
            JOIN face_detections AS fd ON fd.id = im.face_detection_id
            JOIN face_image_results AS fir ON fir.id = fd.face_result_id
            WHERE im.id = ?
            """,
            (int(match_id),),
        ).fetchone()
        if match is None:
            raise KeyError(f"Identity match not found: {match_id}")

        connection.execute(
            """
            UPDATE identity_matches
            SET review_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, timestamp, int(match_id)),
        )
        connection.execute(
            """
            UPDATE image_tags
            SET review_status = ?, updated_at = ?
            WHERE image_id = ?
              AND tag_id IN (
                  SELECT id FROM tags
                  WHERE category = 'identity' AND normalized_name = ?
              )
              AND LOWER(source) LIKE 'face:%'
            """,
            (
                status,
                timestamp,
                int(match["image_id"]),
                str(match["normalized_name"]),
            ),
        )

    def _strongest_identity_rows(
        self,
        connection: sqlite3.Connection,
        image_ids: list[int],
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in image_ids)
        return connection.execute(
            f"""
            SELECT
                fir.image_id,
                im.id AS match_id,
                im.review_status
            FROM identity_matches AS im
            JOIN face_detections AS fd ON fd.id = im.face_detection_id
            JOIN face_image_results AS fir ON fir.id = fd.face_result_id
            WHERE fir.image_id IN ({placeholders})
              AND im.is_suggested = 1
              AND im.id = (
                  SELECT im2.id
                  FROM identity_matches AS im2
                  JOIN face_detections AS fd2 ON fd2.id = im2.face_detection_id
                  JOIN face_image_results AS fir2 ON fir2.id = fd2.face_result_id
                  WHERE fir2.image_id = fir.image_id AND im2.is_suggested = 1
                  ORDER BY im2.similarity DESC, im2.id DESC
                  LIMIT 1
              )
            ORDER BY fir.image_id
            """,
            image_ids,
        ).fetchall()

    @staticmethod
    def _keyword_change_targets(
        before_state: dict[str, Any],
        action: str,
        keyword: str,
    ) -> list[int]:
        targets: list[int] = []
        normalized = keyword.casefold()
        for item in before_state["images"]:
            current = list(item["manual_keywords"])
            if action == "clear":
                if current:
                    targets.append(int(item["image_id"]))
            elif action == "set":
                names = [str(entry["normalized_name"]) for entry in current]
                if names != [normalized]:
                    targets.append(int(item["image_id"]))
        return targets

    @staticmethod
    def _review_change_targets(
        before_state: dict[str, Any],
        target_status: str,
    ) -> list[int]:
        targets: list[int] = []
        for item in before_state["images"]:
            review = item["review_state"]
            current_status = str(review["status"]) if review is not None else "unreviewed"
            if current_status != target_status:
                targets.append(int(item["image_id"]))
        return targets

    @staticmethod
    def _describe_batch_request(request: BatchEditRequest) -> str:
        parts: list[str] = []
        if request.keyword_action == "set":
            parts.append(f'Set Trigger Keyword "{request.keyword}"')
        elif request.keyword_action == "clear":
            parts.append("Clear manual Trigger Keyword")
        if request.review_status is not None:
            label = {
                "unreviewed": "Unreviewed",
                "keep": "Keep",
                "review": "Needs follow-up",
                "reject": "Reject",
                "quarantined": "Quarantined",
            }.get(request.review_status, request.review_status)
            parts.append(f"Set disposition to {label}")
        if request.identity_status is not None:
            label = {
                "suggested": "Reset identity review",
                "confirmed": "Confirm identity suggestions",
                "rejected": "Reject identity suggestions",
            }[request.identity_status]
            parts.append(label)
        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Connection and validation helpers
    # ------------------------------------------------------------------

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = self.database_path.as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        else:
            connection = sqlite3.connect(self.database_path, timeout=30.0)

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _normalize_ids(image_ids: Iterable[int]) -> list[int]:
        ids = sorted({int(image_id) for image_id in image_ids})
        if not ids:
            raise ValueError("At least one image must be selected")
        return ids

    @staticmethod
    def _require_images(connection: sqlite3.Connection, image_ids: list[int]) -> None:
        placeholders = ",".join("?" for _ in image_ids)
        found = {
            int(row[0])
            for row in connection.execute(
                f"SELECT id FROM images WHERE id IN ({placeholders})", image_ids
            )
        }
        missing = sorted(set(image_ids) - found)
        if missing:
            raise KeyError(f"Catalog image IDs not found: {missing}")

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
