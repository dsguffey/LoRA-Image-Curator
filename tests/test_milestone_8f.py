"""Dependency-light regressions for Milestone 8F selection culling."""

from __future__ import annotations

import tempfile

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from catalog import Catalog
from catalog_browser import CatalogBrowserRepository
from catalog_import import CatalogImportOptions, import_catalog_folder
from selection_culling import CullChecks, CullCriteria, build_cull_plan


CRITERIA = CullCriteria(
    profile_label="Flux Character LoRA",
    minimum_short_side=768,
    blur_threshold=100.0,
    duplicate_similarity_percent=98.0,
)


def _record(image_id: int, **overrides: object) -> SimpleNamespace:
    """Create a complete browser-like record with useful default evidence."""
    values: dict[str, object] = {
        "image_id": image_id,
        "filename": f"image-{image_id}.png",
        "review_status": "unreviewed",
        "file_status": "present",
        "quality_status": "success",
        "quality_error": "",
        "width": 1024,
        "height": 1024,
        "sharpness_score": 250.0,
        "perceptual_hash": f"{image_id - 1:016x}",
        "likely_screenshot_or_ui": "no",
        "person_count": 1,
        "face_count": 1,
        "face_analysis_available": True,
        "largest_face_area_ratio": 0.03,
        "second_largest_face_area_ratio": None,
        "identity_similarity": 0.75,
        "identity_review_status": "suggested",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_serious_issue_reasons_are_combined() -> None:
    criteria = replace(
        CRITERIA,
        checks=CullChecks(any_multiple_people_or_faces=True),
    )
    plan = build_cull_plan(
        (
            _record(
                1,
                review_status="reject",
                file_status="missing",
                quality_status="error",
                quality_error="decode failed",
                width=400,
                height=700,
                sharpness_score=None,
                likely_screenshot_or_ui="yes",
                person_count=3,
                face_count=2,
                largest_face_area_ratio=0.001,
                perceptual_hash="",
            ),
            _record(2, perceptual_hash="ffffffffffffffff"),
        ),
        criteria,
    )
    assert plan.removed_image_ids == (1,)
    reasons = plan.decisions[0].reasons
    assert any("marked Reject" in reason for reason in reasons)
    assert any("source file" in reason for reason in reasons)
    assert any("could not read" in reason for reason in reasons)
    assert any("400 px" in reason for reason in reasons)
    assert any("screenshot" in reason.casefold() for reason in reasons)
    assert any("Multiple people" in reason for reason in reasons)
    assert any("0.10%" in reason for reason in reasons)


def test_missing_optional_analysis_never_removes_by_itself() -> None:
    plan = build_cull_plan(
        (
            _record(
                1,
                quality_status="",
                sharpness_score=None,
                perceptual_hash="",
                likely_screenshot_or_ui="not_evaluated",
                person_count=None,
                face_count=None,
                face_analysis_available=False,
                largest_face_area_ratio=None,
            ),
        ),
        CRITERIA,
    )
    assert plan.removed_image_ids == ()
    assert plan.kept_image_ids == (1,)
    unavailable = dict(plan.unavailable_counts)
    assert unavailable["Quality not analyzed"] == 1
    assert unavailable["Similarity hash unavailable"] == 1
    assert unavailable["Screenshot check unavailable"] == 1
    assert unavailable["Face-prominence check unavailable"] == 1


def test_direct_similarity_avoids_transitive_overculling() -> None:
    """A-B and B-C similarity must not automatically make A and C redundant."""
    plan = build_cull_plan(
        (
            _record(
                1,
                filename="best-a.png",
                perceptual_hash="0000000000000000",
                review_status="keep",
                sharpness_score=400.0,
            ),
            _record(
                2,
                filename="middle-b.png",
                perceptual_hash="0000000000000001",
                sharpness_score=300.0,
            ),
            _record(
                3,
                filename="distinct-c.png",
                perceptual_hash="0000000000000003",
                sharpness_score=200.0,
            ),
        ),
        CRITERIA,
    )
    assert plan.removed_image_ids == (2,)
    assert plan.kept_image_ids == (1, 3)
    assert "best-a.png" in plan.decisions[0].reasons[0]
    assert dict(plan.reason_counts)["Near-duplicate"] == 1


def test_prominence_keeps_background_people_but_flags_two_leads() -> None:
    """Several faces are acceptable when only one is large enough to dominate."""
    plan = build_cull_plan(
        (
            _record(
                1,
                filename="clear-lead.png",
                person_count=3,
                face_count=3,
                largest_face_area_ratio=0.05,
                second_largest_face_area_ratio=0.005,
                perceptual_hash="1111111111111111",
            ),
            _record(
                2,
                filename="two-leads.png",
                person_count=2,
                face_count=2,
                largest_face_area_ratio=0.05,
                second_largest_face_area_ratio=0.04,
                perceptual_hash="eeeeeeeeeeeeeeee",
            ),
        ),
        CRITERIA,
    )
    assert plan.kept_image_ids == (1,)
    assert plan.removed_image_ids == (2,)
    assert "second-largest face is 80%" in plan.decisions[0].reasons[0]


def test_granular_checks_can_disable_near_duplicate_culling() -> None:
    criteria = replace(
        CRITERIA,
        checks=CullChecks(
            already_rejected=False,
            missing_or_unreadable=False,
            low_resolution=False,
            blur=False,
            screenshot_or_ui=False,
            no_person_or_face=False,
            subject_too_small=False,
            multiple_prominent_faces=False,
            any_multiple_people_or_faces=False,
            near_duplicates=False,
        ),
    )
    plan = build_cull_plan(
        (
            _record(1, perceptual_hash="aaaaaaaaaaaaaaaa"),
            _record(2, perceptual_hash="aaaaaaaaaaaaaaaa"),
        ),
        criteria,
    )
    assert plan.removed_image_ids == ()
    assert plan.kept_image_ids == (1, 2)


def test_stronger_duplicate_version_is_retained() -> None:
    plan = build_cull_plan(
        (
            _record(
                1,
                filename="small.png",
                perceptual_hash="aaaaaaaaaaaaaaaa",
                width=800,
                height=800,
                sharpness_score=150.0,
                identity_similarity=0.55,
            ),
            _record(
                2,
                filename="strong.png",
                perceptual_hash="aaaaaaaaaaaaaaaa",
                width=1600,
                height=1200,
                sharpness_score=320.0,
                identity_similarity=0.88,
                identity_review_status="confirmed",
            ),
        ),
        CRITERIA,
    )
    assert plan.removed_image_ids == (1,)
    assert plan.kept_image_ids == (2,)
    assert "strong.png" in plan.decisions[0].reasons[0]


def test_browser_projects_face_visibility() -> None:
    """Bounding-box area must reach culling as an image-relative ratio."""
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8f_face_") as temporary:
        root = Path(temporary)
        source = root / "images"
        source.mkdir()
        image_path = source / "subject.png"
        Image.new("RGB", (1000, 1000), (80, 120, 160)).save(image_path)
        database = root / "catalog" / "dataset_tools.db"
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=source,
                target_database=database,
                mode="create",
            )
        )

        with Catalog(database) as catalog:
            row = catalog.connection.execute(
                "SELECT i.id AS image_id, f.id AS file_id "
                "FROM images AS i JOIN files AS f ON f.image_id = i.id"
            ).fetchone()
            assert row is not None
            model_id = catalog.register_face_model(
                provider_key="test",
                provider_version="1",
                model_name="fixture",
                model_fingerprint="fixture-v1",
                model_root="",
                embedding_dimension=2,
                license_label="test only",
            )
            catalog.store_face_result(
                image_id=int(row["image_id"]),
                source_file_id=int(row["file_id"]),
                face_model_id=model_id,
                status="success",
                error="",
                processing_seconds=0.01,
                detections=[
                    {
                        "face_index": 0,
                        "bbox_x1": 10.0,
                        "bbox_y1": 20.0,
                        "bbox_x2": 60.0,
                        "bbox_y2": 70.0,
                        "detection_score": 0.99,
                        "landmarks_json": "[]",
                        "embedding": b"\x00" * 8,
                        "embedding_dimension": 2,
                        "embedding_norm": 1.0,
                    },
                    {
                        "face_index": 1,
                        "bbox_x1": 100.0,
                        "bbox_y1": 120.0,
                        "bbox_x2": 130.0,
                        "bbox_y2": 150.0,
                        "detection_score": 0.95,
                        "landmarks_json": "[]",
                        "embedding": b"\x00" * 8,
                        "embedding_dimension": 2,
                        "embedding_norm": 1.0,
                    },
                ],
            )

        record = CatalogBrowserRepository(database).fetch_records()[0]
        assert record.face_analysis_available
        assert record.face_count == 2
        assert record.largest_face_area_ratio is not None
        assert abs(record.largest_face_area_ratio - 0.0025) < 0.0000001
        assert record.second_largest_face_area_ratio is not None
        assert abs(record.second_largest_face_area_ratio - 0.0009) < 0.0000001


def run() -> None:
    test_serious_issue_reasons_are_combined()
    test_missing_optional_analysis_never_removes_by_itself()
    test_direct_similarity_avoids_transitive_overculling()
    test_prominence_keeps_background_people_but_flags_two_leads()
    test_granular_checks_can_disable_near_duplicate_culling()
    test_stronger_duplicate_version_is_retained()
    test_browser_projects_face_visibility()
    print(
        "Milestone 8F tests passed: explainable issue culling, conservative "
        "missing-analysis handling, and direct best-version redundancy removal."
    )


if __name__ == "__main__":
    run()
