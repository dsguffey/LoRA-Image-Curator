"""Create a small synthetic catalog for the complete historical test chain.

The oldest maintained regressions predate self-contained fixtures and expect a
catalog containing several images, successful Florence-style object tags, and
one face-identity suggestion. This module creates exactly that durable state in
a temporary directory. It never reads, copies, migrates, or edits a user's real
catalog, which makes the golden-build command safe to run during handoff.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from catalog import Catalog, ImportRunCounts


def create_golden_fixture(root: Path) -> Path:
    """Create and return a schema-current three-image regression catalog."""
    root = root.expanduser().resolve()
    image_root = root / "fixture_images"
    image_root.mkdir(parents=True, exist_ok=True)
    database = root / "golden_fixture.db"

    registrations: list[tuple[int, int]] = []
    colors = ((30, 70, 140), (140, 70, 30), (70, 140, 30))
    with Catalog(database) as catalog:
        run_id = catalog.start_import_run(
            input_root=image_root,
            output_folder=root,
            model_name="golden-fixture",
            transformers_version="test",
            analysis_version=1,
            include_triage=True,
            reuse_stored_analysis=False,
        )
        for index, color in enumerate(colors, start=1):
            image_path = image_root / f"fixture_{index:02d}.png"
            Image.new("RGB", (96, 128), color).save(image_path)
            registration = catalog.register_file(
                file_path=image_path,
                input_root=image_root,
                run_id=run_id,
            )
            registrations.append(
                (registration.image_id, registration.file_id)
            )
        catalog.finish_import_run(
            run_id,
            status="complete",
            counts=ImportRunCounts(
                discovered_files=len(registrations),
                new_unique_images=len(registrations),
            ),
        )

        # Every image receives a current successful analysis, and at least two
        # share the same object tag. That is the historical tag-curation
        # fixture contract; raw provider output remains independent of edits.
        for image_id, file_id in registrations:
            catalog.store_successful_analysis(
                image_id=image_id,
                source_file_id=file_id,
                model_name="golden-fixture",
                transformers_version="test",
                analysis_version=1,
                include_triage=True,
                result={
                    "caption": f"Synthetic portrait fixture {image_id}.",
                    "detected_object_count": 2,
                    "object_labels": "person|portrait",
                    "person_count": 1,
                    "ocr_region_count": 0,
                    "ocr_character_count": 0,
                    "ocr_text": "",
                    "likely_screenshot_or_ui": "no",
                    "candidate_recommendation": "keep",
                    "recommendation_reason": "Golden-build fixture",
                    "triage_status": "complete",
                    "triage_error": "",
                    "processing_seconds": 0.001,
                },
            )

        # One image has a suggested identity and the others deliberately do
        # not, allowing the historical mixed-selection and identity-review
        # contracts to run without any external face model.
        first_image_id, first_file_id = registrations[0]
        face_model_id = catalog.register_face_model(
            provider_key="golden-fixture",
            provider_version="1",
            model_name="synthetic-face",
            model_fingerprint="golden-fixture-v1",
            model_root="",
            embedding_dimension=2,
            license_label="test fixture only",
        )
        _, detection_ids = catalog.store_face_result(
            image_id=first_image_id,
            source_file_id=first_file_id,
            face_model_id=face_model_id,
            status="success",
            error="",
            processing_seconds=0.001,
            detections=[
                {
                    "face_index": 0,
                    "bbox_x1": 20.0,
                    "bbox_y1": 16.0,
                    "bbox_x2": 72.0,
                    "bbox_y2": 78.0,
                    "detection_score": 0.99,
                    "landmarks_json": "[]",
                    "embedding": b"\x00" * 8,
                    "embedding_dimension": 2,
                    "embedding_norm": 1.0,
                }
            ],
        )
        identity_id = catalog.get_or_create_identity("Golden Subject")
        profile_id = catalog.upsert_identity_profile(
            identity_id=identity_id,
            face_model_id=face_model_id,
            profile_embedding=b"\x00" * 8,
            embedding_dimension=2,
            reference_count=1,
            reference_details_json="[]",
        )
        catalog.upsert_identity_match(
            face_detection_id=detection_ids[0],
            identity_profile_id=profile_id,
            similarity=0.99,
            threshold=0.65,
            is_suggested=True,
        )
        tag_id = catalog.get_or_create_tag(
            "Golden Subject",
            category="identity",
        )
        catalog.assign_tag(
            image_id=first_image_id,
            tag_id=tag_id,
            source=(
                f"face:golden-fixture:model-{face_model_id}:"
                f"identity-{identity_id}"
            ),
            confidence=0.99,
            review_status="suggested",
            notes="Synthetic golden-build identity suggestion.",
        )

    return database
