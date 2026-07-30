"""Persistent named image sets for catalog-scoped workflows.

Image sets are a deliberate user artifact rather than implicit activity. The
repository therefore persists names and memberships, but it does not remember
which set was last viewed or mirror the browser's temporary selection. Every
operation is catalog-local and changes no source image, analysis result, tag,
review decision, or exported file.
"""

from __future__ import annotations

import sqlite3

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from catalog import Catalog, utc_now_text


MAX_IMAGE_SET_NAME_LENGTH = 120


@dataclass(slots=True, frozen=True)
class ImageSetSummary:
    """One named set and its current membership count."""

    set_id: int
    name: str
    image_count: int
    created_at: str
    updated_at: str


class ImageSetRepository:
    """Apply short, transactional image-set operations to one catalog."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    def list_sets(self) -> list[ImageSetSummary]:
        """Return sets alphabetically with counts derived from memberships."""
        self._ensure_catalog()
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT
                    image_sets.id,
                    image_sets.name,
                    image_sets.created_at,
                    image_sets.updated_at,
                    COUNT(image_set_members.image_id) AS image_count
                FROM image_sets
                LEFT JOIN image_set_members
                    ON image_set_members.image_set_id = image_sets.id
                GROUP BY image_sets.id
                ORDER BY image_sets.name COLLATE NOCASE, image_sets.id
                """
            ).fetchall()
        return [
            ImageSetSummary(
                set_id=int(row["id"]),
                name=str(row["name"]),
                image_count=int(row["image_count"] or 0),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def create_set(self, name: str, image_ids: Iterable[int] = ()) -> ImageSetSummary:
        """Create a set and atomically add every valid requested catalog image."""
        clean_name = normalize_image_set_name(name)
        ids = _normalize_image_ids(image_ids)
        self._ensure_catalog()
        timestamp = utc_now_text()
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO image_sets(name, description, created_at, updated_at)
                    VALUES (?, '', ?, ?)
                    """,
                    (clean_name, timestamp, timestamp),
                )
                set_id = int(cursor.lastrowid)
                self._insert_members(connection, set_id, ids, timestamp)
        except sqlite3.IntegrityError as error:
            if "image_sets.name" in str(error):
                raise ValueError(f'An image set named "{clean_name}" already exists.') from error
            raise
        return self.get_set(set_id)

    def get_set(self, set_id: int) -> ImageSetSummary:
        """Return one set or raise a clear error after concurrent deletion."""
        match = next((item for item in self.list_sets() if item.set_id == int(set_id)), None)
        if match is None:
            raise ValueError("The selected image set no longer exists.")
        return match

    def get_image_ids(self, set_id: int) -> tuple[int, ...]:
        """Return member IDs in stable insertion order."""
        self._ensure_catalog()
        with closing(self._connect()) as connection, connection:
            exists = connection.execute(
                "SELECT 1 FROM image_sets WHERE id = ?", (int(set_id),)
            ).fetchone()
            if exists is None:
                raise ValueError("The selected image set no longer exists.")
            rows = connection.execute(
                """
                SELECT image_id
                FROM image_set_members
                WHERE image_set_id = ?
                ORDER BY added_at, image_id
                """,
                (int(set_id),),
            ).fetchall()
        return tuple(int(row["image_id"]) for row in rows)

    def rename_set(self, set_id: int, name: str) -> ImageSetSummary:
        """Rename one set without changing membership or implicit UI state."""
        self._ensure_catalog()
        clean_name = normalize_image_set_name(name)
        timestamp = utc_now_text()
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    "UPDATE image_sets SET name = ?, updated_at = ? WHERE id = ?",
                    (clean_name, timestamp, int(set_id)),
                )
                if cursor.rowcount != 1:
                    raise ValueError("The selected image set no longer exists.")
        except sqlite3.IntegrityError as error:
            if "image_sets.name" in str(error):
                raise ValueError(f'An image set named "{clean_name}" already exists.') from error
            raise
        return self.get_set(set_id)

    def replace_images(
        self,
        set_id: int,
        image_ids: Iterable[int],
    ) -> ImageSetSummary:
        """Make one set exactly match a deliberate browser selection.

        Membership validation, additions, removals, and the timestamp update
        share one transaction.  Existing memberships are retained where
        possible so stable insertion order is not rewritten unnecessarily.
        """
        desired_ids = _normalize_image_ids(image_ids)
        desired_set = set(desired_ids)
        self._ensure_catalog()
        timestamp = utc_now_text()
        with closing(self._connect()) as connection, connection:
            self._require_set(connection, set_id)
            existing_ids = {
                int(row["image_id"])
                for row in connection.execute(
                    """
                    SELECT image_id
                    FROM image_set_members
                    WHERE image_set_id = ?
                    """,
                    (int(set_id),),
                ).fetchall()
            }
            if existing_ids != desired_set:
                # Validate every desired image before removing an existing member.
                self._insert_members(
                    connection,
                    int(set_id),
                    desired_ids,
                    timestamp,
                )
                if desired_ids:
                    placeholders = ",".join("?" for _ in desired_ids)
                    connection.execute(
                        f"""
                        DELETE FROM image_set_members
                        WHERE image_set_id = ?
                          AND image_id NOT IN ({placeholders})
                        """,
                        (int(set_id), *desired_ids),
                    )
                else:
                    connection.execute(
                        "DELETE FROM image_set_members WHERE image_set_id = ?",
                        (int(set_id),),
                    )
                connection.execute(
                    "UPDATE image_sets SET updated_at = ? WHERE id = ?",
                    (timestamp, int(set_id)),
                )
        return self.get_set(set_id)

    def add_images(self, set_id: int, image_ids: Iterable[int]) -> int:
        """Add valid images idempotently and return the number newly added."""
        self._ensure_catalog()
        ids = _normalize_image_ids(image_ids)
        if not ids:
            return 0
        timestamp = utc_now_text()
        with closing(self._connect()) as connection, connection:
            self._require_set(connection, set_id)
            before = connection.total_changes
            self._insert_members(connection, int(set_id), ids, timestamp)
            added = connection.total_changes - before
            if added:
                connection.execute(
                    "UPDATE image_sets SET updated_at = ? WHERE id = ?",
                    (timestamp, int(set_id)),
                )
        return int(added)

    def remove_images(self, set_id: int, image_ids: Iterable[int]) -> int:
        """Remove requested memberships while preserving images and the set."""
        self._ensure_catalog()
        ids = _normalize_image_ids(image_ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        timestamp = utc_now_text()
        with closing(self._connect()) as connection, connection:
            self._require_set(connection, set_id)
            cursor = connection.execute(
                f"DELETE FROM image_set_members WHERE image_set_id = ? "
                f"AND image_id IN ({placeholders})",
                (int(set_id), *ids),
            )
            removed = int(cursor.rowcount)
            if removed:
                connection.execute(
                    "UPDATE image_sets SET updated_at = ? WHERE id = ?",
                    (timestamp, int(set_id)),
                )
        return removed

    def delete_set(self, set_id: int) -> bool:
        """Delete a set and its memberships, never the referenced images."""
        self._ensure_catalog()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM image_sets WHERE id = ?", (int(set_id),)
            )
        return cursor.rowcount == 1

    def _ensure_catalog(self) -> None:
        if not self.database_path.exists():
            raise FileNotFoundError(f"Catalog not found: {self.database_path}")
        with Catalog(self.database_path):
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _require_set(connection: sqlite3.Connection, set_id: int) -> None:
        if connection.execute(
            "SELECT 1 FROM image_sets WHERE id = ?", (int(set_id),)
        ).fetchone() is None:
            raise ValueError("The selected image set no longer exists.")

    @staticmethod
    def _insert_members(
        connection: sqlite3.Connection,
        set_id: int,
        image_ids: tuple[int, ...],
        timestamp: str,
    ) -> None:
        if not image_ids:
            return
        placeholders = ",".join("?" for _ in image_ids)
        valid_rows = connection.execute(
            f"SELECT id FROM images WHERE id IN ({placeholders})",
            image_ids,
        ).fetchall()
        valid_ids = {int(row["id"]) for row in valid_rows}
        missing = [image_id for image_id in image_ids if image_id not in valid_ids]
        if missing:
            raise ValueError(
                "One or more selected images are no longer present in the catalog."
            )
        connection.executemany(
            """
            INSERT OR IGNORE INTO image_set_members(image_set_id, image_id, added_at)
            VALUES (?, ?, ?)
            """,
            ((int(set_id), image_id, timestamp) for image_id in image_ids),
        )


def normalize_image_set_name(name: str) -> str:
    """Normalize display whitespace while preserving the user's capitalization."""
    clean_name = " ".join(str(name).split()).strip()
    if not clean_name:
        raise ValueError("Image set name cannot be blank.")
    if len(clean_name) > MAX_IMAGE_SET_NAME_LENGTH:
        raise ValueError(
            f"Image set names are limited to {MAX_IMAGE_SET_NAME_LENGTH} characters."
        )
    return clean_name


def _normalize_image_ids(image_ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({int(image_id) for image_id in image_ids}))
