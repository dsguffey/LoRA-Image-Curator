"""Real export regression for stale and multiple catalog file locations."""

from __future__ import annotations

import csv
import hashlib
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from PIL import Image

from catalog_edits import CatalogEditService
from dataset_export import DatasetExportRepository, ExportOptions, build_export_plan, execute_export
from image_sets import ImageSetRepository
from tests.test_milestone_7d import NOW, _seed_catalog
from tests.test_trigger_export_regression import _PreviewHarness
from training_text import BUILTIN_TRAINING_PROFILES, build_training_text


def _manifest_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _seed_real_image(root: Path) -> tuple[Path, Path, Path, int]:
    database, _ids, sources = _seed_catalog(root)
    source = root / "source images ü" / "001_clean_30.jpg"
    source.parent.mkdir()
    with Image.new("RGB", (12, 10), (53, 101, 149)) as image:
        image.save(source)
    sources[0].unlink()
    destination = root / "images" / "Output" / "exports"
    stale_path = destination / source.name
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE images SET content_sha256 = ?, byte_size = ? WHERE id = 1",
            (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size),
        )
        connection.execute(
            "UPDATE files SET path_key = ?, absolute_path = ?, input_root = ?, "
            "input_root_key = ?, relative_path = ?, byte_size = ? WHERE id = 1",
            (str(source).casefold(), str(source), str(source.parent),
             str(source.parent).casefold(), source.name, source.stat().st_size),
        )
        connection.execute(
            "INSERT INTO files(id, image_id, path_key, absolute_path, input_root, "
            "input_root_key, relative_path, byte_size, modified_time_ns, status, "
            "first_seen_at, last_seen_at, last_seen_run_id) "
            "VALUES (107, 1, ?, ?, ?, ?, ?, ?, 1, 'present', ?, ?, NULL)",
            (str(stale_path).casefold(), str(stale_path), str(destination),
             str(destination).casefold(), stale_path.name, source.stat().st_size,
             NOW, NOW),
        )
        connection.execute("DELETE FROM image_tags WHERE tag_id = 1 AND image_id = 1")
        for tag_id, name in ((2, "manualtag"), (3, "aitag1"), (4, "aitag2")):
            connection.execute(
                "UPDATE tags SET name = ?, normalized_name = ? WHERE id = ?",
                (name, name, tag_id),
            )
        connection.execute("DELETE FROM analysis_tag_suggestions WHERE tag_id = 5")
    assert CatalogEditService(database).set_manual_keyword([1], "subjecttoken") == 1
    image_set = ImageSetRepository(database).create_set("Test", [1])
    return database, source, destination, image_set.set_id


def test_real_export_uses_existing_catalog_location() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_export_source_") as temporary:
        root = Path(temporary)
        database, source, destination, set_id = _seed_real_image(root)
        source_bytes = source.read_bytes()
        original_locations: list[tuple[int, str, str]]
        with closing(sqlite3.connect(database)) as connection:
            original_locations = connection.execute(
                "SELECT id, absolute_path, status FROM files WHERE image_id = 1 ORDER BY id"
            ).fetchall()
        assert len(original_locations) == 2
        assert not Path(original_locations[1][1]).exists()

        image_ids = ImageSetRepository(database).get_image_ids(set_id)
        assert image_ids == (1,)
        repository = DatasetExportRepository(database)
        records = repository.fetch_records(image_ids)
        record = records[0]
        assert record.source_path == source
        preview = _PreviewHarness(records, "Flux LoRA")
        profile = preview._current_profile()
        preview._refresh_sample()
        preview_text = str(preview.sample_var.get()).split("\n", 1)[1]
        canonical_text = build_training_text(record.layers, profile)
        assert preview_text == canonical_text == "subjecttoken, manualtag, aitag1, aitag2"

        plan = build_export_plan(records, ExportOptions(
            destination=destination, profile=profile, handoff_scope='Image set "Test"',
        ))
        item = plan.items[0]
        assert item.planned_status == "planned"
        assert item.record.source_path == source
        assert item.image_path is not None and item.image_path != source
        assert item.sidecar_path is not None and item.sidecar_path != source
        result = execute_export(plan, repository=repository)
        assert (result.status, result.requested_count, result.exported_count,
                result.skipped_count, result.failed_count) == ("complete", 1, 1, 0, 0)
        assert item.image_path.read_bytes() == source_bytes
        assert item.sidecar_path.read_text(encoding="utf-8") == canonical_text + "\n"
        assert item.sidecar_path.read_text(encoding="utf-8").count("subjecttoken") == 1
        assert result.manifest_path is not None
        manifest = _manifest_rows(result.manifest_path)[0]
        assert manifest["status"] == "exported" and manifest["error"] == ""
        assert manifest["source_path"] == str(source)
        assert manifest["exported_filename"] == item.image_path.name
        assert manifest["sidecar_filename"] == item.sidecar_path.name
        assert result.readme_path is not None
        readme = result.readme_path.read_text(encoding="utf-8")
        assert "Requested catalog images: 1" in readme
        assert "Successfully processed catalog images: 1" in readme
        assert "Skipped catalog images: 0" in readme
        assert "Failed catalog images: 0" in readme
        assert "Copy images requested: Yes" in readme
        assert "Same-name TXT sidecars requested: Yes" in readme
        assert source.read_bytes() == source_bytes
        with closing(sqlite3.connect(database)) as connection:
            after_locations = connection.execute(
                "SELECT id, absolute_path, status FROM files WHERE image_id = 1 ORDER BY id"
            ).fetchall()
        assert after_locations == original_locations

        # A second export sees the occupied filename, chooses a new destination,
        # and still keeps the original source identity and bytes.
        next_plan = build_export_plan(records, ExportOptions(
            destination=destination, profile=profile,
        ))
        next_item = next_plan.items[0]
        assert next_item.record.source_path == source
        assert next_item.image_path is not None and next_item.image_path.name == "001_clean_30_2.jpg"
        assert next_item.sidecar_path is not None and next_item.sidecar_path.name == "001_clean_30_2.txt"
        assert execute_export(next_plan, repository=repository).exported_count == 1
        assert next_item.image_path.read_bytes() == source_bytes
        assert source.read_bytes() == source_bytes


def test_multiple_existing_locations_use_stable_preference() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_export_locations_") as temporary:
        root = Path(temporary)
        database, source, _destination, set_id = _seed_real_image(root)
        newer_source = root / "another location" / source.name
        newer_source.parent.mkdir()
        newer_source.write_bytes(source.read_bytes())
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "INSERT INTO files(id, image_id, path_key, absolute_path, input_root, "
                "input_root_key, relative_path, byte_size, modified_time_ns, status, "
                "first_seen_at, last_seen_at, last_seen_run_id) "
                "VALUES (108, 1, ?, ?, ?, ?, ?, ?, 1, 'present', ?, ?, NULL)",
                (str(newer_source).casefold(), str(newer_source), str(newer_source.parent),
                 str(newer_source.parent).casefold(), newer_source.name,
                 newer_source.stat().st_size, NOW, NOW),
            )
        repository = DatasetExportRepository(database)
        ids = ImageSetRepository(database).get_image_ids(set_id)
        assert repository.fetch_records(ids)[0].source_path == newer_source
        newer_source.unlink()
        assert repository.fetch_records(ids)[0].source_path == source


def test_missing_all_locations_skips_truthfully() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_export_missing_") as temporary:
        root = Path(temporary)
        database, _source, _destination, _set_id = _seed_real_image(root)
        destination = root / "missing_out"
        repository = DatasetExportRepository(database)
        records = repository.fetch_records([3])
        assert records[0].source_path is not None and not records[0].source_path.exists()
        plan = build_export_plan(records, ExportOptions(
            destination=destination, profile=BUILTIN_TRAINING_PROFILES["flux_lora"],
        ))
        assert plan.items[0].planned_status == "skipped"
        result = execute_export(plan, repository=repository)
        assert (result.status, result.exported_count, result.skipped_count, result.failed_count) == (
            "partial", 0, 1, 0,
        )
        assert result.manifest_path is not None
        row = _manifest_rows(result.manifest_path)[0]
        assert row["status"] == "skipped"
        assert row["error"] == "Source image is missing or unavailable."
        assert result.readme_path is not None
        readme = result.readme_path.read_text(encoding="utf-8")
        assert "Successfully processed catalog images: 0" in readme
        assert "Copy images requested: Yes" in readme
        assert sorted(path.name for path in destination.iterdir()) == ["README.txt", "manifest.csv"]


if __name__ == "__main__":
    test_real_export_uses_existing_catalog_location()
    test_multiple_existing_locations_use_stable_preference()
    test_missing_all_locations_skips_truthfully()
    print("Export source resolution regression passed.")
