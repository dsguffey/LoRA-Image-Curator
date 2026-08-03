"""Small dependency-free regression tests for Milestone 6B."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from catalog_browser import CatalogBrowserRepository
from catalog_edits import CatalogEditService


def run(source_catalog: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        test_db = Path(temporary) / "dataset_tools.db"
        shutil.copy2(source_catalog, test_db)
        records = CatalogBrowserRepository(test_db).fetch_records()
        assert records, "Test catalog must contain at least one image"
        assert all(record.first_seen_at for record in records)

        chosen = [record.image_id for record in records[: min(3, len(records))]]
        untouched = records[-1].image_id if records[-1].image_id not in chosen else None
        service = CatalogEditService(test_db)
        service.set_manual_keyword(chosen, "test_subject")
        service.set_review_state(chosen, "keep", "Milestone 6B regression test")

        # ``sqlite3.Connection.__exit__`` commits or rolls back but does not
        # close the handle.  The explicit ``closing`` wrapper is essential on
        # Windows, where an open handle prevents TemporaryDirectory cleanup.
        with closing(sqlite3.connect(test_db)) as connection, connection:
            tagged = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT it.image_id
                    FROM image_tags it JOIN tags t ON t.id = it.tag_id
                    WHERE t.normalized_name = 'test_subject'
                      AND t.category = 'set_keyword'
                      AND it.source = 'manual'
                      AND it.review_status = 'confirmed'
                    """
                )
            }
            assert tagged == set(chosen)
            reviewed = {
                row[0]
                for row in connection.execute(
                    "SELECT image_id FROM image_review_state WHERE status = 'keep'"
                )
            }
            assert set(chosen).issubset(reviewed)
            if untouched is not None:
                assert untouched not in tagged

        reopened = CatalogBrowserRepository(test_db).fetch_records()
        by_id = {record.image_id: record for record in reopened}
        assert all(by_id[image_id].has_manual_metadata for image_id in chosen)
        print(f"Milestone 6B tests passed for {len(records)} catalog records.")


if __name__ == "__main__":
    import sys
    run(Path(sys.argv[1]))
