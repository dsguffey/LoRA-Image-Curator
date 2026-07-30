"""
florence_analyzer.py

Florence-2 analysis engine with a persistent SQLite catalog.

Version 0.5 Florence-provider workflow
--------------------
1. Discover supported image files.
2. Register each path and content hash in ``dataset_tools.db``.
3. Reuse a compatible stored analysis when requested.
4. Load Florence only when at least one image still requires analysis.
5. Write a complete CSV report in input-path order.
6. Store newly generated successful results in SQLite.
7. Mark formerly known files under the same input root as missing.
8. Return database and run statistics to the GUI.

The SQLite catalog is the source of truth for future application features.
CSV remains a convenient human-readable report and future export format.
"""

from __future__ import annotations

import csv
import re
import time

from dataclasses import asdict, dataclass, fields
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

import torch

from PIL import Image, UnidentifiedImageError
from transformers import AutoModelForCausalLM, AutoProcessor

from analysis_control import (
    AnalysisCancelled,
    raise_if_cancelled,
    wait_if_paused,
)
from catalog import (
    CATALOG_FILENAME,
    Catalog,
    CatalogSummary,
    FileRegistration,
    ImportRunCounts,
)
from image_discovery import (
    SUPPORTED_IMAGE_EXTENSIONS,
    discover_supported_images,
)
from video_origin import VideoOriginManifestCache


# =============================================================================
# Model and analysis configuration
# =============================================================================

MODEL_NAME = "microsoft/Florence-2-large-ft"
KNOWN_WORKING_TRANSFORMERS_VERSION = "4.49.0"

# Increment this only when LoRA Image Curator changes the meaning or structure of
# generated analysis data. It is separate from the SQLite schema version.
ANALYSIS_VERSION = 1

CAPTION_TASK = "<MORE_DETAILED_CAPTION>"
OBJECT_DETECTION_TASK = "<OD>"
OCR_WITH_REGION_TASK = "<OCR_WITH_REGION>"

TASK_MAX_NEW_TOKENS = {
    CAPTION_TASK: 512,
    OBJECT_DETECTION_TASK: 1024,
    OCR_WITH_REGION_TASK: 1024,
}

NUM_BEAMS = 3

SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
REPORT_FLUSH_INTERVAL = 25

PERSON_LABEL_WORDS = {
    "person",
    "people",
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "boys",
    "girl",
    "girls",
    "male",
    "female",
}

SCREENSHOT_CAPTION_MARKERS = {
    "screenshot",
    "computer screen",
    "website",
    "web page",
    "webpage",
    "text conversation",
    "chat conversation",
    "comment section",
    "user interface",
    "dialog box",
    "menu",
    "button",
    "window open",
    "profile picture",
    "award and share",
    "reply share",
    "caption that reads",
}


# =============================================================================
# Public result structures
# =============================================================================

@dataclass(slots=True)
class ImageAnalysisResult:
    """
    Represent one CSV row.

    Catalog fields appear first so a report row can always be traced back to
    its durable image and file records.
    """

    catalog_image_id: int
    catalog_file_id: int
    content_sha256: str
    catalog_action: str
    analysis_source: str

    filename: str
    relative_path: str
    width: int | None
    height: int | None

    caption: str

    detected_object_count: int | None
    object_labels: str
    person_count: int | None

    ocr_region_count: int | None
    ocr_character_count: int | None
    ocr_text: str

    likely_screenshot_or_ui: str
    candidate_recommendation: str
    recommendation_reason: str

    triage_status: str
    triage_error: str

    status: str
    error: str
    processing_seconds: float


@dataclass(slots=True)
class BatchAnalysisSummary:
    """Represent the final outcome of one completed catalog/analysis run."""

    total_images: int
    successful_images: int
    failed_images: int
    total_seconds: float

    output_csv: Path
    catalog_database: Path
    triage_enabled: bool
    reuse_stored_analysis: bool

    new_unique_images: int
    new_locations_existing_images: int
    unchanged_files: int
    changed_files: int
    missing_files_marked: int
    reused_analyses: int
    generated_analyses: int

    catalog_unique_images: int
    catalog_file_locations: int
    catalog_present_file_locations: int
    catalog_missing_file_locations: int
    catalog_defined_tags: int
    catalog_tag_assignments: int


class BatchAnalysisError(RuntimeError):
    """Report a fatal batch failure while preserving useful file locations."""

    def __init__(
        self,
        message: str,
        *,
        partial_csv: Path | None = None,
        catalog_database: Path | None = None,
    ) -> None:
        details: list[str] = [message]

        if partial_csv is not None:
            details.append(
                "Completed CSV rows, if any, were preserved here:\n"
                f"{partial_csv}"
            )

        if catalog_database is not None:
            details.append(
                "The catalog remains here:\n"
                f"{catalog_database}"
            )

        super().__init__("\n\n".join(details))


ProgressCallback = Callable[[str, int, int, Path], None]
StatusCallback = Callable[[str], None]


# =============================================================================
# General helpers
# =============================================================================

def emit_status(
    callback: StatusCallback | None,
    message: str,
) -> None:
    """Send a status message when a callback was supplied."""
    if callback is not None:
        callback(message)


def emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    completed: int,
    total: int,
    path: Path,
) -> None:
    """Send a phase-aware progress update."""
    if callback is not None:
        callback(
            phase,
            completed,
            total,
            path,
        )


def validate_input_folder(input_folder: Path) -> Path:
    """Return a normalized input folder or raise a clear error."""
    normalized = input_folder.expanduser().resolve()

    if not normalized.exists():
        raise FileNotFoundError(
            f"The selected input folder does not exist:\n{normalized}"
        )

    if not normalized.is_dir():
        raise NotADirectoryError(
            f"The selected input path is not a folder:\n{normalized}"
        )

    return normalized


def validate_output_folder(output_folder: Path) -> Path:
    """Return a normalized output folder, creating it when necessary."""
    normalized = output_folder.expanduser().resolve()

    if normalized.exists() and not normalized.is_dir():
        raise NotADirectoryError(
            f"The selected output path is not a folder:\n{normalized}"
        )

    normalized.mkdir(parents=True, exist_ok=True)
    return normalized


def find_image_files(
    input_folder: Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    """Find supported images in the configured scope and deterministic order."""
    return discover_supported_images(input_folder, recursive=recursive)


def create_report_paths(output_folder: Path) -> tuple[Path, Path]:
    """Create matching partial and final timestamped CSV paths."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_name = f"florence_results_{timestamp}"

    return (
        output_folder / f"{base_name}.partial.csv",
        output_folder / f"{base_name}.csv",
    )


def choose_device_and_dtype() -> tuple[str, torch.dtype]:
    """Use CUDA/float16 when available, otherwise CPU/float32."""
    if torch.cuda.is_available():
        return "cuda", torch.float16

    return "cpu", torch.float32


def get_transformers_version() -> str:
    """Return the installed Transformers version."""
    try:
        return version("transformers")
    except PackageNotFoundError:
        return "not installed"


def count_registration_action(
    counts: ImportRunCounts,
    action: str,
) -> None:
    """Increment the correct run counter for a registration action."""
    if action == "new_image":
        counts.new_unique_images += 1
    elif action == "new_location_existing_image":
        counts.new_locations_existing_images += 1
    elif action == "unchanged_file":
        counts.unchanged_files += 1
    elif action in {
        "changed_file_content",
        "changed_file_metadata",
    }:
        counts.changed_files += 1
    else:
        raise ValueError(
            f"Unknown catalog registration action: {action}"
        )


# =============================================================================
# Florence loading and task execution
# =============================================================================

def load_florence(
    model_name: str,
    device: str,
    dtype: torch.dtype,
) -> tuple[AutoProcessor, AutoModelForCausalLM]:
    """Load Florence-2 and its processor once for the required work."""
    processor = AutoProcessor.from_pretrained(
        model_name,
        trust_remote_code=True,
    )

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=dtype,
        trust_remote_code=True,
    )

    model = model.to(device)
    model.eval()

    return processor, model


def open_image(image_path: Path) -> Image.Image:
    """Open an image, normalize it to RGB, and detach it from its file."""
    with Image.open(image_path) as original_image:
        return original_image.convert("RGB").copy()


def prepare_inputs_for_task(
    processor: AutoProcessor,
    image: Image.Image,
    task_prompt: str,
    device: str,
    dtype: torch.dtype,
) -> dict[str, torch.Tensor]:
    """
    Convert one image and task prompt into model-ready tensors.

    Input token IDs remain integers. Only pixel data uses the model's floating
    point precision.
    """
    inputs = processor(
        text=task_prompt,
        images=image,
        return_tensors="pt",
    )

    inputs["input_ids"] = inputs["input_ids"].to(device)
    inputs["pixel_values"] = inputs["pixel_values"].to(
        device=device,
        dtype=dtype,
    )

    return inputs


def run_florence_task(
    model: AutoModelForCausalLM,
    processor: AutoProcessor,
    image: Image.Image,
    task_prompt: str,
    device: str,
    dtype: torch.dtype,
) -> Any:
    """Run one Florence task and return its post-processed value."""
    inputs = prepare_inputs_for_task(
        processor=processor,
        image=image,
        task_prompt=task_prompt,
        device=device,
        dtype=dtype,
    )

    with torch.inference_mode():
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=TASK_MAX_NEW_TOKENS[task_prompt],
            num_beams=NUM_BEAMS,
            do_sample=False,
        )

    generated_text = processor.batch_decode(
        generated_ids,
        skip_special_tokens=False,
    )[0]

    parsed_result = processor.post_process_generation(
        generated_text,
        task=task_prompt,
        image_size=image.size,
    )

    if task_prompt not in parsed_result:
        raise ValueError(
            "Florence-2 returned an unexpected result structure for "
            f"{task_prompt}:\n{parsed_result}"
        )

    return parsed_result[task_prompt]


# =============================================================================
# Triage parsing
# =============================================================================

def normalize_label(label: object) -> str:
    """Normalize punctuation and case for tolerant label comparison."""
    text = str(label).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def label_represents_person(label: object) -> bool:
    """Return True when a detection label appears to describe a person."""
    return bool(
        set(normalize_label(label).split())
        & PERSON_LABEL_WORDS
    )


def parse_object_detection(
    task_result: object,
) -> tuple[int, list[str], int]:
    """Extract object count, labels, and estimated person count."""
    if not isinstance(task_result, dict):
        raise ValueError(
            f"Object detection did not return a dictionary: {task_result}"
        )

    raw_labels = task_result.get("labels", [])
    raw_bboxes = task_result.get("bboxes", [])

    if not isinstance(raw_labels, list):
        raise ValueError(
            f"Object-detection labels were not a list: {raw_labels}"
        )

    labels = [
        str(label).strip()
        for label in raw_labels
    ]

    object_count = max(
        len(labels),
        (
            len(raw_bboxes)
            if isinstance(raw_bboxes, list)
            else 0
        ),
    )

    person_count = sum(
        label_represents_person(label)
        for label in labels
    )

    return object_count, labels, person_count


def parse_ocr_with_regions(
    task_result: object,
) -> tuple[int, int, str]:
    """Extract OCR region count, text size, and joined readable text."""
    if not isinstance(task_result, dict):
        raise ValueError(
            f"OCR did not return a dictionary: {task_result}"
        )

    raw_labels = task_result.get("labels", [])

    if not isinstance(raw_labels, list):
        raise ValueError(
            f"OCR labels were not a list: {raw_labels}"
        )

    text_regions = [
        " ".join(str(label).split())
        for label in raw_labels
        if str(label).strip()
    ]

    return (
        len(text_regions),
        sum(len(text) for text in text_regions),
        " | ".join(text_regions),
    )


def estimate_screenshot_likelihood(
    caption: str,
    ocr_region_count: int,
    ocr_character_count: int,
) -> tuple[str, list[str]]:
    """Estimate screenshot/UI likelihood using conservative signals."""
    normalized_caption = caption.casefold()

    matched_markers = sorted(
        marker
        for marker in SCREENSHOT_CAPTION_MARKERS
        if marker in normalized_caption
    )

    reasons: list[str] = []

    if matched_markers:
        reasons.append(
            "caption suggests screenshot/UI "
            f"({', '.join(matched_markers[:3])})"
        )

    if ocr_region_count >= 12:
        reasons.append(
            f"many OCR regions ({ocr_region_count})"
        )

    if ocr_character_count >= 200:
        reasons.append(
            "large amount of recognized text "
            f"({ocr_character_count} characters)"
        )

    strong_text_signal = (
        ocr_region_count >= 12
        or ocr_character_count >= 200
    )
    moderate_text_signal = (
        ocr_region_count >= 5
        or ocr_character_count >= 80
    )

    if strong_text_signal or (
        matched_markers
        and moderate_text_signal
    ):
        return "yes", reasons

    if matched_markers or moderate_text_signal:
        if not reasons:
            reasons.append(
                "moderate amount of recognized text"
            )
        return "uncertain", reasons

    return "no", ["no strong screenshot/UI signal"]


def create_recommendation(
    person_count: int,
    screenshot_likelihood: str,
) -> tuple[str, str]:
    """Create a cautious candidate/manual-review recommendation."""
    if (
        person_count == 1
        and screenshot_likelihood == "no"
    ):
        return (
            "candidate",
            "one person detected and no strong screenshot/UI signal",
        )

    reasons: list[str] = []

    if person_count == 0:
        reasons.append(
            "no person detected; object detection can miss people"
        )
    elif person_count > 1:
        reasons.append(
            f"multiple people detected ({person_count})"
        )

    if screenshot_likelihood == "yes":
        reasons.append(
            "likely screenshot or user interface"
        )
    elif screenshot_likelihood == "uncertain":
        reasons.append(
            "possible screenshot or text-heavy image"
        )

    if not reasons:
        reasons.append(
            "automatic evidence was inconclusive"
        )

    return "review", "; ".join(reasons)


# =============================================================================
# Result construction
# =============================================================================

def analyze_one_image(
    *,
    image_path: Path,
    input_folder: Path,
    registration: FileRegistration,
    processor: AutoProcessor,
    model: AutoModelForCausalLM,
    device: str,
    dtype: torch.dtype,
    include_triage: bool,
) -> ImageAnalysisResult:
    """Generate a fresh Florence result for one registered image."""
    start_time = time.perf_counter()
    relative_path = image_path.relative_to(input_folder)

    try:
        image = open_image(image_path)
        width, height = image.size

        caption_result = run_florence_task(
            model=model,
            processor=processor,
            image=image,
            task_prompt=CAPTION_TASK,
            device=device,
            dtype=dtype,
        )

        if not isinstance(caption_result, str):
            raise ValueError(
                "Detailed caption did not return text:\n"
                f"{caption_result}"
            )

        caption = caption_result.strip()

        detected_object_count: int | None = None
        object_labels: list[str] = []
        person_count: int | None = None

        ocr_region_count: int | None = None
        ocr_character_count: int | None = None
        ocr_text = ""

        likely_screenshot_or_ui = "not_evaluated"
        candidate_recommendation = "not_evaluated"
        recommendation_reason = "triage was not requested"
        triage_status = "not_requested"
        triage_errors: list[str] = []

        if include_triage:
            try:
                object_result = run_florence_task(
                    model=model,
                    processor=processor,
                    image=image,
                    task_prompt=OBJECT_DETECTION_TASK,
                    device=device,
                    dtype=dtype,
                )

                (
                    detected_object_count,
                    object_labels,
                    person_count,
                ) = parse_object_detection(object_result)

            except (
                RuntimeError,
                ValueError,
                TypeError,
            ) as error:
                triage_errors.append(
                    "object detection: "
                    f"{type(error).__name__}: {error}"
                )

            try:
                ocr_result = run_florence_task(
                    model=model,
                    processor=processor,
                    image=image,
                    task_prompt=OCR_WITH_REGION_TASK,
                    device=device,
                    dtype=dtype,
                )

                (
                    ocr_region_count,
                    ocr_character_count,
                    ocr_text,
                ) = parse_ocr_with_regions(ocr_result)

            except (
                RuntimeError,
                ValueError,
                TypeError,
            ) as error:
                triage_errors.append(
                    f"OCR: {type(error).__name__}: {error}"
                )

            if (
                person_count is not None
                and ocr_region_count is not None
                and ocr_character_count is not None
            ):
                (
                    likely_screenshot_or_ui,
                    screenshot_reasons,
                ) = estimate_screenshot_likelihood(
                    caption=caption,
                    ocr_region_count=ocr_region_count,
                    ocr_character_count=ocr_character_count,
                )

                (
                    candidate_recommendation,
                    recommendation_reason,
                ) = create_recommendation(
                    person_count=person_count,
                    screenshot_likelihood=likely_screenshot_or_ui,
                )

                recommendation_reason += (
                    "; screenshot evidence: "
                    + "; ".join(screenshot_reasons)
                )

            else:
                likely_screenshot_or_ui = "uncertain"
                candidate_recommendation = "review"
                recommendation_reason = (
                    "triage was incomplete; inspect manually"
                )

            triage_status = (
                "partial"
                if triage_errors
                else "success"
            )

        return ImageAnalysisResult(
            catalog_image_id=registration.image_id,
            catalog_file_id=registration.file_id,
            content_sha256=registration.content_sha256,
            catalog_action=registration.action,
            analysis_source="generated",
            filename=image_path.name,
            relative_path=str(relative_path),
            width=width,
            height=height,
            caption=caption,
            detected_object_count=detected_object_count,
            object_labels=" | ".join(object_labels),
            person_count=person_count,
            ocr_region_count=ocr_region_count,
            ocr_character_count=ocr_character_count,
            ocr_text=ocr_text,
            likely_screenshot_or_ui=likely_screenshot_or_ui,
            candidate_recommendation=candidate_recommendation,
            recommendation_reason=recommendation_reason,
            triage_status=triage_status,
            triage_error="; ".join(triage_errors),
            status="success",
            error="",
            processing_seconds=round(
                time.perf_counter() - start_time,
                3,
            ),
        )

    except (
        UnidentifiedImageError,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
    ) as error:
        if (
            device == "cuda"
            and torch.cuda.is_available()
        ):
            torch.cuda.empty_cache()

        return ImageAnalysisResult(
            catalog_image_id=registration.image_id,
            catalog_file_id=registration.file_id,
            content_sha256=registration.content_sha256,
            catalog_action=registration.action,
            analysis_source="generated",
            filename=image_path.name,
            relative_path=str(relative_path),
            width=None,
            height=None,
            caption="",
            detected_object_count=None,
            object_labels="",
            person_count=None,
            ocr_region_count=None,
            ocr_character_count=None,
            ocr_text="",
            likely_screenshot_or_ui="not_evaluated",
            candidate_recommendation="review",
            recommendation_reason="image analysis failed",
            triage_status="not_evaluated",
            triage_error="",
            status="error",
            error=f"{type(error).__name__}: {error}",
            processing_seconds=round(
                time.perf_counter() - start_time,
                3,
            ),
        )


def result_from_stored_analysis(
    *,
    image_path: Path,
    input_folder: Path,
    registration: FileRegistration,
    stored: Mapping[str, Any],
) -> ImageAnalysisResult:
    """
    Reconstruct a CSV row from a compatible successful SQLite result.
    """
    return ImageAnalysisResult(
        catalog_image_id=registration.image_id,
        catalog_file_id=registration.file_id,
        content_sha256=registration.content_sha256,
        catalog_action=registration.action,
        analysis_source="reused",
        filename=image_path.name,
        relative_path=str(
            image_path.relative_to(input_folder)
        ),
        width=stored["stored_width"],
        height=stored["stored_height"],
        caption=str(stored["caption"]),
        detected_object_count=stored["detected_object_count"],
        object_labels=str(stored["object_labels"]),
        person_count=stored["person_count"],
        ocr_region_count=stored["ocr_region_count"],
        ocr_character_count=stored["ocr_character_count"],
        ocr_text=str(stored["ocr_text"]),
        likely_screenshot_or_ui=str(
            stored["likely_screenshot_or_ui"]
        ),
        candidate_recommendation=str(
            stored["candidate_recommendation"]
        ),
        recommendation_reason=str(
            stored["recommendation_reason"]
        ),
        triage_status=str(stored["triage_status"]),
        triage_error=str(stored["triage_error"]),
        status="success",
        error="",
        processing_seconds=float(
            stored["processing_seconds"]
        ),
    )


# =============================================================================
# Complete batch workflow
# =============================================================================

def analyze_folder(
    input_folder: Path,
    output_folder: Path,
    *,
    include_triage: bool = True,
    reuse_stored_analysis: bool = True,
    recursive: bool = True,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
) -> BatchAnalysisSummary:
    """
    Catalog and analyze every supported image in a folder.

    Re-running against unchanged files with reuse enabled should avoid model
    inference and finish substantially faster.
    """
    input_folder = validate_input_folder(input_folder)
    output_folder = validate_output_folder(output_folder)

    image_files = find_image_files(input_folder, recursive=recursive)
    raise_if_cancelled(cancel_event)

    if not image_files:
        raise FileNotFoundError(
            "No supported images were found in the selected input folder.\n\n"
            f"Folder checked:\n{input_folder}\n\n"
            "Supported extensions:\n"
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    partial_csv, final_csv = create_report_paths(
        output_folder
    )
    catalog_database = output_folder / CATALOG_FILENAME
    transformers_version = get_transformers_version()

    counts = ImportRunCounts(
        discovered_files=len(image_files)
    )
    registrations: list[
        tuple[Path, FileRegistration, Mapping[str, Any] | None]
    ] = []

    batch_start_time = time.perf_counter()
    run_id: int | None = None
    catalog_summary: CatalogSummary | None = None
    successful_images = 0
    failed_images = 0
    video_origins = VideoOriginManifestCache()

    emit_status(
        status_callback,
        f"Images discovered: {len(image_files)}",
    )
    emit_status(
        status_callback,
        f"Image folder checked: {input_folder}",
    )
    emit_status(
        status_callback,
        f"Catalog database: {catalog_database}",
    )

    try:
        with Catalog(catalog_database) as catalog:
            run_id = catalog.start_import_run(
                input_root=input_folder,
                output_folder=output_folder,
                model_name=MODEL_NAME,
                transformers_version=transformers_version,
                analysis_version=ANALYSIS_VERSION,
                include_triage=include_triage,
                reuse_stored_analysis=reuse_stored_analysis,
            )

            emit_status(
                status_callback,
                "Cataloging files and checking content hashes...",
            )

            for index, image_path in enumerate(
                image_files,
                start=1,
            ):
                wait_if_paused(pause_event, cancel_event)
                registration = catalog.register_file(
                    file_path=image_path,
                    input_root=input_folder,
                    run_id=run_id,
                )
                video_origin = video_origins.origin_for(image_path)
                if video_origin is not None:
                    catalog.store_file_video_origin(
                        file_id=registration.file_id,
                        source_video=video_origin.source_video,
                        sampling_mode=video_origin.sampling_mode,
                        timestamp_seconds=video_origin.timestamp_seconds,
                        frame_number=video_origin.frame_number,
                        interval_seconds=video_origin.interval_seconds,
                    )

                count_registration_action(
                    counts,
                    registration.action,
                )

                stored_result = None

                if reuse_stored_analysis:
                    stored_result = catalog.get_reusable_analysis(
                        image_id=registration.image_id,
                        model_name=MODEL_NAME,
                        transformers_version=transformers_version,
                        analysis_version=ANALYSIS_VERSION,
                        requested_triage=include_triage,
                    )

                registrations.append(
                    (
                        image_path,
                        registration,
                        stored_result,
                    )
                )

                emit_progress(
                    progress_callback,
                    "Cataloging",
                    index,
                    len(image_files),
                    image_path,
                )

            counts.missing_files_marked = (
                catalog.mark_unseen_files_missing(
                    input_root=input_folder,
                    run_id=run_id,
                )
            )

            work_requiring_model = sum(
                stored_result is None
                for _, _, stored_result in registrations
            )

            emit_status(
                status_callback,
                "Catalog registration complete.",
            )
            emit_status(
                status_callback,
                f"Stored analyses reusable: "
                f"{len(image_files) - work_requiring_model}",
            )
            emit_status(
                status_callback,
                f"Images requiring Florence: "
                f"{work_requiring_model}",
            )

            processor = None
            model = None
            device = "not_loaded"
            dtype = torch.float32

            if work_requiring_model:
                wait_if_paused(pause_event, cancel_event)
                device, dtype = choose_device_and_dtype()

                emit_status(
                    status_callback,
                    f"Transformers version: {transformers_version}",
                )

                if (
                    transformers_version
                    != KNOWN_WORKING_TRANSFORMERS_VERSION
                ):
                    emit_status(
                        status_callback,
                        "Warning: this project was tested with Transformers "
                        f"{KNOWN_WORKING_TRANSFORMERS_VERSION}.",
                    )
                if include_triage:
                    emit_status(
                        status_callback,
                        "Provider note: Florence object detection and regional OCR "
                        "follow the official 1,024-token generation example. Some "
                        "Transformers builds print a model-length warning for those "
                        "calls; LoRA Image Curator already runs one image and one "
                        "task at a time.",
                    )

                emit_status(
                    status_callback,
                    f"Processing device: {device}",
                )

                if device == "cuda":
                    emit_status(
                        status_callback,
                        f"GPU: {torch.cuda.get_device_name(0)}",
                    )
                else:
                    emit_status(
                        status_callback,
                        "CUDA was not detected. Processing will use the CPU.",
                    )

                emit_status(
                    status_callback,
                    "Loading Florence-2...",
                )

                processor, model = load_florence(
                    model_name=MODEL_NAME,
                    device=device,
                    dtype=dtype,
                )

                emit_status(
                    status_callback,
                    "Florence-2 loaded successfully.",
                )
            else:
                emit_status(
                    status_callback,
                    "All compatible results are already stored. "
                    "Florence does not need to load.",
                )

            field_names = [
                field.name
                for field in fields(ImageAnalysisResult)
            ]
            with partial_csv.open(
                mode="w",
                newline="",
                encoding="utf-8-sig",
            ) as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=field_names,
                )
                writer.writeheader()
                csv_file.flush()

                remaining_completed = 0
                for index, (
                    image_path,
                    registration,
                    stored_result,
                ) in enumerate(registrations, start=1):
                    wait_if_paused(pause_event, cancel_event)
                    if stored_result is not None:
                        result = result_from_stored_analysis(
                            image_path=image_path,
                            input_folder=input_folder,
                            registration=registration,
                            stored=stored_result,
                        )
                        counts.reused_analyses += 1

                    else:
                        if processor is None or model is None:
                            raise RuntimeError(
                                "Florence was not loaded even though an image "
                                "requires analysis."
                            )

                        result = analyze_one_image(
                            image_path=image_path,
                            input_folder=input_folder,
                            registration=registration,
                            processor=processor,
                            model=model,
                            device=device,
                            dtype=dtype,
                            include_triage=include_triage,
                        )
                        remaining_completed += 1

                        if result.status == "success":
                            counts.generated_analyses += 1

                            catalog.update_image_dimensions(
                                registration.image_id,
                                result.width,
                                result.height,
                            )

                            catalog.store_successful_analysis(
                                image_id=registration.image_id,
                                source_file_id=registration.file_id,
                                model_name=MODEL_NAME,
                                transformers_version=transformers_version,
                                analysis_version=ANALYSIS_VERSION,
                                include_triage=include_triage,
                                result=asdict(result),
                            )
                        else:
                            counts.failed_analyses += 1

                    if result.status == "success":
                        successful_images += 1
                    else:
                        failed_images += 1
                    writer.writerow(asdict(result))
                    if index % REPORT_FLUSH_INTERVAL == 0:
                        csv_file.flush()

                    source_note = (
                        "reused"
                        if result.analysis_source == "reused"
                        else result.candidate_recommendation
                    )

                    emit_status(
                        status_callback,
                        f"[{index}/{len(image_files)}] "
                        f"{result.relative_path} — {source_note}",
                    )

                    # Florence ETA measures only inference still required for
                    # this run. Reused records may be interleaved with fresh
                    # work and must not make the measured rate look thousands
                    # of times faster when an interrupted batch is resumed.
                    if stored_result is None:
                        emit_progress(
                            progress_callback,
                            "Florence analysis",
                            remaining_completed,
                            work_requiring_model,
                            image_path,
                        )

            partial_csv.replace(final_csv)

            catalog_summary = catalog.get_summary()

            catalog.finish_import_run(
                run_id,
                status="complete",
                counts=counts,
            )

    except AnalysisCancelled as error:
        if run_id is not None:
            try:
                with Catalog(catalog_database) as recovery_catalog:
                    recovery_catalog.finish_import_run(
                        run_id,
                        status="failed",
                        counts=counts,
                        error_message=str(error),
                    )
            except Exception:
                pass
        raise

    except Exception as error:
        if run_id is not None:
            try:
                with Catalog(catalog_database) as recovery_catalog:
                    recovery_catalog.finish_import_run(
                        run_id,
                        status="failed",
                        counts=counts,
                        error_message=(
                            f"{type(error).__name__}: {error}"
                        ),
                    )
            except Exception:
                # The original exception is more useful than a secondary
                # failure while trying to record it.
                pass

        raise BatchAnalysisError(
            "The batch stopped because of an unexpected error:\n"
            f"{type(error).__name__}: {error}",
            partial_csv=(
                partial_csv
                if partial_csv.exists()
                else None
            ),
            catalog_database=(
                catalog_database
                if catalog_database.exists()
                else None
            ),
        ) from error

    finally:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    if catalog_summary is None:
        raise RuntimeError(
            "The catalog summary was not created."
        )

    return BatchAnalysisSummary(
        total_images=successful_images + failed_images,
        successful_images=successful_images,
        failed_images=failed_images,
        total_seconds=(
            time.perf_counter() - batch_start_time
        ),
        output_csv=final_csv,
        catalog_database=catalog_database,
        triage_enabled=include_triage,
        reuse_stored_analysis=reuse_stored_analysis,
        new_unique_images=counts.new_unique_images,
        new_locations_existing_images=(
            counts.new_locations_existing_images
        ),
        unchanged_files=counts.unchanged_files,
        changed_files=counts.changed_files,
        missing_files_marked=counts.missing_files_marked,
        reused_analyses=counts.reused_analyses,
        generated_analyses=counts.generated_analyses,
        catalog_unique_images=catalog_summary.unique_images,
        catalog_file_locations=catalog_summary.file_locations,
        catalog_present_file_locations=(
            catalog_summary.present_file_locations
        ),
        catalog_missing_file_locations=(
            catalog_summary.missing_file_locations
        ),
        catalog_defined_tags=catalog_summary.defined_tags,
        catalog_tag_assignments=(
            catalog_summary.tag_assignments
        ),
    )
