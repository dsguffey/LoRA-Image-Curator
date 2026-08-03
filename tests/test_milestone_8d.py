"""Regression tests for Milestone 8D catalog import and management.

The suite uses temporary images and catalogs only.  It verifies the durable
service layer independently of Tk so Windows users do not have to perform these
data-integrity checks manually.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path
from threading import Event

from PIL import Image

from catalog import SCHEMA_VERSION
from catalog_import import (
    CatalogImportCancelled,
    CatalogImportOptions,
    discover_image_files,
    format_import_summary,
    import_catalog_folder,
)
from image_sets import ImageSetRepository


def _make_image(path: Path, color: tuple[int, int, int], size: tuple[int, int]) -> None:
    """Create a small valid image fixture whose bytes are safe to hash/import."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_counts(database: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(database)) as connection:
        unique_images = int(connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])
        file_locations = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    return unique_images, file_locations


def test_create_nonrecursive_duplicate_and_set(root: Path) -> Path:
    source = root / "first_import"
    primary = source / "portrait.jpg"
    duplicate = source / "portrait-copy.jpg"
    nested = source / "nested" / "ignored.png"
    _make_image(primary, (180, 30, 30), (80, 60))
    shutil.copyfile(primary, duplicate)
    _make_image(nested, (20, 120, 190), (50, 70))
    source_hashes_before = {_sha256(path) for path in (primary, duplicate, nested)}

    discovered = discover_image_files(source, recursive=False)
    assert discovered == sorted((primary.resolve(), duplicate.resolve()))

    database = root / "catalog" / "dataset_tools.db"
    summary = import_catalog_folder(
        CatalogImportOptions(
            source_folder=source,
            target_database=database,
            mode="create",
            recursive=False,
            create_image_set=True,
            image_set_name="First candidates",
        )
    )
    assert summary.discovered_files == 2
    assert summary.cataloged_files == 2
    assert summary.new_unique_images == 1
    assert summary.exact_duplicate_files == 1
    assert summary.duplicate_sha256_values == (_sha256(primary),)
    assert _catalog_counts(database) == (1, 2)
    assert {_sha256(path) for path in (primary, duplicate, nested)} == source_hashes_before

    sets = ImageSetRepository(database).list_sets()
    assert [(item.name, item.image_count) for item in sets] == [("First candidates", 1)]

    with closing(sqlite3.connect(database)) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        dimensions = connection.execute("SELECT width, height FROM images").fetchone()
        assert dimensions == (80, 60)

    report = format_import_summary(summary)
    assert "Exact SHA-256 duplicates skipped as additional images: 1" in report
    assert _sha256(primary) in report
    return database


def test_merge_preserves_content_and_avoids_set_name_collision(root: Path, database: Path) -> None:
    source = root / "merge_import"
    source.mkdir()
    existing_duplicate = source / "same-content.jpg"
    new_image = source / "new-image.png"
    shutil.copyfile(root / "first_import" / "portrait.jpg", existing_duplicate)
    _make_image(new_image, (15, 210, 70), (96, 64))

    summary = import_catalog_folder(
        CatalogImportOptions(
            source_folder=source,
            target_database=database,
            mode="merge",
            recursive=True,
            create_image_set=True,
            image_set_name="First candidates",
        )
    )
    assert summary.new_unique_images == 1
    assert summary.exact_duplicate_files == 1
    assert summary.image_set_name == "First candidates (2)"
    assert _catalog_counts(database) == (2, 4)
    assert [(item.name, item.image_count) for item in ImageSetRepository(database).list_sets()] == [
        ("First candidates", 1),
        ("First candidates (2)", 2),
    ]


def test_cancel_does_not_publish_staging_changes(root: Path, database: Path) -> None:
    source = root / "cancelled_import"
    _make_image(source / "cancel.jpg", (40, 40, 220), (30, 30))
    database_hash_before = _sha256(database)
    cancel_event = Event()
    cancel_event.set()
    try:
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=source,
                target_database=database,
                mode="merge",
            ),
            cancel_event=cancel_event,
        )
    except CatalogImportCancelled:
        pass
    else:
        raise AssertionError("A pre-cancelled import must not publish a catalog")
    assert _sha256(database) == database_hash_before
    assert not list(database.parent.glob(".*.import-*.tmp*"))


def test_replace_removes_old_catalog_owned_content(root: Path, database: Path) -> None:
    replacement = root / "replacement"
    _make_image(replacement / "replacement.webp", (100, 80, 220), (120, 90))
    summary = import_catalog_folder(
        CatalogImportOptions(
            source_folder=replacement,
            target_database=database,
            mode="replace",
            recursive=True,
            create_image_set=True,
            image_set_name="Replacement",
        )
    )
    assert summary.mode == "replace"
    assert _catalog_counts(database) == (1, 1)
    assert [(item.name, item.image_count) for item in ImageSetRepository(database).list_sets()] == [
        ("Replacement", 1)
    ]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_invalid_supported_file_is_reported_not_cataloged(root: Path) -> None:
    source = root / "invalid_import"
    source.mkdir()
    invalid = source / "broken.jpg"
    invalid.write_bytes(b"not a JPEG")
    database = root / "invalid_catalog" / "dataset_tools.db"
    summary = import_catalog_folder(
        CatalogImportOptions(
            source_folder=source,
            target_database=database,
            mode="create",
            create_image_set=True,
        )
    )
    assert summary.discovered_files == 1
    assert summary.cataloged_files == 0
    assert len(summary.failed_files) == 1
    assert summary.image_set_name == ""
    assert _catalog_counts(database) == (0, 0)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8d_") as temporary:
        root = Path(temporary)
        database = test_create_nonrecursive_duplicate_and_set(root)
        test_merge_preserves_content_and_avoids_set_name_collision(root, database)
        test_cancel_does_not_publish_staging_changes(root, database)
        test_replace_removes_old_catalog_owned_content(root, database)
        test_invalid_supported_file_is_reported_not_cataloged(root)
    print(
        "Milestone 8D tests passed: staged create/merge/replace, recursion, "
        "SHA-256 duplicate reporting, image sets, cancellation, and integrity."
    )


if __name__ == "__main__":
    main()
