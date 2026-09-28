"""Pre-feedback workflow UI and orchestration contracts for v0.28.4."""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import tempfile
import tomllib

from contextlib import closing
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from advanced_search import record_matches_query
from analysis_control import AnalysisCancelled
from analysis_pipeline import run_pipeline
from app_identity import APP_VERSION
from browser_workflow import BrowserFilterState, READINESS_ISSUE_LABELS
from catalog import Catalog, SCHEMA_VERSION
from catalog_lifecycle import create_catalog_database, validate_catalog_database
from catalog_browser import CatalogBrowserFrame, CatalogBrowserRepository
from dataset_readiness import build_readiness_report, evaluate_prominent_overlay
from quality_analysis import QualityAnalysisSummary, measure_image_quality
from settings_manager import AppSettings


ROOT = PROJECT_ROOT


def _record(image_id: int, **overrides) -> SimpleNamespace:
    values = {
        "image_id": image_id,
        "search_blob": "portrait\nvisible conference title",
        "manual_tags": "portrait",
        "ai_tags_active": "person",
        "ai_tags_excluded": "",
        "manual_keyword": "demo_person",
        "caption": "A portrait at a conference.",
        "filename": "portrait.png",
        "relative_path": "portrait.png",
        "absolute_path": "C:/images/portrait.png",
        "image_set_names": "",
        "ocr_text": "VISIBLE CONFERENCE TITLE",
        "ocr_regions_json": "[]",
        "overlay_regions_json": "[]",
        "face_boxes_json": "[]",
        "likely_screenshot_or_ui": "no",
        "review_status": "keep",
        "file_status": "present",
        "suggested_identity": "",
        "identity_review_status": "",
        "face_count": 1,
        "face_analysis_available": False,
        "body_analysis_available": False,
        "body_landmarks_json": "[]",
        "width": 1024,
        "height": 1024,
        "quality_status": "success",
        "sharpness_score": 200.0,
        "perceptual_hash": f"{image_id:016x}",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_release_identity_and_default_quality_setting() -> None:
    assert APP_VERSION == "0.28.4"
    assert AppSettings().run_quality_analysis is True
    assert AppSettings().overlay_coverage_threshold_percent == 5
    assert AppSettings().overlay_spatial_mode == "either"
    assert BrowserFilterState(overlay_spatial_mode="Face and Body").normalized().overlay_spatial_mode == "both"
    assert "Version 0.28.4" in (ROOT / "VERSION.txt").read_text(encoding="utf-8")
    assert 'version = "0.28.4"' in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_combined_workflow_runs_quality_between_catalog_and_florence() -> None:
    events: list[str] = []
    quality = QualityAnalysisSummary(1, 1, 0, 0, False, 0.1)

    def fake_quality(_database: Path, **_kwargs) -> QualityAnalysisSummary:
        events.append("quality")
        return quality

    def fake_florence(**kwargs):
        events.append("catalog")
        kwargs["catalog_ready_callback"](kwargs["catalog_database"])
        events.append("florence")
        return SimpleNamespace(catalog_database=kwargs["catalog_database"])

    summary = run_pipeline(
        input_folder=Path("images"),
        output_folder=Path("output"),
        include_triage=True,
        reuse_stored_analysis=True,
        run_quality_analysis=True,
        run_face_analysis=False,
        florence_runner=fake_florence,
        quality_runner=fake_quality,
    )

    assert events == ["catalog", "quality", "florence"]
    assert summary.quality is quality


def test_custom_catalog_is_the_only_pipeline_database() -> None:
    """A selected catalog must reach every provider despite a default beside it."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        custom = create_catalog_database(root / "Face Catalog.db")
        default = create_catalog_database(root / "dataset_tools.db")
        events: list[tuple[str, Path]] = []

        def fake_quality(database: Path, **_kwargs) -> QualityAnalysisSummary:
            events.append(("quality", database))
            return QualityAnalysisSummary(0, 0, 0, 0, False, 0.0)

        def fake_florence(**kwargs):
            database = kwargs.get("catalog_database", kwargs["output_folder"] / "dataset_tools.db")
            events.append(("florence", database))
            kwargs["catalog_ready_callback"](database)
            return SimpleNamespace(catalog_database=database)

        def fake_face(**kwargs):
            events.append(("face", kwargs["catalog_database"]))
            return SimpleNamespace(catalog_database=kwargs["catalog_database"])

        result = run_pipeline(
            input_folder=root,
            output_folder=root,
            catalog_database=custom,
            include_triage=False,
            reuse_stored_analysis=True,
            run_quality_analysis=True,
            run_face_analysis=True,
            florence_runner=fake_florence,
            face_runner=fake_face,
            quality_runner=fake_quality,
        )
        assert events == [("florence", custom), ("quality", custom), ("face", custom)]
        assert result.catalog_database == custom
        assert validate_catalog_database(default) == default


def test_new_catalog_names_and_legacy_extensionless_recovery() -> None:
    from catalog_lifecycle import normalize_new_catalog_path

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ("FaceCatalog", "Face Catalog", "肖像 catalog", "x" * 80):
            target = normalize_new_catalog_path(root / name)
            assert target.name == name + ".db"
            assert validate_catalog_database(create_catalog_database(target)) == target
        assert normalize_new_catalog_path(root / "other.sqlite").name == "other.sqlite"
        legacy = create_catalog_database(root / "LegacyCatalog")
        assert validate_catalog_database(legacy) == legacy
        assert not (root / "LegacyCatalog.db").exists()
        try:
            normalize_new_catalog_path(legacy)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Existing extensionless catalog was hidden by normalization")


def test_real_face_provider_writes_and_reuses_only_custom_catalog() -> None:
    """A deterministic provider exercises actual catalog writes and reuse."""
    from catalog_import import CatalogImportOptions, import_catalog_folder
    from face_analyzer import analyze_faces
    from quality_analysis import analyze_catalog_quality
    from tests.test_v0283_regression import _DeterministicFaceProvider

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "images"
        reports = root / "reports"
        source.mkdir()
        reports.mkdir()
        Image.new("RGB", (64, 64), "navy").save(source / "one.png")
        custom = reports / "肖像 Catalog.db"
        default = reports / "dataset_tools.db"
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=source,
                target_database=custom,
                mode="create",
                create_image_set=False,
            )
        )
        quality = analyze_catalog_quality(custom)
        assert quality.analyzed_images == 1
        first = analyze_faces(
            source, reports, catalog_database=custom,
            provider=_DeterministicFaceProvider(),
        )
        assert not default.exists()
        create_catalog_database(default)
        second = analyze_faces(
            source, reports, catalog_database=custom,
            provider=_DeterministicFaceProvider(),
        )
        if first.catalog_database != custom:
            print(
                "Face catalog path spellings: "
                f"returned={ascii(str(first.catalog_database))}, "
                f"requested={ascii(str(custom))}"
            )
        assert first.catalog_database.samefile(custom), (
            f"Face result used {first.catalog_database!a} instead of {custom!a}"
        )
        assert first.generated_images == 1
        if second.catalog_database != custom:
            print(
                "Reused Face catalog path spellings: "
                f"returned={ascii(str(second.catalog_database))}, "
                f"requested={ascii(str(custom))}"
            )
        assert second.catalog_database.samefile(custom), (
            f"Face reuse used {second.catalog_database!a} instead of {custom!a}"
        )
        assert second.reused_images == 1
        with closing(sqlite3.connect(custom)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM face_detections").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM image_quality_results").fetchone()[0] == 1
        with closing(sqlite3.connect(default)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM face_detections").fetchone()[0] == 0

        cancelled = Event()
        cancelled.set()
        try:
            analyze_faces(
                source, reports, catalog_database=custom,
                provider=_DeterministicFaceProvider(), cancel_event=cancelled,
            )
        except AnalysisCancelled:
            pass
        else:
            raise AssertionError("Pre-cancelled face analysis unexpectedly ran")


def test_pipeline_stops_when_provider_reports_another_catalog() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        selected = create_catalog_database(root / "selected.db")
        other = create_catalog_database(root / "dataset_tools.db")

        def wrong_florence(**kwargs):
            kwargs["catalog_ready_callback"](other)
            return SimpleNamespace(catalog_database=other)

        try:
            run_pipeline(
                input_folder=root, output_folder=root, catalog_database=selected,
                include_triage=False, reuse_stored_analysis=True,
                run_quality_analysis=False, run_face_analysis=True,
                florence_runner=wrong_florence,
                face_runner=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("Face provider should not run after catalog mismatch")
                ),
            )
        except ValueError as error:
            assert "catalog mismatch" in str(error).lower()
        else:
            raise AssertionError("Provider catalog mismatch was not stopped")


def test_app_workers_forward_the_selected_catalog() -> None:
    """Direct Face and combined/Florence launch share the app's selected path."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        selected = create_catalog_database(root / "FaceCatalog.db")
        create_catalog_database(root / "dataset_tools.db")
        with patch.dict(os.environ, {"APPDATA": str(root), "LORA_IMAGE_CURATOR_TEST_MODE": "1"}):
            from app import DatasetToolsApp, shutdown_logging

        application = object.__new__(DatasetToolsApp)
        application.root = object()
        application.catalog_browser = SimpleNamespace(catalog_path=selected)
        application.latest_catalog_database = None
        application.output_folder_var = SimpleNamespace(get=lambda: str(root))
        application.message_queue = queue.Queue()
        application.analysis_cancel_event = Event()
        application.analysis_pause_event = Event()
        application._queue_progress = lambda *_args: None
        application._queue_status = lambda *_args: None
        assert application._current_catalog_path() == selected
        shown: list[str] = []
        application.catalog_path_var = SimpleNamespace(set=shown.append)
        application._update_catalog_path_display()
        assert shown == [str(selected)]

        legacy = create_catalog_database(root / "LegacyCatalog")
        opened: list[Path] = []
        with patch("app.filedialog.askopenfilename", return_value=str(legacy)) as chooser:
            with patch.object(DatasetToolsApp, "_activate_catalog", lambda _self, path, **_kwargs: opened.append(path)):
                application._open_catalog()
        assert opened == [legacy]
        assert any(pattern == "*" for _label, pattern in chooser.call_args.kwargs["filetypes"])

        captured: list[tuple[str, Path]] = []

        def fake_pipeline(**kwargs):
            phase = "combined" if kwargs["run_face_analysis"] else "florence"
            captured.append((phase, kwargs["catalog_database"]))
            return SimpleNamespace(catalog_database=selected)

        def fake_face(**kwargs):
            captured.append(("face", kwargs["catalog_database"]))
            return SimpleNamespace(catalog_database=selected)

        with patch("analysis_pipeline.run_pipeline", fake_pipeline), patch("app.analyze_faces", fake_face):
            application._analysis_worker(
                root, root, selected, False, True, False, True, False,
                "", None, None, False, True, True, True,
            )
            application._analysis_worker(
                root, root, selected, False, True, False, False, False,
                "", None, None, False, True, True, True,
            )
            application._face_analysis_worker(
                root, root, selected, "", None, None, False, True, True, True,
            )
        assert captured == [
            ("combined", selected), ("florence", selected), ("face", selected)
        ]
        assert [application.message_queue.get_nowait()[0] for _ in range(3)] == [
            "complete", "complete", "face_complete"
        ]
        shutdown_logging()


def test_body_dialog_worker_uses_the_selected_catalog() -> None:
    """The optional Body boundary must receive the exact active database."""
    from body_analysis_dialog import BodyAnalysisDialog

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        selected = create_catalog_database(root / "selected.sqlite3")
        messages: queue.Queue = queue.Queue()
        dialog = SimpleNamespace(
            database_path=selected,
            model_path=root / "synthetic.task",
            options=object(),
            image_ids=None,
            _messages=messages,
            _cancel=Event(),
            _pause=Event(),
        )
        seen: list[Path] = []

        def fake_analysis(database_path, *_args, **_kwargs):
            seen.append(database_path)
            return SimpleNamespace()

        with patch("body_analysis_dialog.inspect_body_setup", return_value=SimpleNamespace(ready=True)):
            with patch("body_analysis_dialog.analyze_catalog_bodies", fake_analysis):
                BodyAnalysisDialog._run_worker(dialog)
        assert seen == [selected]
        assert messages.get_nowait()[0] == "complete"
        assert not (root / "dataset_tools.db").exists()


def test_prominent_overlay_and_ocr_search_are_review_evidence_only() -> None:
    ordinary = _record(1)
    screenshot = _record(2, likely_screenshot_or_ui="yes")
    overlay = _record(
        3,
        ocr_text="DRAFT",
        ocr_regions_json=json.dumps(
            [{"text": "DRAFT", "x1": 100, "y1": 350, "x2": 900, "y2": 500}]
        ),
    )
    report = build_readiness_report(
        (ordinary, screenshot, overlay),
        overlay_spatial_mode="none",
    )
    issue = next(item for item in report.issues if item.label == "Prominent Overlay")
    assert issue.image_ids == (3,)
    assert issue.severity == "advisory"
    assert "Prominent Overlay" in READINESS_ISSUE_LABELS
    assert record_matches_query(ordinary, "ocr:conference")
    assert record_matches_query(ordinary, "conference")
    strict_report = build_readiness_report(
        (ordinary, screenshot, overlay),
        overlay_coverage_threshold_percent=20,
        overlay_spatial_mode="none",
    )
    strict_issue = next(
        item for item in strict_report.issues if item.label == "Prominent Overlay"
    )
    assert strict_issue.image_ids == ()


def _body_landmarks() -> str:
    points = [
        {"x": 0.0, "y": 0.0, "visibility": 0.0, "presence": 0.0}
        for _index in range(33)
    ]
    for index, x, y in (
        (11, 0.40, 0.35),
        (12, 0.60, 0.35),
        (23, 0.44, 0.65),
        (24, 0.56, 0.65),
    ):
        points[index] = {"x": x, "y": y, "visibility": 1.0, "presence": 1.0}
    return json.dumps([points])


def _spatial_record(image_id: int, *regions: tuple[str, int, int, int, int]):
    payload = [
        {"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        for text, x1, y1, x2, y2 in regions
    ]
    return _record(
        image_id,
        ocr_text=" ".join(region[0] for region in regions),
        ocr_regions_json=json.dumps(payload),
        face_boxes_json=json.dumps(
            [{"x1": 400, "y1": 100, "x2": 600, "y2": 320}]
        ),
        face_analysis_available=True,
        body_analysis_available=True,
        body_landmarks_json=_body_landmarks(),
        width=1000,
        height=1000,
    )


def test_overlay_spatial_modes_use_area_not_character_count() -> None:
    background = ("BACKGROUND", 20, 750, 980, 850)
    face = ("DRAFT", 430, 150, 570, 260)
    body = ("X", 420, 400, 580, 560)
    background_only = _spatial_record(1, background)
    face_only = _spatial_record(2, background, face)
    body_only = _spatial_record(3, background, body)
    both = _spatial_record(4, background, face, body)
    records = (background_only, face_only, body_only, both)

    expected = {
        "none": (1, 2, 3, 4),
        "face": (2, 4),
        "body": (3, 4),
        "either": (2, 3, 4),
        "both": (4,),
    }
    for mode, image_ids in expected.items():
        report = build_readiness_report(
            records,
            overlay_coverage_threshold_percent=5,
            overlay_spatial_mode=mode,
        )
        issue = next(
            item for item in report.issues if item.label == "Prominent Overlay"
        )
        assert issue.image_ids == image_ids

    unavailable = _record(
        5,
        ocr_regions_json='[{"text":"DRAFT","x1":0.1,"y1":0.1,"x2":0.9,"y2":0.3}]',
    )
    evidence = evaluate_prominent_overlay(
        unavailable,
        coverage_threshold_percent=5,
        spatial_mode="either",
    )
    assert not evidence.matched
    assert not evidence.spatial_available

    bar_only = _spatial_record(6)
    bar_only.overlay_regions_json = json.dumps(
        [{"kind": "bar", "x1": 0.40, "y1": 0.14, "x2": 0.60, "y2": 0.27}]
    )
    bar_evidence = evaluate_prominent_overlay(
        bar_only,
        coverage_threshold_percent=5,
        spatial_mode="face",
    )
    assert bar_evidence.matched
    assert bar_evidence.text_region_count == 0
    assert bar_evidence.bar_region_count == 1

    duplicate_geometry = _record(
        7,
        ocr_regions_json='[{"text":"DRAFT","x1":0.1,"y1":0.1,"x2":0.5,"y2":0.2}]',
        overlay_regions_json='[{"kind":"bar","x1":0.1,"y1":0.1,"x2":0.5,"y2":0.2}]',
    )
    union_evidence = evaluate_prominent_overlay(
        duplicate_geometry,
        coverage_threshold_percent=6,
        spatial_mode="none",
    )
    assert not union_evidence.matched
    assert round(union_evidence.image_coverage_percent, 3) == 4.0


def test_quality_analysis_detects_obvious_bar_without_flagging_clean_fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_overlay_quality_") as temporary:
        root = Path(temporary)
        results: dict[str, list[object]] = {}
        for name, has_bar in (("clean", False), ("bar", True)):
            image = Image.new("RGB", (400, 500), (215, 185, 160))
            draw = ImageDraw.Draw(image)
            draw.ellipse((100, 60, 300, 300), fill=(235, 195, 165))
            draw.ellipse((150, 120, 170, 145), fill=(45, 55, 55))
            draw.ellipse((230, 120, 250, 145), fill=(45, 55, 55))
            if has_bar:
                draw.rectangle((70, 105, 330, 175), fill=(45, 45, 45))
            path = root / f"{name}.png"
            image.save(path)
            results[name] = json.loads(
                measure_image_quality(path).overlay_regions_json
            )

        assert results["clean"] == []
        assert len(results["bar"]) == 1
        assert results["bar"][0]["kind"] == "bar"


def test_schema_14_migration_and_overlay_region_storage() -> None:
    with tempfile.TemporaryDirectory(prefix="lora_v02845_schema_") as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        image_path = root / "sample.png"
        image_path.write_bytes(b"not-an-image-but-stable-catalog-content")

        with Catalog(database):
            pass
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("ALTER TABLE analysis_results DROP COLUMN ocr_regions_json")
            connection.execute("ALTER TABLE image_quality_results DROP COLUMN overlay_regions_json")
            connection.execute("PRAGMA user_version = 12")
            connection.commit()

        with Catalog(database) as catalog:
            assert catalog.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 14
            columns = {
                row[1]
                for row in catalog.connection.execute(
                    "PRAGMA table_info(analysis_results)"
                ).fetchall()
            }
            assert "ocr_regions_json" in columns
            quality_columns = {
                row[1]
                for row in catalog.connection.execute(
                    "PRAGMA table_info(image_quality_results)"
                ).fetchall()
            }
            assert "overlay_regions_json" in quality_columns
            run_id = catalog.start_import_run(
                input_root=root,
                output_folder=root,
                model_name="test-model",
                transformers_version="test-version",
                analysis_version=2,
                include_triage=True,
                reuse_stored_analysis=False,
            )
            registered = catalog.register_file(
                file_path=image_path,
                input_root=root,
                run_id=run_id,
            )
            region_json = '[{"text":"hello","x1":1,"y1":2,"x2":3,"y2":4}]'
            catalog.store_successful_analysis(
                image_id=registered.image_id,
                source_file_id=registered.file_id,
                model_name="test-model",
                transformers_version="test-version",
                analysis_version=2,
                include_triage=True,
                result={
                    "caption": "sample",
                    "detected_object_count": 0,
                    "object_labels": "",
                    "person_count": 0,
                    "ocr_region_count": 1,
                    "ocr_character_count": 5,
                    "ocr_text": "hello",
                    "ocr_regions_json": region_json,
                    "likely_screenshot_or_ui": "no",
                    "candidate_recommendation": "review",
                    "recommendation_reason": "test",
                    "triage_status": "success",
                    "triage_error": "",
                    "processing_seconds": 0.1,
                },
            )
            stored = catalog.connection.execute(
                "SELECT ocr_regions_json FROM analysis_results"
            ).fetchone()
            assert stored is not None and stored[0] == region_json

        records = CatalogBrowserRepository(database).fetch_records()
        assert len(records) == 1
        assert records[0].ocr_regions_json == region_json
        assert records[0].overlay_regions_json == "[]"
        assert records[0].face_boxes_json == "[]"
        assert records[0].body_landmarks_json == "[]"


def test_duplicate_group_cards_wrap_inside_their_own_outline() -> None:
    """A narrow group must add a row, never spill cards into the next group."""
    class GridTarget:
        def __init__(self) -> None:
            self.grid_calls: list[dict[str, object]] = []
            self.column_calls: list[dict[str, object]] = []

        def grid(self, **kwargs) -> None:
            self.grid_calls.append(kwargs)

        def columnconfigure(self, _column: int, **kwargs) -> None:
            self.column_calls.append(kwargs)

        def update_idletasks(self) -> None:
            pass

    cards = {image_id: SimpleNamespace(outer=GridTarget()) for image_id in range(1, 7)}
    group = GridTarget()
    card_area = GridTarget()
    browser = SimpleNamespace(
        _layout_after_id=None,
        _closing=False,
        canvas=SimpleNamespace(winfo_width=lambda: 750),
        duplicate_group_frames=[(group, card_area, tuple(cards))],
        card_container=GridTarget(),
        cards_by_id=cards,
        _last_column_count=1,
        winfo_exists=lambda: True,
        _update_scroll_region=lambda: None,
    )

    CatalogBrowserFrame._layout_cards(browser, force=True)

    assert [card.outer.grid_calls[0]["row"] for card in cards.values()] == [0, 0, 0, 1, 1, 1]
    assert len(group.grid_calls) == 1
    assert all(card.outer.grid_calls[0]["column"] in {0, 1, 2} for card in cards.values())


def test_release_gates_include_v0284() -> None:
    regressions = (ROOT / "tools" / "run_regressions.py").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
    golden = (ROOT / "tests" / "test_golden_build.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "repository-checks.yml").read_text(
        encoding="utf-8"
    )
    assert '"tests/test_v0284_regression.py"' in regressions
    assert '"tests/test_v0284_regression.py"' in builder
    assert '"tests/test_v0284_gui.py"' in builder
    assert 'GUI_ENTRYPOINT = "tests/test_v0284_gui.py"' in golden
    assert "tests.test_v0284_regression" in workflow


def test_release_inventory_contains_florence_spatial_ocr_parser() -> None:
    """Keep spatial OCR code in the signed source archive that runs Florence."""
    manifest = (ROOT / "RELEASE_MANIFEST.sha256").read_text(encoding="utf-8")
    florence = (ROOT / "florence_analyzer.py").read_text(encoding="utf-8")
    assert "  florence_analyzer.py" in manifest
    assert "def parse_ocr_with_regions(" in florence


def test_standalone_gui_smoke_bootstraps_project_imports() -> None:
    """Keep the documented direct Windows command independent of PYTHONPATH."""
    gui = (ROOT / "tests" / "test_v0284_gui.py").read_text(encoding="utf-8")
    historical_gui = (ROOT / "tests" / "test_v0252_gui.py").read_text(
        encoding="utf-8"
    )
    assert "PROJECT_ROOT = Path(__file__).resolve().parents[1]" in gui
    assert "sys.path.insert(0, str(PROJECT_ROOT))" in gui
    assert '"--latest-only"' in gui
    assert "run(include_history=not arguments.latest_only)" in gui
    assert "len(READINESS_ISSUE_LABELS)" not in historical_gui
    v0281_gui = (ROOT / "tests" / "test_v0281_gui.py").read_text(
        encoding="utf-8"
    )
    assert 'tools_menu.type(index)) != "separator"' in v0281_gui
    assert 'str(profile_combo.cget("state")) in {' in gui
    assert "finally:" in gui


def test_logging_shutdown_detaches_closed_temporary_handlers() -> None:
    """Cumulative GUI runs must not reopen logs in deleted temp folders."""
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "root_logger.removeHandler(handler)" in app_source
    assert "root_logger.addHandler(logging.NullHandler())" in app_source


def test_current_provider_setup_and_public_gate_contracts() -> None:
    """Cover active contracts that superseded several historical source assertions."""
    from lic_dependencies.profile import PROFILE
    from setup_assistant import REQUIRED_EXACT_VERSIONS

    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "repository-checks.yml").read_text(
        encoding="utf-8"
    )
    assert 'command=self._choose_face_model_root' in app_source
    assert 'model_entry.insert(0, "OpenCV YuNet + SFace")' in app_source
    assert "Current work: Face Scanning / OpenCV YuNet + SFace" in app_source
    assert '"Download InsightFace buffalo_l?"' not in app_source
    assert REQUIRED_EXACT_VERSIONS["transformers"] == PROFILE["transformers"] == "4.56.2"
    assert "Send2Trash>=1.8,<3" in metadata["project"]["dependencies"]
    assert "mediapipe==0.10.35" in metadata["project"]["optional-dependencies"]["body"]
    assert f"v{APP_VERSION}" in readme
    assert "LIC Install Manager has its own version number" in readme
    assert "python -m pip install" in workflow
    assert "tests.test_v0284_regression" in workflow


if __name__ == "__main__":
    test_release_identity_and_default_quality_setting()
    test_combined_workflow_runs_quality_between_catalog_and_florence()
    test_custom_catalog_is_the_only_pipeline_database()
    test_new_catalog_names_and_legacy_extensionless_recovery()
    test_real_face_provider_writes_and_reuses_only_custom_catalog()
    test_pipeline_stops_when_provider_reports_another_catalog()
    test_app_workers_forward_the_selected_catalog()
    test_body_dialog_worker_uses_the_selected_catalog()
    test_prominent_overlay_and_ocr_search_are_review_evidence_only()
    test_overlay_spatial_modes_use_area_not_character_count()
    test_quality_analysis_detects_obvious_bar_without_flagging_clean_fixture()
    test_schema_14_migration_and_overlay_region_storage()
    test_duplicate_group_cards_wrap_inside_their_own_outline()
    test_release_gates_include_v0284()
    test_release_inventory_contains_florence_spatial_ocr_parser()
    test_standalone_gui_smoke_bootstraps_project_imports()
    test_logging_shutdown_detaches_closed_temporary_handlers()
    test_current_provider_setup_and_public_gate_contracts()
    print(
        "v0.28.4 regression tests passed: catalog-quality-provider ordering, "
        "canonical custom catalog paths, extensionless recovery, Face write/reuse, "
        "Body path forwarding, "
        "global quality defaults, area-based overlays, schema 14 migration, "
        "OCR search, and standalone GUI smoke imports."
    )
