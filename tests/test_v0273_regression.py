"""Focused v0.27.3 regressions for large-catalog workflow corrections.

The suite remains display- and provider-independent. It protects the defects
reported during the 14,000-image cleanup: settings persistence, responsive
transactional record removal, non-quadratic Browser startup, bounded integer
controls, scoped wheel routing, and the new read-only review surfaces.
"""

from __future__ import annotations

import os
import sqlite3
import threading

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory

from PIL import Image

from catalog import Catalog, ImportRunCounts
from browser_workflow import matches_catalog_state
from file_actions import FileActionService
from quality_analysis import (
    duplicate_candidate_clusters,
    duplicate_candidates_at_threshold,
    nearest_duplicate_candidates,
)
from settings_manager import AppSettings, load_settings, save_settings


def _register_image(database: Path, image_path: Path) -> int:
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
        return registration.image_id


def test_delete_record_setting_survives_round_trip() -> None:
    with TemporaryDirectory() as temporary:
        prior_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = temporary
        try:
            settings = AppSettings(delete_catalog_record_with_file=True)
            save_settings(settings)
            assert load_settings().delete_catalog_record_with_file is True
        finally:
            if prior_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = prior_appdata

    app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    save_method = app_source.split(
        "    def _save_current_settings(self) -> None:", 1
    )[1].split(
        "    def _save_video_settings", 1
    )[0]
    assert "delete_catalog_record_with_file=(" in save_method
    assert "browser_settings.delete_catalog_record_with_file" in save_method


def test_record_removal_reports_progress_and_cancels_transactionally() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        image_ids: list[int] = []
        for index in range(7):
            path = root / f"frame_{index:02d}.png"
            Image.new("RGB", (10, 10), (index * 20, 10, 10)).save(path)
            image_ids.append(_register_image(database, path))

        cancelled = threading.Event()
        cancelled.set()
        summary = FileActionService(database).remove_catalog_records(
            image_ids,
            cancel_event=cancelled,
        )
        assert summary.cancelled
        assert summary.removed_images == 0
        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 7

        progress: list[tuple[int, int, str]] = []
        summary = FileActionService(database).remove_catalog_records(
            image_ids,
            progress_callback=lambda current, total, detail: progress.append(
                (current, total, detail)
            ),
        )
        assert summary.removed_images == 7
        assert not summary.cancelled
        assert progress[0][0] == 0
        assert progress[-1][:2] == (7, 7)


@dataclass(slots=True, frozen=True)
class _HashRecord:
    image_id: int
    perceptual_hash: str


def test_indexed_duplicate_results_match_brute_force() -> None:
    rng = Random(273)
    records = tuple(
        _HashRecord(index, f"{rng.getrandbits(64):016x}")
        for index in range(90)
    )
    nearest = nearest_duplicate_candidates(records)
    for record in records:
        expected_distance, expected_id = min(
            (
                (
                    int(record.perceptual_hash, 16)
                    ^ int(candidate.perceptual_hash, 16)
                ).bit_count(),
                candidate.image_id,
            )
            for candidate in records
            if candidate.image_id != record.image_id
        )
        assert nearest[record.image_id][0] == expected_id
        assert nearest[record.image_id][1] == (
            (64 - expected_distance) / 64
        ) * 100.0

    # Seed known two-bit and exact-hash relationships among otherwise random
    # records so the optimized 96-100% clustering path has positive groups.
    base = int(records[0].perceptual_hash, 16)
    clustered = (
        *records,
        _HashRecord(1000, f"{base ^ 1:016x}"),
        _HashRecord(1001, records[3].perceptual_hash),
    )
    for threshold in range(96, 101):
        got = duplicate_candidate_clusters(clustered, threshold)
        assert got == _brute_clusters(clustered, threshold)
        candidates = duplicate_candidates_at_threshold(clustered, threshold)
        expected_ids = {
            image_id
            for group in got
            for image_id in group
        }
        assert set(candidates) == expected_ids


def test_no_face_filter_distinguishes_unrun_analysis() -> None:
    unrun = type(
        "Record",
        (),
        {"face_analysis_available": False, "face_count": 0},
    )()
    analyzed_without_face = type(
        "Record",
        (),
        {"face_analysis_available": True, "face_count": 0},
    )()
    assert matches_catalog_state(unrun, "Face analysis not run")
    assert not matches_catalog_state(unrun, "No face")
    assert matches_catalog_state(analyzed_without_face, "No face")


def _brute_clusters(
    records: tuple[_HashRecord, ...],
    threshold: int,
) -> tuple[tuple[int, ...], ...]:
    parent = {record.image_id: record.image_id for record in records}

    def find(image_id: int) -> int:
        while parent[image_id] != image_id:
            parent[image_id] = parent[parent[image_id]]
            image_id = parent[image_id]
        return image_id

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            smaller, larger = sorted((first_root, second_root))
            parent[larger] = smaller

    for index, first in enumerate(records):
        first_hash = int(first.perceptual_hash, 16)
        for second in records[index + 1 :]:
            distance = (first_hash ^ int(second.perceptual_hash, 16)).bit_count()
            if ((64 - distance) / 64) * 100.0 >= threshold:
                union(first.image_id, second.image_id)
    groups: dict[int, list[int]] = {}
    for record in records:
        groups.setdefault(find(record.image_id), []).append(record.image_id)
    result = [
        tuple(sorted(image_ids))
        for image_ids in groups.values()
        if len(image_ids) >= 2
    ]
    return tuple(sorted(result, key=lambda values: (values[0], values)))


def test_v0273_source_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    browser = (root / "catalog_browser.py").read_text(encoding="utf-8")
    settings_dialog = (root / "settings_dialog.py").read_text(encoding="utf-8")
    readiness = (root / "readiness_frame.py").read_text(encoding="utf-8")
    filters = (root / "browser_workflow_dialogs.py").read_text(encoding="utf-8")
    scroll = (root / "ui_scroll.py").read_text(encoding="utf-8")
    bulk = (root / "bulk_action_dialog.py").read_text(encoding="utf-8")

    fetch_records = browser.split(
        "    def fetch_records(self) -> list[CatalogImageRecord]:", 1
    )[1].split(
        "    def _validate_catalog_identity", 1
    )[0]
    assert "nearest_duplicate_candidates(records)" not in fetch_records
    assert "Browser refresh:" in browser
    assert "BulkActionDialog" in browser
    assert "progress_callback=report" in browser
    assert "ImageReviewDialog" in browser
    assert "ImageQualityDialog" in browser
    assert "Enlarge / Review" in browser
    assert "Image Quality…" in browser
    assert "Show Likely Non-Person" in filters
    assert "register_mousewheel_region(self.analysis_canvas)" in (
        root / "app.py"
    ).read_text(encoding="utf-8")
    assert "closest registered ancestor" in scroll
    assert "threading.Thread" in bulk
    assert "grab_set()" in bulk
    assert "Body / pose detection strictness" in settings_dialog
    assert "whole number from 0 to 10,000" in settings_dialog
    assert "duplicate_similarity_description" in readiness
    assert 'state="readonly"' in filters


if __name__ == "__main__":
    test_delete_record_setting_survives_round_trip()
    test_record_removal_reports_progress_and_cancels_transactionally()
    test_indexed_duplicate_results_match_brute_force()
    test_no_face_filter_distinguishes_unrun_analysis()
    test_v0273_source_contracts()
    print(
        "v0.27.3 regression tests passed: delete-setting persistence, "
        "transactional bulk progress/cancellation, indexed duplicate accuracy, "
        "bounded integer controls, scoped wheel routing, modal worker progress, "
        "large-image review, and read-only quality details."
    )
