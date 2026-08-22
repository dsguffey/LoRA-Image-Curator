"""
face_analyzer.py

Modular face detection, embedding, and identity-reference analysis.

Design goals
------------
This module deliberately keeps the LoRA Image Curator catalog independent from one
vendor-specific Python package.  The batch workflow consumes the small
``FaceProvider`` interface defined below.  ``InsightFaceProvider`` is the first
implementation, while tests and future releases can supply another provider
without changing the database or GUI workflow.

The provider is responsible only for turning an image into structured face
records:

- bounding box
- detector confidence
- optional landmarks
- normalized identity embedding

LoRA Image Curator remains responsible for persistence, identity profiles,
similarity comparisons, review-state preservation, tags, CSV reporting, and
safe reuse of prior results.

Privacy and scope
-----------------
All processing is local. LoRA Image Curator does not upload source images,
embeddings, or identity names.  This release intentionally does *not* request
age, gender, emotion, or face-swap modules because those outputs are unrelated
to dataset curation and would add unnecessary dependencies and sensitive data.

Model licensing
---------------
InsightFace's Python code is MIT licensed, but the pretrained model packs that
InsightFace distributes (including ``buffalo_l``) are restricted to
non-commercial research use unless the user obtains another license.  The GUI
shows that distinction before allowing an automatic model download.  A future
model manager can point this provider at a separately licensed/user-supplied
model pack through ``model_name`` and ``model_root``.

Model location contract
-----------------------
InsightFace resolves a pack as ``<root>/models/<name>``. Public helpers in this
module validate that ``name`` is one safe path component and translate a folder
selected in the GUI back into that root/name pair. This both provides a usable
Browse workflow and prevents a typed model name from escaping the intended
``models`` directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import numpy as np

from PIL import Image, UnidentifiedImageError

from analysis_control import (
    AnalysisCancelled,
    raise_if_cancelled,
    wait_if_paused,
)
from catalog import CATALOG_FILENAME, Catalog
from image_discovery import (
    SUPPORTED_IMAGE_EXTENSIONS,
    discover_supported_images,
)


PROVIDER_KEY = "insightface"
DEFAULT_MODEL_NAME = "buffalo_l"
DEFAULT_SIMILARITY_THRESHOLD = 0.48
DEFAULT_DETECTION_THRESHOLD = 0.50
MODEL_LICENSE_LABEL = "InsightFace pretrained model: non-commercial research only"

SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
REPORT_FLUSH_INTERVAL = 25

ProgressCallback = Callable[[str, int, int, Path], None]
StatusCallback = Callable[[str], None]


# =============================================================================
# Public data structures
# =============================================================================

@dataclass(slots=True)
class FaceDetection:
    """Provider-neutral representation of one detected face."""

    bbox: tuple[float, float, float, float]
    detection_score: float
    landmarks: tuple[tuple[float, float], ...]
    embedding: np.ndarray


@dataclass(slots=True)
class FaceAnalysisOptions:
    """Configuration that affects face detection and result compatibility."""

    model_name: str = DEFAULT_MODEL_NAME
    model_root: str = ""
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    detection_threshold: float = DEFAULT_DETECTION_THRESHOLD


@dataclass(slots=True)
class FaceSetupStatus:
    """Dependency/model information suitable for a GUI diagnostic dialog."""

    insightface_installed: bool
    insightface_version: str
    onnxruntime_installed: bool
    onnxruntime_version: str
    available_execution_providers: tuple[str, ...]
    model_path: Path
    model_installed: bool
    recommended_execution_provider: str
    notes: tuple[str, ...]


@dataclass(slots=True)
class FaceImageReportRow:
    """One human-readable CSV row for the face-analysis pass."""

    catalog_image_id: int
    catalog_file_id: int
    content_sha256: str
    filename: str
    relative_path: str

    face_analysis_source: str
    face_count: int
    best_identity_name: str
    best_similarity: float | None
    suggested_identity: str
    matched_face_index: int | None

    provider_key: str
    provider_version: str
    model_name: str
    model_fingerprint: str
    execution_provider: str
    similarity_threshold: float

    status: str
    error: str
    processing_seconds: float


@dataclass(slots=True)
class FaceBatchSummary:
    """Final statistics returned to the pipeline and GUI."""

    total_images: int
    successful_images: int
    failed_images: int
    total_seconds: float

    output_csv: Path
    catalog_database: Path

    identity_name: str
    identity_matching_enabled: bool
    identity_profile_warning: str
    reference_images_found: int
    reference_faces_used: int
    similarity_threshold: float

    provider_key: str
    provider_version: str
    model_name: str
    model_fingerprint: str
    execution_provider: str

    generated_images: int
    reused_images: int
    faces_detected: int
    suggestions_created: int


class FaceProviderUnavailableError(RuntimeError):
    """Raised when optional face dependencies are absent or unusable."""


class FaceModelDownloadRequiredError(RuntimeError):
    """Raised when the selected model is absent and download was not approved."""


class IdentityProfileUnavailableError(RuntimeError):
    """Carry reference diagnostics when detection can continue without matching.

    A missing identity profile is not a face-detector failure.  The catalog can
    still store bounding boxes, landmarks, and embeddings for every input
    image, so callers use this typed exception to enter an explicit
    detection-only fallback instead of discarding the entire provider run.
    """

    def __init__(
        self,
        message: str,
        *,
        reference_details: list[dict[str, Any]],
        reference_images_found: int,
    ) -> None:
        super().__init__(message)
        self.reference_details = reference_details
        self.reference_images_found = int(reference_images_found)


@runtime_checkable
class FaceProvider(Protocol):
    """Small interface every face-analysis backend must implement."""

    provider_key: str
    provider_version: str
    model_name: str
    model_root: Path
    model_fingerprint: str
    execution_provider: str
    license_label: str
    embedding_dimension: int

    def analyze_image(self, image_path: Path) -> list[FaceDetection]:
        """Return every detected face in deterministic display order."""


# =============================================================================
# General helpers
# =============================================================================

def emit_status(callback: StatusCallback | None, message: str) -> None:
    """Send a status line when the caller supplied a callback."""
    if callback is not None:
        callback(message)


def emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    completed: int,
    total: int,
    path: Path,
) -> None:
    """Send phase-aware progress without coupling this module to Tkinter."""
    if callback is not None:
        callback(phase, completed, total, path)


def resolve_model_root(model_root: str | Path) -> Path:
    """Return the InsightFace home directory used to locate ``models``."""
    if str(model_root).strip():
        return Path(model_root).expanduser().resolve()

    return (Path.home() / ".insightface").resolve()


def normalize_model_name(model_name: str) -> str:
    """Return one safe InsightFace model-pack directory name.

    InsightFace accepts a pack *name*, not an arbitrary path. Rejecting
    separators and traversal components keeps model discovery beneath the
    selected ``<root>/models`` directory and produces a clear GUI error for
    values that a Browse action should represent instead.
    """
    normalized = " ".join(str(model_name).split())
    if not normalized:
        raise ValueError("An InsightFace model-pack name is required.")

    if (
        normalized in {".", ".."}
        or Path(normalized).is_absolute()
        or "/" in normalized
        or "\\" in normalized
        or len(Path(normalized).parts) != 1
    ):
        raise ValueError(
            "The InsightFace model-pack name must be one folder name, not a path."
        )
    return normalized


def get_model_path(model_name: str, model_root: str | Path) -> Path:
    """Return the validated conventional InsightFace model-pack directory."""
    return (
        resolve_model_root(model_root)
        / "models"
        / normalize_model_name(model_name)
    )


def model_selection_from_pack_folder(
    selected_folder: str | Path,
) -> tuple[str, Path]:
    """Translate a browsed pack directory into InsightFace name/root settings.

    A loadable InsightFace pack has the layout
    ``<root>/models/<pack name>/*.onnx``. Requiring that established layout
    avoids copying or relocating large model files and guarantees the values
    handed to ``FaceAnalysis`` describe the folder the user actually selected.
    """
    pack_path = Path(selected_folder).expanduser().resolve()
    if not pack_path.exists() or not pack_path.is_dir():
        raise ValueError("The selected InsightFace model-pack folder is not valid.")
    if pack_path.parent.name.casefold() != "models":
        raise ValueError(
            "Choose the model-pack folder directly inside an InsightFace "
            "'models' folder."
        )
    if not any(pack_path.rglob("*.onnx")):
        raise ValueError(
            "The selected InsightFace model-pack folder contains no ONNX files."
        )

    model_name = normalize_model_name(pack_path.name)
    model_root = pack_path.parent.parent.resolve()
    if get_model_path(model_name, model_root) != pack_path:
        raise ValueError("The selected model-pack folder could not be normalized.")
    return model_name, model_root


def find_image_files(
    folder: Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    """Find supported images in the configured scope and deterministic order."""
    return discover_supported_images(folder, recursive=recursive)


def validate_folder(folder: Path, label: str) -> Path:
    """Normalize and validate a required directory."""
    normalized = folder.expanduser().resolve()

    if not normalized.exists() or not normalized.is_dir():
        raise FileNotFoundError(
            f"The selected {label} folder is not valid:\n{normalized}"
        )

    return normalized


def create_report_paths(output_folder: Path) -> tuple[Path, Path]:
    """Create partial/final CSV paths so interrupted work remains inspectable."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    final_path = output_folder / f"face_results_{timestamp}.csv"
    partial_path = final_path.with_suffix(".partial.csv")
    return partial_path, final_path


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    """Return a finite float32 unit vector or raise a clear model-data error."""
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)

    if vector.size == 0 or not np.all(np.isfinite(vector)):
        raise ValueError("The face model returned an invalid embedding.")

    norm = float(np.linalg.norm(vector))

    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("The face model returned a zero-length embedding.")

    return vector / norm


def calculate_model_fingerprint(model_path: Path) -> str:
    """
    Create a stable, inexpensive fingerprint for the selected ONNX pack.

    Hashing hundreds of megabytes of model weights on every launch would add
    needless delay.  The fingerprint instead hashes each ONNX file's relative
    name, byte size, and nanosecond modification time.  Replacing a model file
    therefore invalidates stored analysis in normal workflows while startup
    remains quick.
    """
    model_files = sorted(
        model_path.rglob("*.onnx"),
        key=lambda path: str(path.relative_to(model_path)).casefold(),
    )

    if not model_files:
        raise FileNotFoundError(
            "The selected model folder does not contain any ONNX files:\n"
            f"{model_path}"
        )

    digest = hashlib.sha256()

    for model_file in model_files:
        stat_result = model_file.stat()
        relative_name = str(model_file.relative_to(model_path)).replace("\\", "/")
        manifest_line = (
            f"{relative_name}|{stat_result.st_size}|{stat_result.st_mtime_ns}\n"
        )
        digest.update(manifest_line.encode("utf-8"))

    return digest.hexdigest()


def embedding_from_blob(blob: bytes, expected_dimension: int) -> np.ndarray:
    """Rehydrate one float32 embedding stored by SQLite."""
    vector = np.frombuffer(blob, dtype=np.float32).copy()

    if vector.size != expected_dimension:
        raise ValueError(
            "Stored face embedding dimension does not match its database "
            f"metadata ({vector.size} != {expected_dimension})."
        )

    return normalize_embedding(vector)


def largest_face(detections: Sequence[FaceDetection]) -> FaceDetection:
    """Choose the largest detected face for an identity-reference image."""
    return max(
        detections,
        key=lambda detection: max(0.0, detection.bbox[2] - detection.bbox[0])
        * max(0.0, detection.bbox[3] - detection.bbox[1]),
    )


def _insightface_embedding_dimension(application: Any) -> int:
    """Read the recognition model's output width without analyzing an image.

    Detection-only fallback still needs to register the exact face model before
    catalog rows can be stored.  InsightFace exposes the recognition ONNX
    session through its prepared application; the final output dimension is
    the embedding width (512 for ``buffalo_l``).  Returning zero keeps custom
    providers usable even when they do not expose equivalent metadata.  A
    later successful identity profile will update the registered model with
    the observed dimension.
    """
    models = getattr(application, "models", None)
    recognition = models.get("recognition") if isinstance(models, dict) else None
    session = getattr(recognition, "session", None)
    if session is None:
        return 0

    try:
        outputs = session.get_outputs()
        output_shape = outputs[0].shape if outputs else ()
        dimension = output_shape[-1] if output_shape else 0
        return int(dimension) if int(dimension) > 0 else 0
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0


# =============================================================================
# Optional dependency and model diagnostics
# =============================================================================

def inspect_face_setup(
    model_name: str = DEFAULT_MODEL_NAME,
    model_root: str = "",
) -> FaceSetupStatus:
    """Inspect optional packages without importing the heavy face model."""
    notes: list[str] = []

    try:
        insightface_version = version("insightface")
        insightface_installed = True
    except PackageNotFoundError:
        insightface_version = "not installed"
        insightface_installed = False
        notes.append(
            "Install InsightFace with the included dependency installer."
        )

    available_providers: tuple[str, ...] = ()

    try:
        onnxruntime_version = version("onnxruntime-gpu")
        onnxruntime_installed = True
    except PackageNotFoundError:
        try:
            onnxruntime_version = version("onnxruntime")
            onnxruntime_installed = True
        except PackageNotFoundError:
            onnxruntime_version = "not installed"
            onnxruntime_installed = False
            notes.append(
                "Install ONNX Runtime GPU (or CPU fallback) with the included "
                "dependency installer."
            )

    if onnxruntime_installed:
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]

            available_providers = tuple(ort.get_available_providers())
        except Exception as error:
            notes.append(
                "ONNX Runtime is installed but could not initialize: "
                f"{type(error).__name__}: {error}"
            )

    if "CUDAExecutionProvider" in available_providers:
        recommended_provider = "CUDAExecutionProvider"
    elif "CPUExecutionProvider" in available_providers:
        recommended_provider = "CPUExecutionProvider"
        notes.append(
            "CUDAExecutionProvider is unavailable; face analysis will use the CPU."
        )
    else:
        recommended_provider = "unavailable"

    model_name = normalize_model_name(model_name)
    model_path = get_model_path(model_name, model_root)
    model_installed = model_path.exists() and any(model_path.rglob("*.onnx"))

    if not model_installed:
        notes.append(
            "The selected model pack is not installed. LoRA Image Curator can ask "
            "InsightFace to download it after you accept its model license."
        )

    return FaceSetupStatus(
        insightface_installed=insightface_installed,
        insightface_version=insightface_version,
        onnxruntime_installed=onnxruntime_installed,
        onnxruntime_version=onnxruntime_version,
        available_execution_providers=available_providers,
        model_path=model_path,
        model_installed=model_installed,
        recommended_execution_provider=recommended_provider,
        notes=tuple(notes),
    )


# =============================================================================
# InsightFace provider implementation
# =============================================================================

class InsightFaceProvider:
    """InsightFace implementation of the provider-neutral face interface."""

    provider_key = PROVIDER_KEY
    license_label = MODEL_LICENSE_LABEL

    def __init__(
        self,
        options: FaceAnalysisOptions,
        *,
        allow_model_download: bool,
        status_callback: StatusCallback | None = None,
    ) -> None:
        self.model_name = normalize_model_name(options.model_name)
        self.model_root = resolve_model_root(options.model_root)
        self._detection_threshold = float(options.detection_threshold)

        setup = inspect_face_setup(self.model_name, str(self.model_root))

        if not setup.insightface_installed or not setup.onnxruntime_installed:
            raise FaceProviderUnavailableError(
                "Face-analysis dependencies are not installed.\n\n"
                "Open Setup & Repair from the Tools menu, or run "
                "Setup and Launch LoRA Image Curator.bat and choose optional "
                "face analysis. Then use Check Face Setup in the app."
            )

        if not setup.model_installed and not allow_model_download:
            raise FaceModelDownloadRequiredError(
                "The selected InsightFace model pack is not installed:\n"
                f"{setup.model_path}"
            )

        if not setup.model_installed and allow_model_download:
            emit_status(
                status_callback,
                "Downloading the approved InsightFace model pack...",
            )

        # Importing torch before ONNX Runtime lets recent ORT releases reuse the
        # CUDA/cuDNN DLLs already shipped with the project's working PyTorch
        # installation.  This avoids requiring a second system-wide CUDA setup.
        try:
            import torch  # noqa: F401
        except Exception:
            pass

        try:
            import onnxruntime as ort  # type: ignore[import-not-found]

            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls()
                except Exception:
                    # Provider selection below supplies the actionable result.
                    pass

            available_providers = tuple(ort.get_available_providers())
        except Exception as error:
            raise FaceProviderUnavailableError(
                "ONNX Runtime could not initialize.\n\n"
                f"{type(error).__name__}: {error}\n\n"
                "Run Check Face Setup for details."
            ) from error

        if "CUDAExecutionProvider" in available_providers:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            ctx_id = 0
            self.execution_provider = "CUDAExecutionProvider"
        elif "CPUExecutionProvider" in available_providers:
            providers = ["CPUExecutionProvider"]
            ctx_id = -1
            self.execution_provider = "CPUExecutionProvider"
        else:
            raise FaceProviderUnavailableError(
                "ONNX Runtime did not report a usable CUDA or CPU execution "
                "provider."
            )

        try:
            import insightface  # type: ignore[import-not-found]
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except Exception as error:
            raise FaceProviderUnavailableError(
                "InsightFace could not be imported.\n\n"
                f"{type(error).__name__}: {error}"
            ) from error

        self.provider_version = str(
            getattr(insightface, "__version__", setup.insightface_version)
        )

        emit_status(
            status_callback,
            f"Loading face provider: InsightFace {self.provider_version}",
        )
        emit_status(
            status_callback,
            f"Face execution provider: {self.execution_provider}",
        )

        try:
            self._application = FaceAnalysis(
                name=self.model_name,
                root=str(self.model_root),
                allowed_modules=["detection", "recognition"],
                providers=providers,
            )
            self._application.prepare(
                ctx_id=ctx_id,
                det_thresh=self._detection_threshold,
                det_size=(640, 640),
            )
        except Exception as error:
            raise RuntimeError(
                "InsightFace could not load the selected model pack.\n\n"
                f"Model: {self.model_name}\n"
                f"Root: {self.model_root}\n\n"
                f"{type(error).__name__}: {error}"
            ) from error

        self.embedding_dimension = _insightface_embedding_dimension(
            self._application
        )

        model_path = get_model_path(self.model_name, self.model_root)
        self.model_fingerprint = calculate_model_fingerprint(model_path)

    def analyze_image(self, image_path: Path) -> list[FaceDetection]:
        """Open one image and return normalized, deterministic face records."""
        try:
            with Image.open(image_path) as source_image:
                rgb_image = source_image.convert("RGB")
                rgb_array = np.asarray(rgb_image, dtype=np.uint8)
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError(f"Could not open image: {error}") from error

        # InsightFace/OpenCV expects BGR channel order.  ``copy`` produces a
        # contiguous positive-stride array rather than a reversed view.
        bgr_array = rgb_array[:, :, ::-1].copy()
        raw_faces = self._application.get(bgr_array)

        detections: list[FaceDetection] = []

        for raw_face in raw_faces:
            bbox_values = np.asarray(raw_face.bbox, dtype=np.float32).reshape(4)
            score = float(getattr(raw_face, "det_score", 0.0))
            landmarks_value = getattr(raw_face, "kps", None)

            if landmarks_value is None:
                landmarks: tuple[tuple[float, float], ...] = ()
            else:
                landmarks_array = np.asarray(
                    landmarks_value,
                    dtype=np.float32,
                ).reshape(-1, 2)
                landmarks = tuple(
                    (float(point[0]), float(point[1]))
                    for point in landmarks_array
                )

            embedding = normalize_embedding(raw_face.normed_embedding)
            if self.embedding_dimension <= 0:
                self.embedding_dimension = int(embedding.size)

            detections.append(
                FaceDetection(
                    bbox=tuple(float(value) for value in bbox_values),
                    detection_score=score,
                    landmarks=landmarks,
                    embedding=embedding,
                )
            )

        # Model output order is not a durable identifier.  Sorting spatially
        # makes face_index stable enough for CSV review and repeatable tests.
        detections.sort(
            key=lambda detection: (
                round(detection.bbox[1], 3),
                round(detection.bbox[0], 3),
            )
        )
        return detections


# =============================================================================
# Reference profile construction
# =============================================================================

def build_identity_profile(
    *,
    provider: FaceProvider,
    reference_folder: Path,
    status_callback: StatusCallback | None,
    recursive: bool = True,
) -> tuple[np.ndarray, list[dict[str, Any]], int]:
    """
    Analyze a reference folder and return a normalized mean identity embedding.

    When a reference image contains multiple faces, the largest face is used and
    a warning is recorded.  This is predictable and useful for ordinary portrait
    folders, but the GUI explains that clean one-person reference images are
    preferable.
    """
    reference_files = find_image_files(reference_folder, recursive=recursive)

    if not reference_files:
        raise IdentityProfileUnavailableError(
            "No supported images were found in the identity reference folder.",
            reference_details=[],
            reference_images_found=0,
        )

    embeddings: list[np.ndarray] = []
    details: list[dict[str, Any]] = []

    emit_status(
        status_callback,
        f"Building identity profile from {len(reference_files)} reference images...",
    )

    for index, reference_path in enumerate(reference_files, start=1):
        try:
            detections = provider.analyze_image(reference_path)

            if not detections:
                details.append(
                    {
                        "path": str(reference_path),
                        "status": "no_face",
                        "face_count": 0,
                    }
                )
                emit_status(
                    status_callback,
                    f"Reference [{index}/{len(reference_files)}] no face: "
                    f"{reference_path.name}",
                )
                continue

            selected_face = largest_face(detections)
            embeddings.append(normalize_embedding(selected_face.embedding))

            details.append(
                {
                    "path": str(reference_path),
                    "status": "used",
                    "face_count": len(detections),
                    "selection": "largest_face",
                    "detection_score": selected_face.detection_score,
                    "bbox": list(selected_face.bbox),
                }
            )

            suffix = " (largest of multiple faces)" if len(detections) > 1 else ""
            emit_status(
                status_callback,
                f"Reference [{index}/{len(reference_files)}] used: "
                f"{reference_path.name}{suffix}",
            )

        except Exception as error:
            details.append(
                {
                    "path": str(reference_path),
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            emit_status(
                status_callback,
                f"Reference [{index}/{len(reference_files)}] error: "
                f"{reference_path.name} — {error}",
            )

    if not embeddings:
        raise IdentityProfileUnavailableError(
            "No usable face was found in the identity reference folder. "
            "Face detection continued, but identity matching was skipped.",
            reference_details=details,
            reference_images_found=len(reference_files),
        )

    dimensions = {embedding.size for embedding in embeddings}

    if len(dimensions) != 1:
        raise RuntimeError(
            "The face provider returned inconsistent embedding dimensions for "
            "the reference images."
        )

    profile = normalize_embedding(np.mean(np.stack(embeddings), axis=0))
    return profile, details, len(reference_files)


# =============================================================================
# Complete face-analysis workflow
# =============================================================================

def analyze_faces(
    input_folder: Path,
    output_folder: Path,
    *,
    identity_name: str = "",
    reference_folder: Path | None = None,
    options: FaceAnalysisOptions | None = None,
    reuse_stored_analysis: bool = True,
    recursive: bool = True,
    reference_recursive: bool = True,
    allow_model_download: bool = False,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    provider: FaceProvider | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
) -> FaceBatchSummary:
    """
    Detect and store faces, then optionally compare them to an identity profile.

    ``provider`` is injectable for tests and future backends.  Production calls
    normally leave it as ``None``, which selects ``InsightFaceProvider``. A
    Trigger Keyword and valid reference folder enable identity matching; face
    detection remains available without either one.
    """
    batch_start = time.perf_counter()
    options = options or FaceAnalysisOptions()

    input_folder = validate_folder(input_folder, "input")
    output_folder = validate_folder(output_folder, "output")

    cleaned_identity_name = " ".join(identity_name.split())
    validated_reference_folder: Path | None = None
    reference_configuration_warning = ""
    if reference_folder is None:
        reference_configuration_warning = (
            "No identity reference folder was configured. Face detection "
            "continued, but identity matching was skipped."
        )
    else:
        try:
            validated_reference_folder = validate_folder(
                reference_folder,
                "identity reference",
            )
        except FileNotFoundError as error:
            reference_configuration_warning = (
                f"{error} Face detection continued, but identity matching was "
                "skipped."
            )

    if not cleaned_identity_name:
        reference_configuration_warning = (
            "No Trigger Keyword was configured. Face detection continued, but "
            "identity matching was skipped."
        )

    if not 0.0 <= float(options.similarity_threshold) <= 1.0:
        raise ValueError("Face similarity threshold must be between 0 and 1.")

    if not 0.0 <= float(options.detection_threshold) <= 1.0:
        raise ValueError("Face detection threshold must be between 0 and 1.")

    image_files = find_image_files(input_folder, recursive=recursive)
    wait_if_paused(pause_event, cancel_event)

    if not image_files:
        raise FileNotFoundError(
            "No supported images were found in the selected input folder.\n\n"
            f"Folder checked:\n{input_folder}\n\n"
            "Supported extensions:\n"
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )
    emit_status(
        status_callback,
        f"Face image folder checked: {input_folder}",
    )
    emit_status(
        status_callback,
        f"Supported images found for face analysis: {len(image_files):,}",
    )

    partial_csv, final_csv = create_report_paths(output_folder)
    catalog_database = output_folder / CATALOG_FILENAME

    if not catalog_database.exists():
        raise FileNotFoundError(
            "The face provider expected the catalog created by the Florence "
            "stage, but it was not found:\n"
            f"{catalog_database}"
        )

    if provider is None:
        wait_if_paused(pause_event, cancel_event)
        provider = InsightFaceProvider(
            options,
            allow_model_download=allow_model_download,
            status_callback=status_callback,
        )

    identity_matching_enabled = not reference_configuration_warning
    identity_profile_warning = reference_configuration_warning
    profile_embedding: np.ndarray | None = None
    reference_details: list[dict[str, Any]] = []
    reference_images_found = 0
    if identity_matching_enabled and validated_reference_folder is not None:
        try:
            profile_embedding, reference_details, reference_images_found = (
                build_identity_profile(
                    provider=provider,
                    reference_folder=validated_reference_folder,
                    status_callback=status_callback,
                    recursive=reference_recursive,
                )
            )
        except IdentityProfileUnavailableError as error:
            identity_matching_enabled = False
            identity_profile_warning = str(error)
            reference_details = error.reference_details
            reference_images_found = error.reference_images_found

    if not identity_matching_enabled:
        emit_status(
            status_callback,
            "WARNING: Identity matching is unavailable. Continuing with face "
            "detection only; no trigger-word suggestions will be created. "
            f"Reason: {identity_profile_warning}",
        )
    wait_if_paused(pause_event, cancel_event)
    embedding_dimension = (
        int(profile_embedding.size)
        if profile_embedding is not None
        else max(0, int(getattr(provider, "embedding_dimension", 0)))
    )

    generated_images = 0
    reused_images = 0
    failed_images = 0
    faces_detected = 0
    suggestions_created = 0
    successful_images = 0
    run_id: int | None = None

    field_names = [field.name for field in fields(FaceImageReportRow)]

    try:
        with Catalog(catalog_database) as catalog:
            face_model_id = catalog.register_face_model(
                provider_key=provider.provider_key,
                provider_version=provider.provider_version,
                model_name=provider.model_name,
                model_fingerprint=provider.model_fingerprint,
                model_root=str(provider.model_root),
                embedding_dimension=embedding_dimension,
                license_label=provider.license_label,
            )

            identity_id: int | None = None
            profile_id: int | None = None
            identity_tag_id: int | None = None
            tag_source = ""

            if identity_matching_enabled and profile_embedding is not None:
                identity_id = catalog.get_or_create_identity(cleaned_identity_name)
                profile_id = catalog.upsert_identity_profile(
                    identity_id=identity_id,
                    face_model_id=face_model_id,
                    profile_embedding=(
                        profile_embedding.astype(np.float32).tobytes()
                    ),
                    embedding_dimension=embedding_dimension,
                    reference_count=len(
                        [
                            detail
                            for detail in reference_details
                            if detail.get("status") == "used"
                        ]
                    ),
                    reference_details_json=json.dumps(
                        reference_details,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                identity_tag_id = catalog.get_or_create_tag(
                    cleaned_identity_name,
                    category="identity",
                )
                tag_source = (
                    f"face:{provider.provider_key}:model-{face_model_id}:"
                    f"identity-{identity_id}"
                )

            run_id = catalog.start_face_analysis_run(
                input_root=input_folder,
                face_model_id=face_model_id,
                identity_name=(
                    cleaned_identity_name if identity_matching_enabled else ""
                ),
                similarity_threshold=options.similarity_threshold,
                reuse_stored_analysis=reuse_stored_analysis,
                execution_provider=provider.execution_provider,
                discovered_files=len(image_files),
            )

            with partial_csv.open(
                mode="w",
                newline="",
                encoding="utf-8-sig",
            ) as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=field_names)
                writer.writeheader()
                csv_file.flush()

                for index, image_path in enumerate(image_files, start=1):
                    wait_if_paused(pause_event, cancel_event)
                    image_start = time.perf_counter()
                    file_record = catalog.get_file_record(image_path)

                    if file_record is None:
                        raise RuntimeError(
                            "A source image was not registered by the catalog "
                            f"stage: {image_path}"
                        )

                    image_id = int(file_record["image_id"])
                    file_id = int(file_record["file_id"])
                    analysis_source = "generated"
                    detection_rows: list[tuple[int, FaceDetection]] = []
                    error_text = ""
                    status = "success"

                    stored_result = None
                    if reuse_stored_analysis:
                        stored_result = catalog.get_reusable_face_result(
                            image_id=image_id,
                            face_model_id=face_model_id,
                        )

                    if stored_result is not None:
                        analysis_source = "reused"
                        reused_images += 1

                        for stored_detection in catalog.get_face_detections(
                            int(stored_result["id"])
                        ):
                            landmarks_raw = json.loads(
                                str(stored_detection["landmarks_json"])
                            )
                            detection_rows.append(
                                (
                                    int(stored_detection["id"]),
                                    FaceDetection(
                                        bbox=(
                                            float(stored_detection["bbox_x1"]),
                                            float(stored_detection["bbox_y1"]),
                                            float(stored_detection["bbox_x2"]),
                                            float(stored_detection["bbox_y2"]),
                                        ),
                                        detection_score=float(
                                            stored_detection["detection_score"]
                                        ),
                                        landmarks=tuple(
                                            (float(point[0]), float(point[1]))
                                            for point in landmarks_raw
                                        ),
                                        embedding=embedding_from_blob(
                                            bytes(stored_detection["embedding"]),
                                            int(
                                                stored_detection[
                                                    "embedding_dimension"
                                                ]
                                            ),
                                        ),
                                    ),
                                )
                            )
                    else:
                        try:
                            detections = provider.analyze_image(image_path)
                            detection_payloads: list[dict[str, Any]] = []

                            for face_index, detection in enumerate(detections):
                                normalized = normalize_embedding(
                                    detection.embedding
                                )
                                detection_payloads.append(
                                    {
                                        "face_index": face_index,
                                        "bbox_x1": detection.bbox[0],
                                        "bbox_y1": detection.bbox[1],
                                        "bbox_x2": detection.bbox[2],
                                        "bbox_y2": detection.bbox[3],
                                        "detection_score": (
                                            detection.detection_score
                                        ),
                                        "landmarks_json": json.dumps(
                                            detection.landmarks
                                        ),
                                        "embedding": (
                                            normalized.astype(np.float32).tobytes()
                                        ),
                                        "embedding_dimension": normalized.size,
                                        "embedding_norm": float(
                                            np.linalg.norm(normalized)
                                        ),
                                    }
                                )

                            _, detection_ids = catalog.store_face_result(
                                image_id=image_id,
                                source_file_id=file_id,
                                face_model_id=face_model_id,
                                status="success",
                                error="",
                                processing_seconds=(
                                    time.perf_counter() - image_start
                                ),
                                detections=detection_payloads,
                            )
                            detection_rows = list(
                                zip(detection_ids, detections, strict=True)
                            )
                            generated_images += 1

                        except Exception as error:
                            status = "error"
                            error_text = f"{type(error).__name__}: {error}"
                            failed_images += 1
                            catalog.store_face_result(
                                image_id=image_id,
                                source_file_id=file_id,
                                face_model_id=face_model_id,
                                status="error",
                                error=error_text,
                                processing_seconds=(
                                    time.perf_counter() - image_start
                                ),
                                detections=[],
                            )

                    best_similarity: float | None = None
                    best_face_index: int | None = None

                    if status == "success":
                        successful_images += 1
                        faces_detected += len(detection_rows)

                        if (
                            identity_matching_enabled
                            and profile_embedding is not None
                            and profile_id is not None
                        ):
                            for face_index, (
                                detection_id,
                                detection,
                            ) in enumerate(detection_rows):
                                similarity = float(
                                    np.dot(
                                        normalize_embedding(detection.embedding),
                                        profile_embedding,
                                    )
                                )
                                is_suggested = (
                                    similarity >= options.similarity_threshold
                                )

                                catalog.upsert_identity_match(
                                    face_detection_id=detection_id,
                                    identity_profile_id=profile_id,
                                    similarity=similarity,
                                    threshold=options.similarity_threshold,
                                    is_suggested=is_suggested,
                                )

                                if (
                                    best_similarity is None
                                    or similarity > best_similarity
                                ):
                                    best_similarity = similarity
                                    best_face_index = face_index

                    if identity_tag_id is not None:
                        catalog.remove_suggested_tag_assignment(
                            image_id=image_id,
                            tag_id=identity_tag_id,
                            source=tag_source,
                        )

                    is_image_suggested = (
                        best_similarity is not None
                        and best_similarity >= options.similarity_threshold
                    )

                    if is_image_suggested and identity_tag_id is not None:
                        catalog.assign_tag(
                            image_id=image_id,
                            tag_id=identity_tag_id,
                            source=tag_source,
                            confidence=min(1.0, max(0.0, best_similarity)),
                            review_status="suggested",
                            notes=(
                                "Generated by face identity comparison. "
                                "Review before treating as confirmed."
                            ),
                        )
                        suggestions_created += 1

                    elapsed = time.perf_counter() - image_start
                    row = FaceImageReportRow(
                        catalog_image_id=image_id,
                        catalog_file_id=file_id,
                        content_sha256=str(file_record["content_sha256"]),
                        filename=image_path.name,
                        relative_path=str(image_path.relative_to(input_folder)),
                        face_analysis_source=analysis_source,
                        face_count=len(detection_rows),
                        best_identity_name=(
                            cleaned_identity_name
                            if identity_matching_enabled
                            else ""
                        ),
                        best_similarity=best_similarity,
                        suggested_identity=(
                            cleaned_identity_name if is_image_suggested else ""
                        ),
                        matched_face_index=(
                            best_face_index if is_image_suggested else None
                        ),
                        provider_key=provider.provider_key,
                        provider_version=provider.provider_version,
                        model_name=provider.model_name,
                        model_fingerprint=provider.model_fingerprint,
                        execution_provider=provider.execution_provider,
                        similarity_threshold=options.similarity_threshold,
                        status=status,
                        error=error_text,
                        processing_seconds=elapsed,
                    )
                    writer.writerow(asdict(row))
                    if index % REPORT_FLUSH_INTERVAL == 0:
                        csv_file.flush()

                    if best_similarity is not None:
                        score_note = f"best identity score {best_similarity:.3f}"
                    elif detection_rows and not identity_matching_enabled:
                        score_note = "identity matching skipped"
                    else:
                        score_note = "no face"
                    emit_status(
                        status_callback,
                        f"Face [{index}/{len(image_files)}] "
                        f"{row.relative_path} — {len(detection_rows)} face(s), "
                        f"{score_note}, {analysis_source}",
                    )
                    emit_progress(
                        progress_callback,
                        "Face analysis",
                        index,
                        len(image_files),
                        image_path,
                    )

            partial_csv.replace(final_csv)

            catalog.finish_face_analysis_run(
                run_id,
                status="complete",
                generated_images=generated_images,
                reused_images=reused_images,
                failed_images=failed_images,
                faces_detected=faces_detected,
                suggestions_created=suggestions_created,
            )

    except AnalysisCancelled as error:
        if run_id is not None:
            try:
                with Catalog(catalog_database) as recovery_catalog:
                    recovery_catalog.finish_face_analysis_run(
                        run_id,
                        status="failed",
                        generated_images=generated_images,
                        reused_images=reused_images,
                        failed_images=failed_images,
                        faces_detected=faces_detected,
                        suggestions_created=suggestions_created,
                        error_message=str(error),
                    )
            except Exception:
                pass
        raise

    except Exception as error:
        if run_id is not None:
            try:
                with Catalog(catalog_database) as recovery_catalog:
                    recovery_catalog.finish_face_analysis_run(
                        run_id,
                        status="failed",
                        generated_images=generated_images,
                        reused_images=reused_images,
                        failed_images=failed_images,
                        faces_detected=faces_detected,
                        suggestions_created=suggestions_created,
                        error_message=f"{type(error).__name__}: {error}",
                    )
            except Exception:
                pass

        raise RuntimeError(
            "Face analysis stopped because of an unexpected error:\n"
            f"{type(error).__name__}: {error}\n\n"
            f"Catalog: {catalog_database}\n"
            + (
                f"Partial report: {partial_csv}"
                if partial_csv.exists()
                else "No partial face report was created."
            )
        ) from error

    return FaceBatchSummary(
        total_images=len(image_files),
        successful_images=successful_images,
        failed_images=failed_images,
        total_seconds=time.perf_counter() - batch_start,
        output_csv=final_csv,
        catalog_database=catalog_database,
        identity_name=cleaned_identity_name,
        identity_matching_enabled=identity_matching_enabled,
        identity_profile_warning=identity_profile_warning,
        reference_images_found=reference_images_found,
        reference_faces_used=len(
            [
                detail
                for detail in reference_details
                if detail.get("status") == "used"
            ]
        ),
        similarity_threshold=options.similarity_threshold,
        provider_key=provider.provider_key,
        provider_version=provider.provider_version,
        model_name=provider.model_name,
        model_fingerprint=provider.model_fingerprint,
        execution_provider=provider.execution_provider,
        generated_images=generated_images,
        reused_images=reused_images,
        faces_detected=faces_detected,
        suggestions_created=suggestions_created,
    )
