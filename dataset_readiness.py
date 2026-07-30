"""Transparent Dataset Readiness calculations over catalog projections.

Milestone 8A deliberately avoids pretending that a formula can predict LoRA
quality. The score summarizes correctable catalog preparation work and keeps
every deduction visible. Milestone 8B adds locally cached sharpness and
perceptual-hash facts while retaining the distinction between a measurement
and a user decision.
"""

from __future__ import annotations

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


def build_readiness_report(
    records: Iterable[Any],
    *,
    profile_key: str = DEFAULT_READINESS_PROFILE_KEY,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    duplicate_similarity_percent: int = DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
) -> DatasetReadinessReport:
    """Summarize records for one explicitly selected LoRA target profile.

    Rejected and quarantined records remain visible in composition statistics
    but are not treated as intended training images.  Each scoring deduction is
    proportional to the affected share of eligible images and capped by the
    documented maximum, making the score stable across small and large sets.
    """
    items = list(records)
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
