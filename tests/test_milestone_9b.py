"""Dependency-light regressions for Milestone 9B workflow polish.

These checks concentrate on boundaries that can be verified without a display:

* confirmed catalog replacement is staged and atomic;
* unconfirmed ``create`` operations still refuse existing catalogs;
* the old catalog is replaced without modifying either source folder;
* Exact Copies no longer appears as a second duplicate-review control; and
* the backup path that caused the v0.18.0 ResourceWarning closes both SQLite
  connections explicitly.
"""

from __future__ import annotations

import gc
import sqlite3
import tempfile
import warnings

from contextlib import closing
from pathlib import Path

from PIL import Image

from catalog_browser import CatalogBrowserRepository
from catalog_edits import CatalogEditService
from catalog_import import CatalogImportOptions, import_catalog_folder
from catalog_lifecycle import (
    replace_catalog_database_with_empty,
    validate_catalog_database,
)
from dataset_readiness import build_readiness_report


def _make_image(path: Path, color: str) -> bytes:
    """Create one deterministic fixture and return its original bytes."""
    Image.new("RGB", (96, 128), color).save(path, format="PNG")
    return path.read_bytes()


def _image_count(database: Path) -> int:
    with closing(sqlite3.connect(database)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_9b_") as temporary:
        root = Path(temporary)
        first_source = root / "first_source"
        first_source.mkdir()
        first_image = first_source / "first.png"
        first_bytes = _make_image(first_image, "#6F85A3")

        database = root / "catalog" / "dataset_tools.db"
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=first_source,
                target_database=database,
                mode="create",
                create_image_set=False,
            )
        )
        assert _image_count(database) == 1

        # The backend must never infer destructive permission merely because a
        # target exists. The GUI records confirmation through this explicit
        # option, and the staged importer validates the old catalog first.
        try:
            import_catalog_folder(
                CatalogImportOptions(
                    source_folder=first_source,
                    target_database=database,
                    mode="create",
                    create_image_set=False,
                )
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("Unconfirmed create unexpectedly replaced a catalog")

        second_source = root / "second_source"
        second_source.mkdir()
        second_image = second_source / "second.png"
        second_bytes = _make_image(second_image, "#A36F85")
        summary = import_catalog_folder(
            CatalogImportOptions(
                source_folder=second_source,
                target_database=database,
                mode="create",
                create_image_set=False,
                overwrite_existing=True,
            )
        )
        assert summary.mode == "create"
        assert summary.new_unique_images == 1
        records = CatalogBrowserRepository(database).fetch_records()
        assert [record.filename for record in records] == ["second.png"]
        assert first_image.read_bytes() == first_bytes
        assert second_image.read_bytes() == second_bytes

        # Creating an empty replacement uses the same ownership boundary:
        # catalog metadata is replaced, while both image sources remain intact.
        replace_catalog_database_with_empty(database)
        validate_catalog_database(database)
        assert _image_count(database) == 0
        assert first_image.read_bytes() == first_bytes
        assert second_image.read_bytes() == second_bytes

        # Re-import one record so readiness and backup cleanup can be checked.
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=second_source,
                target_database=database,
                mode="create",
                create_image_set=False,
                overwrite_existing=True,
            )
        )
        report = build_readiness_report(
            CatalogBrowserRepository(database).fetch_records(),
            profile_key="flux_character_lora",
        )
        assert all(issue.label != "Exact Copies" for issue in report.issues)
        assert any(issue.label == "Possible Duplicates" for issue in report.issues)

        backup = root / "explicit_backup.db"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            edit_service = CatalogEditService(database)
            edit_service.set_manual_keyword((1,), "resource_test")
            assert edit_service.get_last_undoable_operation() is None
            assert edit_service.get_next_redoable_operation() is None
            created = edit_service.create_backup(backup)
            gc.collect()
        assert created == backup
        assert validate_catalog_database(backup) == backup.resolve()
        sqlite_warnings = [
            warning
            for warning in caught
            if issubclass(warning.category, ResourceWarning)
            and "sqlite3.Connection" in str(warning.message)
        ]
        assert not sqlite_warnings, sqlite_warnings

    print(
        "Milestone 9B tests passed: confirmed catalog replacement is staged, "
        "unconfirmed overwrite remains blocked, source images stay untouched, "
        "Similarity Match is the single duplicate-review control, and SQLite "
        "edit/backup connections close without ResourceWarnings."
    )


if __name__ == "__main__":
    run()
