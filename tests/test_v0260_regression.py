"""Dependency-light v0.26.0 regression coverage.

The suite validates normalized body evidence, schema migration, browser
semantics, privacy defaults, and real quarantine/restore behavior. It does not
require MediaPipe or Send2Trash; their native/runtime setup remains in the
Windows GUI smoke test.
"""

from __future__ import annotations

import sqlite3

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from body_analysis import (
    BodyAnalysisOptions,
    BodyAnalysisResult,
    _pose_evidence,
    inspect_body_setup,
)
from browser_workflow import matches_catalog_state
from catalog import Catalog, ImportRunCounts, SCHEMA_VERSION
import catalog_import
from catalog_import import CatalogImportOptions, import_catalog_folder
from file_actions import FileActionService
from settings_manager import AppSettings


@dataclass(slots=True)
class FilterRecord:
    body_analysis_available: bool
    body_detected: bool
    body_face_visible: bool
    body_pose_count: int
    full_body: bool


def _visible_landmarks(value: float = 0.95) -> list[dict[str, float]]:
    return [
        {
            "x": 0.5,
            "y": 0.5,
            "z": 0.0,
            "visibility": value,
            "presence": value,
        }
        for _ in range(33)
    ]


def _create_catalog(database: Path, image_path: Path) -> int:
    with Catalog(database) as catalog:
        run_id = catalog.start_import_run(
            input_root=image_path.parent,
            output_folder=database.parent,
            model_name="v0.26 regression",
            transformers_version="not applicable",
            analysis_version=0,
            include_triage=False,
            reuse_stored_analysis=False,
        )
        registration = catalog.register_file(
            file_path=image_path,
            input_root=image_path.parent,
            run_id=run_id,
        )
        catalog.update_image_dimensions(registration.image_id, 64, 96)
        catalog.finish_import_run(
            run_id,
            status="complete",
            counts=ImportRunCounts(discovered_files=1, new_unique_images=1),
        )
        return registration.image_id


def main() -> None:
    settings = AppSettings()
    assert settings.allow_provider_telemetry is False
    assert settings.confirm_trash_deletion is True
    assert 60 <= settings.body_full_body_threshold_percent <= 100

    options = BodyAnalysisOptions(
        landmark_visibility_threshold=0.50,
        full_body_threshold_percent=70,
    )
    score, face_visible, feet_visible = _pose_evidence(
        _visible_landmarks(),
        options,
    )
    assert score > 0.90
    assert face_visible is True
    assert feet_visible is True

    full = FilterRecord(True, True, True, 1, True)
    partial = FilterRecord(True, True, True, 1, False)
    none = FilterRecord(True, False, False, 0, False)
    unknown = FilterRecord(False, False, False, 0, False)
    multiple = FilterRecord(True, True, True, 2, False)
    assert matches_catalog_state(full, "Full body")
    assert matches_catalog_state(partial, "Partial body")
    assert matches_catalog_state(none, "No body / pose")
    assert matches_catalog_state(unknown, "Body analysis not run")
    assert matches_catalog_state(multiple, "Multiple poses")

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "dataset_tools.db"
        source = root / "source" / "candidate.png"
        source.parent.mkdir()
        Image.new("RGB", (64, 96), (25, 50, 75)).save(source)
        image_id = _create_catalog(database, source)

        with closing(sqlite3.connect(database)) as connection, connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == (
                SCHEMA_VERSION
            ) == 12
            assert connection.execute(
                "SELECT COUNT(*) FROM body_models"
            ).fetchone()[0] == 0

        service = FileActionService(database)
        quarantine_root = root / "quarantine"
        quarantined = service.quarantine([image_id], quarantine_root)
        assert quarantined.completed_files == 1
        assert not source.exists()
        items = service.quarantined_items([image_id])
        assert len(items) == 1 and items[0].source_path.exists()

        restored = service.restore([image_id])
        assert restored.completed_files == 1
        assert source.exists()
        assert service.present_items([image_id])[0].source_path == source.resolve()

        missing_status = inspect_body_setup(
            root / "missing" / "pose_landmarker_full.task",
            perform_runtime_check=False,
        )
        assert missing_status.model_exists is False
        assert missing_status.ready is False

        # Exercise the import-exclusion contract without requiring MediaPipe.
        import_source = root / "filtered-import"
        import_source.mkdir()
        Image.new("RGB", (64, 96), (255, 0, 0)).save(
            import_source / "no-body.png"
        )
        Image.new("RGB", (64, 96), (0, 255, 0)).save(
            import_source / "body.png"
        )
        fake_model = root / "pose_landmarker_full.task"
        fake_model.write_bytes(b"vetted-test-model")

        class FakeAnalyzer:
            def __init__(self, _model: Path, _options: BodyAnalysisOptions) -> None:
                pass

            def __enter__(self) -> "FakeAnalyzer":
                return self

            def __exit__(self, *_exception: object) -> None:
                return None

            def analyze(self, image_path: Path) -> BodyAnalysisResult:
                detected = image_path.name == "body.png"
                return BodyAnalysisResult(
                    pose_count=int(detected),
                    body_detected=detected,
                    face_visible=detected,
                    full_body_score=0.90 if detected else 0.0,
                    full_body=detected,
                    classification="full_body" if detected else "no_body",
                    landmarks_json="[]",
                )

        original_analyzer = catalog_import.MediaPipeBodyAnalyzer
        original_inspector = catalog_import.inspect_body_setup
        catalog_import.MediaPipeBodyAnalyzer = FakeAnalyzer
        catalog_import.inspect_body_setup = lambda *_args, **_kwargs: SimpleNamespace(
            ready=True,
            notes=(),
        )
        try:
            filtered_database = root / "filtered.db"
            filtered = import_catalog_folder(
                CatalogImportOptions(
                    source_folder=import_source,
                    target_database=filtered_database,
                    mode="create",
                    skip_without_body=True,
                    body_model_path=str(fake_model),
                )
            )
        finally:
            catalog_import.MediaPipeBodyAnalyzer = original_analyzer
            catalog_import.inspect_body_setup = original_inspector
        assert filtered.discovered_files == 2
        assert filtered.cataloged_files == 1
        assert filtered.skipped_without_body == 1
        with closing(sqlite3.connect(filtered_database)) as connection, connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
            assert connection.execute(
                "SELECT body_detected, full_body FROM body_image_results"
            ).fetchone() == (1, 1)

    print("v0.26.0 regression tests passed.")


if __name__ == "__main__":
    main()
