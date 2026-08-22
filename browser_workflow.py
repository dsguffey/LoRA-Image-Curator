"""Pure browser-filtering and keyword-selection workflow services.

The Thumbnail Browser is the application's main pruning workspace.  This
module keeps its filter semantics independent of Tkinter so image-set scope,
readiness findings, catalog state, and multi-keyword selection can be tested
without opening a window.

Readiness membership is derived from :mod:`dataset_readiness` rather than
reimplementing its rules.  That single-source boundary is important: a browser
filter called ``Blur`` or ``No Training Text`` must identify the same images as
the final readiness report for the same profile and thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from dataset_readiness import (
    DEFAULT_READINESS_PROFILE_KEY,
    DEFAULT_OVERLAY_COVERAGE_PERCENT,
    DEFAULT_OVERLAY_SPATIAL_MODE,
    DatasetReadinessReport,
    build_readiness_report,
    normalize_overlay_spatial_mode,
)
from quality_analysis import (
    DEFAULT_BLUR_THRESHOLD,
    DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
)


ALL_IMAGE_SETS_LABEL = "All catalog images"
GENERAL_STATE_FILTERS = (
    "All images",
    "Identity suggested",
    "Manual / confirmed",
    "Has OCR text",
    "No image file found",
    "Unreviewed",
    "Reviewed",
)
FACE_STATE_FILTERS = (
    "Any face evidence",
    "Face analysis not run",
    "Has face",
    "No face",
    "Visible face (pose evidence)",
    "No visible face (pose evidence)",
)
BODY_STATE_FILTERS = (
    "Any body / pose evidence",
    "Body analysis not run",
    "Has body / pose",
    "No body / pose",
    "Full body",
    "Partial body",
    "Multiple poses",
)
# Retain the historical union as a compatibility surface for saved values and
# older tests. New UI code presents the three categories independently.
CATALOG_STATE_FILTERS = (
    *GENERAL_STATE_FILTERS,
    *FACE_STATE_FILTERS[1:],
    *BODY_STATE_FILTERS[1:],
)
READINESS_ISSUE_LABELS = (
    "Missing Files",
    "Missing Trigger Keyword",
    "Unreviewed",
    "Low Resolution",
    "No Training Text",
    "Identity Unconfirmed",
    "Multiple Faces",
    "Repeated Training Text",
    "Prominent Overlay",
    "Quality Not Analyzed",
    "Blur",
    "Possible Duplicates",
)


@dataclass(slots=True, frozen=True)
class BrowserFilterState:
    """One session-only composition of browser filters.

    The selected image set and issue checkboxes are working state, not durable
    catalog facts.  Readiness profile and quality thresholds may be copied back
    to application preferences because they are explicit interpretations shared
    with Finalize & Export.
    """

    catalog_state: str = "All images"
    face_state: str = "Any face evidence"
    body_state: str = "Any body / pose evidence"
    image_set_id: int | None = None
    image_set_name: str = ""
    readiness_issues: frozenset[str] = field(default_factory=frozenset)
    readiness_match: str = "any"
    profile_key: str = DEFAULT_READINESS_PROFILE_KEY
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD
    duplicate_similarity_percent: int = DEFAULT_DUPLICATE_SIMILARITY_PERCENT
    overlay_coverage_threshold_percent: int = DEFAULT_OVERLAY_COVERAGE_PERCENT
    overlay_spatial_mode: str = DEFAULT_OVERLAY_SPATIAL_MODE

    def normalized(self) -> "BrowserFilterState":
        """Return a defensive, bounded state suitable for evaluation."""
        raw_catalog_state = (
            "No image file found"
            if self.catalog_state == "Missing file"
            else self.catalog_state
        )
        catalog_state = (
            raw_catalog_state
            if raw_catalog_state in CATALOG_STATE_FILTERS
            else "All images"
        )
        face_state = (
            self.face_state
            if self.face_state in FACE_STATE_FILTERS
            else "Any face evidence"
        )
        body_state = (
            self.body_state
            if self.body_state in BODY_STATE_FILTERS
            else "Any body / pose evidence"
        )
        # Migrate a session or saved preference created by the v0.26 single
        # catalog-state dropdown into the appropriate independent category.
        if catalog_state in FACE_STATE_FILTERS:
            face_state = catalog_state
            catalog_state = "All images"
        elif catalog_state in BODY_STATE_FILTERS:
            body_state = catalog_state
            catalog_state = "All images"
        if catalog_state not in GENERAL_STATE_FILTERS:
            catalog_state = "All images"
        issues = frozenset(
            label
            for label in self.readiness_issues
            if label in READINESS_ISSUE_LABELS
        )
        return BrowserFilterState(
            catalog_state=catalog_state,
            face_state=face_state,
            body_state=body_state,
            image_set_id=(
                int(self.image_set_id)
                if self.image_set_id is not None
                else None
            ),
            image_set_name=" ".join(self.image_set_name.split()).strip(),
            readiness_issues=issues,
            readiness_match="all" if self.readiness_match == "all" else "any",
            profile_key=str(self.profile_key),
            blur_threshold=max(0.0, min(10000.0, float(self.blur_threshold))),
            duplicate_similarity_percent=max(
                96,
                min(100, int(self.duplicate_similarity_percent)),
            ),
            overlay_coverage_threshold_percent=max(
                1,
                min(30, int(self.overlay_coverage_threshold_percent)),
            ),
            overlay_spatial_mode=normalize_overlay_spatial_mode(
                self.overlay_spatial_mode
            ),
        )

    def summary(self) -> str:
        """Describe active constraints compactly for the browser toolbar."""
        parts: list[str] = []
        if self.image_set_id is not None:
            parts.append(f"Set: {self.image_set_name or 'selected set'}")
        if self.catalog_state != "All images":
            parts.append(self.catalog_state)
        if self.face_state != "Any face evidence":
            parts.append(self.face_state)
        if self.body_state != "Any body / pose evidence":
            parts.append(self.body_state)
        issue_count = len(self.readiness_issues)
        if issue_count == 1:
            parts.append(next(iter(self.readiness_issues)))
        elif issue_count > 1:
            joiner = "all" if self.readiness_match == "all" else "any"
            parts.append(f"{joiner} of {issue_count} readiness checks")
        return " · ".join(parts) if parts else "All images"

    def is_active(self) -> bool:
        """Return whether the state hides any catalog records."""
        normalized = self.normalized()
        return bool(
            normalized.image_set_id is not None
            or normalized.catalog_state != "All images"
            or normalized.face_state != "Any face evidence"
            or normalized.body_state != "Any body / pose evidence"
            or normalized.readiness_issues
        )


@dataclass(slots=True, frozen=True)
class BrowserFilterResult:
    """Filtered records plus the readiness evidence used to produce them."""

    records: tuple[object, ...]
    readiness_report: DatasetReadinessReport
    issue_image_ids: dict[str, frozenset[int]]


def apply_browser_filter_state(
    records: Iterable[object],
    state: BrowserFilterState,
    *,
    image_set_ids: Iterable[int] | None = None,
) -> BrowserFilterResult:
    """Apply image-set, readiness, and catalog-state constraints in order.

    Readiness is calculated across the complete chosen image-set scope before
    text search or catalog-state narrowing.  This keeps duplicate and repeated
    training-text findings meaningful for the dataset being curated rather
    than recalculating them from whichever thumbnails happen to remain visible.
    """
    normalized = state.normalized()
    scoped_records = list(records)
    if normalized.image_set_id is not None:
        member_ids = {int(image_id) for image_id in (image_set_ids or ())}
        scoped_records = [
            record
            for record in scoped_records
            if int(getattr(record, "image_id")) in member_ids
        ]

    report = build_readiness_report(
        scoped_records,
        profile_key=normalized.profile_key,
        blur_threshold=normalized.blur_threshold,
        duplicate_similarity_percent=normalized.duplicate_similarity_percent,
        overlay_coverage_threshold_percent=normalized.overlay_coverage_threshold_percent,
        overlay_spatial_mode=normalized.overlay_spatial_mode,
    )
    issue_image_ids = {
        issue.label: frozenset(issue.image_ids)
        for issue in report.issues
    }

    selected_issue_sets = [
        issue_image_ids[label]
        for label in normalized.readiness_issues
        if label in issue_image_ids
    ]
    filtered: list[object] = []
    for record in scoped_records:
        image_id = int(getattr(record, "image_id"))
        if selected_issue_sets:
            matches = (
                all(image_id in candidates for candidates in selected_issue_sets)
                if normalized.readiness_match == "all"
                else any(image_id in candidates for candidates in selected_issue_sets)
            )
            if not matches:
                continue
        if (
            matches_catalog_state(record, normalized.catalog_state)
            and matches_catalog_state(record, normalized.face_state)
            and matches_catalog_state(record, normalized.body_state)
        ):
            filtered.append(record)

    return BrowserFilterResult(
        records=tuple(filtered),
        readiness_report=report,
        issue_image_ids=issue_image_ids,
    )


def matches_catalog_state(record: object, choice: str) -> bool:
    """Return whether one record satisfies a familiar catalog-state filter."""
    if choice == "Has face":
        return int(getattr(record, "face_count", 0) or 0) > 0
    if choice == "Face analysis not run":
        return not bool(getattr(record, "face_analysis_available", False))
    if choice == "No face":
        return (
            bool(getattr(record, "face_analysis_available", False))
            and int(getattr(record, "face_count", 0) or 0) == 0
        )
    if choice == "Body analysis not run":
        return not bool(getattr(record, "body_analysis_available", False))
    if choice == "Has body / pose":
        return bool(getattr(record, "body_detected", False))
    if choice == "No body / pose":
        return (
            bool(getattr(record, "body_analysis_available", False))
            and not bool(getattr(record, "body_detected", False))
        )
    if choice == "Full body":
        return bool(getattr(record, "full_body", False))
    if choice == "Partial body":
        return (
            bool(getattr(record, "body_analysis_available", False))
            and bool(getattr(record, "body_detected", False))
            and not bool(getattr(record, "full_body", False))
        )
    if choice == "Visible face (pose evidence)":
        return bool(getattr(record, "body_face_visible", False))
    if choice == "No visible face (pose evidence)":
        return (
            bool(getattr(record, "body_analysis_available", False))
            and not bool(getattr(record, "body_face_visible", False))
        )
    if choice == "Multiple poses":
        return int(getattr(record, "body_pose_count", 0) or 0) > 1
    if choice == "Identity suggested":
        return bool(
            getattr(record, "suggested_identity", "")
            and str(getattr(record, "identity_review_status", "")) != "rejected"
        )
    if choice == "Manual / confirmed":
        return bool(getattr(record, "has_manual_metadata", False))
    if choice == "Has OCR text":
        return bool(str(getattr(record, "ocr_text", "") or "").strip())
    if choice in {"No image file found", "Missing file"}:
        return str(getattr(record, "file_status", "")).casefold() in {
            "",
            "missing",
            "deleted",
        }
    if choice == "Unreviewed":
        return str(getattr(record, "review_status", "")).casefold() == "unreviewed"
    if choice == "Reviewed":
        return str(getattr(record, "review_status", "")).casefold() != "unreviewed"
    return True


def parse_keyword_terms(value: str) -> tuple[str, ...]:
    """Normalize comma/newline-separated terms while preserving phrase spaces."""
    terms: list[str] = []
    seen: set[str] = set()
    for piece in str(value).replace("\n", ",").split(","):
        term = " ".join(piece.split()).strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def record_matches_keyword_terms(
    record: object,
    terms: Iterable[str],
    *,
    match_all: bool = False,
) -> bool:
    """Match terms against the browser's tag/Trigger Keyword search vocabulary."""
    normalized_terms = tuple(
        "_".join(str(term).strip().casefold().split())
        for term in terms
        if str(term).strip()
    )
    if not normalized_terms:
        return False
    blob = str(getattr(record, "search_blob", "") or "").casefold()
    normalized_blob = blob + "\n" + blob.replace(" ", "_")
    matches = (term in normalized_blob for term in normalized_terms)
    return all(matches) if match_all else any(matches)
