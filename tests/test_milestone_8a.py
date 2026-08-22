"""Dependency-free regression tests for Milestone 8A.

The tests cover the pure query/readiness models, schema migration, explicit
saved searches, and privacy-controlled application history. They use only
temporary catalogs and never inspect or modify a real dataset.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from advanced_search import SearchClause, build_search_query, record_matches_query
from catalog import Catalog, SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository
from dataset_readiness import build_readiness_report
from settings_manager import AppSettings, load_settings, save_settings


def _record(**overrides):
    values = {
        "image_id": 1,
        "filename": "portrait.jpg",
        "relative_path": "portraits/portrait.jpg",
        "absolute_path": "C:/dataset/portraits/portrait.jpg",
        "search_blob": "portrait.jpg\nsmiling\nred dress\ngal_gadot",
        "manual_tags": "red dress, studio",
        "ai_tags_active": "smiling, woman",
        "ai_tags_excluded": "outdoors",
        "manual_keyword": "gal_gadot",
        "caption": "A smiling woman in a studio.",
        "review_status": "keep",
        "file_status": "present",
        "suggested_identity": "Gal Gadot",
        "identity_review_status": "confirmed",
        "face_count": 1,
        "width": 1024,
        "height": 1536,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def run() -> None:
    good = _record()
    weak = _record(
        image_id=2,
        filename="small.jpg",
        search_blob="small.jpg\nforest",
        manual_tags="",
        ai_tags_active="",
        ai_tags_excluded="",
        manual_keyword="",
        caption="",
        review_status="unreviewed",
        file_status="missing",
        suggested_identity="Gal Gadot",
        identity_review_status="suggested",
        face_count=2,
        width=320,
        height=480,
    )

    # Typed and dialog-built queries share one parser with explicit precedence.
    assert record_matches_query(good, "trigger:gal_gadot AND review:keep")
    assert record_matches_query(good, "manual:red_dress OR ai:outdoors")
    assert record_matches_query(good, "ai:smiling NOT excluded:smiling")
    assert record_matches_query(good, "manual:studio -excluded:studio")
    assert not record_matches_query(good, "review:reject OR file:missing")
    assert record_matches_query(weak, "(trigger:missing OR file:missing) AND NOT review:keep")
    assert record_matches_query(weak, "identity:multiple_faces resolution:low")
    built = build_search_query(
        (
            SearchClause("Trigger Keyword", "gal_gadot"),
            SearchClause("Excluded AI tag", "smiling", excluded=True),
        )
    )
    assert built == "trigger:gal_gadot AND NOT excluded:smiling"
    assert record_matches_query(good, built)

    report = build_readiness_report((good, weak))
    assert report.total_images == 2
    assert report.eligible_images == 2
    assert report.score < 100
    assert next(issue for issue in report.issues if issue.label == "Missing Files").count == 1
    assert next(issue for issue in report.issues if issue.label == "Multiple Faces").deduction == 0
    assert report.top_trigger_keywords == (("gal_gadot", 1),)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        with Catalog(database):
            pass
        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert SCHEMA_VERSION >= 12
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='saved_searches'"
            ).fetchone()[0] == 1

        repository = CatalogBrowserRepository(database)
        saved = repository.save_named_search("Export Ready", "review:keep AND file:present")
        assert repository.list_saved_searches() == [saved]
        replaced = repository.save_named_search("export ready", "review:keep")
        assert replaced.search_id == saved.search_id
        assert replaced.query == "review:keep"
        assert repository.delete_saved_search(saved.search_id)
        assert repository.list_saved_searches() == []

        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root / "appdata")
        try:
            settings = AppSettings(
                browser_search_history_enabled=False,
                browser_search_history_max=3,
                browser_search_history=["trigger:one", "review:keep"],
            )
            save_settings(settings)
            loaded = load_settings()
            assert not loaded.browser_search_history_enabled
            assert loaded.browser_search_history_max == 3
            assert loaded.browser_search_history == ["trigger:one", "review:keep"]
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

    print(
        "Milestone 8A tests passed: advanced Boolean search, query builder, "
        "readiness scoring, saved searches, and configurable history."
    )


if __name__ == "__main__":
    run()
