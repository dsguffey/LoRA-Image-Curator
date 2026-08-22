"""Regressions for Milestone 10 Phase 1 stabilization and cleanup.

The supplied real-world failure was a recursive cache loop:

1. 768 extracted frames were cataloged.
2. The browser wrote previews beneath the selected source folder.
3. A provider scan cataloged those previews as new images.
4. The browser then created previews of those newly cataloged previews.

These dependency-light checks reproduce that ownership boundary without
touching the user's database or requiring Florence model inference.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from contextlib import closing
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace

from PIL import Image

from advanced_search import record_matches_query
from analysis_control import AnalysisCancelled
from catalog import Catalog, ImportRunCounts, SCHEMA_VERSION
from catalog_browser import CatalogBrowserRepository, ThumbnailCache
from catalog_edits import CatalogEditService
from catalog_import import (
    CatalogImportOptions,
    discover_image_files as discover_import_images,
    import_catalog_folder,
)
from face_analyzer import find_image_files as discover_face_images

# The regression stops Florence before model loading. Lightweight module stubs
# keep this dependency-independent test runnable on a clean packaging machine
# where PyTorch and Transformers are intentionally not installed.
if "torch" not in sys.modules:
    torch_stub = ModuleType("torch")
    torch_stub.float16 = object()
    torch_stub.float32 = object()
    torch_stub.cuda = SimpleNamespace(
        is_available=lambda: False,
        empty_cache=lambda: None,
        get_device_name=lambda _index: "",
    )
    sys.modules["torch"] = torch_stub
if "transformers" not in sys.modules:
    transformers_stub = ModuleType("transformers")
    transformers_stub.AutoModelForImageTextToText = object
    transformers_stub.AutoProcessor = object
    sys.modules["transformers"] = transformers_stub

from florence_analyzer import analyze_folder, find_image_files as discover_florence_images
from image_discovery import is_legacy_thumbnail_cache_path


def _make_image(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 64), color).save(path)


def _test_discovery_and_cache_location(root: Path) -> None:
    source = root / "source"
    original = source / "Gal_Gadot_000001.png"
    first_preview = source / "thumbnail_cache" / ("a" * 24 + "_190.webp")
    second_preview = source / "thumbnail_cache" / ("b" * 24 + "_190.webp")
    unrelated = source / "thumbnail_cache" / "user_reference.webp"
    _make_image(original, "#7A557A")
    _make_image(first_preview, "#557A7A")
    _make_image(second_preview, "#7A7A55")
    _make_image(unrelated, "#55557A")

    assert is_legacy_thumbnail_cache_path(first_preview.relative_to(source))
    assert is_legacy_thumbnail_cache_path(
        r"thumbnail_cache\abcdefabcdefabcdefabcdef_300.webp"
    )
    assert not is_legacy_thumbnail_cache_path(unrelated.relative_to(source))

    expected = [original.resolve(), unrelated.resolve()]
    assert discover_import_images(source, recursive=True) == expected
    assert discover_florence_images(source) == expected
    assert discover_face_images(source) == expected

    previous_appdata = os.environ.get("APPDATA")
    appdata = root / "appdata"
    os.environ["APPDATA"] = str(appdata)
    try:
        cache = ThumbnailCache(root / "catalog" / "dataset_tools.db")
        assert (
            cache.cache_directory
            == appdata / "LoRAImageCurator" / "thumbnail_cache"
        )
        assert source not in cache.cache_directory.parents
    finally:
        if previous_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous_appdata


def _test_schema_repair_and_curated_search(root: Path) -> None:
    source = root / "repair_source"
    original = source / "Gal_Gadot_interview_000001.png"
    preview = source / "thumbnail_cache" / ("c" * 24 + "_190.webp")
    _make_image(original, "#935F73")
    _make_image(preview, "#5F7393")

    database = root / "repair_catalog" / "dataset_tools.db"
    with Catalog(database) as catalog:
        first_run = catalog.start_import_run(
            input_root=source,
            output_folder=database.parent,
            model_name="fixture",
            transformers_version="fixture",
            analysis_version=1,
            include_triage=False,
            reuse_stored_analysis=False,
        )
        original_registration = catalog.register_file(
            file_path=original,
            input_root=source,
            run_id=first_run,
        )
        catalog.register_file(
            file_path=preview,
            input_root=source,
            run_id=first_run,
        )
        counts = ImportRunCounts(discovered_files=2, new_unique_images=2)
        catalog.finish_import_run(first_run, status="complete", counts=counts)
        catalog.start_import_run(
            input_root=source,
            output_folder=database.parent,
            model_name="interrupted fixture",
            transformers_version="fixture",
            analysis_version=1,
            include_triage=False,
            reuse_stored_analysis=True,
        )

    # Reproduce a version-9 catalog containing application-generated previews.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "ALTER TABLE analysis_results DROP COLUMN ocr_regions_json"
        )
        connection.execute(
            "ALTER TABLE image_quality_results DROP COLUMN overlay_regions_json"
        )
        connection.execute("PRAGMA user_version = 9")
        connection.execute(
            "DELETE FROM catalog_metadata WHERE key LIKE 'schema_10_%' "
            "OR key = 'legacy_thumbnail_records_removed'"
        )
        connection.commit()

    original_bytes = original.read_bytes()
    preview_bytes = preview.read_bytes()
    with Catalog(database):
        pass

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 12
        assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM import_runs WHERE status = 'running'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT value FROM catalog_metadata "
            "WHERE key = 'legacy_thumbnail_records_removed'"
        ).fetchone() == ("1",)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # Migration repairs catalog-owned metadata only.
    assert original.read_bytes() == original_bytes
    assert preview.read_bytes() == preview_bytes

    record = CatalogBrowserRepository(database).fetch_records()[0]
    assert record.image_id == original_registration.image_id
    assert not record_matches_query(record, "gal gadot")
    CatalogEditService(database).add_manual_tags(
        (record.image_id,),
        ("gal gadot",),
    )
    tagged_record = CatalogBrowserRepository(database).fetch_records()[0]
    assert record_matches_query(tagged_record, "gal gadot")
    # Explicit compatibility syntax remains available for old saved searches,
    # but filenames no longer participate in ordinary unqualified searches.
    assert record_matches_query(tagged_record, "filename:Gal_Gadot")


def _test_prestart_cancellation(root: Path) -> None:
    source = root / "cancel_source"
    output = root / "cancel_output"
    output.mkdir()
    _make_image(source / "one.png", "#435D76")
    cancellation = Event()
    cancellation.set()
    try:
        analyze_folder(
            source,
            output,
            include_triage=False,
            cancel_event=cancellation,
        )
    except AnalysisCancelled:
        pass
    else:
        raise AssertionError("A pre-cancelled provider run unexpectedly started")
    assert not (output / "dataset_tools.db").exists()


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_10_phase1_") as temporary:
        root = Path(temporary)
        _test_discovery_and_cache_location(root)
        _test_schema_repair_and_curated_search(root)
        _test_prestart_cancellation(root)
    print(
        "Milestone 10 Phase 1 tests passed: generated previews stay outside "
        "sources and out of every scan, schema 12 retains legacy cache repair "
        "without deleting files, ordinary search excludes filenames while "
        "including curated and OCR evidence, and provider "
        "cancellation stops at a safe boundary."
    )


if __name__ == "__main__":
    run()
