"""Transparent Dataset Readiness calculations over catalog projections.

Milestone 8A deliberately avoids pretending that a formula can predict LoRA
quality. The score summarizes correctable catalog preparation work and keeps
every deduction visible. Milestone 8B adds locally cached sharpness and
perceptual-hash facts while retaining the distinction between a measurement
and a user decision.
"""

from __future__ import annotations

import json

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from quality_analysis import (
    DEFAULT_BLUR_THRESHOLD,
    DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
    duplicate_candidate_clusters,
)
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    TrainingTextLayers,
    build_training_text,
    find_repeated_training_text_groups,
)


@dataclass(slots=True, frozen=True)
class ReadinessProfile:
    """Small set of profile-specific preparation expectations."""

    key: str
    label: str
    minimum_short_side: int
    training_text_profile_key: str


READINESS_PROFILES = (
    ReadinessProfile(
        "flux_character_lora",
        "Flux Character LoRA",
        768,
        "flux_lora",
    ),
    ReadinessProfile(
        "sdxl_character_lora",
        "SDXL Character LoRA",
        768,
        "sdxl_lora",
    ),
    ReadinessProfile(
        "sd15_character_lora",
        "SD 1.5 Character LoRA",
        512,
        "sd15_lora",
    ),
    ReadinessProfile(
        "general_lora",
        "General / Other LoRA",
        512,
        "general_lora",
    ),
)
READINESS_PROFILES_BY_KEY = {profile.key: profile for profile in READINESS_PROFILES}
DEFAULT_READINESS_PROFILE_KEY = "flux_character_lora"
MIN_OVERLAY_COVERAGE_PERCENT = 1
MAX_OVERLAY_COVERAGE_PERCENT = 30
DEFAULT_OVERLAY_COVERAGE_PERCENT = 5
OVERLAY_SPATIAL_MODE_LABELS = {
    "none": "Whole image",
    "face": "Face",
    "body": "Body",
    "either": "Face or Body",
    "both": "Face and Body",
}
DEFAULT_OVERLAY_SPATIAL_MODE = "either"
_BODY_SEGMENTS = (
    (11, 13), (13, 15), (12, 14), (14, 16),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (24, 26), (26, 28),
)


@dataclass(slots=True, frozen=True)
class ReadinessIssue:
    """One explainable issue count and the browser query that reveals it."""

    label: str
    count: int
    image_ids: tuple[int, ...]
    query: str
    explanation: str
    severity: str
    maximum_deduction: int = 0
    deduction: float = 0.0


@dataclass(slots=True, frozen=True)
class DatasetReadinessReport:
    """Complete dashboard model, independent of Tkinter presentation."""

    score: int
    status: str
    profile: ReadinessProfile
    total_images: int
    eligible_images: int
    review_counts: dict[str, int]
    file_counts: dict[str, int]
    resolution_counts: dict[str, int]
    quality_counts: dict[str, int]
    issues: tuple[ReadinessIssue, ...]
    top_trigger_keywords: tuple[tuple[str, int], ...]
    top_manual_tags: tuple[tuple[str, int], ...]
    top_ai_tags: tuple[tuple[str, int], ...]
    top_excluded_tags: tuple[tuple[str, int], ...]


@dataclass(slots=True, frozen=True)
class OverlayEvidence:
    """Explain one visible-overlay coverage decision."""

    matched: bool
    spatial_available: bool
    image_coverage_percent: float
    face_coverage_percent: float
    body_coverage_percent: float
    text_region_count: int
    bar_region_count: int


def normalize_overlay_spatial_mode(value: object) -> str:
    """Return a supported spatial mode, preserving the accuracy-first default."""
    normalized = str(value or "").strip().casefold().replace("_", " ")
    aliases = {
        "none": "none",
        "neither": "none",
        "whole image": "none",
        "face": "face",
        "body": "body",
        "either": "either",
        "face or body": "either",
        "both": "both",
        "face and body": "both",
    }
    return aliases.get(normalized, DEFAULT_OVERLAY_SPATIAL_MODE)


def _json_list(raw_value: object) -> list[object]:
    try:
        value = json.loads(str(raw_value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _normalized_rectangle(
    values: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[float, float, float, float] | None:
    x1, y1, x2, y2 = values
    if max(abs(x1), abs(x2)) > 1.5:
        if width <= 0:
            return None
        x1, x2 = x1 / width, x2 / width
    if max(abs(y1), abs(y2)) > 1.5:
        if height <= 0:
            return None
        y1, y2 = y1 / height, y2 / height
    left, right = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    top, bottom = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _ocr_regions(record: Any) -> list[tuple[str, tuple[float, float, float, float]]]:
    width = int(getattr(record, "width", 0) or 0)
    height = int(getattr(record, "height", 0) or 0)
    regions: list[tuple[str, tuple[float, float, float, float]]] = []
    for item in _json_list(getattr(record, "ocr_regions_json", "[]")):
        if not isinstance(item, dict):
            continue
        try:
            rectangle = _normalized_rectangle(
                (
                    float(item["x1"]), float(item["y1"]),
                    float(item["x2"]), float(item["y2"]),
                ),
                width,
                height,
            )
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(item.get("text", "")).split())
        if rectangle is not None and text:
            regions.append((text, rectangle))
    return regions


def _face_regions(record: Any) -> list[tuple[float, float, float, float]]:
    width = int(getattr(record, "width", 0) or 0)
    height = int(getattr(record, "height", 0) or 0)
    regions: list[tuple[float, float, float, float]] = []
    for item in _json_list(getattr(record, "face_boxes_json", "[]")):
        if not isinstance(item, dict):
            continue
        try:
            rectangle = _normalized_rectangle(
                (
                    float(item["x1"]), float(item["y1"]),
                    float(item["x2"]), float(item["y2"]),
                ),
                width,
                height,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if rectangle is not None:
            regions.append(rectangle)
    return regions


def _body_regions(record: Any) -> list[tuple[float, float, float, float]]:
    regions: list[tuple[float, float, float, float]] = []
    for raw_pose in _json_list(getattr(record, "body_landmarks_json", "[]")):
        if not isinstance(raw_pose, list):
            continue
        points: dict[int, tuple[float, float]] = {}
        for index, item in enumerate(raw_pose):
            if not isinstance(item, dict):
                continue
            try:
                visibility = min(
                    float(item.get("visibility", 0.0)),
                    float(item.get("presence", 0.0)),
                )
                x = float(item["x"])
                y = float(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if visibility >= 0.20 and -0.1 <= x <= 1.1 and -0.1 <= y <= 1.1:
                points[index] = (x, y)

        torso_points = [points[index] for index in (11, 12, 23, 24) if index in points]
        if len(torso_points) >= 3:
            xs = [point[0] for point in torso_points]
            ys = [point[1] for point in torso_points]
            rectangle = _normalized_rectangle(
                (min(xs) - 0.03, min(ys) - 0.03, max(xs) + 0.03, max(ys) + 0.03),
                1,
                1,
            )
            if rectangle is not None:
                regions.append(rectangle)

        for start, end in _BODY_SEGMENTS:
            if start not in points or end not in points:
                continue
            x1, y1 = points[start]
            x2, y2 = points[end]
            rectangle = _normalized_rectangle(
                (min(x1, x2) - 0.025, min(y1, y2) - 0.025,
                 max(x1, x2) + 0.025, max(y1, y2) + 0.025),
                1,
                1,
            )
            if rectangle is not None:
                regions.append(rectangle)
    return regions


def _overlay_regions(record: Any) -> list[tuple[float, float, float, float]]:
    """Return conservative bar/banner rectangles cached by Quality Analysis."""
    width = int(getattr(record, "width", 0) or 0)
    height = int(getattr(record, "height", 0) or 0)
    regions: list[tuple[float, float, float, float]] = []
    for item in _json_list(getattr(record, "overlay_regions_json", "[]")):
        if not isinstance(item, dict):
            continue
        try:
            rectangle = _normalized_rectangle(
                (
                    float(item["x1"]), float(item["y1"]),
                    float(item["x2"]), float(item["y2"]),
                ),
                width,
                height,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if rectangle is not None:
            regions.append(rectangle)
    return regions


def _rectangle_intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _rectangle_union_area(
    rectangles: Iterable[tuple[float, float, float, float]],
) -> float:
    """Return exact union area for a small collection of rectangles."""
    items = list(rectangles)
    x_edges = sorted({edge for item in items for edge in (item[0], item[2])})
    area = 0.0
    for left, right in zip(x_edges, x_edges[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (item[1], item[3])
            for item in items
            if item[0] < right and item[2] > left
        )
        covered_height = 0.0
        if intervals:
            current_top, current_bottom = intervals[0]
            for top, bottom in intervals[1:]:
                if top <= current_bottom:
                    current_bottom = max(current_bottom, bottom)
                else:
                    covered_height += current_bottom - current_top
                    current_top, current_bottom = top, bottom
            covered_height += current_bottom - current_top
        area += (right - left) * covered_height
    return area


def _coverage_percent(
    overlays: Iterable[tuple[float, float, float, float]],
    targets: Iterable[tuple[float, float, float, float]],
) -> float:
    """Return the greatest percent of any one target covered by overlays."""
    overlay_items = list(overlays)
    percentages: list[float] = []
    for target in targets:
        target_area = _rectangle_union_area((target,))
        intersections = [
            intersection
            for overlay in overlay_items
            if (intersection := _rectangle_intersection(overlay, target)) is not None
        ]
        percentages.append(
            100.0 * _rectangle_union_area(intersections) / target_area
            if target_area > 0.0
            else 0.0
        )
    return max(percentages, default=0.0)


def evaluate_prominent_overlay(
    record: Any,
    *,
    coverage_threshold_percent: int,
    spatial_mode: str,
) -> OverlayEvidence:
    """Evaluate OCR text and obvious bar/banner regions by visible coverage."""
    mode = normalize_overlay_spatial_mode(spatial_mode)
    ocr_regions = _ocr_regions(record)
    bar_regions = _overlay_regions(record)
    overlays = [rectangle for _text, rectangle in ocr_regions] + bar_regions
    face_available = bool(getattr(record, "face_analysis_available", False))
    body_available = bool(getattr(record, "body_analysis_available", False))
    face_regions = _face_regions(record)
    body_regions = _body_regions(record)
    image_coverage = _coverage_percent(overlays, ((0.0, 0.0, 1.0, 1.0),))
    face_coverage = _coverage_percent(overlays, face_regions)
    body_coverage = _coverage_percent(overlays, body_regions)
    threshold = float(coverage_threshold_percent)

    if mode == "none":
        spatial_available = True
        matched = image_coverage >= threshold
    elif mode == "face":
        spatial_available = face_available and bool(face_regions)
        matched = spatial_available and face_coverage >= threshold
    elif mode == "body":
        spatial_available = body_available and bool(body_regions)
        matched = spatial_available and body_coverage >= threshold
    elif mode == "both":
        spatial_available = (
            face_available and bool(face_regions)
            and body_available and bool(body_regions)
        )
        matched = (
            spatial_available
            and face_coverage >= threshold
            and body_coverage >= threshold
        )
    else:
        spatial_available = (
            (face_available and bool(face_regions))
            or (body_available and bool(body_regions))
        )
        matched = spatial_available and max(face_coverage, body_coverage) >= threshold

    return OverlayEvidence(
        matched=bool(matched),
        spatial_available=spatial_available,
        image_coverage_percent=image_coverage,
        face_coverage_percent=face_coverage,
        body_coverage_percent=body_coverage,
        text_region_count=len(ocr_regions),
        bar_region_count=len(bar_regions),
    )


def build_readiness_report(
    records: Iterable[Any],
    *,
    profile_key: str = DEFAULT_READINESS_PROFILE_KEY,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    duplicate_similarity_percent: int = DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
    overlay_coverage_threshold_percent: int = DEFAULT_OVERLAY_COVERAGE_PERCENT,
    overlay_spatial_mode: str = DEFAULT_OVERLAY_SPATIAL_MODE,
) -> DatasetReadinessReport:
    """Summarize records for one explicitly selected LoRA target profile.

    Rejected and quarantined records remain visible in composition statistics
    but are not treated as intended training images.  Each scoring deduction is
    proportional to the affected share of eligible images and capped by the
    documented maximum, making the score stable across small and large sets.
    """
    items = list(records)
    overlay_coverage_threshold_percent = max(
        MIN_OVERLAY_COVERAGE_PERCENT,
        min(MAX_OVERLAY_COVERAGE_PERCENT, int(overlay_coverage_threshold_percent)),
    )
    overlay_spatial_mode = normalize_overlay_spatial_mode(
        overlay_spatial_mode
    )
    profile = READINESS_PROFILES_BY_KEY.get(
        profile_key,
        READINESS_PROFILES_BY_KEY[DEFAULT_READINESS_PROFILE_KEY],
    )
    eligible = [
        record
        for record in items
        if str(record.review_status).casefold() not in {"reject", "quarantined"}
    ]
    denominator = max(1, len(eligible))
    training_text_profile = BUILTIN_TRAINING_PROFILES[
        profile.training_text_profile_key
    ]
    empty_training_text_ids = tuple(
        int(record.image_id)
        for record in eligible
        if not build_training_text(
            _training_text_layers(record),
            training_text_profile,
        )
    )

    review_counts = Counter(str(record.review_status or "unreviewed") for record in items)
    file_counts = Counter(
        "present" if str(record.file_status).casefold() == "present" else "missing"
        for record in items
    )
    resolution_counts = Counter(_resolution_bucket(record) for record in items)
    quality_counts = Counter(
        str(getattr(record, "quality_status", "") or "not_analyzed")
        for record in items
    )

    issue_specs = (
        (
            "Missing Files",
            tuple(
                int(record.image_id)
                for record in eligible
                if str(record.file_status).casefold() != "present"
            ),
            "file:missing",
            "Eligible catalog records whose preferred source file is unavailable.",
            "blocking",
            25,
        ),
        (
            "Missing Trigger Keyword",
            tuple(
                int(record.image_id)
                for record in eligible
                if not str(record.manual_keyword).strip()
            ),
            "trigger:missing",
            "Eligible images without a user-owned Trigger Keyword.",
            "blocking",
            25,
        ),
        (
            "Unreviewed",
            tuple(
                int(record.image_id)
                for record in eligible
                if str(record.review_status).casefold() == "unreviewed"
            ),
            "review:unreviewed",
            "Eligible images that have not received a manual review decision.",
            "review",
            15,
        ),
        (
            "Low Resolution",
            tuple(
                int(record.image_id)
                for record in eligible
                if _short_side(record) < profile.minimum_short_side
            ),
            f"resolution:below_{profile.minimum_short_side}",
            (
                "Images with an unknown short side or a short side below the "
                f"{profile.minimum_short_side}-pixel expectation for {profile.label}."
            ),
            "review",
            15,
        ),
        (
            "No Training Text",
            empty_training_text_ids,
            _image_id_query(empty_training_text_ids),
            (
                "Eligible images whose exported sidecar would be empty with the "
                f"{training_text_profile.label} profile. This uses the same "
                "training-text builder as export."
            ),
            "blocking",
            10,
        ),
        (
            "Identity Unconfirmed",
            tuple(
                int(record.image_id)
                for record in eligible
                if bool(record.suggested_identity)
                and str(record.identity_review_status).casefold() not in {"confirmed", "rejected"}
            ),
            "identity:unconfirmed",
            "Images with an identity suggestion that has not been confirmed or rejected.",
            "review",
            10,
        ),
    )

    issues: list[ReadinessIssue] = []
    total_deduction = 0.0
    for label, image_ids, query, explanation, severity, maximum in issue_specs:
        count = len(image_ids)
        deduction = maximum * (count / denominator) if eligible else 0.0
        total_deduction += deduction
        issues.append(
            ReadinessIssue(
                label=label,
                count=count,
                image_ids=tuple(image_ids),
                query=query,
                explanation=explanation,
                severity=severity,
                maximum_deduction=maximum,
                deduction=deduction,
            )
        )

    multiple_face_ids = tuple(
        int(record.image_id)
        for record in eligible
        if int(record.face_count or 0) > 1
    )
    issues.append(
        ReadinessIssue(
            label="Multiple Faces",
            count=len(multiple_face_ids),
            image_ids=multiple_face_ids,
            query="identity:multiple_faces",
            explanation=(
                "Images with more than one detected face. This is advisory and does not "
                "reduce the readiness score."
            ),
            severity="advisory",
        )
    )

    repeated_groups = find_repeated_training_text_groups(
        (
            (int(record.image_id), _training_text_layers(record))
            for record in eligible
        ),
        training_text_profile,
    )
    repeated_ids = tuple(
        image_id
        for group in repeated_groups
        for image_id in group
    )
    repeated_training_text = len(repeated_ids)
    repeated_text_deduction = 5 * (repeated_training_text / denominator) if eligible else 0.0
    total_deduction += repeated_text_deduction
    issues.append(
        ReadinessIssue(
            label="Repeated Training Text",
            count=repeated_training_text,
            image_ids=repeated_ids,
            query=_image_id_query(repeated_ids),
            explanation=(
                "Eligible images whose exported sidecar text exactly matches another "
                f"eligible image with the {training_text_profile.label} profile. This "
                "can be intentional for simple identity anchors, but repeated boilerplate "
                "may hide useful pose, outfit, crop, expression, or scene differences."
            ),
            severity="review",
            maximum_deduction=5,
            deduction=repeated_text_deduction,
        )
    )

    overlay_evidence = tuple(
        (
            record,
            evaluate_prominent_overlay(
                record,
                coverage_threshold_percent=overlay_coverage_threshold_percent,
                spatial_mode=overlay_spatial_mode,
            ),
        )
        for record in eligible
    )
    prominent_overlay_ids = tuple(
        int(record.image_id)
        for record, evidence in overlay_evidence
        if evidence.matched
    )
    unavailable_spatial_count = sum(
        1
        for _record, evidence in overlay_evidence
        if (evidence.text_region_count or evidence.bar_region_count)
        and not evidence.spatial_available
    )
    spatial_label = OVERLAY_SPATIAL_MODE_LABELS[overlay_spatial_mode]
    issues.append(
        ReadinessIssue(
            label="Prominent Overlay",
            count=len(prominent_overlay_ids),
            image_ids=prominent_overlay_ids,
            query=_image_id_query(prominent_overlay_ids),
            explanation=(
                f"Images where recognized text or an obvious neutral bar/banner covers "
                f"at least {overlay_coverage_threshold_percent}% of the {spatial_label} "
                f"region. {unavailable_spatial_count} image(s) with overlay candidates "
                "lacked the face/body evidence required by this mode. Run Quality Analysis "
                "after this update to find bars, and Florence OCR to retain text boxes; "
                "Face and Body modes also require their respective analysis. Review "
                "manually before excluding an image."
            ),
            severity="advisory",
        )
    )

    quality_missing_ids = tuple(
        int(record.image_id)
        for record in eligible
        if str(getattr(record, "quality_status", "") or "") != "success"
    )
    quality_missing = len(quality_missing_ids)
    quality_missing_deduction = 5 * (quality_missing / denominator) if eligible else 0.0
    total_deduction += quality_missing_deduction
    issues.append(
        ReadinessIssue(
            label="Quality Not Analyzed",
            count=quality_missing,
            image_ids=quality_missing_ids,
            query="NOT quality:analyzed",
            explanation=(
                "Eligible images without a successful local sharpness and perceptual-hash "
                "measurement. Run quality analysis manually to fill this cache."
            ),
            severity="review",
            maximum_deduction=5,
            deduction=quality_missing_deduction,
        )
    )

    blurry_ids = tuple(
        int(record.image_id)
        for record in eligible
        if getattr(record, "sharpness_score", None) is not None
        and float(record.sharpness_score) < float(blur_threshold)
    )
    blurry = len(blurry_ids)
    blur_deduction = 10 * (blurry / denominator) if eligible else 0.0
    total_deduction += blur_deduction
    issues.append(
        ReadinessIssue(
            label="Blur",
            count=blurry,
            image_ids=blurry_ids,
            query=f"blur:{float(blur_threshold):g}",
            explanation=(
                "Images whose local sharpness score falls below the selected threshold. "
                "This is a heuristic: intentional soft focus may still be useful."
            ),
            severity="review",
            maximum_deduction=10,
            deduction=blur_deduction,
        )
    )

    duplicate_clusters = duplicate_candidate_clusters(
        eligible,
        duplicate_similarity_percent,
    )
    possible_duplicate_ids = tuple(
        image_id
        for cluster in duplicate_clusters
        for image_id in cluster
    )
    possible_duplicates = len(possible_duplicate_ids)
    issues.append(
        ReadinessIssue(
            label="Possible Duplicates",
            count=possible_duplicates,
            image_ids=possible_duplicate_ids,
            query=f"duplicate:{int(duplicate_similarity_percent)}",
            explanation=(
                "Images belonging to an in-scope perceptual-hash cluster at the selected "
                "similarity. These are candidates for review, never automatic reject decisions."
            ),
            severity="advisory",
        )
    )

    score = (
        max(0, min(100, round(100.0 - total_deduction)))
        if items and eligible
        else 0
    )
    blocking_count = sum(issue.count for issue in issues if issue.severity == "blocking")
    if not items:
        status = "No catalog data"
    elif not eligible:
        status = "No eligible images"
    elif score >= 90 and blocking_count == 0:
        status = "Ready to export"
    elif score >= 70:
        status = "Review recommended"
    else:
        status = "Needs preparation"

    return DatasetReadinessReport(
        score=score,
        status=status,
        profile=profile,
        total_images=len(items),
        eligible_images=len(eligible),
        review_counts=dict(review_counts),
        file_counts=dict(file_counts),
        resolution_counts=dict(resolution_counts),
        quality_counts=dict(quality_counts),
        issues=tuple(issues),
        top_trigger_keywords=_top_values(record.manual_keyword for record in items),
        top_manual_tags=_top_tags(record.manual_tags for record in items),
        top_ai_tags=_top_tags(record.ai_tags_active for record in items),
        top_excluded_tags=_top_tags(record.ai_tags_excluded for record in items),
    )


def _short_side(record: Any) -> int:
    width = int(record.width or 0)
    height = int(record.height or 0)
    return min(width, height) if width and height else 0


def _resolution_bucket(record: Any) -> str:
    short_side = _short_side(record)
    if short_side == 0:
        return "unknown"
    if short_side < 512:
        return "below_512"
    if short_side < 768:
        return "512_to_767"
    if short_side < 1024:
        return "768_to_1023"
    return "1024_plus"


def _training_text_layers(record: Any) -> TrainingTextLayers:
    """Project one browser record into the canonical export-text layers.

    Browser records expose comma-separated summaries, while the exporter reads
    individual tag rows.  Sorting each layer by normalized name mirrors the
    export repository's deterministic SQL ordering and prevents database row
    order from changing a validation result.
    """
    return TrainingTextLayers(
        trigger_keyword=str(getattr(record, "manual_keyword", "") or ""),
        manual_tags=_split_tag_summary(getattr(record, "manual_tags", "")),
        active_ai_tags=_split_tag_summary(
            getattr(record, "ai_tags_active", "")
        ),
        raw_caption=str(getattr(record, "caption", "") or ""),
    )


def _split_tag_summary(value: Any) -> tuple[str, ...]:
    tags = {
        " ".join(piece.split()).strip()
        for piece in str(value or "").replace("\n", ",").split(",")
        if piece.strip()
    }
    return tuple(sorted(tags, key=str.casefold))


def _image_id_query(image_ids: Iterable[int]) -> str:
    """Build an exact browser query for a validation result set."""
    values = sorted({int(image_id) for image_id in image_ids})
    return " OR ".join(f"id:{image_id}" for image_id in values)


def _top_values(values: Iterable[str], limit: int = 10) -> tuple[tuple[str, int], ...]:
    counts = Counter(value.strip() for value in values if value and value.strip())
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit])


def _top_tags(values: Iterable[str], limit: int = 10) -> tuple[tuple[str, int], ...]:
    tags: list[str] = []
    for value in values:
        tags.extend(piece.strip() for piece in str(value).split(",") if piece.strip())
    return _top_values(tags, limit=limit)
