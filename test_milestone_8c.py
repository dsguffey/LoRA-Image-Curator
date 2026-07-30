"""Dependency-light regression tests for Milestone 8C image sets.

The suite uses temporary SQLite catalogs only. It verifies additive schema
migration, deliberate set CRUD, membership integrity, browser/search
projection, set-scoped readiness inputs, and non-destructive set deletion.
"""

from __future__ import annotations

import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from advanced_search import record_matches_query
from catalog import Catalog, SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository
from dataset_readiness import build_readiness_report
from image_sets import ImageSetRepository


def _insert_catalog_images(database: Path, count: int = 3) -> None:
    """Insert minimal valid catalog rows without invoking an analysis model."""
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for index in range(1, count + 1):
            connection.execute(
                """
                INSERT INTO images(
                    id, content_sha256, byte_size, width, height,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (index, f"{index:064x}", 1024 * index, 1024, 1536),
            )
            connection.execute(
                """
                INSERT INTO files(
                    image_id, path_key, absolute_path, input_root, input_root_key,
                    relative_path, byte_size, modified_time_ns, status,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    index,
                    f"path-{index}",
                    f"C:/dataset/image_{index}.jpg",
                    "C:/dataset",
                    "dataset-root",
                    f"image_{index}.jpg",
                    1024 * index,
                    index,
                ),
            )


def run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        with Catalog(database):
            pass
        _insert_catalog_images(database)

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='image_sets'"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='image_set_members'"
            ).fetchone()[0] == 1

        repository = ImageSetRepository(database)
        created = repository.create_set("Training Candidates", (1, 2))
        assert created.image_count == 2
        assert repository.get_image_ids(created.set_id) == (1, 2)
        assert repository.add_images(created.set_id, (2, 3)) == 1
        assert repository.get_image_ids(created.set_id) == (1, 2, 3)
        assert repository.remove_images(created.set_id, (1, 99)) == 1
        assert repository.get_image_ids(created.set_id) == (2, 3)

        renamed = repository.rename_set(created.set_id, "Final Training Set")
        assert renamed.name == "Final Training Set"
        assert renamed.image_count == 2

        try:
            repository.create_set("final training set")
        except ValueError as error:
            assert "already exists" in str(error)
        else:
            raise AssertionError("Image-set names must be case-insensitively unique")

        try:
            repository.create_set("Invalid Selection", (999,))
        except ValueError as error:
            assert "no longer present" in str(error)
        else:
            raise AssertionError("A set with a missing catalog image must not commit")
        assert [item.name for item in repository.list_sets()] == ["Final Training Set"]

        records = CatalogBrowserRepository(database).fetch_records()
        by_id = {record.image_id: record for record in records}
        assert not record_matches_query(by_id[1], 'set:"Final Training Set"')
        assert record_matches_query(by_id[2], 'set:"Final Training Set"')
        assert record_matches_query(by_id[3], "set:final_training_set")
        assert record_matches_query(by_id[1], "set:missing")

        scoped = [record for record in records if record.image_id in {2, 3}]
        report = build_readiness_report(scoped)
        assert report.total_images == 2

        assert repository.delete_set(created.set_id)
        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 3
            assert connection.execute("SELECT COUNT(*) FROM image_sets").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM image_set_members").fetchone()[0] == 0

        # Recreate a schema-8 shape and prove the additive migration restores
        # only the new tables without rewriting image records.
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DROP TABLE image_set_members")
            connection.execute("DROP TABLE image_sets")
            connection.execute("PRAGMA user_version = 8")
        with Catalog(database):
            pass
        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 3
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='image_sets'"
            ).fetchone()[0] == 1

    print(
        "Milestone 8C tests passed: schema 9 image sets, CRUD, memberships, "
        "set search, scoped readiness input, and non-destructive deletion."
    )


if __name__ == "__main__":
    run()
