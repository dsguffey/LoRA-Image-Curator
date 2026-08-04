"""Reversible quarantine and recoverable operating-system trash operations.

Catalog cards represent unique image contents, while one image may have
several physical file locations.  File actions therefore resolve and disclose
the complete set of present/quarantined locations before changing anything.

Safety guarantees
-----------------
* Quarantine moves files into one operation-specific folder and records the
  original and target paths.  Restore uses that record and never overwrites an
  existing path.
* Delete delegates to ``send2trash``.  If native trash support is unavailable
  or fails, the operation stops; there is no permanent-delete fallback.
* Each physical file receives a durable ``file_actions`` row.  Partial external
  failures remain visible and successfully completed items keep truthful
  catalog statuses.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from catalog import Catalog, normalize_path_key


FileActionProgress = Callable[[int, int, str], None]
SQL_BATCH_SIZE = 400


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True, frozen=True)
class FileActionItem:
    image_id: int
    file_id: int
    source_path: Path
    relative_path: str
    status: str


@dataclass(slots=True, frozen=True)
class FileActionSummary:
    requested_images: int
    requested_files: int
    completed_files: int
    failed_files: int
    operation_id: str
    errors: tuple[str, ...]
    cancelled: bool = False


@dataclass(slots=True, frozen=True)
class CatalogRemovalSummary:
    """Outcome of a transactional image-record removal."""

    requested_images: int
    removed_images: int
    missing_image_ids: int
    cleared_history_operations: int
    cancelled: bool = False


class FileActionService:
    """Resolve selected catalog images and perform explicit file actions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        with Catalog(self.database_path):
            pass

    def present_items(self, image_ids: Iterable[int]) -> tuple[FileActionItem, ...]:
        """Return every present physical location for the selected images."""
        return self._items(image_ids, status="present")

    def quarantined_items(
        self,
        image_ids: Iterable[int],
    ) -> tuple[FileActionItem, ...]:
        """Return every quarantined physical location for selected images."""
        return self._items(image_ids, status="quarantined")

    def quarantine(
        self,
        image_ids: Iterable[int],
        quarantine_root: Path,
        *,
        resolved_items: Iterable[FileActionItem] | None = None,
        progress_callback: FileActionProgress | None = None,
        cancel_event: Event | None = None,
    ) -> FileActionSummary:
        """Move all present locations into a collision-safe operation folder."""
        ids = {int(image_id) for image_id in image_ids}
        items = (
            tuple(resolved_items)
            if resolved_items is not None
            else self.present_items(ids)
        )
        operation_id = uuid.uuid4().hex
        root = quarantine_root.expanduser().resolve()
        operation_root = root / self.database_path.stem / operation_id
        errors: list[str] = []
        completed = 0
        cancelled = False

        if progress_callback is not None:
            progress_callback(0, len(items), "Preparing quarantine…")

        connection = self._connect()
        try:
            for index, item in enumerate(items, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                target = _quarantine_target(operation_root, item)
                try:
                    if not item.source_path.is_file():
                        raise FileNotFoundError(item.source_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise FileExistsError(
                            f"Quarantine target already exists: {target}"
                        )
                    shutil.move(str(item.source_path), str(target))
                    try:
                        self._record_successful_move(
                            connection,
                            item=item,
                            target=target,
                            operation_id=operation_id,
                        )
                    except Exception:
                        # Preserve catalog/filesystem agreement if the SQLite
                        # write fails after the move.
                        connection.rollback()
                        if target.exists() and not item.source_path.exists():
                            item.source_path.parent.mkdir(
                                parents=True,
                                exist_ok=True,
                            )
                            shutil.move(str(target), str(item.source_path))
                        raise
                    completed += 1
                except Exception as error:
                    message = (
                        f"{item.source_path}: {type(error).__name__}: {error}"
                    )
                    errors.append(message)
                    self._record_failure(
                        connection,
                        item=item,
                        action_type="quarantine",
                        target_path=target,
                        operation_id=operation_id,
                        error=message,
                    )
                if progress_callback is not None:
                    progress_callback(
                        index,
                        len(items),
                        f"Quarantining {item.source_path.name}",
                    )
            self._sync_quarantine_review_states(connection, ids)
        finally:
            connection.close()

        return FileActionSummary(
            requested_images=len(ids),
            requested_files=len(items),
            completed_files=completed,
            failed_files=len(errors),
            operation_id=operation_id,
            errors=tuple(errors),
            cancelled=cancelled,
        )

    def restore(
        self,
        image_ids: Iterable[int],
        *,
        resolved_items: Iterable[FileActionItem] | None = None,
        progress_callback: FileActionProgress | None = None,
        cancel_event: Event | None = None,
    ) -> FileActionSummary:
        """Restore selected quarantined locations to their recorded origins."""
        ids = {int(image_id) for image_id in image_ids}
        items = (
            tuple(resolved_items)
            if resolved_items is not None
            else self.quarantined_items(ids)
        )
        operation_id = uuid.uuid4().hex
        errors: list[str] = []
        completed = 0
        cancelled = False
        if progress_callback is not None:
            progress_callback(0, len(items), "Preparing restore…")
        connection = self._connect()
        try:
            for index, item in enumerate(items, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                action = connection.execute(
                    """
                    SELECT source_path, target_path, details_json
                    FROM file_actions
                    WHERE file_id = ?
                      AND action_type = 'quarantine'
                      AND status = 'complete'
                      AND target_path = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (item.file_id, str(item.source_path)),
                ).fetchone()
                if action is None:
                    message = (
                        f"{item.source_path}: no completed quarantine history "
                        "identifies the original location."
                    )
                    errors.append(message)
                    self._record_failure(
                        connection,
                        item=item,
                        action_type="restore_quarantine",
                        target_path=item.source_path,
                        operation_id=operation_id,
                        error=message,
                    )
                    if progress_callback is not None:
                        progress_callback(
                            index,
                            len(items),
                            f"Could not restore {item.source_path.name}",
                        )
                    continue

                original = Path(str(action["source_path"])).expanduser().resolve()
                try:
                    if not item.source_path.is_file():
                        raise FileNotFoundError(item.source_path)
                    if original.exists():
                        raise FileExistsError(
                            f"Original location is occupied: {original}"
                        )
                    conflicting = connection.execute(
                        """
                        SELECT id FROM files
                        WHERE path_key = ? AND id <> ?
                        LIMIT 1
                        """,
                        (normalize_path_key(original), item.file_id),
                    ).fetchone()
                    if conflicting is not None:
                        raise FileExistsError(
                            "Another catalog file record already owns the "
                            f"original path: {original}"
                        )
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item.source_path), str(original))
                    try:
                        now = utc_now_text()
                        connection.execute(
                            """
                            UPDATE files
                            SET absolute_path = ?,
                                path_key = ?,
                                status = 'present',
                                last_seen_at = ?
                            WHERE id = ?
                            """,
                            (
                                str(original),
                                normalize_path_key(original),
                                now,
                                item.file_id,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO file_actions (
                                file_id, action_type, source_path, target_path,
                                status, details_json, performed_at
                            )
                            VALUES (?, 'restore_quarantine', ?, ?, 'complete', ?, ?)
                            """,
                            (
                                item.file_id,
                                str(item.source_path),
                                str(original),
                                json.dumps({"operation_id": operation_id}),
                                now,
                            ),
                        )
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        if original.exists() and not item.source_path.exists():
                            item.source_path.parent.mkdir(
                                parents=True,
                                exist_ok=True,
                            )
                            shutil.move(str(original), str(item.source_path))
                        raise
                    completed += 1
                except Exception as error:
                    message = (
                        f"{item.source_path}: {type(error).__name__}: {error}"
                    )
                    errors.append(message)
                    self._record_failure(
                        connection,
                        item=item,
                        action_type="restore_quarantine",
                        target_path=original,
                        operation_id=operation_id,
                        error=message,
                    )
                if progress_callback is not None:
                    progress_callback(
                        index,
                        len(items),
                        f"Restoring {item.source_path.name}",
                    )
            self._sync_quarantine_review_states(connection, ids)
        finally:
            connection.close()

        return FileActionSummary(
            requested_images=len(ids),
            requested_files=len(items),
            completed_files=completed,
            failed_files=len(errors),
            operation_id=operation_id,
            errors=tuple(errors),
            cancelled=cancelled,
        )

    def send_to_trash(
        self,
        image_ids: Iterable[int],
        *,
        resolved_items: Iterable[FileActionItem] | None = None,
        progress_callback: FileActionProgress | None = None,
        cancel_event: Event | None = None,
    ) -> FileActionSummary:
        """Send all present locations to native Trash/Recycle Bin.

        Importing lazily lets the application explain an incomplete or damaged
        base installation before any file is touched. Recycle Bin support is a
        standard safety dependency, but this boundary still refuses to fall
        back to permanent deletion if the package is unavailable.
        """
        try:
            from send2trash import send2trash
        except ImportError as error:
            raise RuntimeError(
                "Recycle Bin support is not installed. Run "
                "\"Setup and Launch LoRA Image Curator.bat\" and choose "
                "required app dependency repair. "
                "No files were deleted."
            ) from error

        ids = {int(image_id) for image_id in image_ids}
        items = (
            tuple(resolved_items)
            if resolved_items is not None
            else self.present_items(ids)
        )
        operation_id = uuid.uuid4().hex
        errors: list[str] = []
        completed = 0
        cancelled = False
        if progress_callback is not None:
            progress_callback(0, len(items), "Preparing Recycle Bin action…")
        connection = self._connect()
        try:
            for index, item in enumerate(items, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    if not item.source_path.is_file():
                        raise FileNotFoundError(item.source_path)
                    send2trash(str(item.source_path))
                    now = utc_now_text()
                    connection.execute(
                        """
                        UPDATE files
                        SET status = 'deleted', last_seen_at = ?
                        WHERE id = ?
                        """,
                        (now, item.file_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO file_actions (
                            file_id, action_type, source_path, target_path,
                            status, details_json, performed_at
                        )
                        VALUES (?, 'send_to_trash', ?, '', 'complete', ?, ?)
                        """,
                        (
                            item.file_id,
                            str(item.source_path),
                            json.dumps(
                                {
                                    "operation_id": operation_id,
                                    "recovery": "operating_system_trash",
                                }
                            ),
                            now,
                        ),
                    )
                    connection.commit()
                    completed += 1
                except Exception as error:
                    message = (
                        f"{item.source_path}: {type(error).__name__}: {error}"
                    )
                    errors.append(message)
                    self._record_failure(
                        connection,
                        item=item,
                        action_type="send_to_trash",
                        target_path=Path(),
                        operation_id=operation_id,
                        error=message,
                    )
                if progress_callback is not None:
                    progress_callback(
                        index,
                        len(items),
                        f"Sending {item.source_path.name} to Recycle Bin",
                    )
        finally:
            connection.close()

        return FileActionSummary(
            requested_images=len(ids),
            requested_files=len(items),
            completed_files=completed,
            failed_files=len(errors),
            operation_id=operation_id,
            errors=tuple(errors),
            cancelled=cancelled,
        )

    def image_ids_without_present_files(
        self,
        image_ids: Iterable[int],
    ) -> tuple[int, ...]:
        """Return requested catalog images with no location still marked present."""
        ids = sorted({int(image_id) for image_id in image_ids})
        if not ids:
            return ()
        connection = self._connect()
        try:
            found: list[int] = []
            for batch in _batches(ids):
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT images.id
                    FROM images
                    WHERE images.id IN ({placeholders})
                      AND NOT EXISTS (
                          SELECT 1
                          FROM files
                          WHERE files.image_id = images.id
                            AND files.status = 'present'
                      )
                    ORDER BY images.id
                    """,
                    batch,
                ).fetchall()
                found.extend(int(row[0]) for row in rows)
        finally:
            connection.close()
        return tuple(sorted(found))

    def remove_catalog_records(
        self,
        image_ids: Iterable[int],
        *,
        progress_callback: FileActionProgress | None = None,
        cancel_event: Event | None = None,
    ) -> CatalogRemovalSummary:
        """Remove complete image-owned catalog records in one transaction.

        Files on disk are never touched.  Most provider, tag, review, quality,
        and image-set rows disappear through declared ``ON DELETE CASCADE``
        relationships. File-action history must be removed before file rows
        because those rows intentionally use ``RESTRICT``. Export item history
        is removed rather than retained with a null image reference because the
        user explicitly requested that all database data for the image go away.

        Durable edit history is cleared as a unit: its JSON snapshots can
        contain the removed images and could otherwise offer an invalid Undo
        operation. The GUI creates a consistent database backup before calling
        this method.
        """
        ids = sorted({int(image_id) for image_id in image_ids})
        if not ids:
            return CatalogRemovalSummary(0, 0, 0, 0)
        if progress_callback is not None:
            progress_callback(0, len(ids), "Preparing catalog record removal…")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_ids: list[int] = []
            for batch in _batches(ids):
                if cancel_event is not None and cancel_event.is_set():
                    connection.rollback()
                    return CatalogRemovalSummary(
                        requested_images=len(ids),
                        removed_images=0,
                        missing_image_ids=0,
                        cleared_history_operations=0,
                        cancelled=True,
                    )
                placeholders = ",".join("?" for _ in batch)
                existing_ids.extend(
                    int(row[0])
                    for row in connection.execute(
                        f"SELECT id FROM images WHERE id IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
            if not existing_ids:
                connection.rollback()
                return CatalogRemovalSummary(
                    requested_images=len(ids),
                    removed_images=0,
                    missing_image_ids=len(ids),
                    cleared_history_operations=0,
                )

            processed = 0
            for batch in _batches(sorted(existing_ids)):
                if cancel_event is not None and cancel_event.is_set():
                    connection.rollback()
                    return CatalogRemovalSummary(
                        requested_images=len(ids),
                        removed_images=0,
                        missing_image_ids=0,
                        cleared_history_operations=0,
                        cancelled=True,
                    )
                placeholders = ",".join("?" for _ in batch)
                file_ids = [
                    int(row[0])
                    for row in connection.execute(
                        f"SELECT id FROM files "
                        f"WHERE image_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                ]
                for file_batch in _batches(file_ids):
                    file_placeholders = ",".join("?" for _ in file_batch)
                    connection.execute(
                        f"DELETE FROM file_actions "
                        f"WHERE file_id IN ({file_placeholders})",
                        file_batch,
                    )
                connection.execute(
                    f"DELETE FROM export_run_items "
                    f"WHERE image_id IN ({placeholders})",
                    batch,
                )
                connection.execute(
                    f"DELETE FROM files WHERE image_id IN ({placeholders})",
                    batch,
                )
                connection.execute(
                    f"DELETE FROM images WHERE id IN ({placeholders})",
                    batch,
                )
                processed += len(batch)
                if progress_callback is not None:
                    progress_callback(
                        processed,
                        len(existing_ids),
                        f"Removing catalog records ({processed:,} complete)",
                    )
            history_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_edit_operations"
                ).fetchone()[0]
            )
            connection.execute("DELETE FROM catalog_edit_operations")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return CatalogRemovalSummary(
            requested_images=len(ids),
            removed_images=len(existing_ids),
            missing_image_ids=len(ids) - len(existing_ids),
            cleared_history_operations=history_count,
        )

    def _items(
        self,
        image_ids: Iterable[int],
        *,
        status: str,
    ) -> tuple[FileActionItem, ...]:
        ids = sorted({int(image_id) for image_id in image_ids})
        if not ids:
            return ()
        connection = self._connect()
        try:
            rows: list[sqlite3.Row] = []
            for batch in _batches(ids):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    connection.execute(
                        f"""
                        SELECT
                            image_id, id AS file_id, absolute_path,
                            relative_path, status
                        FROM files
                        WHERE image_id IN ({placeholders})
                          AND status = ?
                        ORDER BY image_id, id
                        """,
                        (*batch, status),
                    ).fetchall()
                )
        finally:
            connection.close()
        rows.sort(key=lambda row: (int(row["image_id"]), int(row["file_id"])))
        return tuple(
            FileActionItem(
                image_id=int(row["image_id"]),
                file_id=int(row["file_id"]),
                source_path=Path(str(row["absolute_path"])).expanduser().resolve(),
                relative_path=str(row["relative_path"] or ""),
                status=str(row["status"]),
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _sync_quarantine_review_states(
        connection: sqlite3.Connection,
        image_ids: set[int],
    ) -> None:
        """Mirror complete quarantine state into the existing review field.

        A partially moved image remains in its prior review state because at
        least one present source still exists. Restoring a fully quarantined
        image returns only the automatic ``quarantined`` state to ``unreviewed``;
        explicit Keep/Reject/Needs-follow-up decisions are never overwritten.
        """
        now = utc_now_text()
        for image_id in image_ids:
            present = connection.execute(
                """
                SELECT 1 FROM files
                WHERE image_id = ? AND status = 'present'
                LIMIT 1
                """,
                (image_id,),
            ).fetchone()
            quarantined = connection.execute(
                """
                SELECT 1 FROM files
                WHERE image_id = ? AND status = 'quarantined'
                LIMIT 1
                """,
                (image_id,),
            ).fetchone()
            if quarantined is not None and present is None:
                connection.execute(
                    """
                    INSERT INTO image_review_state (
                        image_id, status, notes, updated_at
                    )
                    VALUES (?, 'quarantined', '', ?)
                    ON CONFLICT(image_id)
                    DO UPDATE SET status = 'quarantined', updated_at = excluded.updated_at
                    """,
                    (image_id, now),
                )
            elif present is not None:
                connection.execute(
                    """
                    UPDATE image_review_state
                    SET status = 'unreviewed', updated_at = ?
                    WHERE image_id = ? AND status = 'quarantined'
                    """,
                    (now, image_id),
                )
        connection.commit()

    @staticmethod
    def _record_successful_move(
        connection: sqlite3.Connection,
        *,
        item: FileActionItem,
        target: Path,
        operation_id: str,
    ) -> None:
        now = utc_now_text()
        connection.execute(
            """
            UPDATE files
            SET absolute_path = ?,
                path_key = ?,
                status = 'quarantined',
                last_seen_at = ?
            WHERE id = ?
            """,
            (str(target), normalize_path_key(target), now, item.file_id),
        )
        connection.execute(
            """
            INSERT INTO file_actions (
                file_id, action_type, source_path, target_path,
                status, details_json, performed_at
            )
            VALUES (?, 'quarantine', ?, ?, 'complete', ?, ?)
            """,
            (
                item.file_id,
                str(item.source_path),
                str(target),
                json.dumps({"operation_id": operation_id}),
                now,
            ),
        )
        connection.commit()

    @staticmethod
    def _record_failure(
        connection: sqlite3.Connection,
        *,
        item: FileActionItem,
        action_type: str,
        target_path: Path,
        operation_id: str,
        error: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO file_actions (
                file_id, action_type, source_path, target_path,
                status, details_json, performed_at
            )
            VALUES (?, ?, ?, ?, 'failed', ?, ?)
            """,
            (
                item.file_id,
                action_type,
                str(item.source_path),
                str(target_path) if str(target_path) != "." else "",
                json.dumps(
                    {
                        "operation_id": operation_id,
                        "error": error,
                    }
                ),
                utc_now_text(),
            ),
        )
        connection.commit()


def _quarantine_target(
    operation_root: Path,
    item: FileActionItem,
) -> Path:
    """Build a safe target while retaining useful relative-folder context."""
    relative = Path(item.relative_path)
    safe_parts = [
        part
        for part in relative.parts
        if part not in {"", ".", ".."} and not Path(part).is_absolute()
    ]
    if not safe_parts:
        safe_parts = [item.source_path.name]
    target = operation_root.joinpath(*safe_parts)
    if target.name != item.source_path.name:
        target = target / item.source_path.name
    if not target.exists():
        return target
    counter = 2
    while True:
        candidate = target.with_name(
            f"{target.stem} ({counter}){target.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def _batches(values: list[int]) -> Iterable[list[int]]:
    """Yield bounded SQL parameter batches for very large selections."""
    for index in range(0, len(values), SQL_BATCH_SIZE):
        yield values[index : index + SQL_BATCH_SIZE]
