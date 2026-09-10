"""Focused production checks for LIC's active OpenCV YuNet + SFace provider.

The live test deliberately takes paths from environment variables so the
repository never carries model weights or personal image paths.  It is run by
the qualification command with the already verified model pair and demo
fixtures.  The persistence test needs no OpenCV model at all.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

from pathlib import Path

import numpy as np

from PIL import Image


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from catalog import CATALOG_FILENAME
from catalog_import import CatalogImportOptions, import_catalog_folder
from face_analyzer import (
    DEFAULT_MODEL_NAME,
    DEFAULT_SIMILARITY_THRESHOLD,
    FaceAnalysisOptions,
    FaceDetection,
    FaceProviderUnavailableError,
    OpenCvYuNetSFaceProvider,
    SFACE_MODEL_FILENAME,
    YUNET_MODEL_FILENAME,
    build_identity_profile,
    calculate_yunet_sface_fingerprint,
    inspect_face_setup,
    normalize_embedding,
    analyze_faces,
)


def _live_assets() -> tuple[Path, Path, Path]:
    model_folder = Path(os.environ["LIC_YUNET_SFACE_MODEL_FOLDER"])
    image_folder = Path(os.environ["LIC_FACE_FIXTURE_FOLDER"])
    reference_folder = Path(os.environ["LIC_FACE_REFERENCE_FOLDER"])
    for path in (model_folder, image_folder, reference_folder):
        if not path.exists():
            raise RuntimeError(f"Missing qualification asset: {path}")
    return model_folder, image_folder, reference_folder


def _copy_fixture(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


class _Provider:
    """Deterministic provider metadata used only to prove catalog reuse guards."""

    def __init__(self, *, provider_key: str, fingerprint: str, dimension: int) -> None:
        self.provider_key = provider_key
        self.provider_version = "test"
        self.model_name = "test-model"
        self.model_root = Path("test-model-root")
        self.model_fingerprint = fingerprint
        self.execution_provider = "test"
        self.license_label = "test"
        self.embedding_dimension = dimension

    def analyze_image(self, _image_path: Path) -> list[FaceDetection]:
        values = np.zeros(self.embedding_dimension, dtype=np.float32)
        values[0] = 1.0
        return [
            FaceDetection(
                bbox=(4.0, 4.0, 40.0, 40.0),
                detection_score=0.99,
                landmarks=((10.0, 12.0),) * 5,
                embedding=values,
            )
        ]


def _catalog_with_one_image(root: Path) -> tuple[Path, Path, Path]:
    input_folder = root / "input"
    output_folder = root / "output"
    output_folder.mkdir()
    image_path = input_folder / "face.png"
    image_path.parent.mkdir()
    Image.new("RGB", (64, 64), "navy").save(image_path)
    database = output_folder / CATALOG_FILENAME
    import_catalog_folder(
        CatalogImportOptions(
            source_folder=input_folder,
            target_database=database,
            mode="create",
            create_image_set=False,
        )
    )
    return input_folder, output_folder, database


def test_provider_metadata_and_missing_pair_are_explicit() -> None:
    assert DEFAULT_MODEL_NAME == "opencv-yunet-sface"
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.50
    with tempfile.TemporaryDirectory(prefix="lic_yunet_missing_") as temporary:
        missing_folder = Path(temporary)
        status = inspect_face_setup(model_root=str(missing_folder))
        assert not status.model_installed
        assert YUNET_MODEL_FILENAME in "\n".join(status.notes)
        try:
            OpenCvYuNetSFaceProvider(FaceAnalysisOptions(model_root=str(missing_folder)))
        except FaceProviderUnavailableError as error:
            assert "YuNet + SFace" in str(error)
        else:
            raise AssertionError("Incomplete YuNet/SFace resources unexpectedly loaded")

        (missing_folder / YUNET_MODEL_FILENAME).write_bytes(b"wrong-yunet")
        (missing_folder / SFACE_MODEL_FILENAME).write_bytes(b"wrong-sface")
        wrong_hash_status = inspect_face_setup(model_root=str(missing_folder))
        assert not wrong_hash_status.model_installed
        assert "bytes do not match" in "\n".join(wrong_hash_status.notes)


def test_live_provider_contract_reference_profile_and_detection_only() -> None:
    model_folder, image_folder, reference_folder = _live_assets()
    status = inspect_face_setup(model_root=str(model_folder))
    assert status.opencv_installed
    assert status.model_installed

    provider = OpenCvYuNetSFaceProvider(
        FaceAnalysisOptions(model_root=str(model_folder))
    )
    assert provider.provider_key == "opencv-yunet-sface"
    assert provider.embedding_dimension == 128
    assert provider.model_fingerprint == calculate_yunet_sface_fingerprint(model_folder)

    reference = image_folder / "001_clean_11.jpg"
    exact_match = image_folder / "002_exact_duplicate_11.jpg"
    different_identity = image_folder / "001_clean_09.jpg"
    reference_face = provider.analyze_image(reference)[0]
    exact_face = provider.analyze_image(exact_match)[0]
    different_face = provider.analyze_image(different_identity)[0]
    assert reference_face.detection_score > 0.0
    assert reference_face.bbox[2] > reference_face.bbox[0]
    assert reference_face.bbox[3] > reference_face.bbox[1]
    assert len(reference_face.landmarks) == 5
    assert reference_face.embedding.shape == (128,)
    assert np.isclose(np.linalg.norm(reference_face.embedding), 1.0, atol=1e-5)
    assert float(np.dot(reference_face.embedding, exact_face.embedding)) >= 0.50
    assert float(np.dot(reference_face.embedding, different_face.embedding)) < 0.50

    profile, details, image_count = build_identity_profile(
        provider=provider,
        reference_folder=reference_folder,
        status_callback=None,
    )
    assert image_count >= 1
    assert any(detail["status"] == "used" for detail in details)
    assert profile.shape == (128,)
    assert np.isclose(np.linalg.norm(profile), 1.0, atol=1e-5)

    with tempfile.TemporaryDirectory(prefix="lic_yunet_live_") as temporary:
        root = Path(temporary)
        input_folder = root / "input"
        output_folder = root / "output"
        output_folder.mkdir()
        for source in (reference, exact_match, different_identity):
            _copy_fixture(source, input_folder / source.name)
        database = output_folder / CATALOG_FILENAME
        import_catalog_folder(
            CatalogImportOptions(
                source_folder=input_folder,
                target_database=database,
                mode="create",
                create_image_set=False,
            )
        )
        detection_only = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            options=FaceAnalysisOptions(model_root=str(model_folder)),
            reuse_stored_analysis=False,
        )
        assert not detection_only.identity_matching_enabled
        assert detection_only.faces_detected == 3
        assert detection_only.suggestions_created == 0


def test_persistence_never_reuses_legacy_or_mismatched_model_results() -> None:
    with tempfile.TemporaryDirectory(prefix="lic_yunet_reuse_") as temporary:
        input_folder, output_folder, database = _catalog_with_one_image(Path(temporary))
        legacy = _Provider(provider_key="insightface", fingerprint="legacy-512", dimension=512)
        active = _Provider(
            provider_key="opencv-yunet-sface", fingerprint="qualified-pair-a", dimension=128
        )
        changed_pair = _Provider(
            provider_key="opencv-yunet-sface", fingerprint="qualified-pair-b", dimension=128
        )

        first = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            provider=legacy,
            reuse_stored_analysis=True,
        )
        second = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            provider=active,
            reuse_stored_analysis=True,
        )
        third = analyze_faces(
            input_folder=input_folder,
            output_folder=output_folder,
            provider=changed_pair,
            reuse_stored_analysis=True,
        )

        assert first.generated_images == 1
        assert second.generated_images == 1 and second.reused_images == 0
        assert third.generated_images == 1 and third.reused_images == 0
        connection = sqlite3.connect(database)
        try:
            records = connection.execute(
                "SELECT provider_key, model_fingerprint, embedding_dimension "
                "FROM face_models ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        assert records == [
            ("insightface", "legacy-512", 512),
            ("opencv-yunet-sface", "qualified-pair-a", 128),
            ("opencv-yunet-sface", "qualified-pair-b", 128),
        ]


if __name__ == "__main__":
    test_provider_metadata_and_missing_pair_are_explicit()
    test_live_provider_contract_reference_profile_and_detection_only()
    test_persistence_never_reuses_legacy_or_mismatched_model_results()
    print("YuNet/SFace provider tests passed.")
