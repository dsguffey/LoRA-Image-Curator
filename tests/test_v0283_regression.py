"""Face-detection/identity-reference separation contracts for v0.28.3.

The regression uses a deterministic provider and temporary synthetic images.
It never imports InsightFace, loads an ONNX model, or touches a user catalog.
"""

from __future__ import annotations

import csv
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

import numpy as np

from PIL import Image

from app_identity import APP_VERSION
from catalog import CATALOG_FILENAME
from catalog_import import CatalogImportOptions, import_catalog_folder
from face_analyzer import FaceDetection, analyze_faces


ROOT = Path(__file__).resolve().parents[1]


class _DeterministicFaceProvider:
    """Return no reference face for one folder and a stable face elsewhere."""

    provider_key = "regression-provider"
    provider_version = "1"
    model_name = "synthetic-face-model"
    model_root = Path("synthetic-model-root")
    model_fingerprint = "v0283-synthetic-model"
    execution_provider = "TestExecutionProvider"
    license_label = "Synthetic test fixture"
    embedding_dimension = 3

    def analyze_image(self, image_path: Path) -> list[FaceDetection]:
        if image_path.parent.name == "bad-reference":
            return []
        return [
            FaceDetection(
                bbox=(8.0, 6.0, 56.0, 60.0),
                detection_score=0.99,
                landmarks=((20.0, 24.0), (44.0, 24.0)),
                embedding=np.asarray((1.0, 0.0, 0.0), dtype=np.float32),
            )
        ]


def _make_image(path: Path) -> None:
    """Create one valid supported image; inference is supplied by the fake."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "navy").save(path)


def test_release_identity_is_synchronized() -> None:
    assert APP_VERSION == "0.28.4"
    assert "Version 0.28.4" in (ROOT / "VERSION.txt").read_text(encoding="utf-8")
    assert 'version = "0.28.4"' in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_bad_reference_falls_back_to_detection_then_valid_reference_matches() -> None:
    """Reference failure must not prevent catalog face detection or later reuse."""
    with tempfile.TemporaryDirectory(prefix="lora_v0283_face_fallback_") as temp:
        root = Path(temp)
        input_folder = root / "input"
        output_folder = root / "output"
        bad_reference = root / "bad-reference"
        good_reference = root / "good-reference"
        input_image = input_folder / "catalog-face.png"
        _make_image(input_image)
        _make_image(bad_reference / "001_clean_11.png")
        _make_image(good_reference / "clear-reference.png")
        output_folder.mkdir()

        database = output_folder / CATALOG_FILENAME
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=input_folder,
                target_database=database,
                mode="create",
                recursive=True,
                create_image_set=False,
            )
        )

        statuses: list[str] = []
        provider = _DeterministicFaceProvider()
        fallback = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            identity_name="demo_trigger",
            reference_folder=bad_reference,
            reuse_stored_analysis=False,
            provider=provider,
            status_callback=statuses.append,
        )

        assert fallback.identity_matching_enabled is False
        assert fallback.reference_images_found == 1
        assert fallback.reference_faces_used == 0
        assert fallback.faces_detected == 1
        assert fallback.generated_images == 1
        assert fallback.suggestions_created == 0
        assert "Face detection continued" in fallback.identity_profile_warning
        assert any("Continuing with face detection only" in line for line in statuses)

        with fallback.output_csv.open(
            newline="", encoding="utf-8-sig"
        ) as report_file:
            fallback_rows = list(csv.DictReader(report_file))
        assert len(fallback_rows) == 1
        assert fallback_rows[0]["face_count"] == "1"
        assert fallback_rows[0]["best_identity_name"] == ""
        assert fallback_rows[0]["suggested_identity"] == ""

        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM face_detections"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM identity_profiles"
            ).fetchone()[0] == 0
            run = connection.execute(
                "SELECT status, identity_name, faces_detected, suggestions_created "
                "FROM face_analysis_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert run == ("complete", "", 1, 0)

        # A later valid reference must reuse the stored face detection, compare
        # its embedding, and create the requested trigger-word suggestion.
        matched = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            identity_name="demo_trigger",
            reference_folder=good_reference,
            reuse_stored_analysis=True,
            provider=provider,
        )
        assert matched.identity_matching_enabled is True
        assert matched.reference_faces_used == 1
        assert matched.faces_detected == 1
        assert matched.generated_images == 0
        assert matched.reused_images == 1
        assert matched.suggestions_created == 1

        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM identity_profiles"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM identity_matches WHERE is_suggested = 1"
            ).fetchone()[0] == 1
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reference_and_trigger_keyword_are_optional_for_face_detection() -> None:
    """A plain face scan must not require identity configuration at all."""
    with tempfile.TemporaryDirectory(prefix="lora_v0283_detection_only_") as temp:
        root = Path(temp)
        input_folder = root / "input"
        output_folder = root / "output"
        _make_image(input_folder / "catalog-face.png")
        output_folder.mkdir()
        database = output_folder / CATALOG_FILENAME
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=input_folder,
                target_database=database,
                mode="create",
                create_image_set=False,
            )
        )

        summary = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            provider=_DeterministicFaceProvider(),
        )
        assert summary.identity_matching_enabled is False
        assert summary.identity_name == ""
        assert summary.reference_images_found == 0
        assert summary.faces_detected == 1
        assert summary.suggestions_created == 0
        assert "No Trigger Keyword" in summary.identity_profile_warning


def test_release_gates_include_v0283() -> None:
    regressions = (ROOT / "tools" / "run_regressions.py").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
    golden = (ROOT / "tests" / "test_golden_build.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "repository-checks.yml").read_text(
        encoding="utf-8"
    )
    assert '"tests/test_v0283_regression.py"' in regressions
    assert '"tests/test_v0283_regression.py"' in builder
    assert '"tests/test_v0283_gui.py"' in builder
    assert 'GUI_ENTRYPOINT = "tests/test_v0284_gui.py"' in golden
    assert "tests.test_v0284_regression" in workflow


if __name__ == "__main__":
    test_release_identity_is_synchronized()
    test_bad_reference_falls_back_to_detection_then_valid_reference_matches()
    test_reference_and_trigger_keyword_are_optional_for_face_detection()
    test_release_gates_include_v0283()
    print(
        "v0.28.3 regression tests passed: face detection survives an unusable "
        "identity reference, creates no guessed trigger-word suggestions, and "
        "a later valid reference reuses detections for identity matching."
    )
