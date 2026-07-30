"""Focused v0.27.2 regressions for stress-test usability and data safety.

The tests avoid native provider inference and a graphical display. They protect
the contracts most likely to cause costly mistakes on a large catalog:
page-relative destructive confirmation, fresh backups before record removal,
complete cascading cleanup, durable video-origin metadata, honest resumed ETA,
and cooperative pause/resume.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from analysis_control import wait_if_paused
from analysis_progress import WorkflowProgressTracker
from browser_workflow import matches_catalog_state
from catalog import Catalog, ImportRunCounts, SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository
from catalog_edits import CatalogEditService
from file_actions import FileActionService
from ui_theme import AppTheme
from video_origin import (
    VideoOriginManifestCache,
    format_video_timestamp,
    update_video_origin_manifest,
)


def _register_image(database: Path, image_path: Path) -> tuple[int, int]:
    with Catalog(database) as catalog:
        run_id = catalog.start_import_run(
            input_root=image_path.parent,
            output_folder=database.parent,
            model_name="fixture",
            transformers_version="fixture",
            analysis_version=0,
            include_triage=False,
            reuse_stored_analysis=False,
        )
        registration = catalog.register_file(
            file_path=image_path,
            input_root=image_path.parent,
            run_id=run_id,
        )
        catalog.finish_import_run(
            run_id,
            status="complete",
            counts=ImportRunCounts(discovered_files=1, new_unique_images=1),
        )
        return registration.image_id, registration.file_id


def test_video_origin_round_trip_and_schema() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        frame = root / "movie_000121.jpg"
        Image.new("RGB", (32, 32), "navy").save(frame)
        manifest = update_video_origin_manifest(
            destination_folder=root,
            source_video=root / "movie.mkv",
            sampling_mode="interval",
            interval_seconds=0.5,
            output_files=(frame,),
        )
        assert manifest.exists()
        origin = VideoOriginManifestCache().origin_for(frame)
        assert origin is not None
        assert origin.frame_number == 121
        assert origin.timestamp_seconds == 60.0
        assert format_video_timestamp(origin.timestamp_seconds) == "00:01:00.0"

        database = root / "dataset_tools.db"
        image_id, file_id = _register_image(database, frame)
        with Catalog(database) as catalog:
            catalog.store_file_video_origin(
                file_id=file_id,
                source_video=origin.source_video,
                sampling_mode=origin.sampling_mode,
                timestamp_seconds=origin.timestamp_seconds,
                frame_number=origin.frame_number,
                interval_seconds=origin.interval_seconds,
            )
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == (
                SCHEMA_VERSION
            ) == 12
            stored = connection.execute(
                """
                SELECT source_video, timestamp_seconds, frame_number
                FROM file_video_origins
                WHERE file_id = ?
                """,
                (file_id,),
            ).fetchone()
            assert stored is not None
            assert stored[1:] == (60.0, 121)
            assert image_id > 0
        finally:
            connection.close()
        records = CatalogBrowserRepository(database).fetch_records()
        assert len(records) == 1
        assert records[0].source_video.endswith("movie.mkv")
        assert records[0].video_timestamp_seconds == 60.0


def test_record_removal_requires_recoverable_current_backup() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        frame = root / "frame.jpg"
        Image.new("RGB", (24, 24), "orange").save(frame)
        database = root / "dataset_tools.db"
        image_id, file_id = _register_image(database, frame)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO file_actions (
                    file_id, action_type, source_path, target_path,
                    status, details_json, performed_at
                ) VALUES (?, 'fixture', ?, '', 'complete', '{}', CURRENT_TIMESTAMP)
                """,
                (file_id, str(frame)),
            )
            connection.execute(
                """
                INSERT INTO catalog_edit_operations (
                    operation_type, description, affected_image_count,
                    before_state_json, after_state_json, created_at, undone_at,
                    discarded_at
                ) VALUES ('fixture', 'fixture', 1, '[]', '[]',
                          CURRENT_TIMESTAMP, NULL, NULL)
                """
            )

        backup = CatalogEditService(database).create_backup()
        summary = FileActionService(database).remove_catalog_records((image_id,))
        assert summary.removed_images == 1
        assert summary.cleared_history_operations == 1
        assert frame.exists(), "catalog-only removal must not touch image files"

        with closing(sqlite3.connect(database)) as current, current:
            current.execute("PRAGMA foreign_keys = ON")
            assert current.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
            assert current.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
            assert current.execute("SELECT COUNT(*) FROM file_actions").fetchone()[0] == 0
            assert current.execute("PRAGMA foreign_key_check").fetchall() == []
        with closing(sqlite3.connect(backup)) as recovery, recovery:
            assert recovery.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
            assert recovery.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_missing_file_filter_excludes_quarantine() -> None:
    records = (
        SimpleNamespace(
            image_id=1,
            file_status="missing",
            face_count=0,
            body_analysis_available=False,
        ),
        SimpleNamespace(
            image_id=2,
            file_status="deleted",
            face_count=0,
            body_analysis_available=False,
        ),
        SimpleNamespace(
            image_id=3,
            file_status="quarantined",
            face_count=0,
            body_analysis_available=False,
        ),
    )
    assert {
        record.image_id
        for record in records
        if matches_catalog_state(record, "No image file found")
    } == {1, 2}


def test_resumed_eta_waits_for_new_measurements() -> None:
    now = [0.0]
    tracker = WorkflowProgressTracker(
        ("Florence analysis",),
        clock=lambda: now[0],
    )
    assert tracker.update("Florence analysis", 1600, 2000).estimated_remaining_seconds is None
    for completion in range(1601, 1605):
        now[0] += 1.0
        assert (
            tracker.update(
                "Florence analysis",
                completion,
                2000,
            ).estimated_remaining_seconds
            is None
        )
    now[0] += 1.0
    assert (
        tracker.update(
            "Florence analysis",
            1605,
            2000,
        ).estimated_remaining_seconds
        is not None
    )


def test_pause_retains_worker_until_resume() -> None:
    pause = threading.Event()
    cancel = threading.Event()
    pause.set()
    finished = threading.Event()

    def worker() -> None:
        wait_if_paused(pause, cancel, poll_seconds=0.02)
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.08)
    assert not finished.is_set()
    pause.clear()
    thread.join(timeout=1.0)
    assert finished.is_set()


def test_provider_device_worker_does_not_read_tk_variables() -> None:
    """Keep background device detection independent of the Tcl interpreter."""
    app_source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    method_source = app_source.split(
        "    def _refresh_provider_device_status(self) -> None:", 1
    )[1].split(
        "    def _build_body_provider(self, parent: ttk.Frame) -> None:", 1
    )[0]
    worker_source = method_source.split("        def inspect_devices() -> None:", 1)[1]

    assert "face_model_name = self.face_model_name_var.get().strip()" in (
        method_source
    )
    assert "face_model_root = self.face_model_root_var.get().strip()" in (
        method_source
    )
    assert "face_model_name=face_model_name" in worker_source
    assert "face_model_root=face_model_root" in worker_source
    assert ".get()" not in worker_source
    assert "self." not in worker_source


def test_v0272_source_contracts() -> None:
    root = Path(__file__).parent
    app = (root / "app.py").read_text(encoding="utf-8")
    browser = (root / "catalog_browser.py").read_text(encoding="utf-8")
    settings = (root / "settings_manager.py").read_text(encoding="utf-8")
    settings_gui = (root / "test_v0260_gui.py").read_text(encoding="utf-8")
    historical_gui = (root / "test_v0270_gui.py").read_text(encoding="utf-8")
    assert "analysis_canvas" in app
    assert "Pause / Resume Active Run" in app
    assert 'label="Selected Images"' in app
    assert "Images found: counting" in app
    assert "if affected_images > self.images_per_page" in browser
    assert "if count > self.images_per_page" in browser
    assert "remove_selected_from_catalog" in browser
    assert "delete_catalog_record_with_file: bool = False" in settings
    assert 'assert not hasattr(dialog, "confirm_trash_var")' in settings_gui
    assert "assert dialog.confirm_trash_var.get()" not in settings_gui
    assert '"Run / Restart Body"' in historical_gui
    assert '"Run Body Analysis"' not in historical_gui
    referenced_theme_fields = set(
        re.findall(r"\bself\.theme\.([A-Za-z_][A-Za-z0-9_]*)", app)
    )
    declared_theme_fields = set(AppTheme.__dataclass_fields__)
    assert referenced_theme_fields <= declared_theme_fields, (
        "app.py references undefined AppTheme fields: "
        + ", ".join(sorted(referenced_theme_fields - declared_theme_fields))
    )


if __name__ == "__main__":
    test_video_origin_round_trip_and_schema()
    test_record_removal_requires_recoverable_current_backup()
    test_missing_file_filter_excludes_quarantine()
    test_resumed_eta_waits_for_new_measurements()
    test_pause_retains_worker_until_resume()
    test_provider_device_worker_does_not_read_tk_variables()
    test_v0272_source_contracts()
    print(
        "v0.27.2 regression tests passed: video timestamps, schema 12, "
        "recoverable record removal, missing-file filtering, resumed ETA, "
        "pause/resume, Tk-safe device inspection, current inherited GUI "
        "contracts, and page-relative destructive safeguards."
    )
