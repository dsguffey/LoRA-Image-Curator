"""Conservative, explainable culling for transient browser selections.

Milestone 8F does not attempt to invent a perfect training dataset.  It removes
only candidates for which the catalog already contains a concrete quality,
usability, or redundancy signal.  The caller previews the resulting plan before
changing the in-memory selection; this module never writes to SQLite, changes a
review decision, deletes a source image, or creates an image set.

The algorithm deliberately separates two ideas:

* serious issues are evaluated independently for each image;
* redundancy is evaluated only among the remaining candidates, keeping the
  strongest representative while retaining transitive cluster endpoints that
  are not directly similar to one another.

That second rule matters because perceptual-hash relationships are not
transitive.  If A resembles B and B resembles C, it does not necessarily follow
that A and C are redundant.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from quality_analysis import perceptual_hash_similarity


DEFAULT_SMALL_FACE_AREA_RATIO = 0.0025
DEFAULT_PROMINENT_FACE_RELATIVE_RATIO = 0.45


@dataclass(slots=True, frozen=True)
class CullChecks:
    """Granular checks selected in the browser's curation dialog."""

    already_rejected: bool = True
    missing_or_unreadable: bool = True
    low_resolution: bool = True
    blur: bool = True
    screenshot_or_ui: bool = True
    no_person_or_face: bool = False
    subject_too_small: bool = True
    multiple_prominent_faces: bool = True
    any_multiple_people_or_faces: bool = False
    near_duplicates: bool = True


@dataclass(slots=True, frozen=True)
class CullCriteria:
    """User-visible thresholds borrowed from the current readiness settings."""

    profile_label: str
    minimum_short_side: int
    blur_threshold: float
    duplicate_similarity_percent: float
    small_face_area_ratio: float = DEFAULT_SMALL_FACE_AREA_RATIO
    prominent_face_relative_ratio: float = DEFAULT_PROMINENT_FACE_RELATIVE_RATIO
    checks: CullChecks = field(default_factory=CullChecks)


@dataclass(slots=True, frozen=True)
class CullDecision:
    """One proposed deselection and every concrete reason supporting it."""

    image_id: int
    filename: str
    reasons: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class CullPlan:
    """Complete, immutable preview of one culling pass."""

    considered_image_ids: tuple[int, ...]
    kept_image_ids: tuple[int, ...]
    decisions: tuple[CullDecision, ...]
    reason_counts: tuple[tuple[str, int], ...]
    unavailable_counts: tuple[tuple[str, int], ...]
    criteria: CullCriteria

    @property
    def removed_image_ids(self) -> tuple[int, ...]:
        return tuple(decision.image_id for decision in self.decisions)


def build_cull_plan(
    records: Iterable[object],
    criteria: CullCriteria,
) -> CullPlan:
    """Return an explainable deselection plan for the supplied candidates.

    The records are expected to use the browser projection's attribute names.
    ``getattr`` defaults keep the service usable in dependency-light tests and
    make absent provider results an explicit "not evaluated" condition rather
    than a reason to remove an image.
    """
    candidates = list(records)
    records_by_id = {
        int(getattr(record, "image_id")): record for record in candidates
    }
    ordered_ids = tuple(records_by_id)
    reasons_by_id: dict[int, list[str]] = {image_id: [] for image_id in ordered_ids}
    reason_categories_by_id: dict[int, set[str]] = {
        image_id: set() for image_id in ordered_ids
    }
    unavailable = Counter()

    for image_id in ordered_ids:
        record = records_by_id[image_id]
        reasons = reasons_by_id[image_id]
        categories = reason_categories_by_id[image_id]

        def add(category: str, reason: str) -> None:
            categories.add(category)
            reasons.append(reason)

        review_status = str(getattr(record, "review_status", "") or "").casefold()
        if (
            criteria.checks.already_rejected
            and review_status in {"reject", "quarantined"}
        ):
            add("Already rejected", "Already marked Reject or quarantined")

        file_status = str(getattr(record, "file_status", "") or "").casefold()
        if criteria.checks.missing_or_unreadable and file_status != "present":
            add("Missing source", "Preferred source file is unavailable")

        quality_status = str(
            getattr(record, "quality_status", "") or ""
        ).casefold()
        if quality_status == "error" and criteria.checks.missing_or_unreadable:
            detail = str(getattr(record, "quality_error", "") or "").strip()
            suffix = f": {detail}" if detail else ""
            add("Unreadable image", f"Quality analysis could not read the image{suffix}")
        elif quality_status != "success" and (
            criteria.checks.missing_or_unreadable or criteria.checks.blur
        ):
            unavailable["Quality not analyzed"] += 1

        width = _positive_int(getattr(record, "width", None))
        height = _positive_int(getattr(record, "height", None))
        if criteria.checks.low_resolution and (width is None or height is None):
            unavailable["Resolution unavailable"] += 1
        elif width is not None and height is not None:
            short_side = min(width, height)
            if (
                criteria.checks.low_resolution
                and short_side < int(criteria.minimum_short_side)
            ):
                add(
                    "Low resolution",
                    (
                        f"Short side is {short_side:,} px; {criteria.profile_label} "
                        f"expects at least {criteria.minimum_short_side:,} px"
                    ),
                )

        sharpness = _optional_float(getattr(record, "sharpness_score", None))
        if (
            criteria.checks.blur
            and quality_status == "success"
            and sharpness is not None
        ):
            if sharpness < float(criteria.blur_threshold):
                add(
                    "Blur",
                    (
                        f"Blur score {sharpness:.1f} is below the "
                        f"{criteria.blur_threshold:g} threshold"
                    ),
                )

        screenshot = str(
            getattr(record, "likely_screenshot_or_ui", "") or ""
        ).casefold()
        if criteria.checks.screenshot_or_ui and screenshot == "yes":
            add("Screenshot/UI", "Florence marked it as a likely screenshot or UI image")
        elif criteria.checks.screenshot_or_ui and screenshot in {"", "not_evaluated"}:
            unavailable["Screenshot check unavailable"] += 1

        person_count = _optional_nonnegative_int(
            getattr(record, "person_count", None)
        )
        face_analysis_available = bool(
            getattr(record, "face_analysis_available", True)
        )
        face_count = (
            _optional_nonnegative_int(getattr(record, "face_count", None))
            if face_analysis_available
            else None
        )
        known_counts = [
            count for count in (person_count, face_count) if count is not None
        ]
        maximum_people = max(known_counts, default=None)
        if (
            criteria.checks.any_multiple_people_or_faces
            and maximum_people is not None
            and maximum_people > 1
        ):
            add(
                "Multiple people",
                f"Multiple people/faces were detected ({maximum_people})",
            )
        if (
            criteria.checks.no_person_or_face
            and person_count == 0
            and face_count == 0
            and face_analysis_available
        ):
            add(
                "No person or face",
                "Florence found no person and face analysis found no face",
            )
        elif criteria.checks.no_person_or_face and (
            person_count is None or not face_analysis_available
        ):
            unavailable["No-person check unavailable"] += 1

        if (
            criteria.checks.any_multiple_people_or_faces
            and person_count is None
            and face_count is None
        ):
            unavailable["People count unavailable"] += 1

        face_ratio = _optional_float(
            getattr(record, "largest_face_area_ratio", None)
        )
        second_face_ratio = _optional_float(
            getattr(record, "second_largest_face_area_ratio", None)
        )
        if (
            criteria.checks.subject_too_small
            and face_count is not None
            and face_count > 0
        ):
            if face_ratio is None:
                unavailable["Face-size check unavailable"] += 1
            elif face_ratio < float(criteria.small_face_area_ratio):
                add(
                    "Subject too small",
                    (
                        "Largest detected face covers only "
                        f"{face_ratio * 100:.2f}% of the image"
                    ),
                )
        elif criteria.checks.subject_too_small and face_count is None:
            unavailable["Face-size check unavailable"] += 1

        if criteria.checks.multiple_prominent_faces:
            if face_count is None:
                unavailable["Face-prominence check unavailable"] += 1
            elif face_count > 1:
                if face_ratio is None or second_face_ratio is None:
                    unavailable["Face-prominence check unavailable"] += 1
                elif (
                    second_face_ratio >= float(criteria.small_face_area_ratio)
                    and face_ratio > 0
                    and (
                        second_face_ratio / face_ratio
                        >= float(criteria.prominent_face_relative_ratio)
                    )
                ):
                    add(
                        "Multiple prominent faces",
                        (
                            "The second-largest face is "
                            f"{(second_face_ratio / face_ratio) * 100:.0f}% "
                            "of the largest face"
                        ),
                    )

    # Redundancy is considered only after serious per-image issues.  A greedy
    # best-first pass avoids collapsing an entire connected component to one
    # image when its endpoints do not directly meet the similarity threshold.
    survivors = [
        records_by_id[image_id]
        for image_id in ordered_ids
        if not reasons_by_id[image_id]
    ]
    if criteria.checks.near_duplicates:
        hashed_survivors: list[object] = []
        for record in survivors:
            perceptual_hash = str(
                getattr(record, "perceptual_hash", "") or ""
            ).strip()
            if _has_compatible_hash(perceptual_hash):
                hashed_survivors.append(record)
            else:
                unavailable["Similarity hash unavailable"] += 1

        ranked = sorted(hashed_survivors, key=_utility_key, reverse=True)
        retained_hashed: list[object] = []
        threshold = float(criteria.duplicate_similarity_percent)
        for record in ranked:
            record_hash = str(getattr(record, "perceptual_hash", "") or "")
            redundant_with: object | None = None
            redundant_similarity = -1.0
            for retained in retained_hashed:
                similarity = perceptual_hash_similarity(
                    record_hash,
                    str(getattr(retained, "perceptual_hash", "") or ""),
                )
                if similarity >= threshold and similarity > redundant_similarity:
                    redundant_with = retained
                    redundant_similarity = similarity

            if redundant_with is None:
                retained_hashed.append(record)
                continue

            image_id = int(getattr(record, "image_id"))
            kept_filename = _filename(redundant_with)
            reasons_by_id[image_id].append(
                (
                    f"Near-duplicate ({redundant_similarity:.1f}% similar) of the "
                    f"stronger retained candidate “{kept_filename}”"
                )
            )
            reason_categories_by_id[image_id].add("Near-duplicate")

    decisions = tuple(
        CullDecision(
            image_id=image_id,
            filename=_filename(records_by_id[image_id]),
            reasons=tuple(reasons_by_id[image_id]),
        )
        for image_id in ordered_ids
        if reasons_by_id[image_id]
    )
    removed_ids = {decision.image_id for decision in decisions}
    kept_ids = tuple(image_id for image_id in ordered_ids if image_id not in removed_ids)

    reason_counts = Counter(
        category
        for image_id in ordered_ids
        for category in reason_categories_by_id[image_id]
    )
    return CullPlan(
        considered_image_ids=ordered_ids,
        kept_image_ids=kept_ids,
        decisions=decisions,
        reason_counts=tuple(sorted(reason_counts.items())),
        unavailable_counts=tuple(sorted(unavailable.items())),
        criteria=criteria,
    )


def _utility_key(record: object) -> tuple[float | int | str, ...]:
    """Rank near-duplicate versions without claiming a universal aesthetic.

    Manual Keep and confirmed identity decisions lead because they represent
    actual user judgment.  The remaining fields prefer a present, single-person,
    clearly identified, sharp, adequately sized source.  Image ID is reversed in
    the final position so older deterministic catalog entries win exact ties.
    """
    review_status = str(getattr(record, "review_status", "") or "").casefold()
    identity_status = str(
        getattr(record, "identity_review_status", "") or ""
    ).casefold()
    file_present = (
        str(getattr(record, "file_status", "") or "").casefold() == "present"
    )
    person_count = _optional_nonnegative_int(getattr(record, "person_count", None))
    face_count = (
        _optional_nonnegative_int(getattr(record, "face_count", None))
        if bool(getattr(record, "face_analysis_available", True))
        else None
    )
    single_person = person_count == 1 or (person_count is None and face_count == 1)
    screenshot_clear = (
        str(getattr(record, "likely_screenshot_or_ui", "") or "").casefold()
        == "no"
    )
    identity_similarity = _optional_float(
        getattr(record, "identity_similarity", None)
    )
    sharpness = _optional_float(getattr(record, "sharpness_score", None))
    width = _positive_int(getattr(record, "width", None)) or 0
    height = _positive_int(getattr(record, "height", None)) or 0
    short_side = min(width, height) if width and height else 0
    pixel_area = width * height
    face_ratio = _optional_float(
        getattr(record, "largest_face_area_ratio", None)
    )
    image_id = int(getattr(record, "image_id"))
    return (
        int(review_status == "keep"),
        int(identity_status == "confirmed"),
        int(file_present),
        int(single_person),
        int(screenshot_clear),
        identity_similarity if identity_similarity is not None else -1.0,
        sharpness if sharpness is not None else -1.0,
        short_side,
        pixel_area,
        face_ratio if face_ratio is not None else -1.0,
        -image_id,
    )


def _filename(record: object) -> str:
    value = str(getattr(record, "filename", "") or "").strip()
    if value:
        return value
    return f"Image {int(getattr(record, 'image_id'))}"


def _has_compatible_hash(value: str) -> bool:
    if len(value) != 16:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _positive_int(value: object) -> int | None:
    try:
        converted = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _optional_nonnegative_int(value: object) -> int | None:
    try:
        converted = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def _optional_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
