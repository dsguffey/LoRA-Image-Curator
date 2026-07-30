"""Optional, local body/pose analysis behind a provider-neutral boundary.

MediaPipe Pose Landmarker is the first vetted implementation because it runs
on-device, has a modest Windows installation footprint, and exposes 33
landmarks with visibility evidence.  The rest of LoRA Image Curator consumes
the normalized :class:`BodyAnalysisResult` below rather than MediaPipe objects.
That boundary lets a future RTMPose/ONNX provider produce the same catalog
fields without rewriting import filters or browser filters.

Privacy boundary
----------------
This module performs no network requests and contains no telemetry endpoint.
It reads a user-selected local image and local ``.task`` model.  Downloading a
model is a separate, explicit setup operation.

Evidence boundary
-----------------
``full_body_score`` is a review aid, not an anatomical truth.  It averages the
strongest visibility evidence for five regions (head, shoulders, hips, knees,
and feet).  ``full_body`` additionally requires visible head and foot evidence
so a high-confidence torso cannot be mislabeled as a complete figure.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


PROVIDER_KEY = "mediapipe_pose"
PROVIDER_LABEL = "Google MediaPipe Pose Landmarker"
SUPPORTED_MODEL_FILENAMES = frozenset(
    {
        "pose_landmarker_lite.task",
        "pose_landmarker_full.task",
        "pose_landmarker_heavy.task",
    }
)

# MediaPipe Pose Landmarker landmark indices.
HEAD = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
SHOULDERS = (11, 12)
HIPS = (23, 24)
KNEES = (25, 26)
FEET = (27, 28, 29, 30, 31, 32)
BODY_REGIONS = (HEAD, SHOULDERS, HIPS, KNEES, FEET)


@dataclass(slots=True, frozen=True)
class BodyProviderStatus:
    """Compatibility/setup facts suitable for Settings and smoke tests."""

    provider_key: str
    provider_label: str
    package_installed: bool
    package_version: str
    model_path: Path
    model_exists: bool
    model_filename_vetted: bool
    model_compatible: bool
    model_sha256: str
    notes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return whether analysis may run without a silent fallback."""
        return (
            self.package_installed
            and self.model_exists
            and self.model_filename_vetted
            and self.model_compatible
        )


@dataclass(slots=True, frozen=True)
class BodyAnalysisOptions:
    """User-controlled interpretation thresholds for one analysis pass."""

    detection_threshold: float = 0.50
    landmark_visibility_threshold: float = 0.50
    full_body_threshold_percent: int = 70
    maximum_poses: int = 4

    def normalized(self) -> "BodyAnalysisOptions":
        return BodyAnalysisOptions(
            detection_threshold=max(0.0, min(1.0, float(self.detection_threshold))),
            landmark_visibility_threshold=max(
                0.0,
                min(1.0, float(self.landmark_visibility_threshold)),
            ),
            full_body_threshold_percent=max(
                60,
                min(100, int(self.full_body_threshold_percent)),
            ),
            maximum_poses=max(1, min(10, int(self.maximum_poses))),
        )


@dataclass(slots=True, frozen=True)
class BodyAnalysisResult:
    """Provider-neutral evidence stored for one catalog image."""

    pose_count: int
    body_detected: bool
    face_visible: bool
    full_body_score: float
    full_body: bool
    classification: str
    landmarks_json: str


def calculate_file_sha256(path: Path) -> str:
    """Hash a model without loading the complete file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_body_setup(
    model_path: Path,
    *,
    perform_runtime_check: bool = True,
) -> BodyProviderStatus:
    """Inspect package/model compatibility without downloading anything.

    A manually browsed file must use one of the three official MediaPipe Pose
    Landmarker bundle names.  This is a conservative provenance guard, not a
    cryptographic proof of origin; the bundled installer is the recommended
    route because it downloads from Google's documented model host.
    """
    path = model_path.expanduser().resolve()
    notes: list[str] = []
    try:
        package_version = importlib.metadata.version("mediapipe")
        package_installed = True
    except importlib.metadata.PackageNotFoundError:
        package_version = "not installed"
        package_installed = False
        notes.append("Install the optional MediaPipe body-analysis dependency.")

    model_exists = path.is_file()
    if not model_exists:
        notes.append("Choose or install a local Pose Landmarker .task model.")
    filename_vetted = path.name.casefold() in SUPPORTED_MODEL_FILENAMES
    if model_exists and not filename_vetted:
        notes.append(
            "This release accepts only the vetted lite, full, or heavy "
            "MediaPipe Pose Landmarker bundle names."
        )

    model_sha256 = ""
    if model_exists:
        try:
            model_sha256 = calculate_file_sha256(path)
        except OSError as error:
            notes.append(f"Model could not be read: {error}")

    compatible = bool(package_installed and model_exists and filename_vetted)
    if compatible and perform_runtime_check:
        try:
            _create_landmarker(path, BodyAnalysisOptions(maximum_poses=1)).close()
        except Exception as error:
            compatible = False
            notes.append(
                "MediaPipe rejected the selected model: "
                f"{type(error).__name__}: {error}"
            )

    if compatible:
        notes.append(
            "Compatible local model. Ordinary analysis does not upload images "
            "or results."
        )

    return BodyProviderStatus(
        provider_key=PROVIDER_KEY,
        provider_label=PROVIDER_LABEL,
        package_installed=package_installed,
        package_version=package_version,
        model_path=path,
        model_exists=model_exists,
        model_filename_vetted=filename_vetted,
        model_compatible=compatible,
        model_sha256=model_sha256,
        notes=tuple(notes),
    )


class MediaPipeBodyAnalyzer:
    """Reuse one MediaPipe task instance across a batch of local images."""

    def __init__(
        self,
        model_path: Path,
        options: BodyAnalysisOptions,
    ) -> None:
        self.model_path = model_path.expanduser().resolve()
        self.options = options.normalized()
        status = inspect_body_setup(
            self.model_path,
            perform_runtime_check=False,
        )
        if not status.ready:
            raise RuntimeError("\n".join(status.notes) or "Body provider is not ready.")
        self._landmarker = _create_landmarker(self.model_path, self.options)

    def __enter__(self) -> "MediaPipeBodyAnalyzer":
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        """Release native model resources deterministically."""
        landmarker = getattr(self, "_landmarker", None)
        if landmarker is not None:
            landmarker.close()
            self._landmarker = None

    def analyze(self, image_path: Path) -> BodyAnalysisResult:
        """Analyze one image and normalize MediaPipe's landmark objects."""
        import mediapipe as mp

        mp_image = mp.Image.create_from_file(str(image_path))
        result = self._landmarker.detect(mp_image)
        poses = tuple(result.pose_landmarks or ())
        if not poses:
            return BodyAnalysisResult(
                pose_count=0,
                body_detected=False,
                face_visible=False,
                full_body_score=0.0,
                full_body=False,
                classification="no_body",
                landmarks_json="[]",
            )

        normalized_poses = [
            [
                {
                    "x": float(landmark.x),
                    "y": float(landmark.y),
                    "z": float(landmark.z),
                    "visibility": float(landmark.visibility or 0.0),
                    "presence": float(landmark.presence or 0.0),
                }
                for landmark in pose
            ]
            for pose in poses
        ]
        evidence = [
            _pose_evidence(pose, self.options)
            for pose in normalized_poses
        ]
        best_index = max(
            range(len(evidence)),
            key=lambda index: evidence[index][0],
        )
        score, face_visible, feet_visible = evidence[best_index]
        full_body = bool(
            score * 100.0 >= self.options.full_body_threshold_percent
            and face_visible
            and feet_visible
        )
        if full_body:
            classification = "full_body"
        elif score >= 0.45:
            classification = "partial_body"
        elif face_visible:
            classification = "face_or_closeup"
        else:
            classification = "body_fragment"

        return BodyAnalysisResult(
            pose_count=len(poses),
            body_detected=True,
            face_visible=face_visible,
            full_body_score=score,
            full_body=full_body,
            classification=classification,
            landmarks_json=json.dumps(
                normalized_poses,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )


def _create_landmarker(
    model_path: Path,
    options: BodyAnalysisOptions,
) -> Any:
    """Construct MediaPipe lazily so the main application has no hard import."""
    import mediapipe as mp

    normalized = options.normalized()
    landmarker_options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=normalized.maximum_poses,
        min_pose_detection_confidence=normalized.detection_threshold,
        min_pose_presence_confidence=normalized.detection_threshold,
        output_segmentation_masks=False,
    )
    return mp.tasks.vision.PoseLandmarker.create_from_options(landmarker_options)


def _pose_evidence(
    landmarks: Sequence[dict[str, float]],
    options: BodyAnalysisOptions,
) -> tuple[float, bool, bool]:
    """Return completeness, face evidence, and foot evidence for one pose."""
    threshold = options.landmark_visibility_threshold

    def region_strength(indices: Iterable[int]) -> float:
        strengths = [
            min(
                float(landmarks[index].get("visibility", 0.0)),
                float(landmarks[index].get("presence", 0.0)),
            )
            for index in indices
            if index < len(landmarks)
        ]
        return max(strengths, default=0.0)

    strengths = tuple(region_strength(region) for region in BODY_REGIONS)
    score = sum(strengths) / len(strengths)
    visible_head_points = sum(
        1
        for index in HEAD
        if index < len(landmarks)
        and min(
            float(landmarks[index].get("visibility", 0.0)),
            float(landmarks[index].get("presence", 0.0)),
        )
        >= threshold
    )
    face_visible = visible_head_points >= 3
    feet_visible = strengths[-1] >= threshold
    return score, face_visible, feet_visible

