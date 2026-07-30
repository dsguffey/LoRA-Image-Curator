"""Explicit creation, validation, and deletion of application catalogs.

These functions deliberately operate on the SQLite catalog and its SQLite
sidecars only. Source images, exports, model files, and thumbnail caches are
outside the deletion boundary. The GUI is responsible for naming the exact
target and obtaining confirmation before calling ``delete_catalog_database``.

The validator accepts both the current LoRA Image Curator catalog marker and
the historical LoRA Image Curator marker. This keeps the rename backward compatible
without weakening the table/schema identity check used before replacement or
deletion.
"""

from __future__ import annotations

import os
import sqlite3

from contextlib import closing, suppress
from pathlib import Path
from uuid import uuid4

from app_identity import APP_NAME, SUPPORTED_CATALOG_APPLICATION_IDS
from catalog import Catalog


REQUIRED_CATALOG_TABLES = {
    "catalog_metadata",
    "images",
    "files",
    "analysis_results",
}


def create_catalog_database(database_path: Path) -> Path:
    """Create and return a new empty catalog, refusing to overwrite any file."""
    target = database_path.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"Catalog already exists: {target}")
    with Catalog(target):
        pass
    return target


def replace_catalog_database_with_empty(database_path: Path) -> Path:
    """Atomically replace one confirmed application catalog with an empty one.

    The GUI must obtain explicit confirmation before calling this function.
    Building and validating a uniquely named staging catalog first preserves the
    existing database if creation fails.  Source images and exports are outside
    this operation's ownership boundary.
    """
    target = validate_catalog_database(database_path)
    staging = target.with_name(f".{target.name}.empty-{uuid4().hex}.tmp")
    try:
        with Catalog(staging):
            pass
        validate_catalog_database(staging)

        # Fold any committed write-ahead-log frames into the old database before
        # removing its exact SQLite sidecars.  If publication then fails, the old
        # main database is still complete and valid.
        with closing(sqlite3.connect(target, timeout=30.0)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode = DELETE")
        for suffix in ("-wal", "-shm"):
            with suppress(FileNotFoundError):
                Path(str(target) + suffix).unlink()
        os.replace(staging, target)
        return target
    finally:
        for candidate in (
            staging,
            Path(str(staging) + "-wal"),
            Path(str(staging) + "-shm"),
        ):
            with suppress(FileNotFoundError):
                candidate.unlink()


def validate_catalog_database(database_path: Path) -> Path:
    """Return a resolved path only for a current or legacy application catalog."""
    target = database_path.expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Catalog not found: {target}")

    connection = sqlite3.connect(target, timeout=10.0)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not REQUIRED_CATALOG_TABLES.issubset(tables):
            raise ValueError(
                f"The selected SQLite file is not a {APP_NAME} catalog."
            )
        application_row = connection.execute(
            "SELECT value FROM catalog_metadata WHERE key = 'application'"
        ).fetchone()
        if (
            application_row is None
            or str(application_row[0]) not in SUPPORTED_CATALOG_APPLICATION_IDS
        ):
            raise ValueError(
                f"The selected database does not identify itself as a {APP_NAME} "
                "or compatible legacy catalog."
            )
    finally:
        connection.close()
    return target


def delete_catalog_database(database_path: Path) -> tuple[Path, ...]:
    """Delete a validated catalog plus its exact SQLite WAL/SHM derivatives."""
    target = validate_catalog_database(database_path)
    targets = (target, Path(str(target) + "-wal"), Path(str(target) + "-shm"))
    removed: list[Path] = []
    for candidate in targets:
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    return tuple(removed)
