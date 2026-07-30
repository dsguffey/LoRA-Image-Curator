"""
catalog_browser.py

Visual catalog browser for LoRA Image Curator.

The analysis screen answers the question, "What should be calculated?"  This
module answers the equally important question, "What is already in my
catalog?"  Keeping those responsibilities separate prevents browsing code from
becoming tangled with Florence, InsightFace, or future provider execution.

Version 0.15.0 keeps this surface focused on images and image sets; catalog
creation, opening, import, and deletion now live in the SQLite Catalog section
of the LoRA Image Curator tab. Exact copies remain a SHA-256/file-location fact;
perceptual similarity remains an advisory comparison. A positive duplicate
similarity search temporarily replaces the ordinary grid with clearly bounded
comparison groups; every other browser view retains the ordinary layout.
Remove Unnecessary Images previews explainable, selection-only culling without
changing catalog metadata or source files.

Design notes
------------

* One card represents one unique image-content record, not one file path.
  Duplicate file locations therefore do not clutter the grid.
* SQLite remains the source of truth.  Thumbnail files are disposable cache
  entries and may be deleted at any time.
* Pillow work happens in background threads.  Tk widgets and ``PhotoImage``
  objects are created only on the GUI thread because Tkinter is not thread-safe.
* Selection follows familiar Windows Explorer conventions: click, Ctrl-click,
  Shift-click, Ctrl+A, and Escape.
"""

from __future__ import annotations

import logging
import queue
import re
import sqlite3
import sys
import time

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from tkinter import messagebox, ttk
from typing import Callable, Iterable

import tkinter as tk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from catalog import Catalog
from catalog_lifecycle import validate_catalog_database
from advanced_search import (
    SearchSyntaxError,
    duplicate_review_threshold,
    record_matches_query,
)
from browser_workflow import (
    BrowserFilterState,
    apply_browser_filter_state,
    matches_catalog_state,
    record_matches_keyword_terms,
)
from browser_workflow_dialogs import (
    BrowserFiltersDialog,
    CurationOptions,
    CurationOptionsDialog,
    KeywordSelectionDialog,
)
from bulk_action_dialog import BulkActionDialog
from catalog_edits import (
    BatchEditRequest,
    BatchEditResult,
    CatalogEditService,
    TagEditResult,
)
from dataset_export import DatasetExportRepository
from dataset_readiness import (
    DEFAULT_READINESS_PROFILE_KEY,
    READINESS_PROFILES_BY_KEY,
)
from cull_report_dialog import CullReportDialog
from export_dialog import DatasetExportDialog
from file_actions import (
    CatalogRemovalSummary,
    FileActionService,
    FileActionSummary,
)
from image_set_dialog import ImageSetManagerDialog
from image_sets import ImageSetRepository
from image_quality_dialog import ImageQualityDialog
from image_review_dialog import ImageReviewDialog
from quality_analysis import (
    duplicate_candidate_clusters,
    duplicate_candidates_at_threshold,
    nearest_duplicate_candidate,
)
from selection_culling import CullChecks, CullCriteria, build_cull_plan
from search_dialogs import (
    AdvancedSearchDialog,
    SavedSearchesDialog,
    SearchHistoryDialog,
    ask_saved_search_name,
)
from ui_fonts import get_ui_font
from settings_manager import (
    get_default_quarantine_directory,
    get_settings_directory,
    load_settings,
    save_settings,
)
from ui_helpers import HelpIcon, Tooltip
from ui_scroll import register_mousewheel_region
from ui_theme import get_theme, normalize_theme_key
from video_origin import format_video_timestamp


THUMBNAIL_CACHE_FOLDER = "thumbnail_cache"
CARD_BATCH_SIZE = 100
# A Tk canvas eventually clips embedded windows at very large coordinates on
# Windows.  One 100-card page remains below that boundary even at a narrow
# one-card layout; advancing pages replaces widgets instead of growing the
# canvas forever.
CARD_PAGE_SIZE = CARD_BATCH_SIZE
BROWSER_HISTORY_LIMIT = 40
THUMBNAIL_RESULTS_PER_TICK = 24
DECODED_THUMBNAIL_CACHE_ITEMS = 320
DEFAULT_THUMBNAIL_SIZE = 190
DETAIL_PREVIEW_SIZE = 300
CARD_OUTER_WIDTH = 224
CARD_HORIZONTAL_GAP = 10
CARD_VERTICAL_GAP = 10
SEARCH_DEBOUNCE_MS = 500
LAYOUT_DEBOUNCE_MS = 80
LARGE_EDIT_CONFIRMATION_THRESHOLD = 100
MULTIPLE_VALUES_LABEL = "Multiple values"
DRAG_THRESHOLD_PIXELS = 5
PAGE_SHORTCUT_DEBOUNCE_SECONDS = 0.18
ALT_NAVIGATION_BINDTAG = "LoRAImageCuratorAltNavigation"

# Tk's named system colors make the selected state feel native on Windows.
# The fallbacks keep the module usable under Linux/macOS themes and automated
# tests that do not expose every Windows color name.
FALLBACK_SELECTION_COLOR = "#2B579A"
FALLBACK_SELECTION_TEXT = "#FFFFFF"
FALLBACK_CARD_BORDER = "#C9D2DB"
FALLBACK_CARD_BACKGROUND = "#FFFFFF"
FALLBACK_CARD_TEXT = "#1E252D"
FALLBACK_MUTED_TEXT = "#5D6975"
MANUAL_ACCENT = "#B65F00"
AI_TAG_FOREGROUND = "#174A7E"
AI_TAG_BACKGROUND = "#D7E9FF"
MANUAL_TAG_FOREGROUND = "#7A3D00"
MANUAL_TAG_BACKGROUND = "#F2C38B"
EXCLUDED_TAG_FOREGROUND = "#666666"
EXCLUDED_TAG_BACKGROUND = "#D9D9D9"
MISSING_ACCENT = "#9A3B3B"
DUPLICATE_GROUP_BACKGROUND = "#EAF0F7"
DUPLICATE_GROUP_BORDER = "#7890A8"
DUPLICATE_GROUP_HEADING = "#263F59"

REVIEW_LABEL_TO_STATUS = {
    "Unreviewed": "unreviewed",
    "Keep": "keep",
    "Needs follow-up": "review",
    "Reject": "reject",
}
REVIEW_STATUS_TO_LABEL = {
    status: label for label, status in REVIEW_LABEL_TO_STATUS.items()
}
IDENTITY_STATUS_LABELS = {
    "suggested": "AI suggestion — not reviewed",
    "confirmed": "Confirmed by you",
    "rejected": "Rejected by you",
}


@dataclass(slots=True, frozen=True)
class CatalogImageRecord:
    """One browser card assembled from several catalog tables."""

    image_id: int
    file_id: int | None
    absolute_path: str
    relative_path: str
    filename: str
    file_status: str
    file_location_count: int
    content_sha256: str
    byte_size: int
    width: int | None
    height: int | None
    caption: str
    object_labels: str
    ocr_text: str
    recommendation: str
    recommendation_reason: str
    person_count: int | None
    likely_screenshot_or_ui: str
    review_status: str
    review_notes: str
    tags: str
    manual_tags: str
    manual_keyword: str
    ai_tags_active: str
    ai_tags_excluded: str
    has_manual_metadata: bool
    face_count: int
    face_analysis_available: bool
    body_analysis_available: bool
    body_pose_count: int
    body_detected: bool
    body_face_visible: bool
    full_body_score: float | None
    full_body: bool
    body_classification: str
    largest_face_area_ratio: float | None
    second_largest_face_area_ratio: float | None
    identity_match_id: int | None
    suggested_identity: str
    identity_similarity: float | None
    identity_review_status: str
    first_seen_at: str
    sharpness_score: float | None
    perceptual_hash: str
    quality_status: str
    quality_error: str
    quality_analyzed_at: str
    image_set_names: str
    source_video: str = ""
    video_sampling_mode: str = ""
    video_timestamp_seconds: float | None = None
    video_frame_number: int | None = None
    video_interval_seconds: float | None = None
    face_max_detection_score: float | None = None
    face_analyzed_at: str = ""
    body_detection_threshold: float | None = None
    body_visibility_threshold: float | None = None
    body_full_body_threshold_percent: int | None = None
    body_analyzed_at: str = ""
    nearest_duplicate_image_id: int | None = None
    nearest_duplicate_similarity: float | None = None

    @property
    def source_path(self) -> Path | None:
        """Return the preferred file location, or ``None`` when absent."""
        return Path(self.absolute_path) if self.absolute_path else None

    @property
    def dimensions_text(self) -> str:
        """Format dimensions without pretending unknown values are zero."""
        if self.width is None or self.height is None:
            return "Unknown"
        return f"{self.width} × {self.height}"

    @property
    def byte_size_text(self) -> str:
        """Return a compact human-readable file size."""
        value = float(self.byte_size)
        units = ("bytes", "KB", "MB", "GB", "TB")
        unit_index = 0

        while value >= 1024.0 and unit_index < len(units) - 1:
            value /= 1024.0
            unit_index += 1

        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"

    @property
    def search_blob(self) -> str:
        """Combine tag metadata for the ordinary unqualified search.

        Filenames and paths are deliberately absent. Video-extracted frames
        commonly share a long subject name, so including file identity made a
        subject search match every frame regardless of its actual tags.
        Advanced field operators continue to handle non-tag review questions.
        """
        blob = "\n".join(
            (
                self.tags,
                self.manual_tags,
                self.manual_keyword,
                self.ai_tags_active,
                self.ai_tags_excluded,
            )
        ).casefold()
        return blob + "\n" + blob.replace(" ", "_")


@dataclass(slots=True, frozen=True)
class SelectionTagRecord:
    """One unambiguous tag chip shared by the entire current selection."""

    name: str
    normalized_name: str
    kind: str  # ``manual``, ``ai_active``, or ``ai_excluded``


@dataclass(slots=True, frozen=True)
class SavedSearchRecord:
    """One explicitly named, catalog-local query."""

    search_id: int
    name: str
    query: str


@dataclass(slots=True, frozen=True)
class BrowserHistoryEntry:
    """One chronological browser action handled by shared Undo and Redo.

    Selection entries carry before/after image-ID snapshots.  Catalog entries
    carry the durable operation ID written by :class:`CatalogEditService`.
    Keeping both kinds in one ordered stack makes Ctrl+Z/Ctrl+Y describe the
    user's actual action sequence instead of privileging one subsystem.
    """

    kind: str  # ``selection``, ``filter``, or ``catalog``
    description: str
    before: frozenset[int] = frozenset()
    after: frozenset[int] = frozenset()
    before_filter: BrowserFilterState | None = None
    after_filter: BrowserFilterState | None = None
    operation_id: int | None = None


def parse_manual_tag_input(raw_text: str) -> list[str]:
    """Parse a compact user entry into safe, case-insensitively unique tags."""
    pieces = re.split(r"[,;\n]+", raw_text)
    tags: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        tag = " ".join(piece.split()).strip()
        normalized = tag.casefold()
        if not tag or normalized in seen:
            continue
        if len(tag) > 120:
            raise ValueError(f'Tag is longer than 120 characters: "{tag[:40]}…"')
        seen.add(normalized)
        tags.append(tag)
    if not tags:
        raise ValueError("Enter at least one tag.")
    if len(tags) > 100:
        raise ValueError("Add no more than 100 distinct tags in one action.")
    return tags


class CatalogBrowserRepository:
    """
    Read browser records from one LoRA Image Curator catalog.

    The repository opens a short-lived SQLite connection for each refresh.  A
    long-lived GUI connection is unnecessary and would make it easier to leave
    a catalog locked after the user switches to another database.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    def fetch_records(self) -> list[CatalogImageRecord]:
        """Return one row per unique image, with the best available metadata."""
        if not self.database_path.exists():
            raise FileNotFoundError(f"Catalog not found: {self.database_path}")

        # Refuse to modify an unrelated SQLite file. ``Catalog`` can create a
        # schema in a blank database, which is useful to the analysis pipeline
        # but inappropriate for an Open dialog pointed at an arbitrary .db.
        self._validate_catalog_identity()

        # Opening through Catalog applies any supported additive schema
        # migrations. This repository performs no catalog edits after migration.
        with Catalog(self.database_path):
            pass

        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")

        try:
            rows = connection.execute(self._browser_query()).fetchall()
        finally:
            connection.close()

        records: list[CatalogImageRecord] = []
        for row in rows:
            absolute_path = str(row["absolute_path"] or "")
            if not absolute_path:
                filename = "(no file location)"
            elif "\\" in absolute_path:
                # A catalog copied from Windows should still display sensible
                # filenames during cross-platform inspection and testing.
                filename = PureWindowsPath(absolute_path).name
            else:
                filename = Path(absolute_path).name

            records.append(
                CatalogImageRecord(
                    image_id=int(row["image_id"]),
                    file_id=(
                        int(row["file_id"])
                        if row["file_id"] is not None
                        else None
                    ),
                    absolute_path=absolute_path,
                    relative_path=str(row["relative_path"] or ""),
                    filename=filename,
                    file_status=str(row["file_status"] or "missing"),
                    file_location_count=int(row["file_location_count"] or 0),
                    content_sha256=str(row["content_sha256"]),
                    byte_size=int(row["byte_size"] or 0),
                    width=(int(row["width"]) if row["width"] is not None else None),
                    height=(
                        int(row["height"]) if row["height"] is not None else None
                    ),
                    caption=str(row["caption"] or ""),
                    object_labels=str(row["object_labels"] or ""),
                    ocr_text=str(row["ocr_text"] or ""),
                    recommendation=str(row["candidate_recommendation"] or ""),
                    recommendation_reason=str(row["recommendation_reason"] or ""),
                    person_count=(
                        int(row["person_count"])
                        if row["person_count"] is not None
                        else None
                    ),
                    likely_screenshot_or_ui=str(
                        row["likely_screenshot_or_ui"] or ""
                    ),
                    review_status=str(row["review_status"] or "unreviewed"),
                    review_notes=str(row["review_notes"] or ""),
                    tags=str(row["tags"] or ""),
                    manual_tags=str(row["manual_tags"] or ""),
                    manual_keyword=str(row["manual_keyword"] or ""),
                    ai_tags_active=str(row["ai_tags_active"] or ""),
                    ai_tags_excluded=str(row["ai_tags_excluded"] or ""),
                    has_manual_metadata=bool(row["has_manual_metadata"] or 0),
                    face_count=int(row["face_count"] or 0),
                    face_analysis_available=bool(
                        row["face_analysis_available"] or 0
                    ),
                    body_analysis_available=bool(
                        row["body_analysis_available"] or 0
                    ),
                    body_pose_count=int(row["body_pose_count"] or 0),
                    body_detected=bool(row["body_detected"] or 0),
                    body_face_visible=bool(row["body_face_visible"] or 0),
                    full_body_score=(
                        float(row["full_body_score"])
                        if row["full_body_score"] is not None
                        else None
                    ),
                    full_body=bool(row["full_body"] or 0),
                    body_classification=str(
                        row["body_classification"] or ""
                    ),
                    largest_face_area_ratio=(
                        float(row["largest_face_area"])
                        / (int(row["width"]) * int(row["height"]))
                        if row["largest_face_area"] is not None
                        and row["width"] is not None
                        and row["height"] is not None
                        and int(row["width"]) > 0
                        and int(row["height"]) > 0
                        else None
                    ),
                    second_largest_face_area_ratio=(
                        float(row["second_largest_face_area"])
                        / (int(row["width"]) * int(row["height"]))
                        if row["second_largest_face_area"] is not None
                        and row["width"] is not None
                        and row["height"] is not None
                        and int(row["width"]) > 0
                        and int(row["height"]) > 0
                        else None
                    ),
                    identity_match_id=(
                        int(row["identity_match_id"])
                        if row["identity_match_id"] is not None
                        else None
                    ),
                    suggested_identity=str(row["suggested_identity"] or ""),
                    identity_similarity=(
                        float(row["identity_similarity"])
                        if row["identity_similarity"] is not None
                        else None
                    ),
                    identity_review_status=str(
                        row["identity_review_status"] or ""
                    ),
                    first_seen_at=str(row["first_seen_at"] or ""),
                    sharpness_score=(
                        float(row["sharpness_score"])
                        if row["sharpness_score"] is not None
                        else None
                    ),
                    perceptual_hash=str(row["perceptual_hash"] or ""),
                    quality_status=str(row["quality_status"] or ""),
                    quality_error=str(row["quality_error"] or ""),
                    quality_analyzed_at=str(row["quality_analyzed_at"] or ""),
                    image_set_names=str(row["image_set_names"] or ""),
                    source_video=str(row["source_video"] or ""),
                    video_sampling_mode=str(
                        row["video_sampling_mode"] or ""
                    ),
                    video_timestamp_seconds=(
                        float(row["video_timestamp_seconds"])
                        if row["video_timestamp_seconds"] is not None
                        else None
                    ),
                    video_frame_number=(
                        int(row["video_frame_number"])
                        if row["video_frame_number"] is not None
                        else None
                    ),
                    video_interval_seconds=(
                        float(row["video_interval_seconds"])
                        if row["video_interval_seconds"] is not None
                        else None
                    ),
                    face_max_detection_score=(
                        float(row["face_max_detection_score"])
                        if row["face_max_detection_score"] is not None
                        else None
                    ),
                    face_analyzed_at=str(row["face_analyzed_at"] or ""),
                    body_detection_threshold=(
                        float(row["body_detection_threshold"])
                        if row["body_detection_threshold"] is not None
                        else None
                    ),
                    body_visibility_threshold=(
                        float(row["body_visibility_threshold"])
                        if row["body_visibility_threshold"] is not None
                        else None
                    ),
                    body_full_body_threshold_percent=(
                        int(row["body_full_body_threshold_percent"])
                        if row["body_full_body_threshold_percent"] is not None
                        else None
                    ),
                    body_analyzed_at=str(row["body_analyzed_at"] or ""),
                )
            )

        # Nearest-neighbor enrichment used to compare every pair on every
        # Browser load. At 14,000 analyzed images that meant roughly 98 million
        # comparisons before the first page could appear. Duplicate groups now
        # use their bounded indexed path, the Image Quality popup computes one
        # selected neighbor on demand, and selection culling enriches only its
        # explicit candidate scope.
        records.sort(key=lambda item: (item.filename.casefold(), item.absolute_path.casefold()))
        return records

    def _validate_catalog_identity(self) -> None:
        """Confirm that the chosen SQLite file is a LoRA Image Curator catalog."""
        validate_catalog_database(self.database_path)

    @staticmethod
    def _browser_query() -> str:
        """
        Return the catalog projection used by the browser.

        Several correlated subqueries deliberately choose one preferred record:

        * one file path per image, preferring a present location
        * the newest successful Florence result
        * the strongest identity suggestion, including its user review state

        This avoids duplicate cards while still exposing the number of known
        file locations in the details pane.
        """
        return """
        WITH preferred_files AS (
            SELECT
                f.id AS file_id,
                f.image_id,
                f.absolute_path,
                f.relative_path,
                f.status AS file_status
            FROM files AS f
            WHERE f.id = (
                SELECT f2.id
                FROM files AS f2
                WHERE f2.image_id = f.image_id
                ORDER BY
                    CASE f2.status
                        WHEN 'present' THEN 0
                        WHEN 'quarantined' THEN 1
                        WHEN 'missing' THEN 2
                        ELSE 3
                    END,
                    f2.last_seen_at DESC,
                    f2.id DESC
                LIMIT 1
            )
        ),
        file_counts AS (
            SELECT image_id, COUNT(*) AS file_location_count
            FROM files
            GROUP BY image_id
        ),
        chosen_analysis AS (
            SELECT ar.*
            FROM analysis_results AS ar
            WHERE ar.id = (
                SELECT ar2.id
                FROM analysis_results AS ar2
                WHERE ar2.image_id = ar.image_id
                ORDER BY
                    CASE ar2.status WHEN 'success' THEN 0 ELSE 1 END,
                    ar2.analyzed_at DESC,
                    ar2.id DESC
                LIMIT 1
            )
        ),
        chosen_tag_analysis AS (
            SELECT ar.id, ar.image_id
            FROM analysis_results AS ar
            WHERE ar.status = 'success'
              AND ar.include_triage = 1
              AND ar.id = (
                  SELECT ar2.id
                  FROM analysis_results AS ar2
                  WHERE ar2.image_id = ar.image_id
                    AND ar2.status = 'success'
                    AND ar2.include_triage = 1
                  ORDER BY ar2.analyzed_at DESC, ar2.id DESC
                  LIMIT 1
              )
        ),
        tag_summary AS (
            SELECT
                it.image_id,
                GROUP_CONCAT(
                    CASE
                        WHEN it.review_status <> 'rejected' THEN t.name
                        ELSE NULL
                    END,
                    ', '
                ) AS tags,
                GROUP_CONCAT(
                    CASE
                        WHEN it.review_status <> 'rejected'
                         AND t.category = 'manual_tag'
                         AND LOWER(it.source) IN ('manual', 'user', 'user_manual')
                        THEN t.name
                        ELSE NULL
                    END,
                    ', '
                ) AS manual_tags,
                GROUP_CONCAT(
                    CASE
                        WHEN it.review_status <> 'rejected'
                         AND t.category = 'set_keyword'
                         AND LOWER(it.source) IN ('manual', 'user', 'user_manual')
                        THEN t.name
                        ELSE NULL
                    END,
                    ', '
                ) AS manual_keyword,
                MAX(
                    CASE
                        WHEN it.review_status <> 'rejected'
                         AND (
                            LOWER(it.source) IN ('manual', 'user', 'user_manual')
                            OR it.review_status = 'confirmed'
                         )
                        THEN 1
                        ELSE 0
                    END
                ) AS has_manual_metadata
            FROM image_tags AS it
            JOIN tags AS t ON t.id = it.tag_id
            GROUP BY it.image_id
        ),
        ai_tag_summary AS (
            SELECT
                ats.image_id,
                GROUP_CONCAT(
                    CASE WHEN exclusions.tag_id IS NULL THEN t.name END,
                    ', '
                ) AS ai_tags_active,
                GROUP_CONCAT(
                    CASE WHEN exclusions.tag_id IS NOT NULL THEN t.name END,
                    ', '
                ) AS ai_tags_excluded
            FROM analysis_tag_suggestions AS ats
            JOIN chosen_tag_analysis
                ON chosen_tag_analysis.id = ats.analysis_result_id
            JOIN tags AS t
                ON t.id = ats.tag_id
            LEFT JOIN image_tag_exclusions AS exclusions
                ON exclusions.image_id = ats.image_id
               AND exclusions.tag_id = ats.tag_id
            GROUP BY ats.image_id
        ),
        chosen_face_results AS (
            SELECT fir.*
            FROM face_image_results AS fir
            WHERE fir.status = 'success'
              AND fir.id = (
                  SELECT fir2.id
                  FROM face_image_results AS fir2
                  WHERE fir2.image_id = fir.image_id
                    AND fir2.status = 'success'
                  ORDER BY fir2.analyzed_at DESC, fir2.id DESC
                  LIMIT 1
              )
        ),
        ranked_face_areas AS (
            SELECT
                fir.image_id,
                (
                    MAX(0.0, fd.bbox_x2 - fd.bbox_x1)
                    * MAX(0.0, fd.bbox_y2 - fd.bbox_y1)
                ) AS face_area,
                fd.detection_score,
                ROW_NUMBER() OVER (
                    PARTITION BY fir.image_id
                    ORDER BY
                        (
                            MAX(0.0, fd.bbox_x2 - fd.bbox_x1)
                            * MAX(0.0, fd.bbox_y2 - fd.bbox_y1)
                        ) DESC,
                        fd.id ASC
                ) AS area_rank
            FROM chosen_face_results AS fir
            JOIN face_detections AS fd
                ON fd.face_result_id = fir.id
        ),
        face_summary AS (
            SELECT
                fir.image_id,
                MAX(fir.face_count) AS face_count,
                MAX(fir.analyzed_at) AS face_analyzed_at,
                MAX(areas.detection_score) AS max_detection_score,
                MAX(
                    CASE WHEN areas.area_rank = 1 THEN areas.face_area END
                ) AS largest_face_area,
                MAX(
                    CASE WHEN areas.area_rank = 2 THEN areas.face_area END
                ) AS second_largest_face_area
            FROM chosen_face_results AS fir
            LEFT JOIN ranked_face_areas AS areas
                ON areas.image_id = fir.image_id
            GROUP BY fir.image_id
        ),
        chosen_body_results AS (
            SELECT bir.*
            FROM body_image_results AS bir
            WHERE bir.id = (
                SELECT bir2.id
                FROM body_image_results AS bir2
                WHERE bir2.image_id = bir.image_id
                ORDER BY
                    CASE bir2.status WHEN 'success' THEN 0 ELSE 1 END,
                    bir2.analyzed_at DESC,
                    bir2.id DESC
                LIMIT 1
            )
        ),
        image_set_summary AS (
            SELECT
                members.image_id,
                GROUP_CONCAT(image_sets.name, CHAR(31)) AS image_set_names
            FROM image_set_members AS members
            JOIN image_sets
                ON image_sets.id = members.image_set_id
            GROUP BY members.image_id
        ),
        best_identity AS (
            SELECT
                fir.image_id,
                im.id AS identity_match_id,
                identities.name AS suggested_identity,
                im.similarity AS identity_similarity,
                im.review_status AS identity_review_status
            FROM identity_matches AS im
            JOIN identity_profiles AS ip
                ON ip.id = im.identity_profile_id
            JOIN identities
                ON identities.id = ip.identity_id
            JOIN face_detections AS fd
                ON fd.id = im.face_detection_id
            JOIN face_image_results AS fir
                ON fir.id = fd.face_result_id
            WHERE im.id = (
                SELECT im2.id
                FROM identity_matches AS im2
                JOIN face_detections AS fd2
                    ON fd2.id = im2.face_detection_id
                JOIN face_image_results AS fir2
                    ON fir2.id = fd2.face_result_id
                WHERE fir2.image_id = fir.image_id
                  AND im2.is_suggested = 1
                ORDER BY im2.similarity DESC, im2.id DESC
                LIMIT 1
            )
        )
        SELECT
            images.id AS image_id,
            preferred_files.file_id,
            preferred_files.absolute_path,
            preferred_files.relative_path,
            preferred_files.file_status,
            COALESCE(file_counts.file_location_count, 0) AS file_location_count,
            images.content_sha256,
            images.byte_size,
            images.width,
            images.height,
            images.first_seen_at,
            chosen_analysis.caption,
            chosen_analysis.object_labels,
            chosen_analysis.ocr_text,
            chosen_analysis.candidate_recommendation,
            chosen_analysis.recommendation_reason,
            chosen_analysis.person_count,
            chosen_analysis.likely_screenshot_or_ui,
            COALESCE(image_review_state.status, 'unreviewed') AS review_status,
            COALESCE(image_review_state.notes, '') AS review_notes,
            tag_summary.tags,
            tag_summary.manual_tags,
            tag_summary.manual_keyword,
            ai_tag_summary.ai_tags_active,
            ai_tag_summary.ai_tags_excluded,
            COALESCE(tag_summary.has_manual_metadata, 0) AS has_manual_metadata,
            COALESCE(face_summary.face_count, 0) AS face_count,
            face_summary.max_detection_score AS face_max_detection_score,
            face_summary.face_analyzed_at,
            CASE WHEN face_summary.face_count IS NULL THEN 0 ELSE 1 END
                AS face_analysis_available,
            CASE WHEN chosen_body_results.id IS NULL THEN 0 ELSE 1 END
                AS body_analysis_available,
            COALESCE(chosen_body_results.pose_count, 0) AS body_pose_count,
            COALESCE(chosen_body_results.body_detected, 0) AS body_detected,
            COALESCE(chosen_body_results.face_visible, 0) AS body_face_visible,
            chosen_body_results.full_body_score,
            COALESCE(chosen_body_results.full_body, 0) AS full_body,
            COALESCE(chosen_body_results.classification, '')
                AS body_classification,
            chosen_body_results.detection_threshold
                AS body_detection_threshold,
            chosen_body_results.visibility_threshold
                AS body_visibility_threshold,
            chosen_body_results.full_body_threshold_percent
                AS body_full_body_threshold_percent,
            chosen_body_results.analyzed_at AS body_analyzed_at,
            face_summary.largest_face_area,
            face_summary.second_largest_face_area,
            best_identity.identity_match_id,
            best_identity.suggested_identity,
            best_identity.identity_similarity,
            best_identity.identity_review_status,
            quality.sharpness_score,
            quality.perceptual_hash,
            quality.status AS quality_status,
            quality.error AS quality_error,
            quality.analyzed_at AS quality_analyzed_at,
            image_set_summary.image_set_names
            , video_origin.source_video
            , video_origin.sampling_mode AS video_sampling_mode
            , video_origin.timestamp_seconds AS video_timestamp_seconds
            , video_origin.frame_number AS video_frame_number
            , video_origin.interval_seconds AS video_interval_seconds
        FROM images
        LEFT JOIN preferred_files
            ON preferred_files.image_id = images.id
        LEFT JOIN file_counts
            ON file_counts.image_id = images.id
        LEFT JOIN chosen_analysis
            ON chosen_analysis.image_id = images.id
        LEFT JOIN image_review_state
            ON image_review_state.image_id = images.id
        LEFT JOIN tag_summary
            ON tag_summary.image_id = images.id
        LEFT JOIN ai_tag_summary
            ON ai_tag_summary.image_id = images.id
        LEFT JOIN face_summary
            ON face_summary.image_id = images.id
        LEFT JOIN chosen_body_results
            ON chosen_body_results.image_id = images.id
        LEFT JOIN best_identity
            ON best_identity.image_id = images.id
        LEFT JOIN image_quality_results AS quality
            ON quality.image_id = images.id
        LEFT JOIN image_set_summary
            ON image_set_summary.image_id = images.id
        LEFT JOIN file_video_origins AS video_origin
            ON video_origin.file_id = preferred_files.file_id
        """


    def fetch_common_tags(self, image_ids: Iterable[int]) -> list[SelectionTagRecord]:
        """
        Return only effective tag states shared by every supplied image.

        The method intentionally omits partial or mixed states. A chip shown for
        a multi-selection is therefore always a true statement about every
        selected image, matching the user's Explorer-style mental model.
        """
        ids = sorted({int(image_id) for image_id in image_ids})
        if not ids:
            return []

        self._validate_catalog_identity()
        with Catalog(self.database_path):
            pass

        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        placeholders = ",".join("?" for _ in ids)
        by_image: dict[int, dict[str, SelectionTagRecord]] = {
            image_id: {} for image_id in ids
        }

        try:
            manual_rows = connection.execute(
                f"""
                SELECT it.image_id, t.name, t.normalized_name
                FROM image_tags AS it
                JOIN tags AS t ON t.id = it.tag_id
                WHERE it.image_id IN ({placeholders})
                  AND t.category = 'manual_tag'
                  AND LOWER(it.source) IN ('manual', 'user', 'user_manual')
                  AND it.review_status <> 'rejected'
                ORDER BY t.normalized_name
                """,
                ids,
            ).fetchall()
            for row in manual_rows:
                image_id = int(row["image_id"])
                normalized = str(row["normalized_name"])
                by_image[image_id][normalized] = SelectionTagRecord(
                    name=str(row["name"]),
                    normalized_name=normalized,
                    kind="manual",
                )

            ai_rows = connection.execute(
                f"""
                WITH chosen_tag_analysis AS (
                    SELECT ar.id, ar.image_id
                    FROM analysis_results AS ar
                    WHERE ar.status = 'success'
                      AND ar.include_triage = 1
                      AND ar.id = (
                          SELECT ar2.id
                          FROM analysis_results AS ar2
                          WHERE ar2.image_id = ar.image_id
                            AND ar2.status = 'success'
                            AND ar2.include_triage = 1
                          ORDER BY ar2.analyzed_at DESC, ar2.id DESC
                          LIMIT 1
                      )
                )
                SELECT
                    ats.image_id,
                    t.name,
                    t.normalized_name,
                    CASE WHEN exclusions.tag_id IS NULL THEN 0 ELSE 1 END AS excluded
                FROM analysis_tag_suggestions AS ats
                JOIN chosen_tag_analysis AS ca
                    ON ca.id = ats.analysis_result_id
                JOIN tags AS t
                    ON t.id = ats.tag_id
                LEFT JOIN image_tag_exclusions AS exclusions
                    ON exclusions.image_id = ats.image_id
                   AND exclusions.tag_id = ats.tag_id
                WHERE ats.image_id IN ({placeholders})
                ORDER BY t.normalized_name
                """,
                ids,
            ).fetchall()
            for row in ai_rows:
                image_id = int(row["image_id"])
                normalized = str(row["normalized_name"])
                # A manual assertion wins visually and during export. The AI
                # suggestion still remains stored and becomes visible again if
                # the user later removes the manual tag.
                if normalized in by_image[image_id]:
                    continue
                by_image[image_id][normalized] = SelectionTagRecord(
                    name=str(row["name"]),
                    normalized_name=normalized,
                    kind=("ai_excluded" if bool(row["excluded"]) else "ai_active"),
                )
        finally:
            connection.close()

        common_names = set(by_image[ids[0]])
        for image_id in ids[1:]:
            common_names.intersection_update(by_image[image_id])

        common: list[SelectionTagRecord] = []
        for normalized in common_names:
            candidates = [by_image[image_id][normalized] for image_id in ids]
            first = candidates[0]
            if all(candidate.kind == first.kind for candidate in candidates[1:]):
                common.append(first)

        kind_order = {"manual": 0, "ai_active": 1, "ai_excluded": 2}
        return sorted(
            common,
            key=lambda tag: (kind_order.get(tag.kind, 9), tag.name.casefold()),
        )

    def list_saved_searches(self) -> list[SavedSearchRecord]:
        """Return explicitly saved catalog queries in name order."""
        self._validate_catalog_identity()
        with Catalog(self.database_path):
            pass
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            rows = connection.execute(
                "SELECT id, name, query_text FROM saved_searches "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            return [
                SavedSearchRecord(int(row[0]), str(row[1]), str(row[2]))
                for row in rows
            ]
        finally:
            connection.close()

    def save_named_search(self, name: str, query: str) -> SavedSearchRecord:
        """Create or replace one named search after an explicit user action."""
        clean_name = " ".join(name.split()).strip()
        clean_query = query.strip()
        if not clean_name:
            raise ValueError("Enter a name for the saved search.")
        if len(clean_name) > 120:
            raise ValueError("Saved-search names must be 120 characters or fewer.")
        if not clean_query:
            raise ValueError("Enter a search before saving it.")
        self._validate_catalog_identity()
        with Catalog(self.database_path):
            pass
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO saved_searches(name, query_text, created_at, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    query_text = excluded.query_text,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (clean_name, clean_query),
            )
            row = connection.execute(
                "SELECT id, name, query_text FROM saved_searches WHERE name = ? COLLATE NOCASE",
                (clean_name,),
            ).fetchone()
            connection.commit()
            assert row is not None
            return SavedSearchRecord(int(row[0]), str(row[1]), str(row[2]))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_saved_search(self, search_id: int) -> bool:
        """Delete one named search without affecting catalog images or metadata."""
        self._validate_catalog_identity()
        with Catalog(self.database_path):
            pass
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            cursor = connection.execute(
                "DELETE FROM saved_searches WHERE id = ?", (int(search_id),)
            )
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()


class AddTagsDialog(tk.Toplevel):
    """Small modal editor for one or many comma/newline-separated tags."""

    def __init__(self, parent: tk.Misc, selected_count: int) -> None:
        super().__init__(parent)
        self.result: list[str] | None = None
        self.title("Add Tags")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text=f"Add manual tags to {selected_count:,} selected image"
                 f"{'s' if selected_count != 1 else ''}:",
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=(
                "Separate tags with commas, semicolons, or new lines. "
                "Existing tags are skipped, so duplicates are never created."
            ),
            wraplength=430,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))

        self.entry = tk.Text(body, width=55, height=7, wrap="word")
        self.entry.grid(row=2, column=0, sticky="ew")
        self.entry.bind("<Control-Return>", self._submit)

        ttk.Label(
            body,
            text="Example: red_dress, smiling, studio_lighting",
            foreground=FALLBACK_MUTED_TEXT,
            font=get_ui_font(self, size=8),
        ).grid(row=3, column=0, sticky="w", pady=(4, 8))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=0)
        ttk.Button(buttons, text="Add Tags", command=self._submit).grid(
            row=0, column=1, padx=(7, 0)
        )

        self.bind("<Escape>", lambda _event: self._cancel())
        self.grab_set()
        self.entry.focus_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 3)
        self.geometry(f"+{x}+{y}")

    def _submit(self, _event: tk.Event | None = None) -> str:
        raw = self.entry.get("1.0", "end-1c")
        try:
            parsed = parse_manual_tag_input(raw)
        except ValueError as error:
            messagebox.showinfo("Invalid tags", str(error), parent=self)
            return "break"
        self.result = parsed
        self.destroy()
        return "break"

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class ThumbnailCache:
    """Create and reuse disposable WebP previews in per-user application data.

    Preview files must never live beside a catalog or source-image folder.
    Keeping them under LoRA Image Curator's local application directory prevents a
    recursive source scan from treating generated previews as training images.
    Content hashes make the cache safely reusable across multiple catalogs.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.cache_directory = get_settings_directory() / THUMBNAIL_CACHE_FOLDER

    def cache_path(self, record: CatalogImageRecord, size: int) -> Path:
        """Return a deterministic cache filename for one image and size."""
        safe_hash = record.content_sha256[:24]
        return self.cache_directory / f"{safe_hash}_{size}.webp"

    def get_or_create(self, record: CatalogImageRecord, size: int) -> Path | None:
        """
        Return a cached preview, creating it from the source image if needed.

        Missing/corrupt files return ``None``.  They are represented by a clear
        placeholder in the GUI rather than turning a catalog refresh into an
        error.
        """
        cache_path = self.cache_path(record, size)
        if cache_path.exists():
            return cache_path

        source_path = record.source_path
        if source_path is None or not source_path.exists() or not source_path.is_file():
            return None

        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(".webp.tmp")

        try:
            with Image.open(source_path) as source_image:
                image = ImageOps.exif_transpose(source_image)
                image = image.convert("RGB")
                image.thumbnail((size, size), Image.Resampling.LANCZOS)

                # A fixed square makes every card align without cropping the
                # original.  The neutral background is intentionally quiet so
                # it does not compete with the dataset image.
                preview = Image.new("RGB", (size, size), (235, 235, 235))
                x = (size - image.width) // 2
                y = (size - image.height) // 2
                preview.paste(image, (x, y))
                # These files are disposable UI previews. A low WebP effort
                # avoids spending substantial CPU time for a marginal cache
                # size reduction that the user never sees.
                preview.save(temporary_path, format="WEBP", quality=82, method=1)

            temporary_path.replace(cache_path)
            return cache_path

        except (OSError, UnidentifiedImageError, ValueError):
            logging.exception("Could not create thumbnail for %s", source_path)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None


class DecodedThumbnailCache:
    """Bounded GUI-thread cache for decoded Tk thumbnail objects.

    The on-disk WebP cache avoids regenerating previews, but reopening and
    decoding those files still creates a visible reload when a page is rebuilt.
    This small least-recently-used (LRU) layer retains enough ``PhotoImage``
    objects for the current and recently visited pages.  File metadata is part
    of the key so a replaced cache file cannot display stale pixels.
    """

    def __init__(self, max_items: int = DECODED_THUMBNAIL_CACHE_ITEMS) -> None:
        if max_items < 1:
            raise ValueError("Decoded thumbnail cache size must be positive.")
        self.max_items = int(max_items)
        self._items: OrderedDict[
            tuple[str, int, int],
            ImageTk.PhotoImage | tk.PhotoImage,
        ] = OrderedDict()

    @staticmethod
    def _key(image_path: Path) -> tuple[str, int, int] | None:
        try:
            status = image_path.stat()
            resolved = image_path.resolve()
        except OSError:
            return None
        return (str(resolved), int(status.st_mtime_ns), int(status.st_size))

    def get_if_cached(
        self,
        image_path: Path,
    ) -> ImageTk.PhotoImage | tk.PhotoImage | None:
        """Return a retained Tk image without decoding the file again."""
        key = self._key(image_path)
        if key is None:
            return None
        photo = self._items.pop(key, None)
        if photo is None:
            return None
        self._items[key] = photo
        return photo

    def get_or_load(
        self,
        image_path: Path,
    ) -> ImageTk.PhotoImage | tk.PhotoImage | None:
        """Reuse or decode one preview on the Tk main thread."""
        cached = self.get_if_cached(image_path)
        if cached is not None:
            return cached

        key = self._key(image_path)
        if key is None:
            return None
        try:
            with Image.open(image_path) as image:
                photo = ImageTk.PhotoImage(image.convert("RGB"))
        except (OSError, UnidentifiedImageError, tk.TclError):
            logging.exception("Could not decode cached thumbnail %s", image_path)
            return None

        # A changed file at the same path gets a new metadata key. Remove its
        # older retained version before inserting the replacement.
        path_key = key[0]
        for old_key in tuple(self._items):
            if old_key[0] == path_key:
                self._items.pop(old_key, None)
        self._items[key] = photo
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return photo

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        """Release every Tk image while the owning GUI thread is still alive."""
        self._items.clear()


class ExpandImageIcon(tk.Canvas):
    """Draw one compact, familiar maximize control over a thumbnail.

    Canvas lines avoid depending on a particular Unicode symbol font on
    Windows. The four outward corners read as "expand" while leaving almost
    the complete thumbnail unobstructed.
    """

    SIZE = 25

    def __init__(
        self,
        parent: tk.Widget,
        *,
        command: Callable[[], None],
    ) -> None:
        self._command = command
        self._hovered = False
        super().__init__(
            parent,
            width=self.SIZE,
            height=self.SIZE,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#E7E7E7",
            background="#252525",
            cursor="hand2",
            takefocus=True,
        )
        Tooltip(self, "Enlarge / review image")
        self.bind("<Button-1>", self._activate)
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<Configure>", self._draw, add="+")
        self._draw()

    def _activate(self, _event: tk.Event | None = None) -> str:
        self._command()
        return "break"

    def _enter(self, _event: tk.Event) -> None:
        self._hovered = True
        self._draw()

    def _leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self._draw()

    def _draw(self, _event: tk.Event | None = None) -> None:
        foreground = "#FFFFFF"
        self.configure(background="#3D6F9E" if self._hovered else "#252525")
        self.delete("expand")
        inset = 6
        length = 5
        far = self.SIZE - inset - 1
        segments = (
            (inset, inset + length, inset, inset, inset + length, inset),
            (far - length, inset, far, inset, far, inset + length),
            (inset, far - length, inset, far, inset + length, far),
            (far - length, far, far, far, far, far - length),
        )
        for points in segments:
            self.create_line(
                *points,
                fill=foreground,
                width=2,
                capstyle="round",
                joinstyle="round",
                tags=("expand",),
            )


class ThumbnailCard:
    """Small visual card that delegates selection policy to the browser frame."""

    def __init__(
        self,
        parent: tk.Widget,
        record: CatalogImageRecord,
        thumbnail_size: int,
        on_click: Callable[[CatalogImageRecord, tk.Event], None],
        on_double_click: Callable[[CatalogImageRecord], None],
        colors: dict[str, str],
    ) -> None:
        self.record = record
        self.thumbnail_size = thumbnail_size
        self.on_click = on_click
        self.on_double_click = on_double_click
        self.colors = colors
        self.photo_image: ImageTk.PhotoImage | tk.PhotoImage | None = None

        self.outer = tk.Frame(
            parent,
            width=CARD_OUTER_WIDTH,
            highlightthickness=3,
            highlightbackground=colors["card_border"],
            highlightcolor=colors["card_border"],
            background=colors["card_background"],
            cursor="hand2",
            takefocus=True,
        )
        # ThumbnailCard is a plain Python controller, not a Tk widget.  Fonts
        # must therefore be resolved through its actual outer frame so
        # get_ui_font() can reach the correct Tk interpreter and root-owned
        # cache.  Passing ``self`` here caused catalog loading to stop on the
        # first card with ``AttributeError: ... has no attribute '_root'``.
        font_owner = self.outer
        self.outer.grid_propagate(False)
        self.outer.configure(height=thumbnail_size + 92)
        self.outer.columnconfigure(0, weight=1)

        image_stack = tk.Frame(
            self.outer,
            width=thumbnail_size,
            height=thumbnail_size,
            background=colors["card_background"],
        )
        image_stack.grid(row=0, column=0, padx=8, pady=(8, 4))
        image_stack.grid_propagate(False)

        self.image_label = tk.Label(
            image_stack,
            borderwidth=0,
            background="#EBEBEB",
            text="Loading…",
            foreground=colors["muted_text"],
            compound="center",
        )
        self.image_label.place(
            relx=0.5,
            rely=0.5,
            anchor="center",
            width=thumbnail_size,
            height=thumbnail_size,
        )

        self.manual_badge = tk.Label(
            image_stack,
            text="MANUAL",
            font=get_ui_font(font_owner, size=7, weight="bold"),
            foreground="#FFFFFF",
            background=MANUAL_ACCENT,
            padx=4,
            pady=2,
        )
        if record.has_manual_metadata:
            self.manual_badge.place(relx=0.0, rely=1.0, x=5, y=-5, anchor="sw")

        self.missing_badge = tk.Label(
            image_stack,
            text="FILE MISSING",
            font=get_ui_font(font_owner, size=7, weight="bold"),
            foreground="#FFFFFF",
            background=MISSING_ACCENT,
            padx=4,
            pady=2,
        )
        if record.file_status != "present":
            self.missing_badge.place(relx=0.0, rely=0.0, x=5, y=5, anchor="nw")

        self.expand_icon = ExpandImageIcon(
            image_stack,
            command=lambda: self.on_double_click(self.record),
        )
        self.expand_icon.place(
            relx=1.0,
            rely=1.0,
            x=-5,
            y=-5,
            anchor="se",
        )

        self.filename_label = tk.Label(
            self.outer,
            text=record.filename,
            font=get_ui_font(font_owner, size=9, weight="bold"),
            anchor="w",
            justify="left",
            background=colors["card_background"],
            foreground=colors["card_text"],
        )
        self.filename_label.grid(row=1, column=0, sticky="ew", padx=9)

        subtitle = self._subtitle_for(record)
        self.subtitle_label = tk.Label(
            self.outer,
            text=subtitle,
            font=get_ui_font(font_owner, size=8),
            anchor="nw",
            justify="left",
            wraplength=CARD_OUTER_WIDTH - 20,
            background=colors["card_background"],
            foreground=colors["muted_text"],
        )
        self.subtitle_label.grid(row=2, column=0, sticky="nsew", padx=9, pady=(2, 7))

        self._bind_clicks(self.outer)

    @staticmethod
    def _subtitle_for(record: CatalogImageRecord) -> str:
        """Prefer identity, then caption, while keeping the card compact."""
        if (
            record.suggested_identity
            and record.identity_review_status != "rejected"
        ):
            if record.identity_similarity is not None:
                return f"{record.suggested_identity} · {record.identity_similarity:.2f}"
            return record.suggested_identity

        caption = " ".join(record.caption.split())
        if len(caption) > 88:
            caption = caption[:85].rstrip() + "…"
        return caption or "No caption"

    def _bind_clicks(self, widget: tk.Widget) -> None:
        """Make every visible part of the card behave as one hit target."""
        if widget is self.expand_icon:
            # The overlay is the one card child with its own single-click
            # command. Letting the general card binder replace it would turn
            # the maximize icon into an ordinary selection target.
            return
        widget.bind("<Button-1>", lambda event: self.on_click(self.record, event))
        widget.bind("<Double-Button-1>", lambda _event: self.on_double_click(self.record))
        widget.bind("<Return>", lambda _event: self.on_double_click(self.record))
        widget.bind("<space>", lambda event: self.on_click(self.record, event))

        for child in widget.winfo_children():
            self._bind_clicks(child)

    def set_thumbnail(self, image_path: Path | None) -> None:
        """Load a cached preview on the GUI thread."""
        if image_path is None or not image_path.exists():
            self.image_label.configure(
                image="",
                text="Preview\nunavailable",
                font=get_ui_font(self.outer, size=9),
            )
            self.photo_image = None
            return

        try:
            with Image.open(image_path) as image:
                prepared = image.convert("RGB")
                self.photo_image = ImageTk.PhotoImage(prepared)
            self.image_label.configure(image=self.photo_image, text="")
        except (OSError, UnidentifiedImageError, tk.TclError):
            logging.exception("Could not load cached thumbnail %s", image_path)
            self.image_label.configure(image="", text="Preview\nunavailable")
            self.photo_image = None

    def set_decoded_thumbnail(
        self,
        image_path: Path | None,
        photo_image: ImageTk.PhotoImage | tk.PhotoImage | None,
    ) -> None:
        """Display a browser-owned decoded preview without reopening its file."""
        if image_path is None or photo_image is None:
            self.image_label.configure(
                image="",
                text="Preview\nunavailable",
                font=get_ui_font(self.outer, size=9),
            )
            self.photo_image = None
            return
        self.photo_image = photo_image
        self.image_label.configure(image=self.photo_image, text="")

    def set_selected(self, selected: bool) -> None:
        """Highlight the entire card without adding a separate selection badge."""
        if selected:
            background = self.colors["selection_color"]
            foreground = self.colors["selection_text"]
            self.outer.configure(
                highlightbackground=self.colors["selection_color"],
                highlightcolor=self.colors["selection_color"],
                background=background,
            )
            self.filename_label.configure(background=background, foreground=foreground)
            self.subtitle_label.configure(background=background, foreground=foreground)
        else:
            background = self.colors["card_background"]
            self.outer.configure(
                highlightbackground=self.colors["card_border"],
                highlightcolor=self.colors["card_border"],
                background=background,
            )
            self.filename_label.configure(
                background=background,
                foreground=self.colors["card_text"],
            )
            self.subtitle_label.configure(
                background=background,
                foreground=self.colors["muted_text"],
            )



class CatalogBrowserFrame(ttk.Frame):
    """Explorer-style thumbnail browser embedded in the main application."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        initial_catalog_path: Path | None = None,
    ) -> None:
        super().__init__(parent, padding=10)

        self.catalog_path: Path | None = None
        self.thumbnail_cache: ThumbnailCache | None = None
        self.repository: CatalogBrowserRepository | None = None
        self.all_records: list[CatalogImageRecord] = []
        self.visible_records: list[CatalogImageRecord] = []
        self.records_by_id: dict[int, CatalogImageRecord] = {}
        self.cards_by_id: dict[int, ThumbnailCard] = {}
        self.duplicate_review_threshold: float | None = None
        self.duplicate_review_clusters: tuple[tuple[int, ...], ...] = ()
        self.duplicate_group_frames: list[tuple[tk.Frame, tk.Frame, tuple[int, ...]]] = []
        self.selected_image_ids: set[int] = set()
        self.anchor_image_id: int | None = None
        self.focused_image_id: int | None = None

        self.thumbnail_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="dataset-thumbnail",
        )
        self.thumbnail_results: queue.Queue[tuple[str, int, int, Path | None]] = queue.Queue()
        self.pending_thumbnail_keys: set[tuple[str, int, int]] = set()
        self.decoded_thumbnail_cache = DecodedThumbnailCache()
        self._search_after_id: str | None = None
        self._layout_after_id: str | None = None
        self._load_more_after_id: str | None = None
        self._closing = False
        self._last_page_shortcut_at = 0.0
        # Windows sends Alt through its native menu path before ordinary
        # toplevel bindings receive the following arrow.  The browser therefore
        # owns the modifier from key-down through key-up while this tab is
        # visible, using a first-priority bind tag installed by
        # ``_bind_shortcuts``.  Arrow state distinguishes a deliberate series
        # of presses from OS-generated repeat events without imposing a timing
        # penalty on fast navigation.
        self._capture_alt_modifier_for_page_navigation = sys.platform == "win32"
        self._alt_modifier_held = False
        self._alt_page_navigation_active = False
        self._alt_navigation_keys_down: set[str] = set()
        self._last_column_count = 0
        self.rendered_record_count = 0
        self.card_page_index = 0
        self.page_loaded_count = 0
        self._details_preview_photo: ImageTk.PhotoImage | None = None
        self.edit_service: CatalogEditService | None = None
        self.export_repository: DatasetExportRepository | None = None
        self.image_set_repository: ImageSetRepository | None = None
        self.file_action_service: FileActionService | None = None
        self.on_image_sets_changed: Callable[[], None] | None = None
        self.on_filter_settings_changed: (
            Callable[[str, float, int], None] | None
        ) = None
        self.on_command_state_changed: Callable[[], None] | None = None
        self._session_backup_path: Path | None = None
        self._history_undo_stack: list[BrowserHistoryEntry] = []
        self._history_redo_stack: list[BrowserHistoryEntry] = []

        # Marquee selection state.  Root coordinates make intersection tests
        # independent of canvas scrolling, while canvas coordinates draw the
        # visible rectangle in the correct scrolled location.
        self._drag_start_root: tuple[int, int] | None = None
        self._drag_start_canvas: tuple[float, float] | None = None
        self._drag_initial_selection: set[int] = set()
        self._drag_border_windows: list[tk.Toplevel] = []
        self._drag_active = False
        self._drag_additive = False

        # The literal words "Multiple values" are a display sentinel only.
        # This flag prevents them from ever becoming a saved Trigger Keyword.
        self._keyword_shows_multiple = False
        self._suppress_review_event = False
        self._displayed_selection_tags: list[SelectionTagRecord] = []

        self.settings = load_settings()
        self.images_per_page = max(
            25,
            min(100, int(self.settings.browser_images_per_page)),
        )
        # The current query is session state. It is never restored as general
        # browser state; only completed history entries are optionally saved.
        self.search_var = tk.StringVar()
        self.sort_var = tk.StringVar(value=self.settings.browser_sort or "Filename (A–Z)")
        self.filter_var = tk.StringVar(value=self.settings.browser_filter or "All images")
        self.browser_filter_state = BrowserFilterState(
            catalog_state=self.filter_var.get(),
            profile_key=self.settings.readiness_profile_key,
            blur_threshold=self.settings.quality_blur_threshold,
            duplicate_similarity_percent=(
                self.settings.quality_duplicate_similarity_percent
            ),
        ).normalized()
        self.filter_var.set(self.browser_filter_state.catalog_state)
        self._filter_image_set_ids: frozenset[int] = frozenset()
        self._browser_filter_cache_key: tuple[
            int,
            BrowserFilterState,
            frozenset[int],
        ] | None = None
        self._browser_filter_cache_records: tuple[object, ...] = ()
        self.catalog_path_var = tk.StringVar(value="No catalog selected")
        self.results_var = tk.StringVar(value="0 images")
        self.selection_var = tk.StringVar(value="0 selected")
        self.review_decision_var = tk.StringVar(value="Unreviewed")
        self.review_notes_var = tk.StringVar()
        self.manual_keyword_var = tk.StringVar()
        self.identity_summary_var = tk.StringVar(value="No identity suggestion")
        self.identity_status_var = tk.StringVar(value="")
        self.edit_status_var = tk.StringVar(value="")
        self.page_status_var = tk.StringVar(value="")
        self._thumbnail_results_after_id: str | None = None

        # Selection-pruning choices remain session-only, but they now live in a
        # focused modal workflow rather than consuming permanent browser width.
        self.curation_options = CurationOptions()

        self.colors = self._resolve_colors()
        self._build_interface()
        self._bind_shortcuts()

        self._thumbnail_results_after_id = self.after(
            100,
            self._process_thumbnail_results,
        )

        if initial_catalog_path is not None:
            self.set_catalog_path(initial_catalog_path, load=True, quiet=True)

    # =========================================================================
    # Interface construction
    # =========================================================================

    def _build_interface(self) -> None:
        """Construct the compact toolbar, thumbnail grid, and details pane."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        path_row = ttk.Frame(self)
        path_row.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        path_row.columnconfigure(0, weight=1)

        self.path_label = ttk.Label(
            path_row,
            textvariable=self.catalog_path_var,
            anchor="w",
        )
        self.path_label.grid(row=0, column=0, sticky="ew")

        toolbar = ttk.Frame(self)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)

        search_label = ttk.Frame(toolbar)
        search_label.grid(row=0, column=0, padx=(0, 6))
        ttk.Label(search_label, text="Search:").pack(side="left")
        self.search_help = HelpIcon(
            search_label,
            "Search applied tags and Trigger Keywords. Use Advanced Search for filename and other metadata fields.",
        )
        self.search_help.pack(side="left", padx=(4, 0))
        self.search_entry = ttk.Combobox(
            toolbar,
            textvariable=self.search_var,
            values=tuple(self.settings.browser_search_history),
        )
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self._schedule_search)
        self.search_entry.bind("<Return>", self._commit_search)
        self.search_entry.bind("<<ComboboxSelected>>", self._commit_search)

        self.clear_search_button = ttk.Button(
            toolbar,
            text="Clear",
            command=self._clear_search,
        )
        self.clear_search_button.grid(row=0, column=2, padx=(6, 6))

        self.advanced_search_button = ttk.Button(
            toolbar,
            text="Advanced…",
            command=self._open_advanced_search,
        )
        self.advanced_search_button.grid(row=0, column=3, padx=(0, 6))

        sort_controls = ttk.Frame(toolbar)
        sort_controls.grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(7, 0),
        )
        sort_label = ttk.Frame(sort_controls)
        sort_label.pack(side="left", padx=(0, 5))
        ttk.Label(sort_label, text="Sort:").pack(side="left")
        self.sort_help = HelpIcon(
            sort_label,
            "Change the order of the current browser results without changing selection.",
        )
        self.sort_help.pack(side="left", padx=(4, 0))
        self.sort_combo = ttk.Combobox(
            sort_controls,
            textvariable=self.sort_var,
            state="readonly",
            width=20,
            values=(
                "Filename (A–Z)",
                "Filename (Z–A)",
                "Newest added",
                "Oldest added",
                "Identity confidence",
                "Most faces",
                "Full-body evidence",
                "Largest dimensions",
                "Largest file",
            ),
        )
        self.sort_combo.pack(side="left")
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_view_option_changed)

        self.filter_button = ttk.Button(
            sort_controls,
            text="Filters",
            command=self._open_browser_filters,
        )
        self.filter_button.pack(side="left", padx=(8, 0))
        Tooltip(
            self.filter_button,
            (
                "Combine image-set, face, body/pose, catalog-state, quality, and "
                "readiness filters across every result page."
            ),
        )
        self._update_filter_button_state()
        # A fixed two-column layout is intentionally used here.  Earlier builds
        # exposed a draggable sash, but Tk produced several visual artifacts while
        # the pane was resized.  The thumbnail area remains flexible while the
        # compact details inspector keeps a stable width.
        browser_area = ttk.Frame(self)
        browser_area.grid(row=2, column=0, sticky="nsew")
        browser_area.columnconfigure(0, weight=1)
        browser_area.rowconfigure(0, weight=1)

        grid_shell = ttk.Frame(browser_area)
        grid_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        grid_shell.columnconfigure(0, weight=1)
        grid_shell.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            grid_shell,
            borderwidth=0,
            highlightthickness=0,
            background=self.colors["browser_background"],
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        vertical_scrollbar = ttk.Scrollbar(
            grid_shell,
            orient="vertical",
            command=self._on_canvas_scroll,
        )
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vertical_scrollbar.set)

        self.card_container = tk.Frame(
            self.canvas,
            background=self.colors["browser_background"],
        )
        self.card_window_id = self.canvas.create_window(
            (0, 0),
            anchor="nw",
            window=self.card_container,
        )

        self.card_container.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        register_mousewheel_region(
            self.canvas,
            post_scroll=self._schedule_load_more_if_needed,
        )
        self._bind_drag_surface(self.canvas)
        self._bind_drag_surface(self.card_container)

        self.empty_label: tk.Label | None = tk.Label(
            self.card_container,
            text=(
                "Choose or create a SQLite catalog on the LoRA Image Curator tab.\n\n"
                "Select an image to review its identity and Trigger Keyword."
            ),
            font=get_ui_font(self, size=11),
            justify="center",
            background=self.colors["browser_background"],
            foreground=self.colors["muted_text"],
        )
        self.empty_label.grid(row=0, column=0, padx=30, pady=80)
        self._bind_drag_surface(self.empty_label)

        self.details_frame = ttk.LabelFrame(
            browser_area,
            text="Image Details",
            padding=9,
            width=330,
        )
        self.details_frame.grid(row=0, column=1, sticky="ns")
        self.details_frame.grid_propagate(False)
        self._build_details_pane()

        footer = ttk.Frame(self)
        footer.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        footer.columnconfigure(8, weight=1)

        ttk.Label(footer, textvariable=self.results_var).grid(row=0, column=0, sticky="w")
        self.first_page_button = ttk.Button(
            footer,
            text="First",
            command=lambda: self._show_card_page(0),
            state="disabled",
            width=6,
        )
        self.first_page_button.grid(row=0, column=1, padx=(8, 3), sticky="w")
        self.back_ten_pages_button = ttk.Button(
            footer,
            text="−10",
            command=lambda: self._jump_card_pages(-10),
            state="disabled",
            width=4,
        )
        self.back_ten_pages_button.grid(row=0, column=2, padx=(0, 3), sticky="w")
        self.previous_page_button = ttk.Button(
            footer,
            text="Prev",
            command=self._show_previous_card_page,
            state="disabled",
            width=6,
        )
        self.previous_page_button.grid(row=0, column=3, padx=(0, 3), sticky="w")
        self.load_more_button = ttk.Button(
            footer,
            text="Next",
            command=self._append_next_card_batch,
            state="disabled",
            width=6,
        )
        self.load_more_button.grid(row=0, column=4, padx=(0, 3), sticky="w")
        self.forward_ten_pages_button = ttk.Button(
            footer,
            text="+10",
            command=lambda: self._jump_card_pages(10),
            state="disabled",
            width=4,
        )
        self.forward_ten_pages_button.grid(row=0, column=5, padx=(0, 3), sticky="w")
        self.last_page_button = ttk.Button(
            footer,
            text="Last",
            command=self._show_last_card_page,
            state="disabled",
            width=6,
        )
        self.last_page_button.grid(row=0, column=6, padx=(0, 8), sticky="w")
        ttk.Label(
            footer,
            textvariable=self.page_status_var,
            foreground=self.colors["muted_text"],
        ).grid(row=0, column=7, padx=(0, 10), sticky="w")
        ttk.Label(
            footer,
            text=(
                "Click: select one   Ctrl-click: toggle   Shift-click: range   "
                "Drag: box select   Ctrl+A: page   Ctrl+Z/Y: undo/redo"
            ),
            foreground=self.colors["muted_text"],
        ).grid(
            row=1,
            column=0,
            columnspan=8,
            pady=(5, 0),
            sticky="w",
        )

        ttk.Label(
            footer,
            textvariable=self.edit_status_var,
            foreground=MANUAL_ACCENT,
        ).grid(
            row=1,
            column=8,
            padx=(8, 0),
            pady=(5, 0),
            sticky="e",
        )

        ttk.Label(
            footer,
            textvariable=self.selection_var,
            font=get_ui_font(self, size=9, weight="bold"),
        ).grid(row=0, column=8, sticky="e")

    def _build_curation_panel(self, parent: ttk.Frame) -> None:
        """Build the persistent, non-destructive curation controls.

        The panel is hidden until requested so ordinary browsing retains its
        familiar width.  A modal dialog is reserved for the final preview and
        confirmation; choosing what constitutes "unnecessary" stays visible
        beside the thumbnails.
        """
        self.curation_frame = ttk.LabelFrame(
            parent,
            text="Remove Unnecessary Images",
            padding=9,
            width=300,
        )
        self.curation_frame.grid(
            row=0,
            column=0,
            sticky="ns",
            padx=(0, 8),
        )
        self.curation_frame.grid_propagate(False)
        self.curation_frame.columnconfigure(0, weight=1)

        ttk.Label(
            self.curation_frame,
            text=(
                "Choose which evidence should deselect images from the current "
                "selection. Nothing here deletes files or changes the catalog."
            ),
            wraplength=275,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        checks = (
            ("Already marked Reject", self.cull_already_rejected_var),
            ("Missing or unreadable source", self.cull_missing_unreadable_var),
            ("Below readiness resolution", self.cull_low_resolution_var),
            ("Blur score below threshold", self.cull_blur_var),
            ("Screenshot / webpage / UI", self.cull_screenshot_var),
            ("No person and no face detected", self.cull_no_person_var),
            ("Main face too small", self.cull_small_subject_var),
            ("Multiple similarly prominent faces", self.cull_prominent_faces_var),
            ("Any multiple people or faces", self.cull_any_multiple_var),
            ("Near-duplicate of stronger image", self.cull_near_duplicates_var),
        )
        for row, (label, variable) in enumerate(checks, start=1):
            checkbutton = ttk.Checkbutton(
                self.curation_frame,
                text=label,
                variable=variable,
            )
            checkbutton.grid(row=row, column=0, sticky="w", pady=1)

        thresholds = ttk.Frame(
            self.curation_frame,
            padding=7,
        )
        thresholds.grid(row=11, column=0, sticky="ew", pady=(9, 7))
        thresholds.columnconfigure(0, weight=1)

        threshold_heading = ttk.Frame(thresholds)
        threshold_heading.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(
            threshold_heading,
            text="Subject thresholds",
            font=get_ui_font(self, size=9, weight="bold"),
        ).pack(side="left")
        self.subject_thresholds_help = HelpIcon(
            threshold_heading,
            (
                "Enter ordinary percentages without the % symbol. For example, "
                "0.25 means the largest face occupies 0.25% of the complete image. "
                "Second-face prominence compares the second-largest detected face "
                "with the largest; 45 means 45% as large."
            ),
        )
        self.subject_thresholds_help.pack(side="left", padx=(4, 0))

        ttk.Label(thresholds, text="Small face below:").grid(
            row=1,
            column=0,
            sticky="w",
        )
        ttk.Spinbox(
            thresholds,
            from_=0.05,
            to=10.0,
            increment=0.05,
            width=9,
            textvariable=self.cull_small_face_percent_var,
        ).grid(row=1, column=1, sticky="e", padx=(6, 0))
        ttk.Label(thresholds, text="% of image").grid(
            row=1, column=2, padx=(4, 0), sticky="w"
        )

        ttk.Label(thresholds, text="Second face at least:").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Spinbox(
            thresholds,
            from_=10,
            to=100,
            increment=5,
            width=9,
            textvariable=self.cull_prominence_percent_var,
        ).grid(row=2, column=1, sticky="e", padx=(6, 0), pady=(6, 0))
        ttk.Label(thresholds, text="% of largest").grid(
            row=2, column=2, padx=(4, 0), pady=(6, 0), sticky="w"
        )

        ttk.Label(
            self.curation_frame,
            text=(
                "Prominence uses detected face boxes. Background people are "
                "kept when their faces are much smaller than the main face."
            ),
            wraplength=275,
            justify="left",
            foreground=self.colors["muted_text"],
        ).grid(row=12, column=0, sticky="w", pady=(0, 8))

        self.curation_apply_button = ttk.Button(
            self.curation_frame,
            text="Preview Deselection",
            command=self._remove_unnecessary_images,
            state="disabled",
        )
        self.curation_apply_button.grid(row=13, column=0, sticky="ew")

        self.curation_frame.grid_remove()

    def _toggle_curation_panel(self) -> None:
        """Compatibility route for callers from releases that opened the old pane."""
        self._remove_unnecessary_images()


    def _build_details_pane(self) -> None:
        """Build a compact, scrollable inspector that yields space to images."""
        self.details_frame.columnconfigure(0, weight=1)
        self.details_frame.rowconfigure(0, weight=1)

        details_canvas = tk.Canvas(
            self.details_frame,
            borderwidth=0,
            highlightthickness=0,
        )
        details_canvas.grid(row=0, column=0, sticky="nsew")

        details_scrollbar = ttk.Scrollbar(
            self.details_frame,
            orient="vertical",
            command=details_canvas.yview,
        )
        details_scrollbar.grid(row=0, column=1, sticky="ns")
        details_canvas.configure(yscrollcommand=details_scrollbar.set)

        self.details_content = ttk.Frame(details_canvas)
        details_window = details_canvas.create_window(
            (0, 0),
            anchor="nw",
            window=self.details_content,
        )

        self.details_content.bind(
            "<Configure>",
            lambda _event: details_canvas.configure(
                scrollregion=details_canvas.bbox("all")
            ),
        )
        details_canvas.bind(
            "<Configure>",
            lambda event: details_canvas.itemconfigure(
                details_window,
                width=max(1, event.width),
            ),
        )
        register_mousewheel_region(details_canvas)

        self.detail_preview_label = ttk.Label(
            self.details_content,
            text="Select an image",
            anchor="center",
            justify="center",
            wraplength=292,
        )
        self.detail_preview_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.detail_filename_var = tk.StringVar(value="No image selected")
        ttk.Label(
            self.details_content,
            textvariable=self.detail_filename_var,
            font=get_ui_font(self, size=11, weight="bold"),
            wraplength=300,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.review_frame = ttk.LabelFrame(
            self.details_content,
            text="Manual Review",
            padding=8,
        )
        self.review_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.review_frame.columnconfigure(1, weight=1)

        decision_label = ttk.Frame(self.review_frame)
        decision_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Label(decision_label, text="Decision:").pack(side="left")
        self.review_decision_help = HelpIcon(
            decision_label,
            "Apply one manual keep, follow-up, reject, or unreviewed decision to every selected image.",
        )
        self.review_decision_help.pack(side="left", padx=(4, 0))
        self.review_decision_combo = ttk.Combobox(
            self.review_frame,
            textvariable=self.review_decision_var,
            state="disabled",
            width=15,
            values=(
                "Unreviewed",
                "Keep",
                "Needs follow-up",
                "Reject",
            ),
        )
        self.review_decision_combo.grid(
            row=0, column=1, columnspan=2, sticky="ew"
        )
        self.review_decision_combo.bind(
            "<<ComboboxSelected>>", self._on_review_decision_selected
        )

        ttk.Separator(self.review_frame, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=7
        )

        self.identity_summary_label = ttk.Label(
            self.review_frame,
            textvariable=self.identity_summary_var,
            wraplength=278,
            justify="left",
            foreground=self.colors["selection_color"],
        )
        self.identity_summary_label.grid(
            row=2, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            self.review_frame,
            textvariable=self.identity_status_var,
            foreground=MANUAL_ACCENT,
            font=get_ui_font(self, size=8, weight="bold"),
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(1, 4))

        identity_buttons = ttk.Frame(self.review_frame)
        identity_buttons.grid(row=4, column=0, columnspan=3, sticky="ew")
        identity_buttons.columnconfigure((0, 1, 2), weight=1)

        self.confirm_identity_button = ttk.Button(
            identity_buttons,
            text="Confirm",
            command=lambda: self._review_identity("confirmed"),
            state="disabled",
        )
        self.confirm_identity_button.grid(row=0, column=0, sticky="ew")
        self.reject_identity_button = ttk.Button(
            identity_buttons,
            text="Reject",
            command=lambda: self._review_identity("rejected"),
            state="disabled",
        )
        self.reject_identity_button.grid(row=0, column=1, sticky="ew", padx=5)
        self.reset_identity_button = ttk.Button(
            identity_buttons,
            text="Reset",
            command=lambda: self._review_identity("suggested"),
            state="disabled",
        )
        self.reset_identity_button.grid(row=0, column=2, sticky="ew")
        self.identity_review_help = HelpIcon(
            identity_buttons,
            "Confirm or reject the strongest stored identity suggestion, or reset review to Suggested. Face analysis itself is retained.",
        )
        self.identity_review_help.grid(row=0, column=3, padx=(5, 0))

        keyword_label_row = ttk.Frame(self.review_frame)
        keyword_label_row.grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        self.manual_keyword_label = ttk.Label(
            keyword_label_row,
            text="Trigger Keyword:",
        )
        self.manual_keyword_label.pack(side="left")
        self.manual_keyword_help = HelpIcon(
            keyword_label_row,
            "Enter the intended LoRA activation text for every selected image. Face detection does not infer this name.",
        )
        self.manual_keyword_help.pack(side="left", padx=(4, 0))
        self.manual_keyword_entry = ttk.Entry(
            self.review_frame,
            textvariable=self.manual_keyword_var,
            state="disabled",
        )
        self.manual_keyword_entry.grid(
            row=6, column=0, columnspan=3, sticky="ew"
        )
        self.manual_keyword_entry.bind("<FocusIn>", self._on_keyword_focus_in)
        self.manual_keyword_entry.bind("<Return>", self._save_manual_keyword)

        keyword_buttons = ttk.Frame(self.review_frame)
        keyword_buttons.grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(5, 0)
        )
        keyword_buttons.columnconfigure((0, 1), weight=1)
        self.save_keyword_button = ttk.Button(
            keyword_buttons,
            text="Save Trigger Keyword",
            command=self._save_manual_keyword,
            state="disabled",
        )
        self.save_keyword_button.grid(row=0, column=0, sticky="ew")
        self.clear_keyword_button = ttk.Button(
            keyword_buttons,
            text="Clear",
            command=self._clear_manual_keyword,
            state="disabled",
        )
        self.clear_keyword_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        ttk.Label(
            self.review_frame,
            text="Edits apply to every selected image. Ctrl+Z undoes them.",
            foreground=self.colors["muted_text"],
            font=get_ui_font(self, size=8),
            wraplength=278,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(7, 0))

        self.tag_frame = ttk.LabelFrame(
            self.details_content,
            text="Training Tags",
            padding=8,
        )
        self.tag_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.tag_frame.columnconfigure(0, weight=1)

        self.tag_text = tk.Text(
            self.tag_frame,
            wrap="word",
            width=34,
            height=7,
            borderwidth=0,
            highlightthickness=0,
            padx=3,
            pady=3,
            cursor="arrow",
        )
        self.tag_text.grid(row=0, column=0, sticky="ew")
        self.tag_text.tag_configure(
            "tag_message",
            foreground=self.colors["muted_text"],
            font=get_ui_font(self, size=8),
        )
        self.tag_text.configure(state="disabled")
        # These are read-only display surfaces inside the outer details canvas,
        # not independently scrollable editors. Route their wheel input to the
        # inspector so scrolling does not appear to stick over tags/details.
        register_mousewheel_region(self.tag_text, details_canvas)

        self.add_tags_button = ttk.Button(
            self.tag_frame,
            text="Add Tags…",
            command=self._add_manual_tags,
            state="disabled",
        )
        self.add_tags_button.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(
            self.tag_frame,
            text=(
                "Blue: active AI   Gray: excluded AI   Orange: manual\n"
                "Click a chip to toggle AI use or remove a manual tag. "
                "For batches, only tags common to every image are shown."
            ),
            foreground=self.colors["muted_text"],
            font=get_ui_font(self, size=8),
            wraplength=278,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.detail_text = tk.Text(
            self.details_content,
            wrap="word",
            width=34,
            height=24,
            borderwidth=0,
            highlightthickness=0,
            padx=2,
            pady=2,
            cursor="arrow",
        )
        self.detail_text.grid(row=4, column=0, sticky="nsew")
        self.detail_text.tag_configure("heading", font=get_ui_font(self, size=9, weight="bold"), spacing1=7)
        self.detail_text.tag_configure("value", font=get_ui_font(self, size=9), lmargin1=0, lmargin2=0)
        self.detail_text.tag_configure("muted", foreground=self.colors["muted_text"])
        self.detail_text.tag_configure("manual", font=get_ui_font(self, size=9, weight="bold"))
        self.detail_text.configure(state="disabled")
        register_mousewheel_region(self.detail_text, details_canvas)

        detail_actions = ttk.Frame(self.details_content)
        detail_actions.grid(row=5, column=0, sticky="ew", pady=(9, 0))
        detail_actions.columnconfigure((0, 1), weight=1)
        self.open_image_button = ttk.Button(
            detail_actions,
            text="Enlarge / Review",
            command=self._open_focused_image,
            state="disabled",
        )
        self.open_image_button.grid(row=0, column=0, sticky="ew")
        self.image_quality_button = ttk.Button(
            detail_actions,
            text="Image Quality…",
            command=self._show_focused_image_quality,
            state="disabled",
        )
        self.image_quality_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(6, 0),
        )
        file_actions = ttk.Frame(self.details_content)
        file_actions.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        file_actions.columnconfigure((0, 1), weight=1)
        self.quarantine_button = ttk.Button(
            file_actions,
            text="Quarantine Selected",
            command=self.quarantine_selected,
            state="disabled",
        )
        self.quarantine_button.grid(row=0, column=0, sticky="ew")
        self.restore_quarantine_button = ttk.Button(
            file_actions,
            text="Restore Selected",
            command=self.restore_selected_from_quarantine,
            state="disabled",
        )
        self.restore_quarantine_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(6, 0),
        )
        self._style_details_text_widgets()

    def _style_details_text_widgets(self) -> None:
        """Keep browser-owned classic Text widgets readable after theme changes."""
        for text_widget in (
            getattr(self, "tag_text", None),
            getattr(self, "detail_text", None),
        ):
            if text_widget is None:
                continue
            text_widget.configure(
                background=self.colors["field_background"],
                foreground=self.colors["card_text"],
                insertbackground=self.colors["card_text"],
                selectbackground=self.colors["selection_color"],
                selectforeground=self.colors["selection_text"],
            )

        if hasattr(self, "tag_text"):
            self.tag_text.tag_configure(
                "tag_message",
                foreground=self.colors["muted_text"],
                font=get_ui_font(self, size=8),
            )
        if hasattr(self, "detail_text"):
            self.detail_text.tag_configure(
                "muted",
                foreground=self.colors["muted_text"],
            )
            self.detail_text.tag_configure(
                "manual",
                foreground=self.colors["manual"],
                font=get_ui_font(self, size=9, weight="bold"),
            )

    def _resolve_colors(self) -> dict[str, str]:
        """Resolve theme colors with safe native selection fallbacks."""
        style = ttk.Style(self)
        theme = get_theme(getattr(self.settings, "appearance_theme", ""))

        def color_or(value: str, fallback: str) -> str:
            return value if value and value not in ("{}", "") else fallback

        selection_color = color_or(
            style.lookup("Treeview", "background", ("selected",)),
            theme.accent or FALLBACK_SELECTION_COLOR,
        )
        selection_text = color_or(
            style.lookup("Treeview", "foreground", ("selected",)),
            theme.accent_text or FALLBACK_SELECTION_TEXT,
        )

        return {
            "selection_color": selection_color,
            "selection_text": selection_text,
            "card_border": theme.card_border or FALLBACK_CARD_BORDER,
            "card_background": theme.card_background or FALLBACK_CARD_BACKGROUND,
            "card_text": theme.text or FALLBACK_CARD_TEXT,
            "muted_text": theme.muted_text or FALLBACK_MUTED_TEXT,
            "browser_background": theme.browser_background,
            "panel_background": theme.panel_background,
            "field_background": theme.field_background,
            "accent": theme.accent,
            "warning": theme.warning,
            "manual": theme.manual,
            "handle_background": theme.handle_background,
            "handle_active_background": theme.handle_active_background,
            "handle_foreground": theme.handle_foreground,
            "ai_tag_foreground": theme.ai_tag_foreground,
            "ai_tag_background": theme.ai_tag_background,
            "manual_tag_foreground": theme.manual_tag_foreground,
            "manual_tag_background": theme.manual_tag_background,
            "excluded_tag_foreground": theme.excluded_tag_foreground,
            "excluded_tag_background": theme.excluded_tag_background,
            "duplicate_background": theme.duplicate_background,
            "duplicate_border": theme.duplicate_border,
            "duplicate_heading": theme.duplicate_heading,
        }

    def apply_theme(self, theme_key: str) -> None:
        """Apply a new appearance theme to browser-owned classic Tk widgets."""
        self.settings.appearance_theme = normalize_theme_key(theme_key)
        self.colors = self._resolve_colors()
        self.configure(style="TFrame")
        self.canvas.configure(background=self.colors["browser_background"])
        self.card_container.configure(background=self.colors["browser_background"])
        self._style_empty_label()
        self._update_filter_button_state()
        for card in self.cards_by_id.values():
            card.colors = self.colors
            card.set_selected(card.record.image_id in self.selected_image_ids)
        self._style_details_text_widgets()
        self._show_selection_details()

    def _style_empty_label(self) -> None:
        """Repaint the current empty-state label when one is still alive.

        Browser page rebuilds destroy every child of ``card_container``.  A
        populated result page does not need an empty-state replacement, so the
        reference must be treated as optional instead of assuming the label
        survives catalog loading.  Clearing a stale reference here is also a
        defensive guard for theme changes queued near a page rebuild.
        """
        label = self.empty_label
        if label is None:
            return
        try:
            if not label.winfo_exists():
                self.empty_label = None
                return
            label.configure(
                background=self.colors["browser_background"],
                foreground=self.colors["muted_text"],
            )
        except tk.TclError:
            self.empty_label = None

    # =========================================================================
    # Catalog loading and filtering
    # =========================================================================

    def set_catalog_path(
        self,
        database_path: Path,
        *,
        load: bool = True,
        quiet: bool = False,
    ) -> None:
        """Point the browser at a catalog and optionally refresh immediately."""
        resolved_path = database_path.expanduser().resolve()
        if self.catalog_path is not None and self.catalog_path != resolved_path:
            self.selected_image_ids.clear()
            self._history_undo_stack.clear()
            self._history_redo_stack.clear()
            self.browser_filter_state = replace(
                self.browser_filter_state,
                image_set_id=None,
                image_set_name="",
            )
            self._filter_image_set_ids = frozenset()
            self._update_filter_button_state()
            self._notify_command_state_changed()
        self.catalog_path = resolved_path
        self.catalog_path_var.set(str(self.catalog_path))
        self.thumbnail_cache = ThumbnailCache(self.catalog_path)
        self.repository = CatalogBrowserRepository(self.catalog_path)
        self.edit_service = CatalogEditService(self.catalog_path)
        self.export_repository = DatasetExportRepository(self.catalog_path)
        self.image_set_repository = ImageSetRepository(self.catalog_path)
        self.file_action_service = FileActionService(self.catalog_path)
        self._session_backup_path = None
        self.edit_status_var.set("")
        self.settings.browser_last_catalog = str(self.catalog_path)
        self._save_browser_settings()
        self._notify_command_state_changed()

        if load:
            self.refresh(quiet=quiet)

    def clear_catalog_path(self) -> None:
        """Detach the browser after catalog deletion without touching files.

        Catalog lifecycle actions live on the LoRA Image Curator tab.  This method
        only resets the browser's in-memory view after the application has
        already completed and confirmed such an action.
        """
        self._clear_records()
        self.catalog_path = None
        self.thumbnail_cache = None
        self.repository = None
        self.edit_service = None
        self.export_repository = None
        self.image_set_repository = None
        self.file_action_service = None
        self.browser_filter_state = replace(
            self.browser_filter_state,
            image_set_id=None,
            image_set_name="",
        )
        self._filter_image_set_ids = frozenset()
        self._update_filter_button_state()
        self.catalog_path_var.set("No catalog selected")
        self.results_var.set("0 images")
        self.settings.browser_last_catalog = ""
        self._save_browser_settings()
        self._notify_command_state_changed()

    def refresh(self, *, quiet: bool = False) -> None:
        """Reload catalog metadata and rebuild the visible cards."""
        refresh_started = time.perf_counter()
        if self.catalog_path is None:
            if not quiet:
                messagebox.showinfo(
                    "Choose a catalog",
                    "Choose a dataset_tools.db catalog first.",
                    parent=self,
                )
            return

        if not self.catalog_path.exists():
            self._clear_records()
            self.results_var.set("Catalog not found")
            if not quiet:
                messagebox.showerror(
                    "Catalog not found",
                    f"The selected catalog does not exist:\n\n{self.catalog_path}",
                    parent=self,
                )
            return

        self.results_var.set("Loading catalog…")
        self.update_idletasks()

        try:
            if self.repository is None:
                self.repository = CatalogBrowserRepository(self.catalog_path)
            records = self.repository.fetch_records()
        except Exception as error:
            logging.exception("Could not load catalog browser records")
            self._clear_records()
            self.results_var.set("Catalog could not be loaded")
            if not quiet:
                messagebox.showerror(
                    "Could not load catalog",
                    f"{type(error).__name__}: {error}",
                    parent=self,
                )
            return
        fetch_seconds = time.perf_counter() - refresh_started
        self.all_records = records
        self.records_by_id = {record.image_id: record for record in records}

        # Keep selections only when the corresponding catalog image still
        # exists.  Refreshing metadata should not unnecessarily discard work.
        self.selected_image_ids.intersection_update(self.records_by_id)
        if self.focused_image_id not in self.records_by_id:
            self.focused_image_id = None
        if self.anchor_image_id not in self.records_by_id:
            self.anchor_image_id = None

        self._reload_filter_image_set_scope()
        self._apply_search()
        logging.info(
            "Browser refresh: %d records; query/projection %.3fs; "
            "filter/sort/page build %.3fs; total %.3fs",
            len(records),
            fetch_seconds,
            (time.perf_counter() - refresh_started) - fetch_seconds,
            time.perf_counter() - refresh_started,
        )

    def _clear_records(self) -> None:
        """Reset browser state after a failed or missing catalog load."""
        self.all_records = []
        self.visible_records = []
        self._browser_filter_cache_key = None
        self._browser_filter_cache_records = ()
        self.duplicate_review_threshold = None
        self.duplicate_review_clusters = ()
        self.records_by_id = {}
        self.cards_by_id = {}
        self.selected_image_ids.clear()
        self._history_undo_stack.clear()
        self._history_redo_stack.clear()
        self.anchor_image_id = None
        self.focused_image_id = None
        self._destroy_cards()
        self._clear_details()
        self._notify_command_state_changed()
        self._update_selection_status()

    def _schedule_search(self, _event: tk.Event | None = None) -> None:
        """Debounce typing so a large grid is not rebuilt for every keystroke."""
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(SEARCH_DEBOUNCE_MS, self._apply_search)

    def _clear_search(self) -> None:
        self.search_var.set("")
        self._apply_search()
        self.search_entry.focus_set()

    def _commit_search(self, _event: tk.Event | None = None) -> str:
        """Apply and optionally remember a completed search."""
        self._apply_search()
        query = self.search_var.get().strip()
        if query and self.settings.browser_search_history_enabled:
            self._remember_search(query)
        return "break" if _event is not None and _event.keysym == "Return" else ""

    def apply_external_query(self, query: str, *, remember: bool = False) -> None:
        """Apply a dashboard or saved-view query through the normal search box."""
        self.search_var.set(query)
        self._apply_search()
        if remember and query.strip() and self.settings.browser_search_history_enabled:
            self._remember_search(query.strip())

    def _remember_search(self, query: str) -> None:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            return
        history = [
            item
            for item in self.settings.browser_search_history
            if item.casefold() != normalized.casefold()
        ]
        history.insert(0, normalized)
        self.settings.browser_search_history = history[
            : self.settings.browser_search_history_max
        ]
        self.search_entry.configure(values=tuple(self.settings.browser_search_history))
        self._save_browser_settings()

    def _open_advanced_search(self) -> None:
        dialog = AdvancedSearchDialog(self)
        self.wait_window(dialog)
        if dialog.result_query:
            self.apply_external_query(dialog.result_query, remember=True)

    def _open_search_history_settings(self) -> None:
        dialog = SearchHistoryDialog(
            self,
            enabled=self.settings.browser_search_history_enabled,
            maximum=self.settings.browser_search_history_max,
            history_count=len(self.settings.browser_search_history),
        )
        self.wait_window(dialog)
        if dialog.result is None:
            return
        enabled, maximum, clear_requested = dialog.result
        self.settings.browser_search_history_enabled = enabled
        self.settings.browser_search_history_max = maximum
        if clear_requested:
            self.settings.browser_search_history = []
        else:
            self.settings.browser_search_history = self.settings.browser_search_history[:maximum]
        self.search_entry.configure(values=tuple(self.settings.browser_search_history))
        self._save_browser_settings()

    def _save_named_search(self) -> None:
        if self.repository is None or self.catalog_path is None:
            messagebox.showinfo(
                "Choose a catalog",
                "Choose a catalog before saving a named search.",
                parent=self,
            )
            return
        query = self.search_var.get().strip()
        if not query:
            messagebox.showinfo(
                "No search to save",
                "Enter or build a search first.",
                parent=self,
            )
            return
        name = ask_saved_search_name(self)
        if name is None:
            return
        try:
            saved = self.repository.save_named_search(name, query)
        except (OSError, sqlite3.Error, ValueError) as error:
            messagebox.showerror("Could not save search", str(error), parent=self)
            return
        self.edit_status_var.set(f'Saved search "{saved.name}"')

    def _open_saved_searches(self) -> None:
        if self.repository is None or self.catalog_path is None:
            messagebox.showinfo(
                "Choose a catalog",
                "Choose a catalog to view its saved searches.",
                parent=self,
            )
            return
        try:
            searches = self.repository.list_saved_searches()
        except (OSError, sqlite3.Error, ValueError) as error:
            messagebox.showerror("Could not load saved searches", str(error), parent=self)
            return
        dialog = SavedSearchesDialog(
            self,
            records=searches,
            on_delete=self.repository.delete_saved_search,
        )
        self.wait_window(dialog)
        if dialog.result_query:
            # Applying an explicitly saved view does not also need to create a
            # duplicate automatic-history entry.
            self.apply_external_query(dialog.result_query, remember=False)

    def _apply_search(self) -> None:
        """Apply unified filters, search, and sorting, then reflow all cards."""
        self._search_after_id = None
        query = self.search_var.get().strip()
        if self.filter_var.get() != self.browser_filter_state.catalog_state:
            self.browser_filter_state = replace(
                self.browser_filter_state,
                catalog_state=self.filter_var.get(),
            ).normalized()
            self._update_filter_button_state()
        filter_cache_key = (
            id(self.all_records),
            self.browser_filter_state,
            self._filter_image_set_ids,
        )
        if filter_cache_key != self._browser_filter_cache_key:
            filter_result = apply_browser_filter_state(
                self.all_records,
                self.browser_filter_state,
                image_set_ids=self._filter_image_set_ids,
            )
            self._browser_filter_cache_key = filter_cache_key
            self._browser_filter_cache_records = filter_result.records
        filtered_records = self._browser_filter_cache_records
        requested_duplicate_threshold = duplicate_review_threshold(query)
        search_records = filtered_records
        if requested_duplicate_threshold is not None:
            # Explicit duplicate searches still expose the historical
            # nearest-match fields used by the Boolean query evaluator. This
            # exact enrichment is intentionally on demand; ordinary Browser
            # loading and ordinary searches never pay its catalog-wide cost.
            nearest = duplicate_candidates_at_threshold(
                filtered_records,
                requested_duplicate_threshold,
            )
            search_records = tuple(
                replace(
                    record,
                    nearest_duplicate_image_id=(
                        nearest[record.image_id][0]
                        if record.image_id in nearest
                        else None
                    ),
                    nearest_duplicate_similarity=(
                        nearest[record.image_id][1]
                        if record.image_id in nearest
                        else None
                    ),
                )
                for record in filtered_records
            )
        try:
            records = [
                record
                for record in search_records
                if not query or self._record_matches_search(record, query)
            ]
        except SearchSyntaxError as error:
            self.visible_records = []
            self.duplicate_review_threshold = None
            self.duplicate_review_clusters = ()
            self._rebuild_cards()
            self.results_var.set(f"Search syntax: {error}")
            self._update_selection_status()
            return
        records = sorted(records, key=self._sort_key, reverse=self._sort_reverse())

        # Grouping is a temporary presentation mode owned only by a positive
        # duplicate-similarity search. Ordinary browsing, filters, image sets,
        # and exact-copy searches retain the familiar flat grid.
        self.duplicate_review_threshold = requested_duplicate_threshold
        self.duplicate_review_clusters = ()
        if self.duplicate_review_threshold is not None:
            clusters = duplicate_candidate_clusters(
                records,
                self.duplicate_review_threshold,
            )
            position = {record.image_id: index for index, record in enumerate(records)}
            ordered_clusters = [
                tuple(sorted(cluster, key=position.__getitem__))
                for cluster in clusters
            ]
            ordered_clusters.sort(key=lambda cluster: position[cluster[0]])
            self.duplicate_review_clusters = tuple(ordered_clusters)
            grouped_ids = {
                image_id
                for cluster in self.duplicate_review_clusters
                for image_id in cluster
            }
            # A perceptual review group is useful only when at least two of its
            # candidates survive the current search/filter/set constraints.
            records = [record for record in records if record.image_id in grouped_ids]

        self.visible_records = records

        # Rebuilding is intentional. Hiding old widgets leaves stale grid slots
        # and produces the visible holes reported during real-world testing.
        self._rebuild_cards()
        if self.duplicate_review_threshold is not None:
            group_count = len(self.duplicate_review_clusters)
            self.results_var.set(
                f"{len(self.visible_records):,} images in {group_count:,} possible "
                f"duplicate group{'s' if group_count != 1 else ''}"
            )
        else:
            self.results_var.set(
                f"{len(self.visible_records):,} of {len(self.all_records):,} images"
            )
        self._update_selection_status()

    def _on_view_option_changed(self, _event: tk.Event | None = None) -> None:
        """Persist compact browser controls and immediately refresh the view."""
        self.settings.browser_sort = self.sort_var.get()
        self.settings.browser_filter = self.filter_var.get()
        self.browser_filter_state = replace(
            self.browser_filter_state,
            catalog_state=self.filter_var.get(),
        ).normalized()
        self._update_filter_button_state()
        self._save_browser_settings()
        self._apply_search()

    def _update_filter_button_state(self) -> None:
        """Make active filtering unmistakable without a verbose toolbar summary."""
        if not hasattr(self, "filter_button"):
            return
        active = self.browser_filter_state.is_active()
        self.filter_button.configure(
            text="Filters On" if active else "Filters",
            style="Active.TButton" if active else "TButton",
        )

    def apply_analysis_settings(
        self,
        *,
        profile_key: str,
        blur_threshold: float,
        duplicate_similarity_percent: int,
    ) -> None:
        """Adopt centrally configured quality interpretation without clearing filters."""
        self.browser_filter_state = replace(
            self.browser_filter_state,
            profile_key=profile_key,
            blur_threshold=blur_threshold,
            duplicate_similarity_percent=duplicate_similarity_percent,
        ).normalized()
        self._browser_filter_cache_key = None
        self._browser_filter_cache_records = ()
        self._update_filter_button_state()
        if self.all_records:
            self._apply_search()

    def _open_browser_filters(self, section: str = "scope") -> None:
        """Open the one filter dialog focused on the requested category."""
        summaries = []
        if self.image_set_repository is not None:
            try:
                summaries = self.image_set_repository.list_sets()
            except (OSError, sqlite3.Error, ValueError) as error:
                messagebox.showerror(
                    "Could not load image sets",
                    str(error),
                    parent=self,
                )
                return
        dialog = BrowserFiltersDialog(
            self,
            initial_state=self.browser_filter_state,
            image_sets=summaries,
            initial_section=section,
        )
        self.wait_window(dialog)
        if dialog.result is None:
            return

        previous_state = self.browser_filter_state
        self.browser_filter_state = dialog.result
        self.filter_var.set(dialog.result.catalog_state)
        self._update_filter_button_state()
        self.settings.browser_filter = dialog.result.catalog_state
        self.settings.readiness_profile_key = dialog.result.profile_key
        self.settings.quality_blur_threshold = dialog.result.blur_threshold
        self.settings.quality_duplicate_similarity_percent = (
            dialog.result.duplicate_similarity_percent
        )
        self._reload_filter_image_set_scope()
        self._save_browser_settings()
        if self.on_filter_settings_changed is not None:
            self.on_filter_settings_changed(
                dialog.result.profile_key,
                dialog.result.blur_threshold,
                dialog.result.duplicate_similarity_percent,
            )
        self._apply_search()
        self._record_filter_change(
            previous_state,
            self.browser_filter_state,
            "Change browser filters",
        )
        self.edit_status_var.set(
            f"Browser filters: {self.browser_filter_state.summary()}."
        )

    def _clear_browser_filters(self, *, apply: bool = True) -> None:
        """Reset all scope/finding constraints while retaining sort and search."""
        previous_state = self.browser_filter_state
        self.browser_filter_state = BrowserFilterState(
            profile_key=self.browser_filter_state.profile_key,
            blur_threshold=self.browser_filter_state.blur_threshold,
            duplicate_similarity_percent=(
                self.browser_filter_state.duplicate_similarity_percent
            ),
        )
        self._filter_image_set_ids = frozenset()
        self.filter_var.set("All images")
        self._update_filter_button_state()
        self.settings.browser_filter = "All images"
        self._save_browser_settings()
        if apply:
            self._apply_search()
        self._record_filter_change(
            previous_state,
            self.browser_filter_state,
            "Clear browser filters",
        )
        self.edit_status_var.set("Browser filters cleared.")

    def _record_filter_change(
        self,
        before: BrowserFilterState,
        after: BrowserFilterState,
        description: str,
    ) -> None:
        """Add one complete session-only filter transition to shared history."""
        normalized_before = before.normalized()
        normalized_after = after.normalized()
        if normalized_before == normalized_after:
            return
        self._history_undo_stack.append(
            BrowserHistoryEntry(
                kind="filter",
                description=description,
                before_filter=normalized_before,
                after_filter=normalized_after,
            )
        )
        if len(self._history_undo_stack) > BROWSER_HISTORY_LIMIT:
            del self._history_undo_stack[0]
        self._history_redo_stack.clear()
        self._notify_command_state_changed()

    def _restore_filter_history_state(
        self,
        state: BrowserFilterState,
    ) -> None:
        """Restore a prior complete filter state without recording another action."""
        self.browser_filter_state = state.normalized()
        self.filter_var.set(self.browser_filter_state.catalog_state)
        self.settings.browser_filter = self.browser_filter_state.catalog_state
        self.settings.readiness_profile_key = self.browser_filter_state.profile_key
        self.settings.quality_blur_threshold = (
            self.browser_filter_state.blur_threshold
        )
        self.settings.quality_duplicate_similarity_percent = (
            self.browser_filter_state.duplicate_similarity_percent
        )
        self._reload_filter_image_set_scope()
        self._update_filter_button_state()
        self._save_browser_settings()
        if self.on_filter_settings_changed is not None:
            self.on_filter_settings_changed(
                self.browser_filter_state.profile_key,
                self.browser_filter_state.blur_threshold,
                self.browser_filter_state.duplicate_similarity_percent,
            )
        self._apply_search()

    def _reload_filter_image_set_scope(self) -> None:
        """Refresh one active set membership or safely clear a vanished set."""
        set_id = self.browser_filter_state.image_set_id
        if set_id is None:
            self._filter_image_set_ids = frozenset()
            return
        if self.image_set_repository is None:
            self._filter_image_set_ids = frozenset()
            return
        try:
            image_ids = self.image_set_repository.get_image_ids(set_id)
            summary = self.image_set_repository.get_set(set_id)
        except (OSError, sqlite3.Error, ValueError):
            self.browser_filter_state = replace(
                self.browser_filter_state,
                image_set_id=None,
                image_set_name="",
            )
            self._filter_image_set_ids = frozenset()
            self._update_filter_button_state()
            self.edit_status_var.set(
                "The filtered image set no longer exists; image-set filtering was cleared."
            )
            return
        self._filter_image_set_ids = frozenset(image_ids)
        if summary.name != self.browser_filter_state.image_set_name:
            self.browser_filter_state = replace(
                self.browser_filter_state,
                image_set_name=summary.name,
            )
            self._update_filter_button_state()

    @staticmethod
    def _record_matches_search(record: CatalogImageRecord, query: str) -> bool:
        """
        Match ordinary words plus small, practical tag-search operators.

        Supported examples::

            woman red_dress
            tag:woman AND tag:red_dress NOT tag:hat
            manual:studio
            excluded:outdoors

        ``AND`` is optional because positive terms are combined with AND by
        default. A leading minus sign is also accepted as a NOT shorthand.
        """
        return record_matches_query(record, query)

    def _record_matches_filter(self, record: CatalogImageRecord) -> bool:
        """Compatibility wrapper for the catalog-state portion of filtering."""
        return matches_catalog_state(record, self.filter_var.get())

    def _sort_key(self, record: CatalogImageRecord) -> tuple:
        """Return deterministic sort keys for every supported browser order."""
        choice = self.sort_var.get()
        filename = record.filename.casefold()
        if choice in ("Newest added", "Oldest added"):
            return (record.first_seen_at, filename, record.image_id)
        if choice == "Identity confidence":
            return (record.identity_similarity if record.identity_similarity is not None else -1.0, filename)
        if choice == "Most faces":
            return (record.face_count, filename)
        if choice == "Full-body evidence":
            return (
                record.full_body_score
                if record.full_body_score is not None
                else -1.0,
                filename,
            )
        if choice == "Largest dimensions":
            area = (record.width or 0) * (record.height or 0)
            return (area, filename)
        if choice == "Largest file":
            return (record.byte_size, filename)
        return (filename, record.absolute_path.casefold(), record.image_id)

    def _sort_reverse(self) -> bool:
        return self.sort_var.get() in {
            "Filename (Z–A)",
            "Newest added",
            "Identity confidence",
            "Most faces",
            "Full-body evidence",
            "Largest dimensions",
            "Largest file",
        }

    def _save_browser_settings(self) -> None:
        """Save browser preferences without making catalog browsing fragile."""
        try:
            save_settings(self.settings)
        except OSError:
            logging.exception("Could not save browser settings")

    def set_images_per_page(self, count: int) -> None:
        """Apply and persist a safe browser page size, then rebuild page one."""
        normalized = max(25, min(100, int(count)))
        if normalized == self.images_per_page:
            return
        self.images_per_page = normalized
        self.settings.browser_images_per_page = normalized
        self._save_browser_settings()
        if self.visible_records:
            self._rebuild_cards()
        self.edit_status_var.set(f"Browser page size set to {normalized} images.")
        self._notify_command_state_changed()

    def _notify_command_state_changed(self) -> None:
        """Let the application refresh menu checkmarks and enabled states."""
        if self.on_command_state_changed is not None:
            self.on_command_state_changed()

    def command_state(self) -> dict[str, bool]:
        """Return menu-facing state without exposing widget implementation."""
        has_catalog = self.catalog_path is not None
        has_results = bool(self.visible_records)
        has_selection = bool(self.selected_image_ids)
        has_page = bool(self._current_loaded_page_records())
        durable_undo = False
        if self.edit_service is not None:
            try:
                durable_undo = (
                    self.edit_service.get_last_undoable_operation() is not None
                )
            except (OSError, sqlite3.Error):
                durable_undo = False
        return {
            "has_catalog": has_catalog,
            "has_results": has_results,
            "has_selection": has_selection,
            "has_page": has_page,
            "can_undo": bool(self._history_undo_stack) or durable_undo,
            "can_redo": bool(self._history_redo_stack),
            "filters_active": self.browser_filter_state.is_active(),
        }

    # =========================================================================
    # Card creation, responsive layout, and thumbnails
    # =========================================================================

    def _destroy_cards(self) -> None:
        for child in self.card_container.winfo_children():
            child.destroy()
        self.empty_label = None
        self.cards_by_id.clear()
        self.duplicate_group_frames.clear()
        self.rendered_record_count = 0
        self.card_page_index = 0
        self.page_loaded_count = 0
        if hasattr(self, "load_more_button"):
            self.load_more_button.configure(state="disabled", text="Next")
        if hasattr(self, "previous_page_button"):
            self.previous_page_button.configure(state="disabled")
        for attribute in (
            "first_page_button",
            "back_ten_pages_button",
            "forward_ten_pages_button",
            "last_page_button",
        ):
            button = getattr(self, attribute, None)
            if button is not None:
                button.configure(state="disabled")
        if hasattr(self, "page_status_var"):
            self.page_status_var.set("")

    def _clear_current_card_page(self) -> None:
        """Destroy only page widgets while preserving page/navigation state."""
        for child in self.card_container.winfo_children():
            child.destroy()
        self.empty_label = None
        self.cards_by_id.clear()
        self.duplicate_group_frames.clear()
        self.page_loaded_count = 0

    def _rebuild_cards(self) -> None:
        """Create cards for the current result set and queue thumbnails."""
        previous_columns = max(1, self._last_column_count)
        self._destroy_cards()
        self.canvas.yview_moveto(0.0)

        # Grid column weights survive child destruction. Reset them before an
        # empty-state label is inserted; otherwise the label can inherit one
        # narrow thumbnail column and appear clipped when a filter finds none.
        for column in range(previous_columns):
            self.card_container.columnconfigure(column, weight=0, uniform="")
        self._last_column_count = 0

        if not self.visible_records:
            if self.duplicate_review_threshold is not None and self.all_records:
                empty_text = (
                    "No complete possible-duplicate groups meet the current "
                    "similarity, search, and filter settings."
                )
            elif self.all_records:
                empty_text = "No catalog images match the current search and filter."
            else:
                empty_text = "This catalog does not contain any images yet."
            self.empty_label = tk.Label(
                self.card_container,
                text=empty_text,
                font=get_ui_font(self, size=11),
                justify="center",
                background=self.colors["browser_background"],
                foreground=self.colors["muted_text"],
            )
            self.card_container.columnconfigure(0, weight=1)
            self.empty_label.grid(
                row=0,
                column=0,
                padx=30,
                pady=80,
                sticky="ew",
            )
            self._bind_drag_surface(self.empty_label)
            self._show_selection_details()
            return

        if self.duplicate_review_clusters:
            self._build_duplicate_review_cards()
            self.rendered_record_count = len(self.visible_records)
            self._layout_cards(force=True)
        else:
            self._append_next_card_batch()
        self._show_selection_details()

    def _append_next_card_batch(self) -> None:
        """Load another batch without allowing one canvas page to grow unbounded."""
        if self.duplicate_review_clusters:
            return

        page_start = self.card_page_index * self.images_per_page
        page_end = min(len(self.visible_records), page_start + self.images_per_page)
        if page_start >= len(self.visible_records):
            return

        # Once the current page is complete, the same button advances to a fresh
        # bounded canvas page.  It never silently replaces images merely because
        # the user reached the scrollbar's end.
        if page_start + self.page_loaded_count >= page_end:
            if page_end >= len(self.visible_records):
                return
            self.card_page_index += 1
            page_start = self.card_page_index * self.images_per_page
            page_end = min(
                len(self.visible_records),
                page_start + self.images_per_page,
            )
            self._clear_current_card_page()
            self.canvas.yview_moveto(0.0)

        start = page_start + self.page_loaded_count
        end = page_end
        for record in self.visible_records[start:end]:
            self._create_thumbnail_card(self.card_container, record)
        self.page_loaded_count = end - page_start
        # Retained for the v0.20 compatibility smoke test and diagnostics: this
        # is the exclusive index reached in the complete result set.
        self.rendered_record_count = end

        if end < len(self.visible_records):
            self.load_more_button.configure(
                state="normal",
                text="Next",
            )
        else:
            self.load_more_button.configure(state="disabled", text="Next")
        self._update_page_navigation_buttons()
        self._update_page_status()
        self._layout_cards(force=True)

    def _show_previous_card_page(self) -> None:
        """Return to the previous bounded page without losing selection state."""
        self._show_card_page(self.card_page_index - 1)

    def _page_count(self) -> int:
        """Return the number of bounded pages in the current ordinary result set."""
        if not self.visible_records or self.duplicate_review_clusters:
            return 0
        return max(
            1,
            (
                len(self.visible_records)
                + self.images_per_page
                - 1
            )
            // self.images_per_page,
        )

    def _show_card_page(self, page_index: int) -> None:
        """Display one clamped page directly without changing selection state."""
        page_count = self._page_count()
        if page_count <= 0:
            return
        target = max(0, min(int(page_index), page_count - 1))
        if (
            target == self.card_page_index
            and self.page_loaded_count > 0
        ):
            return
        self.card_page_index = target
        self._clear_current_card_page()
        self.canvas.yview_moveto(0.0)
        self._append_next_card_batch()

    def _jump_card_pages(self, offset: int) -> None:
        """Move by a large page increment while clamping at either boundary."""
        self._show_card_page(self.card_page_index + int(offset))

    def _show_last_card_page(self) -> None:
        """Display the final bounded page of the current result set."""
        self._show_card_page(self._page_count() - 1)

    def _update_page_navigation_buttons(self) -> None:
        """Keep every large-catalog navigation control honest at the boundaries."""
        page_count = self._page_count()
        has_previous = page_count > 0 and self.card_page_index > 0
        has_next = page_count > 0 and self.card_page_index < page_count - 1
        if hasattr(self, "previous_page_button"):
            self.previous_page_button.configure(
                state="normal" if has_previous else "disabled"
            )
        if hasattr(self, "first_page_button"):
            self.first_page_button.configure(
                state="normal" if has_previous else "disabled"
            )
        if hasattr(self, "back_ten_pages_button"):
            self.back_ten_pages_button.configure(
                state="normal" if has_previous else "disabled"
            )
        if hasattr(self, "load_more_button"):
            self.load_more_button.configure(
                state="normal" if has_next else "disabled"
            )
        if hasattr(self, "forward_ten_pages_button"):
            self.forward_ten_pages_button.configure(
                state="normal" if has_next else "disabled"
            )
        if hasattr(self, "last_page_button"):
            self.last_page_button.configure(
                state="normal" if has_next else "disabled"
            )

    def _current_loaded_page_records(self) -> list[CatalogImageRecord]:
        """Return only records whose cards exist on the current canvas page."""
        page_start = self.card_page_index * self.images_per_page
        page_end = min(
            len(self.visible_records),
            page_start + self.page_loaded_count,
        )
        return self.visible_records[page_start:page_end]

    def _update_page_status(self) -> None:
        if not self.visible_records or self.duplicate_review_clusters:
            self.page_status_var.set("")
            return
        page_count = self._page_count()
        page_total = min(
            self.images_per_page,
            len(self.visible_records)
            - (self.card_page_index * self.images_per_page),
        )
        self.page_status_var.set(
            f"Page {self.card_page_index + 1} of {page_count} · "
            f"{self.page_loaded_count:,}/{page_total:,} shown"
        )

    def _create_thumbnail_card(
        self,
        parent: tk.Widget,
        record: CatalogImageRecord,
    ) -> None:
        """Create one selectable card without owning its eventual grid slot."""
        card = ThumbnailCard(
            parent,
            record,
            DEFAULT_THUMBNAIL_SIZE,
            self._on_card_click,
            self._open_record_image,
            self.colors,
        )
        card.set_selected(record.image_id in self.selected_image_ids)
        self.cards_by_id[record.image_id] = card
        if self.thumbnail_cache is not None:
            cache_path = self.thumbnail_cache.cache_path(
                record,
                DEFAULT_THUMBNAIL_SIZE,
            )
            retained_photo = self.decoded_thumbnail_cache.get_if_cached(cache_path)
            if retained_photo is not None:
                card.set_decoded_thumbnail(cache_path, retained_photo)
                return
        self._queue_thumbnail(record, DEFAULT_THUMBNAIL_SIZE, "card")

    def _build_duplicate_review_cards(self) -> None:
        """Create one clearly bounded comparison area per similarity cluster."""
        record_by_id = {record.image_id: record for record in self.visible_records}
        banner = tk.Label(
            self.card_container,
            text=(
                "Similarity Review — compare images only within the same outlined "
                "group. Selection and saved review decisions remain under your control."
            ),
            font=get_ui_font(self, size=10, weight="bold"),
            justify="left",
            anchor="w",
            wraplength=900,
            padx=10,
            pady=9,
            background=DUPLICATE_GROUP_BACKGROUND,
            foreground=DUPLICATE_GROUP_HEADING,
        )
        banner.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 7))
        self._bind_drag_surface(banner)

        for group_number, image_ids in enumerate(self.duplicate_review_clusters, start=1):
            group = tk.Frame(
                self.card_container,
                background=self.colors["duplicate_background"],
                highlightbackground=self.colors["duplicate_border"],
                highlightcolor=self.colors["duplicate_border"],
                highlightthickness=2,
                padx=8,
                pady=8,
            )
            group.columnconfigure(0, weight=1)

            header = tk.Label(
                group,
                text=(
                    f"Possible duplicate group {group_number}  ·  "
                    f"{len(image_ids):,} images"
                ),
                font=get_ui_font(self, size=11, weight="bold"),
                anchor="w",
                background=self.colors["duplicate_background"],
                foreground=self.colors["duplicate_heading"],
            )
            header.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 2))
            instruction = tk.Label(
                group,
                text="Compare these images with one another; other groups are separate.",
                font=get_ui_font(self, size=9),
                anchor="w",
                background=self.colors["duplicate_background"],
                foreground=self.colors["muted_text"],
            )
            instruction.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 5))
            card_area = tk.Frame(group, background=self.colors["duplicate_background"])
            card_area.grid(row=2, column=0, sticky="ew")

            for surface in (group, header, instruction, card_area):
                self._bind_drag_surface(surface)
            for image_id in image_ids:
                record = record_by_id.get(image_id)
                if record is not None:
                    self._create_thumbnail_card(card_area, record)

            self.duplicate_group_frames.append((group, card_area, image_ids))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        if self._closing:
            return
        self.canvas.itemconfigure(self.card_window_id, width=max(1, event.width))
        if self._layout_after_id is not None:
            self.after_cancel(self._layout_after_id)
        self._layout_after_id = self.after(LAYOUT_DEBOUNCE_MS, self._layout_cards)

    def _layout_cards(self, *, force: bool = False) -> None:
        """Lay out either the ordinary grid or vertically separated groups."""
        self._layout_after_id = None
        if self._closing or not self.winfo_exists():
            return
        available_width = max(self.canvas.winfo_width(), CARD_OUTER_WIDTH + 20)
        if self.duplicate_group_frames:
            # Account for the group's border and inner padding before deciding
            # how many fixed-width cards genuinely fit on one comparison row.
            available_width = max(CARD_OUTER_WIDTH + 20, available_width - 36)
        column_width = CARD_OUTER_WIDTH + CARD_HORIZONTAL_GAP
        column_count = max(1, available_width // column_width)
        previous_column_count = max(1, self._last_column_count)

        if not force and column_count == self._last_column_count:
            return

        self._last_column_count = column_count
        if self.duplicate_group_frames:
            self.card_container.columnconfigure(0, weight=1, uniform="")
            for group_index, (group, card_area, image_ids) in enumerate(
                self.duplicate_group_frames,
                start=1,
            ):
                group.grid(
                    row=group_index,
                    column=0,
                    sticky="ew",
                    padx=6,
                    pady=(0, 10),
                )
                for column in range(max(previous_column_count, column_count)):
                    card_area.columnconfigure(column, weight=0, uniform="")
                for column in range(column_count):
                    card_area.columnconfigure(
                        column,
                        weight=1,
                        uniform=f"duplicate_group_{group_index}",
                    )
                for index, image_id in enumerate(image_ids):
                    card = self.cards_by_id.get(image_id)
                    if card is None:
                        continue
                    row, column = divmod(index, column_count)
                    card.outer.grid(
                        row=row,
                        column=column,
                        padx=CARD_HORIZONTAL_GAP // 2,
                        pady=CARD_VERTICAL_GAP // 2,
                        sticky="n",
                    )
        else:
            for column in range(max(previous_column_count, column_count)):
                self.card_container.columnconfigure(column, weight=0, uniform="")
            for column in range(column_count):
                self.card_container.columnconfigure(
                    column,
                    weight=1,
                    uniform="browser_cards",
                )

            for index, record in enumerate(self._current_loaded_page_records()):
                card = self.cards_by_id.get(record.image_id)
                if card is None:
                    continue
                row, column = divmod(index, column_count)
                card.outer.grid(
                    row=row,
                    column=column,
                    padx=CARD_HORIZONTAL_GAP // 2,
                    pady=CARD_VERTICAL_GAP // 2,
                    sticky="n",
                )

        self.card_container.update_idletasks()
        self._update_scroll_region()

    def _update_scroll_region(self, _event: tk.Event | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _schedule_load_more_if_needed(self) -> None:
        """Extend the grid when the user deliberately reaches its current end."""
        if self._closing:
            return
        if self._load_more_after_id is not None:
            self.after_cancel(self._load_more_after_id)
        self._load_more_after_id = self.after(40, self._load_more_if_at_end)

    def _on_canvas_scroll(self, *arguments: str) -> None:
        """Apply scrollbar movement and extend the grid at its current end."""
        self.canvas.yview(*arguments)
        self._schedule_load_more_if_needed()

    def _load_more_if_at_end(self) -> None:
        self._load_more_after_id = None
        if self._closing or not self.winfo_exists():
            return
        page_start = self.card_page_index * self.images_per_page
        page_end = min(len(self.visible_records), page_start + self.images_per_page)
        if (
            page_start + self.page_loaded_count < page_end
            and self.canvas.yview()[1] >= 0.98
        ):
            self._append_next_card_batch()

    def _queue_thumbnail(
        self,
        record: CatalogImageRecord,
        size: int,
        purpose: str,
    ) -> None:
        """Request a cache image once; duplicate queue entries are suppressed."""
        if self.thumbnail_cache is None:
            return

        key = (purpose, record.image_id, size)
        if key in self.pending_thumbnail_keys:
            return
        self.pending_thumbnail_keys.add(key)

        cache = self.thumbnail_cache
        results_queue = self.thumbnail_results
        image_id = record.image_id

        def worker() -> None:
            path = cache.get_or_create(record, size)
            results_queue.put((purpose, image_id, size, path))

        self.thumbnail_executor.submit(worker)

    def _process_thumbnail_results(self) -> None:
        """Apply completed thumbnails on the Tk main thread."""
        self._thumbnail_results_after_id = None
        if self._closing or not self.winfo_exists():
            return
        for _index in range(THUMBNAIL_RESULTS_PER_TICK):
            try:
                purpose, image_id, size, path = self.thumbnail_results.get_nowait()
            except queue.Empty:
                break
            self.pending_thumbnail_keys.discard((purpose, image_id, size))

            if purpose == "card":
                card = self.cards_by_id.get(image_id)
                if card is not None:
                    photo = (
                        self.decoded_thumbnail_cache.get_or_load(path)
                        if path is not None
                        else None
                    )
                    card.set_decoded_thumbnail(path, photo)
            elif purpose == "detail" and image_id == self.focused_image_id:
                self._set_detail_preview(path)

        if self.winfo_exists():
            delay = 25 if not self.thumbnail_results.empty() else 100
            self._thumbnail_results_after_id = self.after(
                delay,
                self._process_thumbnail_results,
            )

    # =========================================================================
    # Selection behavior
    # =========================================================================

    @staticmethod
    def _event_has_control(event: tk.Event) -> bool:
        """Return whether the Windows/Linux Control modifier is held."""
        return bool(event.state & 0x0004)

    @staticmethod
    def _event_has_shift(event: tk.Event) -> bool:
        """Return whether the Shift modifier is held."""
        return bool(event.state & 0x0001)

    def _on_card_click(self, record: CatalogImageRecord, event: tk.Event) -> None:
        """Apply Windows-style single, Ctrl, and Shift selection semantics."""
        before = set(self.selected_image_ids)
        preferred_focus: int | None = record.image_id

        if self._event_has_shift(event) and self.anchor_image_id is not None:
            self._select_range(
                self.anchor_image_id,
                record.image_id,
                additive=self._event_has_control(event),
            )
        elif self._event_has_control(event):
            if record.image_id in self.selected_image_ids:
                self.selected_image_ids.remove(record.image_id)
                preferred_focus = None
            else:
                self.selected_image_ids.add(record.image_id)
            self.anchor_image_id = record.image_id
        else:
            self.selected_image_ids = {record.image_id}
            self.anchor_image_id = record.image_id

        self._record_selection_change(before, "Thumbnail selection")
        self._selection_changed(preferred_focus)
        card = self.cards_by_id.get(record.image_id)
        if card is not None:
            card.outer.focus_set()

    def _select_range(
        self,
        anchor_image_id: int,
        target_image_id: int,
        *,
        additive: bool,
    ) -> None:
        """Select or add the inclusive visible range between anchor and target."""
        visible_ids = [record.image_id for record in self.visible_records]
        try:
            anchor_index = visible_ids.index(anchor_image_id)
            target_index = visible_ids.index(target_image_id)
        except ValueError:
            self.selected_image_ids = {target_image_id}
            self.anchor_image_id = target_image_id
            return

        start, end = sorted((anchor_index, target_index))
        range_ids = set(visible_ids[start : end + 1])
        if additive:
            self.selected_image_ids.update(range_ids)
        else:
            self.selected_image_ids = range_ids

    def select_all_visible(self) -> None:
        """Compatibility name: select every image on the current page only."""
        self.select_current_page()

    def select_current_page(self) -> None:
        """Add every image displayed on the current bounded page."""
        before = set(self.selected_image_ids)
        page_records = self._current_loaded_page_records()
        self.selected_image_ids.update(record.image_id for record in page_records)
        if page_records and self.anchor_image_id is None:
            self.anchor_image_id = page_records[0].image_id
        self._record_selection_change(before, "Select current page")
        self._selection_changed()

    def select_all_results(self) -> None:
        """Add every current search/filter result, including other pages."""
        before = set(self.selected_image_ids)
        self.selected_image_ids.update(record.image_id for record in self.visible_records)
        if self.visible_records and self.anchor_image_id is None:
            self.anchor_image_id = self.visible_records[0].image_id
        self._record_selection_change(before, "Select all results")
        self._selection_changed()

    def invert_visible_selection(self) -> None:
        """Compatibility name: invert selection on the current page only."""
        self.invert_current_page_selection()

    def invert_current_page_selection(self) -> None:
        """Toggle current-page images without altering other pages or results."""
        before = set(self.selected_image_ids)
        for record in self._current_loaded_page_records():
            if record.image_id in self.selected_image_ids:
                self.selected_image_ids.remove(record.image_id)
            else:
                self.selected_image_ids.add(record.image_id)
        self._record_selection_change(before, "Invert current page")
        self._selection_changed()

    def invert_all_results_selection(self) -> None:
        """Toggle every search/filter result without altering hidden selections."""
        before = set(self.selected_image_ids)
        for record in self.visible_records:
            if record.image_id in self.selected_image_ids:
                self.selected_image_ids.remove(record.image_id)
            else:
                self.selected_image_ids.add(record.image_id)
        self._record_selection_change(before, "Invert all results")
        self._selection_changed()

    def select_by_keyword(self) -> None:
        """Add keyword matches from the complete current result set."""
        self._change_selection_by_keyword("select")

    def deselect_by_keyword(self) -> None:
        """Remove keyword matches from the complete current result set."""
        self._change_selection_by_keyword("deselect")

    def _change_selection_by_keyword(self, action: str) -> None:
        """Apply a multi-keyword operation across all result pages."""
        if not self.visible_records:
            messagebox.showinfo(
                "No browser results",
                "The current browser view contains no images.",
                parent=self,
            )
            return
        dialog = KeywordSelectionDialog(self, action=action)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        terms, match_all = dialog.result
        matching_ids = {
            record.image_id
            for record in self.visible_records
            if record_matches_keyword_terms(
                record,
                terms,
                match_all=match_all,
            )
        }
        before = set(self.selected_image_ids)
        if action == "deselect":
            self.selected_image_ids.difference_update(matching_ids)
            changed = len(before) - len(self.selected_image_ids)
            history_label = "Deselect by keyword"
            verb = "Deselected"
        else:
            self.selected_image_ids.update(matching_ids)
            changed = len(self.selected_image_ids) - len(before)
            history_label = "Select by keyword"
            verb = "Selected"
        if before != self.selected_image_ids:
            self._record_selection_change(before, history_label)
        self.edit_status_var.set(
            f"{verb} {changed:,} image{'s' if changed != 1 else ''}; "
            f"{len(matching_ids):,} current result"
            f"{'s' if len(matching_ids) != 1 else ''} matched."
        )
        self._selection_changed()

    def deselect_current_page(self) -> None:
        """Deselect only images displayed on the current page."""
        before = set(self.selected_image_ids)
        self.selected_image_ids.difference_update(
            record.image_id for record in self._current_loaded_page_records()
        )
        self._record_selection_change(before, "Deselect current page")
        self._selection_changed()

    def clear_selection(self) -> None:
        """Clear all selected catalog images and the details inspector."""
        before = set(self.selected_image_ids)
        self.selected_image_ids.clear()
        self.anchor_image_id = None
        self.focused_image_id = None
        self._record_selection_change(before, "Clear selection")
        self._selection_changed()

    def _record_selection_change(
        self,
        before: set[int],
        description: str,
    ) -> None:
        """Add a selection action to the shared chronological browser history."""
        after = set(self.selected_image_ids)
        if before == after:
            return
        self._history_undo_stack.append(
            BrowserHistoryEntry(
                kind="selection",
                description=description,
                before=frozenset(before),
                after=frozenset(after),
            )
        )
        del self._history_undo_stack[:-BROWSER_HISTORY_LIMIT]
        self._history_redo_stack.clear()
        self._notify_command_state_changed()

    def _record_catalog_change(
        self,
        operation_id: int | None,
        description: str,
    ) -> None:
        """Add one committed catalog edit to the same chronological history."""
        if operation_id is None:
            return
        self._history_undo_stack.append(
            BrowserHistoryEntry(
                kind="catalog",
                description=description,
                operation_id=operation_id,
            )
        )
        del self._history_undo_stack[:-BROWSER_HISTORY_LIMIT]
        self._history_redo_stack.clear()
        self._notify_command_state_changed()

    def _undo_selection(self) -> None:
        """Compatibility wrapper retained for the v0.21.0 GUI regression."""
        self._undo_history()

    def _redo_selection(self) -> None:
        """Compatibility wrapper retained for the v0.21.0 GUI regression."""
        self._redo_history()

    def _update_selection_history_buttons(self) -> None:
        """Compatibility hook: history state now appears in the Edit menu."""
        self._notify_command_state_changed()

    def _selection_changed(self, preferred_focus: int | None = None) -> None:
        """Normalize focus, repaint cards, and rebuild the selection inspector."""
        if len(self.selected_image_ids) == 1:
            self.focused_image_id = next(iter(self.selected_image_ids))
        elif preferred_focus in self.selected_image_ids:
            self.focused_image_id = preferred_focus
        else:
            self.focused_image_id = None

        self._refresh_selection_visuals()
        self._show_selection_details()

    def _refresh_selection_visuals(self) -> None:
        for image_id, card in self.cards_by_id.items():
            card.set_selected(image_id in self.selected_image_ids)
        self._update_selection_status()

    def _update_selection_status(self) -> None:
        visible_selected = sum(
            1
            for record in self.visible_records
            if record.image_id in self.selected_image_ids
        )
        total_selected = len(self.selected_image_ids)

        if visible_selected == total_selected:
            self.selection_var.set(f"{total_selected:,} selected")
        else:
            self.selection_var.set(
                f"{total_selected:,} selected ({visible_selected:,} visible)"
            )
        self._notify_command_state_changed()

    # ------------------------------------------------------------------
    # Explorer-style drag-box selection
    # ------------------------------------------------------------------

    def _bind_drag_surface(self, widget: tk.Widget) -> None:
        """Make a blank grid surface start and update marquee selection."""
        widget.bind("<ButtonPress-1>", self._on_grid_drag_start)
        widget.bind("<B1-Motion>", self._on_grid_drag_motion)
        widget.bind("<ButtonRelease-1>", self._on_grid_drag_end)

    def _on_grid_drag_start(self, event: tk.Event) -> str:
        """Remember a blank-space press without clearing selection prematurely."""
        self._drag_start_root = (int(event.x_root), int(event.y_root))
        self._drag_start_canvas = (
            self.canvas.canvasx(event.x_root - self.canvas.winfo_rootx()),
            self.canvas.canvasy(event.y_root - self.canvas.winfo_rooty()),
        )
        self._drag_initial_selection = set(self.selected_image_ids)
        self._drag_active = False
        self._drag_additive = self._event_has_control(event)
        return "break"

    def _on_grid_drag_motion(self, event: tk.Event) -> str:
        """Draw the marquee and select every visible card it intersects."""
        if self._drag_start_root is None or self._drag_start_canvas is None:
            return "break"

        start_root_x, start_root_y = self._drag_start_root
        distance = max(
            abs(int(event.x_root) - start_root_x),
            abs(int(event.y_root) - start_root_y),
        )
        if not self._drag_active and distance < DRAG_THRESHOLD_PIXELS:
            return "break"

        self._drag_active = True
        self._show_drag_border(
            start_root_x,
            start_root_y,
            int(event.x_root),
            int(event.y_root),
        )

        hits = self._card_ids_intersecting_root_rectangle(
            start_root_x,
            start_root_y,
            int(event.x_root),
            int(event.y_root),
        )
        if self._drag_additive:
            self.selected_image_ids = self._drag_initial_selection | hits
        else:
            self.selected_image_ids = hits
        self._refresh_selection_visuals()
        self._show_selection_details()
        return "break"

    def _on_grid_drag_end(self, _event: tk.Event) -> str:
        """Finish marquee selection or treat a stationary press as blank click."""
        self._hide_drag_border()
        was_drag = self._drag_active
        additive = self._drag_additive
        before = set(self._drag_initial_selection)

        self._drag_start_root = None
        self._drag_start_canvas = None
        self._drag_initial_selection = set()
        self._drag_active = False
        self._drag_additive = False

        if not was_drag and not additive:
            self.selected_image_ids.clear()
            self.anchor_image_id = None
            self.focused_image_id = None
        self._record_selection_change(
            before,
            "Box selection" if was_drag else "Clear selection",
        )
        self._selection_changed()
        return "break"

    def _show_drag_border(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Draw a lightweight Explorer-style marquee above embedded widgets."""
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        width = max(1, right - left)
        height = max(1, bottom - top)
        thickness = 2

        if not self._drag_border_windows:
            for _index in range(4):
                border = tk.Toplevel(self)
                border.overrideredirect(True)
                border.transient(self.winfo_toplevel())
                border.configure(background=self.colors["selection_color"])
                self._drag_border_windows.append(border)

        geometries = (
            (width, thickness, left, top),
            (width, thickness, left, max(top, bottom - thickness)),
            (thickness, height, left, top),
            (thickness, height, max(left, right - thickness), top),
        )
        for border, (border_width, border_height, x, y) in zip(
            self._drag_border_windows, geometries
        ):
            border.geometry(f"{border_width}x{border_height}+{x}+{y}")
            border.deiconify()
            border.lift()

    def _hide_drag_border(self) -> None:
        """Destroy transient marquee windows at the end of a drag."""
        for border in self._drag_border_windows:
            try:
                border.destroy()
            except tk.TclError:
                pass
        self._drag_border_windows.clear()

    def _card_ids_intersecting_root_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> set[int]:
        """Return visible card IDs whose screen rectangles touch the marquee."""
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        hits: set[int] = set()

        for image_id, card in self.cards_by_id.items():
            widget = card.outer
            card_left = widget.winfo_rootx()
            card_top = widget.winfo_rooty()
            card_right = card_left + widget.winfo_width()
            card_bottom = card_top + widget.winfo_height()
            intersects = not (
                card_right < left
                or card_left > right
                or card_bottom < top
                or card_top > bottom
            )
            if intersects:
                hits.add(image_id)
        return hits

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-a>", self._select_all_results_shortcut, add="+")
        self.bind_all("<Control-Shift-a>", self._select_all_shortcut, add="+")
        self.bind_all("<Control-Shift-A>", self._select_all_shortcut, add="+")
        self.bind_all("<Control-d>", self._deselect_all_shortcut, add="+")
        self.bind_all("<Control-D>", self._deselect_all_shortcut, add="+")
        self.bind_all("<Control-Shift-d>", self._deselect_page_shortcut, add="+")
        self.bind_all("<Control-Shift-D>", self._deselect_page_shortcut, add="+")
        self.bind_all("<Control-i>", self._invert_all_shortcut, add="+")
        self.bind_all("<Control-Shift-i>", self._invert_page_shortcut, add="+")
        self.bind_all("<Control-Shift-I>", self._invert_page_shortcut, add="+")
        self.bind_all("<Escape>", self._escape_shortcut, add="+")
        self.bind_all("<Control-z>", self._undo_shortcut, add="+")
        self.bind_all("<Control-y>", self._redo_shortcut, add="+")
        self.bind_all("<Control-Shift-z>", self._redo_shortcut, add="+")
        self.bind_all("<Control-Shift-Z>", self._redo_shortcut, add="+")
        self.bind_all("<KeyPress-n>", self._curation_shortcut, add="+")
        self.bind_all("<KeyPress-N>", self._curation_shortcut, add="+")
        self.bind_all("<F5>", self._refresh_shortcut, add="+")
        self.bind_all("<Control-f>", self._focus_search_shortcut, add="+")
        self.bind_all("<Control-Shift-f>", self._filters_shortcut, add="+")
        self.bind_all("<Control-Shift-F>", self._filters_shortcut, add="+")
        self.bind_all("<Control-e>", self._export_shortcut, add="+")
        self.bind_all("<Delete>", self._delete_to_trash_shortcut, add="+")
        self.bind_all(
            "<Control-Shift-q>",
            self._quarantine_shortcut,
            add="+",
        )
        self.bind_all(
            "<Control-Shift-Q>",
            self._quarantine_shortcut,
            add="+",
        )
        self.bind_all(
            "<Control-Shift-Delete>",
            self._remove_catalog_records_shortcut,
            add="+",
        )
        # A toplevel binding runs after the focused widget and its class binding
        # in Tk's normal bind-tag order.  That is too late for Windows native
        # menu traversal: holding Alt can put the menu system into arrow-key
        # navigation before the browser sees Alt+Left/Right.  A dedicated tag
        # is prepended to every keyboard-focus target, creating the capture-like
        # stage Tk does not otherwise provide.
        toplevel = self.winfo_toplevel()
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<KeyPress-Alt_L>",
            self._alt_page_modifier_pressed,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<KeyPress-Alt_R>",
            self._alt_page_modifier_pressed,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<Alt-Left>",
            self._previous_page_shortcut,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<Alt-Right>",
            self._next_page_shortcut,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<Alt-KeyRelease-Left>",
            self._alt_navigation_arrow_released,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<Alt-KeyRelease-Right>",
            self._alt_navigation_arrow_released,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<KeyRelease-Alt_L>",
            self._alt_page_modifier_released,
        )
        toplevel.bind_class(
            ALT_NAVIGATION_BINDTAG,
            "<KeyRelease-Alt_R>",
            self._alt_page_modifier_released,
        )
        # Retain the v0.24 toplevel bindings as a defensive fallback for a
        # platform or embedded Tk host that strips custom bind tags. The early
        # tag returns "break" first during normal operation, so these do not
        # cause a second page action.
        toplevel.bind("<Alt-Left>", self._previous_page_shortcut, add="+")
        toplevel.bind("<Alt-Right>", self._next_page_shortcut, add="+")
        self._prepend_alt_navigation_bindtag(toplevel)
        focused_widget = toplevel.focus_get()
        if focused_widget is not None:
            self._prepend_alt_navigation_bindtag(focused_widget)
        toplevel.bind("<FocusIn>", self._alt_navigation_focus_entered, add="+")

    @staticmethod
    def _prepend_alt_navigation_bindtag(widget: tk.Widget) -> None:
        """Give Alt paging precedence over widget, class, and native menu logic."""
        tags = tuple(widget.bindtags())
        if ALT_NAVIGATION_BINDTAG not in tags:
            widget.bindtags((ALT_NAVIGATION_BINDTAG, *tags))

    def _alt_navigation_focus_entered(self, event: tk.Event) -> None:
        """Prepare each newly focused widget before it can receive a key chord."""
        widget = getattr(event, "widget", None)
        if widget is None:
            return
        try:
            self._prepend_alt_navigation_bindtag(widget)
        except tk.TclError:
            # A focus transition can be queued while a page rebuild destroys
            # its prior card.  The vanished widget cannot receive a later key
            # event, so no recovery work is necessary.
            return

    @staticmethod
    def _is_text_input(widget: tk.Widget | None) -> bool:
        """Return whether standard text-editing shortcuts own the current focus."""
        if widget is None:
            return False
        return widget.winfo_class() in {
            "Entry",
            "TEntry",
            "Text",
            "TCombobox",
            "Spinbox",
            "TSpinbox",
        }

    def _select_all_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.select_current_page()
        return "break"

    def _select_all_results_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.select_all_results()
        return "break"

    def _deselect_page_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.deselect_current_page()
        return "break"

    def _deselect_all_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.clear_selection()
        return "break"

    def _invert_page_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.invert_current_page_selection()
        return "break"

    def _invert_all_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.invert_all_results_selection()
        return "break"

    def _escape_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self.clear_selection()
        return "break"

    def _undo_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self._undo_history()
        return "break"

    def _redo_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self._is_text_input(self.focus_get()):
            return None
        self._redo_history()
        return "break"

    def _delete_to_trash_shortcut(self, _event: tk.Event) -> str | None:
        """Give Delete its expected browser meaning, never text-edit meaning."""
        if (
            not self.winfo_ismapped()
            or self._is_text_input(self.focus_get())
            or not self.selected_image_ids
        ):
            return None
        self.delete_selected_to_trash()
        return "break"

    def _quarantine_shortcut(self, _event: tk.Event) -> str | None:
        """Quarantine selected browser images without affecting text widgets."""
        if (
            not self.winfo_ismapped()
            or self._is_text_input(self.focus_get())
            or not self.selected_image_ids
        ):
            return None
        self.quarantine_selected()
        return "break"

    def _remove_catalog_records_shortcut(
        self,
        _event: tk.Event,
    ) -> str | None:
        """Remove selected catalog records while leaving physical files alone."""
        if (
            not self.winfo_ismapped()
            or self._is_text_input(self.focus_get())
            or not self.selected_image_ids
        ):
            return None
        self.remove_selected_from_catalog()
        return "break"

    def _curation_shortcut(self, _event: tk.Event) -> str | None:
        if (
            not self.winfo_ismapped()
            or self._is_text_input(self.focus_get())
            or self.catalog_path is None
        ):
            return None
        self._remove_unnecessary_images()
        return "break"

    def _refresh_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self.catalog_path is None:
            return None
        self.refresh()
        return "break"

    def _focus_search_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped():
            return None
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _filters_shortcut(self, _event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or self.catalog_path is None:
            return None
        self._open_browser_filters()
        return "break"

    def _export_shortcut(self, _event: tk.Event) -> str | None:
        if (
            not self.winfo_ismapped()
            or self.catalog_path is None
            or not self.selected_image_ids
            or self._is_text_input(self.focus_get())
        ):
            return None
        self._open_export_dialog()
        return "break"

    def _alt_page_modifier_pressed(self, _event: tk.Event) -> str | None:
        """Prevent Windows menu mode from claiming a browser Alt chord."""
        if (
            not self.winfo_ismapped()
            or not self._capture_alt_modifier_for_page_navigation
        ):
            return None
        self._alt_modifier_held = True
        return "break"

    def _page_shortcut_is_repeat(
        self,
        event: tk.Event | None,
        key_name: str,
    ) -> bool:
        """Return whether an arrow press is OS repeat rather than a new stroke."""
        if event is not None:
            if key_name in self._alt_navigation_keys_down:
                return True
            self._alt_navigation_keys_down.add(key_name)
            return False

        # Dependency-light controller tests do not construct Tk events. Keep a
        # small timing fallback for those callers and for any synthetic command
        # that deliberately invokes the shortcut without an event object.
        now = time.monotonic()
        if now - self._last_page_shortcut_at < PAGE_SHORTCUT_DEBOUNCE_SECONDS:
            return True
        self._last_page_shortcut_at = now
        return False

    def _previous_page_shortcut(self, event: tk.Event | None) -> str | None:
        if not self.winfo_ismapped():
            return None
        self._alt_modifier_held = True
        self._alt_page_navigation_active = True
        if not CatalogBrowserFrame._page_shortcut_is_repeat(self, event, "Left"):
            if self.card_page_index > 0 and not self.duplicate_review_clusters:
                self._show_previous_card_page()
        # Always consume Alt+Left while the browser owns focus—even on page one.
        # Letting repeats or boundary events fall through re-enters native menu
        # traversal while the modifier remains held.
        return "break"

    def _next_page_shortcut(self, event: tk.Event | None) -> str | None:
        if not self.winfo_ismapped():
            return None
        self._alt_modifier_held = True
        self._alt_page_navigation_active = True
        if not CatalogBrowserFrame._page_shortcut_is_repeat(self, event, "Right"):
            if (
                not self.duplicate_review_clusters
                and (self.card_page_index + 1) * self.images_per_page
                < len(self.visible_records)
            ):
                self._append_next_card_batch()
        # Consume repeated/boundary events so a held Alt cannot hand the same
        # keystroke to the native menu system and visually "rubber-band" pages.
        return "break"

    def _alt_navigation_arrow_released(self, event: tk.Event) -> str | None:
        """Allow a new physical arrow press while keeping the Alt chord owned."""
        if not self.winfo_ismapped():
            return None
        self._alt_navigation_keys_down.discard(str(event.keysym))
        if self._alt_modifier_held or self._alt_page_navigation_active:
            return "break"
        return None

    def _alt_page_modifier_released(self, _event: tk.Event) -> str | None:
        """Finish browser-owned Alt input without activating menu traversal."""
        if not self.winfo_ismapped():
            return None
        owned_modifier = self._alt_modifier_held or self._alt_page_navigation_active
        self._alt_modifier_held = False
        self._alt_page_navigation_active = False
        self._alt_navigation_keys_down.clear()
        self._last_page_shortcut_at = 0.0
        return "break" if owned_modifier else None

    # =========================================================================
    # Details pane, unified selection editing, and file opening
    # =========================================================================

    def _selected_records(self) -> list[CatalogImageRecord]:
        """Return selected records in visible order, then hidden selections."""
        visible = [
            record
            for record in self.visible_records
            if record.image_id in self.selected_image_ids
        ]
        visible_ids = {record.image_id for record in visible}
        hidden = [
            record
            for record in self.all_records
            if record.image_id in self.selected_image_ids
            and record.image_id not in visible_ids
        ]
        return visible + hidden

    def _show_selection_details(self) -> None:
        """Show a single image or an honest summary of the current selection."""
        records = self._selected_records()
        if not records:
            self._clear_details()
            return
        if len(records) == 1:
            self._show_single_details(records[0])
            return
        self._show_multi_details(records)

    def _show_single_details(self, record: CatalogImageRecord) -> None:
        self.focused_image_id = record.image_id
        self.detail_filename_var.set(record.filename)
        self.open_image_button.configure(
            state=(
                "normal"
                if record.source_path is not None and record.source_path.exists()
                else "disabled"
            )
        )
        self.image_quality_button.configure(state="normal")

        self._set_detail_text(record)
        self._populate_selection_review_controls([record])
        self._render_selection_tags([record])
        self._queue_thumbnail(record, DETAIL_PREVIEW_SIZE, "detail")

        card = self.cards_by_id.get(record.image_id)
        if card is not None and card.photo_image is not None:
            self.detail_preview_label.configure(image=card.photo_image, text="")
            self._details_preview_photo = card.photo_image  # type: ignore[assignment]
        else:
            self.detail_preview_label.configure(image="", text="Loading preview…")
            self._details_preview_photo = None

    def _show_multi_details(self, records: list[CatalogImageRecord]) -> None:
        """Replace the misleading single preview with aggregate selection facts."""
        self.focused_image_id = None
        count = len(records)
        self.detail_filename_var.set("Selection Review")
        self.open_image_button.configure(state="disabled")
        self.image_quality_button.configure(state="disabled")
        self._details_preview_photo = None

        keyword = self._shared_value(
            [record.manual_keyword or "None" for record in records]
        )
        disposition = self._shared_value(
            [
                REVIEW_STATUS_TO_LABEL.get(record.review_status, "Unreviewed")
                for record in records
            ]
        )
        suggested_identity = self._shared_value(
            [record.suggested_identity or "None" for record in records]
        )
        identity_review = self._shared_value(
            [
                IDENTITY_STATUS_LABELS.get(
                    record.identity_review_status,
                    record.identity_review_status or "None",
                )
                if record.suggested_identity
                else "None"
                for record in records
            ]
        )
        summary = (
            f"{count:,} images selected\n\n"
            f"Trigger Keyword: {keyword}\n"
            f"Disposition: {disposition}\n"
            f"Suggested identity: {suggested_identity}\n"
            f"Identity review: {identity_review}"
        )
        self.detail_preview_label.configure(image="", text=summary)
        self._populate_selection_review_controls(records)
        self._render_selection_tags(records)
        self._set_multi_detail_text(records)

    @staticmethod
    def _shared_value(values: Iterable[str]) -> str:
        """Return one shared value or the explicit mixed-value sentinel."""
        normalized = list(values)
        if not normalized:
            return "None"
        first = normalized[0]
        return first if all(value == first for value in normalized) else MULTIPLE_VALUES_LABEL

    def _set_detail_preview(self, image_path: Path | None) -> None:
        # A detail thumbnail requested before selection changed may finish late.
        # It is valid only while exactly one image remains selected.
        if len(self.selected_image_ids) != 1:
            return
        if image_path is None or not image_path.exists():
            self.detail_preview_label.configure(image="", text="Preview unavailable")
            self._details_preview_photo = None
            return

        self._details_preview_photo = self.decoded_thumbnail_cache.get_or_load(
            image_path
        )
        if self._details_preview_photo is None:
            self.detail_preview_label.configure(image="", text="Preview unavailable")
            return
        self.detail_preview_label.configure(
            image=self._details_preview_photo,
            text="",
        )

    def _set_detail_text(self, record: CatalogImageRecord) -> None:
        """Render the most useful metadata first, keeping internals lower down."""
        text = self.detail_text
        text.configure(state="normal")
        text.delete("1.0", "end")

        def field(label: str, value: str, *, tag: str = "value") -> None:
            text.insert("end", label + "\n", "heading")
            text.insert("end", (value or "—") + "\n", tag)

        field("Path", record.absolute_path)
        field("Dimensions", record.dimensions_text)
        field("File size", record.byte_size_text)
        field("File status", record.file_status)
        field("Known file locations", str(record.file_location_count))
        source_video = record.source_video
        sampling_mode = record.video_sampling_mode
        timestamp_seconds = record.video_timestamp_seconds
        frame_number = record.video_frame_number
        interval_seconds = record.video_interval_seconds
        derived_origin = False
        if not source_video:
            fallback = self._last_extraction_origin(record)
            if fallback is not None:
                (
                    source_video,
                    sampling_mode,
                    timestamp_seconds,
                    frame_number,
                    interval_seconds,
                ) = fallback
                derived_origin = True
        if source_video:
            field(
                (
                    "Source video (derived from last extraction)"
                    if derived_origin
                    else "Source video"
                ),
                source_video,
            )
            field(
                "Video timestamp",
                format_video_timestamp(timestamp_seconds),
            )
            field(
                "Extraction details",
                " · ".join(
                    part
                    for part in (
                        sampling_mode.replace("_", " ").title(),
                        (
                            f"frame {frame_number:,}"
                            if frame_number is not None
                            else ""
                        ),
                        (
                            f"every {interval_seconds:g} sec"
                            if interval_seconds is not None
                            else ""
                        ),
                    )
                    if part
                ),
            )
        field("Caption", record.caption)
        field("Suggested identity", self._identity_detail(record))
        field(
            "Identity review",
            IDENTITY_STATUS_LABELS.get(
                record.identity_review_status,
                record.identity_review_status or "—",
            ),
        )
        field("Active AI tags", record.ai_tags_active)
        field("Excluded AI tags", record.ai_tags_excluded, tag="muted")
        field("Manual tags", record.manual_tags, tag="manual")

        if record.manual_keyword:
            field("Manual Trigger Keyword", record.manual_keyword, tag="manual")
        elif record.has_manual_metadata:
            field(
                "Manual / confirmed metadata",
                record.manual_tags or "Confirmed user metadata",
                tag="manual",
            )

        field("OCR text", record.ocr_text)
        field("Detected objects", record.object_labels)
        field("Candidate recommendation", record.recommendation)
        field("Recommendation reason", record.recommendation_reason)
        field("Review status", record.review_status)
        field("Review notes", record.review_notes)
        field("SHA-256", record.content_sha256, tag="muted")
        field("Catalog image ID", str(record.image_id), tag="muted")
        text.configure(state="disabled")

    def _last_extraction_origin(
        self,
        record: CatalogImageRecord,
    ) -> tuple[str, str, float, int, float] | None:
        """Derive legacy fixed-interval metadata from the last saved extraction.

        v0.27.1 and earlier did not write manifests. This conservative fallback
        applies only when the frame is directly inside the remembered
        destination, the last run used fixed-interval sampling, and the
        filename ends in the deterministic ``_000001`` pattern.
        """
        source = self.settings.video_last_source.strip()
        destination = self.settings.video_last_destination.strip()
        if (
            not source
            or not destination
            or self.settings.video_sampling_mode != "interval"
            or not record.absolute_path
        ):
            return None
        try:
            frame_path = Path(record.absolute_path).expanduser().resolve()
            destination_path = Path(destination).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        if frame_path.parent != destination_path:
            return None
        _prefix, separator, raw_number = frame_path.stem.rpartition("_")
        if not separator or not raw_number.isdigit() or int(raw_number) < 1:
            return None
        frame_number = int(raw_number)
        interval = float(self.settings.video_interval_seconds)
        return (
            str(Path(source).expanduser()),
            "interval",
            (frame_number - 1) * interval,
            frame_number,
            interval,
        )

    def _set_multi_detail_text(self, records: list[CatalogImageRecord]) -> None:
        """Show compact aggregate facts instead of one selected image's metadata."""
        text = self.detail_text
        text.configure(state="normal")
        text.delete("1.0", "end")

        visible_ids = {record.image_id for record in self.visible_records}
        visible_count = sum(
            1 for record in records if record.image_id in visible_ids
        )
        present_count = sum(1 for record in records if record.file_status == "present")
        face_count = sum(record.face_count for record in records)
        suggestions = sum(1 for record in records if record.suggested_identity)
        manual = sum(1 for record in records if record.has_manual_metadata)
        quality_analyzed = sum(
            1 for record in records if record.quality_status == "success"
        )

        def field(label: str, value: str, *, tag: str = "value") -> None:
            text.insert("end", label + "\n", "heading")
            text.insert("end", value + "\n", tag)

        field("Selected images", f"{len(records):,}")
        field("Visible in current results", f"{visible_count:,}")
        field("Files present", f"{present_count:,} of {len(records):,}")
        field("Faces detected", f"{face_count:,}")
        field("Identity suggestions", f"{suggestions:,}")
        field("Manual / confirmed metadata", f"{manual:,}", tag="manual")
        field("Quality analyzed", f"{quality_analyzed:,} of {len(records):,}")
        field(
            "Editing",
            "The controls above apply to every selected image. Values that differ are shown as Multiple values.",
            tag="muted",
        )
        text.configure(state="disabled")

    @staticmethod
    def _identity_detail(record: CatalogImageRecord) -> str:
        if not record.suggested_identity:
            return "—"
        if record.identity_similarity is None:
            return record.suggested_identity
        return f"{record.suggested_identity} ({record.identity_similarity:.3f})"

    def _populate_selection_review_controls(
        self,
        records: list[CatalogImageRecord],
    ) -> None:
        """Populate the same editor for one image or a multi-selection."""
        self.quarantine_button.configure(
            state=(
                "normal"
                if any(record.file_status == "present" for record in records)
                else "disabled"
            )
        )
        self.restore_quarantine_button.configure(
            state=(
                "normal"
                if any(record.file_status == "quarantined" for record in records)
                else "disabled"
            )
        )
        self._suppress_review_event = True
        try:
            disposition_labels = [
                REVIEW_STATUS_TO_LABEL.get(record.review_status, "Unreviewed")
                for record in records
            ]
            disposition = self._shared_value(disposition_labels)
            disposition_values = [
                "Unreviewed",
                "Keep",
                "Needs follow-up",
                "Reject",
            ]
            if disposition == MULTIPLE_VALUES_LABEL:
                disposition_values.insert(0, MULTIPLE_VALUES_LABEL)
            self.review_decision_combo.configure(
                state="readonly",
                values=tuple(disposition_values),
            )
            self.review_decision_var.set(disposition)

            keywords = [record.manual_keyword for record in records]
            keyword = self._shared_value([value or "" for value in keywords])
            self._keyword_shows_multiple = keyword == MULTIPLE_VALUES_LABEL
            self.manual_keyword_var.set(keyword)
            self.manual_keyword_entry.configure(state="normal")
            self.save_keyword_button.configure(state="normal")
            self.clear_keyword_button.configure(
                state="normal" if any(keywords) else "disabled"
            )
            self.manual_keyword_label.configure(
                foreground=MANUAL_ACCENT if any(keywords) else self.colors["card_text"]
            )

            suggestion_records = [
                record
                for record in records
                if record.identity_match_id is not None and record.suggested_identity
            ]
            if not suggestion_records:
                self.identity_summary_var.set("No identity suggestion")
                self.identity_status_var.set("")
                self.confirm_identity_button.configure(state="disabled")
                self.reject_identity_button.configure(state="disabled")
                self.reset_identity_button.configure(state="disabled")
                return

            suggestion_names = [record.suggested_identity for record in suggestion_records]
            shared_suggestion = self._shared_value(suggestion_names)
            if len(suggestion_records) != len(records):
                suggestion_text = (
                    f"Suggested identity: {MULTIPLE_VALUES_LABEL} "
                    f"({len(suggestion_records):,} of {len(records):,} images have suggestions)"
                )
            else:
                suggestion_text = f"Suggested identity: {shared_suggestion}"
                if len(records) == 1 and records[0].identity_similarity is not None:
                    suggestion_text += f" ({records[0].identity_similarity:.3f})"
            self.identity_summary_var.set(suggestion_text)

            statuses = [
                record.identity_review_status or "suggested"
                for record in suggestion_records
            ]
            shared_status = self._shared_value(statuses)
            self.identity_status_var.set(
                MULTIPLE_VALUES_LABEL
                if shared_status == MULTIPLE_VALUES_LABEL
                else IDENTITY_STATUS_LABELS.get(shared_status, shared_status)
            )
            self.confirm_identity_button.configure(
                state=(
                    "normal"
                    if any(status != "confirmed" for status in statuses)
                    else "disabled"
                )
            )
            self.reject_identity_button.configure(
                state=(
                    "normal"
                    if any(status != "rejected" for status in statuses)
                    else "disabled"
                )
            )
            self.reset_identity_button.configure(
                state=(
                    "normal"
                    if any(status != "suggested" for status in statuses)
                    else "disabled"
                )
            )
        finally:
            self._suppress_review_event = False

    def _clear_review_controls(self) -> None:
        """Reset review widgets when no catalog image is selected."""
        self._suppress_review_event = True
        try:
            self.review_decision_var.set("Unreviewed")
            self.review_notes_var.set("")
            self.manual_keyword_var.set("")
            self._keyword_shows_multiple = False
            self.identity_summary_var.set("No identity suggestion")
            self.identity_status_var.set("")

            self.review_decision_combo.configure(state="disabled")
            self.manual_keyword_entry.configure(state="disabled")
            self.save_keyword_button.configure(state="disabled")
            self.clear_keyword_button.configure(state="disabled")
            self.confirm_identity_button.configure(state="disabled")
            self.reject_identity_button.configure(state="disabled")
            self.reset_identity_button.configure(state="disabled")
            self.quarantine_button.configure(state="disabled")
            self.restore_quarantine_button.configure(state="disabled")
            self.manual_keyword_label.configure(foreground=self.colors["card_text"])
        finally:
            self._suppress_review_event = False

    def _on_review_decision_selected(self, _event: tk.Event | None = None) -> None:
        """Immediately apply a deliberate disposition choice to the selection."""
        if self._suppress_review_event:
            return
        label = self.review_decision_var.get()
        if label == MULTIPLE_VALUES_LABEL:
            return
        status = REVIEW_LABEL_TO_STATUS.get(label)
        if status is None:
            return
        self._apply_selection_request(
            BatchEditRequest(review_status=status),
            f'Set disposition to "{label}"',
        )

    def _on_keyword_focus_in(self, _event: tk.Event | None = None) -> None:
        """Clear the mixed-value sentinel when the user begins a replacement."""
        if self._keyword_shows_multiple:
            self.manual_keyword_var.set("")
            self._keyword_shows_multiple = False

    def _render_selection_tags(self, records: list[CatalogImageRecord]) -> None:
        """Render only tag states that are unambiguous across the selection."""
        if self.repository is None:
            self._clear_tag_panel()
            return
        try:
            tags = self.repository.fetch_common_tags(
                record.image_id for record in records
            )
        except Exception:
            logging.exception("Could not load selection tags")
            tags = []

        self._displayed_selection_tags = tags
        self.add_tags_button.configure(state="normal")
        text = self.tag_text
        text.configure(state="normal")
        text.delete("1.0", "end")

        if not tags:
            message = (
                "No tags are common to every selected image. Use Add Tags to "
                "apply a manual tag across the selection."
                if len(records) > 1
                else "No AI or manual tags are available for this image yet."
            )
            text.insert("end", message, "tag_message")
            text.configure(state="disabled")
            return

        style_by_kind = {
            "manual": (
                self.colors["manual_tag_foreground"],
                self.colors["manual_tag_background"],
            ),
            "ai_active": (
                self.colors["ai_tag_foreground"],
                self.colors["ai_tag_background"],
            ),
            "ai_excluded": (
                self.colors["excluded_tag_foreground"],
                self.colors["excluded_tag_background"],
            ),
        }
        for index, tag in enumerate(tags):
            binding_tag = f"selection_tag_{index}"
            foreground, background = style_by_kind[tag.kind]
            text.tag_configure(
                binding_tag,
                foreground=foreground,
                background=background,
                font=get_ui_font(
                    self,
                    size=9,
                    weight="bold" if tag.kind == "manual" else "normal",
                ),
                relief="raised",
                borderwidth=1,
            )
            text.insert("end", f" {tag.name} ", binding_tag)
            text.insert("end", "  ")
            text.tag_bind(
                binding_tag,
                "<Button-1>",
                lambda _event, selected_tag=tag: self._on_tag_chip_clicked(selected_tag),
            )
            text.tag_bind(
                binding_tag,
                "<Enter>",
                lambda _event: text.configure(cursor="hand2"),
            )
            text.tag_bind(
                binding_tag,
                "<Leave>",
                lambda _event: text.configure(cursor="arrow"),
            )
        text.configure(state="disabled")

    def _clear_tag_panel(self) -> None:
        """Reset tag controls when no images are selected."""
        self._displayed_selection_tags = []
        self.add_tags_button.configure(state="disabled")
        self.tag_text.configure(state="normal")
        self.tag_text.delete("1.0", "end")
        self.tag_text.insert("end", "Select an image to curate tags.", "tag_message")
        self.tag_text.configure(state="disabled")

    def _add_manual_tags(self) -> None:
        """Collect one or many tags and apply them idempotently to the selection."""
        if self.edit_service is None or not self.selected_image_ids:
            return
        dialog = AddTagsDialog(self, len(self.selected_image_ids))
        self.wait_window(dialog)
        if not dialog.result:
            return

        tags = list(dialog.result)
        description = (
            f"Add {len(tags):,} manual tag{'s' if len(tags) != 1 else ''}"
        )
        self._apply_tag_operation(
            description,
            lambda: self.edit_service.add_manual_tags(
                sorted(self.selected_image_ids), tags
            ),
        )

    def _on_tag_chip_clicked(self, tag: SelectionTagRecord) -> None:
        """Apply the provenance-appropriate action for one common tag chip."""
        if self.edit_service is None or not self.selected_image_ids:
            return
        image_ids = sorted(self.selected_image_ids)
        if tag.kind == "manual":
            self._apply_tag_operation(
                f'Remove manual tag "{tag.name}"',
                lambda: self.edit_service.remove_manual_tags(image_ids, [tag.name]),
            )
            return

        excluding = tag.kind == "ai_active"
        verb = "Exclude" if excluding else "Restore"
        self._apply_tag_operation(
            f'{verb} AI tag "{tag.name}"',
            lambda: self.edit_service.set_ai_tag_excluded(
                image_ids,
                tag.name,
                excluded=excluding,
            ),
        )

    def _apply_tag_operation(
        self,
        action_description: str,
        operation: Callable[[], TagEditResult],
    ) -> bool:
        """Run one tag action with the same backup, history, and status safety."""
        if not self._confirm_large_selection_edit(action_description):
            self._show_selection_details()
            return False
        try:
            first_edit = self._session_backup_path is None
            backup_path = self._ensure_edit_backup()
            result = operation()
            self.refresh(quiet=True)
        except Exception as error:
            logging.exception("Catalog tag edit failed")
            messagebox.showerror(
                "Tag edit failed",
                (
                    f"LoRA Image Curator could not complete the tag edit.\n\n"
                    f"{type(error).__name__}: {error}\n\n"
                    "No partial edit was committed."
                ),
                parent=self,
            )
            self._show_selection_details()
            return False

        if not result.changed_anything:
            self.edit_status_var.set("No changes were needed; existing tags were not duplicated.")
            self._show_selection_details()
            return True

        self._record_catalog_change(result.operation_id, action_description)
        message = (
            f"{action_description}: {result.changed_image_count:,} image"
            f"{'s' if result.changed_image_count != 1 else ''} changed. Ctrl+Z to undo."
        )
        if first_edit:
            message += f" Backup: {backup_path.name}"
        self.edit_status_var.set(message)
        self._show_selection_details()
        return True

    def _ensure_edit_backup(self) -> Path:
        """Create one safe catalog snapshot before this session's first edit."""
        if self._session_backup_path is not None:
            return self._session_backup_path
        if self.edit_service is None:
            raise RuntimeError("Choose and load a LoRA Image Curator catalog first.")

        self._session_backup_path = self.edit_service.create_backup()
        return self._session_backup_path

    def _confirm_large_selection_edit(self, action_description: str) -> bool:
        """Ask once per large operation without burdening ordinary curation."""
        count = len(self.selected_image_ids)
        if count < LARGE_EDIT_CONFIRMATION_THRESHOLD:
            return True
        return messagebox.askyesno(
            "Confirm large edit",
            (
                f"{action_description} for {count:,} selected images?\n\n"
                "Images already matching the requested value will not be changed.\n\n"
                "You can undo this operation with Ctrl+Z."
            ),
            parent=self,
        )

    def _apply_selection_request(
        self,
        request: BatchEditRequest,
        action_description: str,
    ) -> bool:
        """Apply one transactional edit to the current selection and record history."""
        if self.edit_service is None or not self.selected_image_ids:
            return False
        if not self._confirm_large_selection_edit(action_description):
            self._show_selection_details()
            return False

        image_ids = sorted(self.selected_image_ids)
        try:
            first_edit = self._session_backup_path is None
            backup_path = self._ensure_edit_backup()
            result: BatchEditResult = self.edit_service.apply_batch_edit(
                image_ids,
                request,
            )
            self.refresh(quiet=True)
        except Exception as error:
            logging.exception("Catalog selection edit failed")
            messagebox.showerror(
                "Catalog edit failed",
                (
                    f"LoRA Image Curator could not complete the edit.\n\n"
                    f"{type(error).__name__}: {error}\n\n"
                    "No partial edit was committed."
                ),
                parent=self,
            )
            self._show_selection_details()
            return False

        if not result.changed_anything:
            self.edit_status_var.set("No changes were needed.")
            self._show_selection_details()
            return True

        self._record_catalog_change(result.operation_id, action_description)
        parts = [
            f"{action_description}: {result.changed_image_count:,} image"
            f"{'s' if result.changed_image_count != 1 else ''} changed"
        ]
        if result.identity_skipped_no_suggestion:
            parts.append(
                f"{result.identity_skipped_no_suggestion:,} had no identity suggestion"
            )
        message = "; ".join(parts) + ". Ctrl+Z to undo."
        if first_edit:
            message += f" Backup: {backup_path.name}"
        self.edit_status_var.set(message)
        self._show_selection_details()
        return True

    def _save_manual_keyword(self, _event: tk.Event | None = None) -> str | None:
        """Replace the selection's user-owned training Trigger Keyword."""
        if self._keyword_shows_multiple:
            messagebox.showinfo(
                "Choose a Trigger Keyword",
                "Type a new keyword before saving. Existing mixed values were not changed.",
                parent=self,
            )
            return "break" if _event is not None else None

        keyword = " ".join(self.manual_keyword_var.get().split()).strip()
        if not keyword:
            messagebox.showinfo(
                "Trigger Keyword is blank",
                "Enter a keyword, or use Clear to remove existing keywords.",
                parent=self,
            )
            return "break" if _event is not None else None

        self._apply_selection_request(
            BatchEditRequest(keyword_action="set", keyword=keyword),
            f'Set Trigger Keyword "{keyword}"',
        )
        return "break" if _event is not None else None

    def _clear_manual_keyword(self) -> None:
        """Remove manual Trigger Keywords from every selected image."""
        self._apply_selection_request(
            BatchEditRequest(keyword_action="clear"),
            "Clear manual Trigger Keyword",
        )

    def _review_identity(self, status: str) -> None:
        """Confirm, reject, or reset strongest suggestions across the selection."""
        action_labels = {
            "confirmed": "Confirm identity suggestions",
            "rejected": "Reject identity suggestions",
            "suggested": "Reset identity review",
        }
        self._apply_selection_request(
            BatchEditRequest(identity_status=status),
            action_labels.get(status, "Update identity review"),
        )

    def _undo_history(self) -> None:
        """Undo the newest selection or catalog action in chronological order."""
        entry = (
            self._history_undo_stack.pop()
            if self._history_undo_stack
            else None
        )

        if entry is not None and entry.kind == "selection":
            self.selected_image_ids = set(entry.before).intersection(self.records_by_id)
            self.anchor_image_id = next(iter(self.selected_image_ids), None)
            self.focused_image_id = None
            self._history_redo_stack.append(entry)
            self.edit_status_var.set(f"Undid: {entry.description}. Ctrl+Y to redo.")
            self._selection_changed()
            self._notify_command_state_changed()
            return

        if entry is not None and entry.kind == "filter":
            if entry.before_filter is None:
                self._history_undo_stack.append(entry)
                return
            self._restore_filter_history_state(entry.before_filter)
            self._history_redo_stack.append(entry)
            self.edit_status_var.set(
                f"Undid: {entry.description}. Ctrl+Y to redo."
            )
            self._notify_command_state_changed()
            return

        if self.edit_service is None:
            if entry is not None:
                self._history_undo_stack.append(entry)
            return
        try:
            operation = self.edit_service.get_last_undoable_operation()
            if operation is None:
                if entry is not None:
                    self._history_undo_stack.append(entry)
                self.edit_status_var.set("Nothing to undo.")
                self.bell()
                self._notify_command_state_changed()
                return
            if (
                entry is not None
                and entry.operation_id is not None
                and operation.operation_id != entry.operation_id
            ):
                # An external catalog edit changed the durable cursor.  Do not
                # pretend the in-memory command still identifies the next
                # database operation.
                self._history_undo_stack.clear()
                self._history_redo_stack.clear()
                entry = None
            first_edit = self._session_backup_path is None
            backup_path = self._ensure_edit_backup()
            result = self.edit_service.undo_last_operation()
            self.refresh(quiet=True)
        except Exception as error:
            if entry is not None:
                self._history_undo_stack.append(entry)
            logging.exception("Undo failed")
            messagebox.showerror(
                "Undo failed",
                f"{type(error).__name__}: {error}\n\nNo partial undo was committed.",
                parent=self,
            )
            return

        undone_entry = entry or BrowserHistoryEntry(
            kind="catalog",
            description=result.description,
            operation_id=result.operation_id,
        )
        self._history_redo_stack.append(undone_entry)
        message = (
            f"Undid: {result.description} "
            f"({result.affected_image_count:,} image"
            f"{'s' if result.affected_image_count != 1 else ''})."
        )
        if first_edit:
            message += f" Backup: {backup_path.name}"
        self.edit_status_var.set(message)
        self._show_selection_details()
        self._notify_command_state_changed()

    def _redo_history(self) -> None:
        """Redo the newest action undone through the shared browser history."""
        if not self._history_redo_stack:
            self.edit_status_var.set("Nothing to redo.")
            self.bell()
            self._notify_command_state_changed()
            return

        entry = self._history_redo_stack.pop()
        if entry.kind == "selection":
            self.selected_image_ids = set(entry.after).intersection(self.records_by_id)
            self.anchor_image_id = next(iter(self.selected_image_ids), None)
            self.focused_image_id = None
            self._history_undo_stack.append(entry)
            self.edit_status_var.set(f"Redid: {entry.description}.")
            self._selection_changed()
            self._notify_command_state_changed()
            return

        if entry.kind == "filter":
            if entry.after_filter is None:
                self._history_redo_stack.append(entry)
                return
            self._restore_filter_history_state(entry.after_filter)
            self._history_undo_stack.append(entry)
            self.edit_status_var.set(f"Redid: {entry.description}.")
            self._notify_command_state_changed()
            return

        if self.edit_service is None:
            self._history_redo_stack.append(entry)
            return
        try:
            operation = self.edit_service.get_next_redoable_operation()
            if operation is None:
                self._history_redo_stack.append(entry)
                self.edit_status_var.set("Nothing to redo.")
                self.bell()
                self._notify_command_state_changed()
                return
            if (
                entry.operation_id is not None
                and operation.operation_id != entry.operation_id
            ):
                self._history_undo_stack.clear()
                self._history_redo_stack.clear()
                self.edit_status_var.set(
                    "Redo history changed outside this browser session."
                )
                self.bell()
                self._notify_command_state_changed()
                return
            first_edit = self._session_backup_path is None
            backup_path = self._ensure_edit_backup()
            result = self.edit_service.redo_next_operation()
            self.refresh(quiet=True)
        except Exception as error:
            self._history_redo_stack.append(entry)
            logging.exception("Redo failed")
            messagebox.showerror(
                "Redo failed",
                f"{type(error).__name__}: {error}\n\nNo partial redo was committed.",
                parent=self,
            )
            return

        self._history_undo_stack.append(entry)
        message = (
            f"Redid: {result.description} "
            f"({result.affected_image_count:,} image"
            f"{'s' if result.affected_image_count != 1 else ''})."
        )
        if first_edit:
            message += f" Backup: {backup_path.name}"
        self.edit_status_var.set(message)
        self._show_selection_details()
        self._notify_command_state_changed()

    def _clear_details(self) -> None:
        self.detail_filename_var.set("No image selected")
        self.detail_preview_label.configure(image="", text="Select an image")
        self._details_preview_photo = None
        self.open_image_button.configure(state="disabled")
        self.image_quality_button.configure(state="disabled")
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.configure(state="disabled")
        self._clear_tag_panel()
        self._clear_review_controls()

    def _open_export_dialog(self) -> None:
        """Open the complete non-destructive export workflow for the selection."""
        if self.catalog_path is None or not self.selected_image_ids:
            return
        dialog = DatasetExportDialog(
            self,
            database_path=self.catalog_path,
            image_ids=[record.image_id for record in self._selected_records()],
            settings=self.settings,
            on_settings_saved=self._save_browser_settings,
        )
        self.wait_window(dialog)

    def quarantine_selected(self) -> None:
        """Move every present location for selected images into quarantine."""
        if self.file_action_service is None or not self.selected_image_ids:
            return
        items = self.file_action_service.present_items(self.selected_image_ids)
        if not items:
            messagebox.showinfo(
                "Nothing to quarantine",
                "The selected catalog images have no present file locations.",
                parent=self,
            )
            return
        quarantine_root = Path(
            self.settings.quarantine_directory
            or get_default_quarantine_directory()
        ).expanduser()
        affected_image_ids = {item.image_id for item in items}
        affected_images = len(affected_image_ids)
        approved = messagebox.askyesno(
            "Quarantine selected images?",
            (
                f"Selected catalog images with present files: {affected_images:,}\n"
                f"Physical files that will be moved: {len(items):,}\n\n"
                f"Quarantine folder:\n{quarantine_root}\n\n"
                "This is reversible with Restore Selected. Existing files at an "
                "original location are never overwritten during restore."
            ),
            parent=self,
        )
        if not approved:
            return
        selected_ids = tuple(sorted(self.selected_image_ids))
        service = self.file_action_service
        summary = self._run_bulk_action(
            title="Quarantining Images",
            heading=f"Moving {len(items):,} physical files to quarantine",
            total=len(items),
            worker=lambda report, cancel: service.quarantine(
                selected_ids,
                quarantine_root,
                resolved_items=items,
                progress_callback=report,
                cancel_event=cancel,
            ),
        )
        if summary is None:
            return
        self._finish_file_action("Quarantine", summary)

    def restore_selected_from_quarantine(self) -> None:
        """Restore selected quarantined files to their recorded source paths."""
        if self.file_action_service is None or not self.selected_image_ids:
            return
        items = self.file_action_service.quarantined_items(
            self.selected_image_ids
        )
        if not items:
            messagebox.showinfo(
                "Nothing to restore",
                "The selected catalog images have no quarantined file locations.",
                parent=self,
            )
            return
        approved = messagebox.askyesno(
            "Restore selected images?",
            (
                f"Quarantined physical files to restore: {len(items):,}\n\n"
                "Each file returns to the source path recorded when it was "
                "quarantined. Restore stops for any path that is already occupied."
            ),
            parent=self,
        )
        if not approved:
            return
        selected_ids = tuple(sorted(self.selected_image_ids))
        service = self.file_action_service
        summary = self._run_bulk_action(
            title="Restoring Images",
            heading=f"Restoring {len(items):,} quarantined files",
            total=len(items),
            worker=lambda report, cancel: service.restore(
                selected_ids,
                resolved_items=items,
                progress_callback=report,
                cancel_event=cancel,
            ),
        )
        if summary is None:
            return
        self._finish_file_action("Restore", summary)

    def delete_selected_to_trash(self) -> None:
        """Send selected files to Trash and optionally remove their records.

        The record-removal preference defaults off. Confirmation is based on
        the active browser page size: one page or less proceeds directly, while
        a cross-page action names its complete scope before any file is moved.
        Delete-associated cleanup backs up the catalog only when it removes
        more than one complete image record. The explicit catalog-only removal
        workflow retains its always-back-up rule because it can erase metadata
        without the operating system's Recycle Bin as a recovery path.
        """
        if self.file_action_service is None or not self.selected_image_ids:
            return
        items = self.file_action_service.present_items(self.selected_image_ids)
        if not items:
            messagebox.showinfo(
                "Nothing to delete",
                "The selected catalog images have no present file locations.",
                parent=self,
            )
            return
        affected_image_ids = {item.image_id for item in items}
        affected_images = len(affected_image_ids)
        remove_records = self.settings.delete_catalog_record_with_file
        if affected_images > self.images_per_page:
            approved = messagebox.askyesno(
                "Send selected files to the Recycle Bin?",
                (
                    f"Catalog images affected: {affected_images:,}\n"
                    f"Physical files sent to Recycle Bin: {len(items):,}\n\n"
                    f"Current browser page size: {self.images_per_page:,}\n"
                    "This action affects more than one page.\n\n"
                    "The files are not permanently deleted by this application. "
                    "Recovery is handled by the operating system's Recycle Bin. "
                    "If native trash support fails, no permanent-delete fallback "
                    "is attempted.\n\n"
                    + (
                        "Setting enabled: successfully deleted images will also "
                        "be completely removed from the catalog after a fresh "
                        "database backup is created."
                        if remove_records
                        else
                        "Setting disabled: catalog records and provider results "
                        "will remain and can be found with the “No image file "
                        "found” filter."
                    )
                ),
                parent=self,
            )
            if not approved:
                return
        selected_ids = tuple(sorted(self.selected_image_ids))
        service = self.file_action_service
        edit_service = self.edit_service
        total_steps = len(items) + (affected_images if remove_records else 0)

        def delete_worker(
            report: Callable[[int, int, str], None],
            cancel: object,
        ) -> tuple[
            FileActionSummary,
            CatalogRemovalSummary | None,
            Path | None,
            BaseException | None,
        ]:
            summary = service.send_to_trash(
                selected_ids,
                resolved_items=items,
                progress_callback=(
                    lambda current, _total, detail: report(
                        current,
                        total_steps,
                        detail,
                    )
                ),
                cancel_event=cancel,  # type: ignore[arg-type]
            )
            removal: CatalogRemovalSummary | None = None
            backup: Path | None = None
            cleanup_error: BaseException | None = None
            if remove_records and not summary.cancelled:
                removable_ids = service.image_ids_without_present_files(
                    affected_image_ids
                )
                if removable_ids:
                    try:
                        if edit_service is None:
                            raise RuntimeError("Choose and load a catalog first.")
                        if len(removable_ids) > 1:
                            report(
                                len(items),
                                total_steps,
                                "Creating a fresh catalog backup…",
                            )
                            backup = edit_service.create_backup()
                        removal = service.remove_catalog_records(
                            removable_ids,
                            progress_callback=(
                                lambda current, _total, detail: report(
                                    len(items) + current,
                                    total_steps,
                                    detail,
                                )
                            ),
                            cancel_event=cancel,  # type: ignore[arg-type]
                        )
                    except BaseException as error:
                        cleanup_error = error
            return summary, removal, backup, cleanup_error

        result = self._run_bulk_action(
            title="Sending Images to Recycle Bin",
            heading=(
                f"Deleting {len(items):,} physical files"
                + (
                    " and removing completed catalog records"
                    if remove_records
                    else ""
                )
            ),
            total=total_steps,
            worker=delete_worker,
        )
        if result is None:
            return
        summary, removal_summary, backup_path, cleanup_error = result
        if cleanup_error is not None:
            logging.error(
                "Files were trashed but catalog cleanup failed",
                exc_info=(
                    type(cleanup_error),
                    cleanup_error,
                    cleanup_error.__traceback__,
                ),
            )
            messagebox.showerror(
                "Catalog cleanup failed",
                (
                    "The Recycle Bin action completed, but LoRA Image Curator "
                    "could not remove the corresponding catalog records.\n\n"
                    f"{type(cleanup_error).__name__}: {cleanup_error}\n\n"
                    "The remaining records are still available under the "
                    "“No image file found” filter."
                ),
                parent=self,
            )
        if backup_path is not None:
            self._session_backup_path = backup_path
        self._finish_file_action(
            "Recycle Bin",
            summary,
            removal_summary=removal_summary,
            backup_path=backup_path,
        )

    def remove_selected_from_catalog(self) -> None:
        """Remove complete selected image records without touching image files."""
        if self.file_action_service is None or not self.selected_image_ids:
            return
        count = len(self.selected_image_ids)
        if count > self.images_per_page:
            approved = messagebox.askyesno(
                "Remove records from the catalog?",
                (
                    f"Selected catalog image records: {count:,}\n"
                    f"Current browser page size: {self.images_per_page:,}\n\n"
                    "This action affects more than one page. It removes all "
                    "captions, tags, provider results, review state, image-set "
                    "membership, and file-location records for these images.\n\n"
                    "Physical image files are not changed. A fresh database "
                    "backup is created before removal."
                ),
                parent=self,
            )
            if not approved:
                return
        selected_ids = tuple(sorted(self.selected_image_ids))
        service = self.file_action_service
        edit_service = self.edit_service

        def remove_worker(
            report: Callable[[int, int, str], None],
            cancel: object,
        ) -> tuple[Path, CatalogRemovalSummary]:
            report(0, count, "Creating a fresh catalog backup…")
            if edit_service is None:
                raise RuntimeError("Choose and load a catalog first.")
            backup = edit_service.create_backup()
            summary = service.remove_catalog_records(
                selected_ids,
                progress_callback=report,
                cancel_event=cancel,  # type: ignore[arg-type]
            )
            return backup, summary

        result = self._run_bulk_action(
            title="Removing Catalog Records",
            heading=f"Removing {count:,} complete image records",
            total=count,
            worker=remove_worker,
        )
        if result is None:
            return
        backup_path, summary = result
        self._session_backup_path = backup_path
        if summary.cancelled:
            self.edit_status_var.set(
                "Catalog record removal was cancelled; no records were removed. "
                f"Backup: {backup_path.name}"
            )
            self._notify_command_state_changed()
            return
        self.selected_image_ids.clear()
        self.anchor_image_id = None
        self.focused_image_id = None
        self._history_undo_stack.clear()
        self._history_redo_stack.clear()
        self.refresh(quiet=True)
        self.edit_status_var.set(
            f"Removed {summary.removed_images:,} complete catalog record"
            f"{'s' if summary.removed_images != 1 else ''}. "
            f"Backup: {backup_path.name}"
        )
        messagebox.showinfo(
            "Catalog records removed",
            (
                f"Complete image records removed: {summary.removed_images:,}\n"
                f"Physical image files changed: 0\n\n"
                f"Backup:\n{backup_path}"
            ),
            parent=self,
        )
        self._notify_command_state_changed()

    def _create_catalog_removal_backup(self) -> Path:
        """Create a current database snapshot before every record removal."""
        if self.edit_service is None:
            raise RuntimeError("Choose and load a catalog first.")
        backup = self.edit_service.create_backup()
        # Record removal invalidates durable Undo snapshots. Keep this new
        # backup as the session recovery point shown in later status messages.
        self._session_backup_path = backup
        return backup

    def _finish_file_action(
        self,
        label: str,
        summary: FileActionSummary,
        *,
        removal_summary: CatalogRemovalSummary | None = None,
        backup_path: Path | None = None,
    ) -> None:
        """Refresh after a file action and report partial failures honestly."""
        self.refresh(quiet=True)
        message = (
            f"{label}: {summary.completed_files:,} of "
            f"{summary.requested_files:,} physical files completed."
        )
        if summary.cancelled:
            message += " Cancelled before the remaining files were processed."
        if removal_summary is not None:
            message += (
                f" Removed {removal_summary.removed_images:,} complete "
                "catalog records."
            )
            self.selected_image_ids.intersection_update(self.records_by_id)
            self._history_undo_stack.clear()
            self._history_redo_stack.clear()
            if backup_path is not None:
                message += f" Backup: {backup_path.name}."
        self.edit_status_var.set(message)
        self._show_selection_details()
        self._notify_command_state_changed()
        if summary.errors:
            preview = "\n".join(summary.errors[:8])
            if len(summary.errors) > 8:
                preview += f"\n…and {len(summary.errors) - 8:,} more."
            messagebox.showwarning(
                f"{label} completed with errors",
                f"{message}\n\n{preview}",
                parent=self,
            )
        else:
            messagebox.showinfo(
                f"{label} complete",
                message,
                parent=self,
            )

    def _run_bulk_action(
        self,
        *,
        title: str,
        heading: str,
        total: int,
        worker: Callable[[Callable[[int, int, str], None], object], object],
    ) -> object | None:
        """Run a worker behind a modal progress surface without blocking Tk."""
        dialog = BulkActionDialog(
            self,
            title=title,
            heading=heading,
            total=max(0, int(total)),
            worker=worker,  # type: ignore[arg-type]
        )
        self.wait_window(dialog)
        if dialog.error is not None:
            error = dialog.error
            logging.exception(
                "%s failed",
                title,
                exc_info=(type(error), error, error.__traceback__),
            )
            messagebox.showerror(
                f"{title} failed",
                f"{type(error).__name__}: {error}",
                parent=self,
            )
            return None
        return dialog.result

    def _remove_unnecessary_images(self) -> None:
        """Preview and apply explainable culling to the transient selection.

        This action intentionally requires an existing selection.  Treating an
        empty selection as "select everything and then cull" would quietly turn
        a deselection tool back into the auto-selection behavior rejected during
        planning.  Select Current Page or a saved image set remains the explicit way
        to define the candidate pool.
        """
        selected_records = self._selected_records()
        if not selected_records:
            messagebox.showinfo(
                "Select candidate images",
                (
                    "Select the images you want considered first. You can use "
                    "Select Current Page or load a saved image set, then run Remove "
                    "Unnecessary Images."
                ),
                parent=self,
            )
            return

        options_dialog = CurationOptionsDialog(
            self,
            initial=self.curation_options,
        )
        self.wait_window(options_dialog)
        if options_dialog.result is None:
            return
        self.curation_options = options_dialog.result

        current_settings = load_settings()
        profile = READINESS_PROFILES_BY_KEY.get(
            current_settings.readiness_profile_key,
            READINESS_PROFILES_BY_KEY[DEFAULT_READINESS_PROFILE_KEY],
        )

        # Culling only needs evidence that meets the configured duplicate
        # threshold.  The exposed 96-100% range uses bounded hash buckets, so a
        # 14,000-image selection does not trigger an exact all-neighbor search
        # on Tk's event thread.
        nearest = duplicate_candidates_at_threshold(
            selected_records,
            current_settings.quality_duplicate_similarity_percent,
        )
        selected_records = [
            replace(
                record,
                nearest_duplicate_image_id=(
                    nearest[record.image_id][0]
                    if record.image_id in nearest
                    else None
                ),
                nearest_duplicate_similarity=(
                    nearest[record.image_id][1]
                    if record.image_id in nearest
                    else None
                ),
            )
            for record in selected_records
        ]

        criteria = CullCriteria(
            profile_label=profile.label,
            minimum_short_side=profile.minimum_short_side,
            blur_threshold=current_settings.quality_blur_threshold,
            duplicate_similarity_percent=(
                current_settings.quality_duplicate_similarity_percent
            ),
            small_face_area_ratio=self.curation_options.small_face_percent / 100.0,
            prominent_face_relative_ratio=(
                self.curation_options.prominence_percent / 100.0
            ),
            checks=CullChecks(
                already_rejected=self.curation_options.already_rejected,
                missing_or_unreadable=self.curation_options.missing_or_unreadable,
                low_resolution=self.curation_options.low_resolution,
                blur=self.curation_options.blur,
                screenshot_or_ui=self.curation_options.screenshot_or_ui,
                no_person_or_face=self.curation_options.no_person_or_face,
                subject_too_small=self.curation_options.subject_too_small,
                multiple_prominent_faces=(
                    self.curation_options.multiple_prominent_faces
                ),
                any_multiple_people_or_faces=(
                    self.curation_options.any_multiple_people_or_faces
                ),
                near_duplicates=self.curation_options.near_duplicates,
            ),
        )
        plan = build_cull_plan(selected_records, criteria)
        dialog = CullReportDialog(self, plan)
        self.wait_window(dialog)
        if not dialog.apply_requested:
            return

        before = set(self.selected_image_ids)
        removed_ids = set(plan.removed_image_ids)
        self.selected_image_ids.difference_update(removed_ids)
        if self.anchor_image_id in removed_ids:
            self.anchor_image_id = next(iter(self.selected_image_ids), None)
        if self.focused_image_id in removed_ids:
            self.focused_image_id = None
        self.edit_status_var.set(
            f"Remove Unnecessary Images deselected {len(removed_ids):,}; "
            f"{len(self.selected_image_ids):,} remain selected. "
            "Press Ctrl+Z to restore them."
        )
        self._record_selection_change(before, "Remove Unnecessary Images")
        self._selection_changed()

    def _open_image_sets(self) -> None:
        """Manage named sets using a snapshot of the current browser selection."""
        if self.catalog_path is None:
            return
        dialog = ImageSetManagerDialog(
            self,
            database_path=self.catalog_path,
            selected_image_ids=self.selected_image_ids,
            on_select_images=self._replace_selection_with_saved_image_set,
            on_sets_changed=self._notify_image_sets_changed,
        )
        self.wait_window(dialog)

    def _replace_selection_with_saved_image_set(
        self,
        image_ids: tuple[int, ...],
    ) -> None:
        """Replace transient selection with one deliberate saved image set."""
        before = set(self.selected_image_ids)
        self.selected_image_ids = set(image_ids).intersection(self.records_by_id)
        self.anchor_image_id = next(iter(self.selected_image_ids), None)
        self.edit_status_var.set(
            f"Selected {len(self.selected_image_ids):,} image"
            f"{'s' if len(self.selected_image_ids) != 1 else ''} "
            "from the saved image set."
        )
        self._record_selection_change(before, "Select saved image set")
        self._selection_changed()

    def _select_saved_image_set(self, image_ids: tuple[int, ...]) -> None:
        """Retain the pre-v0.25.2 additive callback for historical regressions."""
        before = set(self.selected_image_ids)
        existing_count = len(self.selected_image_ids)
        self.selected_image_ids.update(set(image_ids).intersection(self.records_by_id))
        self.anchor_image_id = next(iter(self.selected_image_ids), None)
        added_count = len(self.selected_image_ids) - existing_count
        self.edit_status_var.set(
            f"Added {added_count:,} image{'s' if added_count != 1 else ''} "
            "from the saved set to the current selection."
        )
        self._record_selection_change(before, "Load saved image set")
        self._selection_changed()

    def _notify_image_sets_changed(self) -> None:
        """Refresh memberships in the browser projection and readiness choices."""
        self.refresh(quiet=True)
        if self.on_image_sets_changed is not None:
            self.on_image_sets_changed()

    def _open_focused_image(self) -> None:
        if len(self.selected_image_ids) != 1:
            return
        image_id = next(iter(self.selected_image_ids))
        record = self.records_by_id.get(image_id)
        if record is not None:
            self._open_record_image(record)

    def _open_record_image(self, record: CatalogImageRecord) -> None:
        """Open the built-in large review surface at this Browser result."""
        path = record.source_path
        if path is None or not path.exists():
            messagebox.showerror(
                "Image file not found",
                (
                    "The catalog record still exists, but its preferred file "
                    f"location is unavailable:\n\n{path or '(no path stored)'}"
                ),
                parent=self,
            )
            return
        ImageReviewDialog(
            self,
            records=self.visible_records,
            initial_image_id=record.image_id,
            on_delete=self._delete_review_record,
        )

    def _delete_review_record(self, record: CatalogImageRecord) -> None:
        """Delete one reviewed image through the normal Browser action path."""
        self.selected_image_ids = {record.image_id}
        self._selection_changed()
        self.delete_selected_to_trash()

    def _show_focused_image_quality(self) -> None:
        """Explain the filter evidence for the one focused catalog image."""
        if len(self.selected_image_ids) != 1:
            return
        record = self.records_by_id.get(next(iter(self.selected_image_ids)))
        if record is None:
            return

        nearest = nearest_duplicate_candidate(
            record.image_id,
            record.perceptual_hash,
            self.all_records,
        )
        if record.sharpness_score is None:
            blur_value = "Not analyzed"
        else:
            verdict = (
                "Flagged by the current Blur threshold"
                if record.sharpness_score
                < float(self.settings.quality_blur_threshold)
                else "Not flagged by the current Blur threshold"
            )
            blur_value = (
                f"{record.sharpness_score:.1f} (higher is sharper)\n"
                f"Current threshold: {self.settings.quality_blur_threshold:g}\n"
                f"{verdict}"
            )
        duplicate_value = (
            f"Image {nearest[0]} — {nearest[1]:.1f}% similar\n"
            f"Current Possible Duplicates threshold: "
            f"{self.settings.quality_duplicate_similarity_percent}%"
            if nearest is not None
            else "No compatible perceptual-hash neighbor is available."
        )
        face_value = (
            "Not analyzed"
            if not record.face_analysis_available
            else "\n".join(
                (
                    f"Faces detected: {record.face_count}",
                    (
                        "Strongest detector confidence: "
                        f"{record.face_max_detection_score * 100:.1f}%"
                        if record.face_max_detection_score is not None
                        else "Strongest detector confidence: unavailable"
                    ),
                    (
                        "Largest face: "
                        f"{record.largest_face_area_ratio * 100:.2f}% of image"
                        if record.largest_face_area_ratio is not None
                        else "Largest face size: unavailable"
                    ),
                    (
                        f"Analyzed: {record.face_analyzed_at}"
                        if record.face_analyzed_at
                        else "Analysis time: unavailable"
                    ),
                )
            )
        )
        body_value = (
            "Not analyzed"
            if not record.body_analysis_available
            else "\n".join(
                (
                    f"Body / pose detected: {'yes' if record.body_detected else 'no'}",
                    f"Classification: "
                    f"{record.body_classification.replace('_', ' ').title() or '—'}",
                    f"Pose count: {record.body_pose_count}",
                    (
                        f"Body completeness: {record.full_body_score * 100:.1f}%"
                        if record.full_body_score is not None
                        else "Body completeness: unavailable"
                    ),
                    f"Visible face landmarks: "
                    f"{'yes' if record.body_face_visible else 'no'}",
                    (
                        "Detection strictness used: "
                        f"{record.body_detection_threshold * 100:.0f}%"
                        if record.body_detection_threshold is not None
                        else "Detection strictness used: unavailable"
                    ),
                    (
                        "Landmark visibility used: "
                        f"{record.body_visibility_threshold * 100:.0f}%"
                        if record.body_visibility_threshold is not None
                        else "Landmark visibility used: unavailable"
                    ),
                    (
                        "Full-body threshold used: "
                        f"{record.body_full_body_threshold_percent}%"
                        if record.body_full_body_threshold_percent is not None
                        else "Full-body threshold used: unavailable"
                    ),
                    (
                        f"Analyzed: {record.body_analyzed_at}"
                        if record.body_analyzed_at
                        else "Analysis time: unavailable"
                    ),
                )
            )
        )
        quality_status = record.quality_status.replace("_", " ").title() or "Not run"
        if record.quality_error:
            quality_status += f"\nError: {record.quality_error}"
        if record.quality_analyzed_at:
            quality_status += f"\nAnalyzed: {record.quality_analyzed_at}"

        ImageQualityDialog(
            self,
            filename=record.filename,
            fields=(
                ("Local sharpness / Blur", blur_value),
                ("Possible duplicate evidence", duplicate_value),
                ("Quality-analysis status", quality_status),
                ("Face evidence", face_value),
                ("Florence person count", (
                    str(record.person_count)
                    if record.person_count is not None
                    else "Not available"
                )),
                ("Body / pose evidence", body_value),
                ("Screenshot / UI evidence", record.likely_screenshot_or_ui or "Not available"),
            ),
        )


    # =========================================================================
    # Scrolling and shutdown
    # =========================================================================

    def shutdown(self) -> None:
        """Stop transient UI and thumbnail work before Tk is destroyed."""
        self._closing = True
        self._hide_drag_border()
        for attribute_name in (
            "_search_after_id",
            "_layout_after_id",
            "_load_more_after_id",
            "_thumbnail_results_after_id",
        ):
            after_id = getattr(self, attribute_name)
            if after_id is None:
                continue
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
            setattr(self, attribute_name, None)

        # PhotoImage finalizers call back into Tcl. Drop every browser-owned
        # image reference here, while shutdown is still running on Tk's main
        # thread, instead of leaving cyclic card/controller objects for a
        # background worker or later garbage-collection pass to finalize.
        try:
            self.detail_preview_label.configure(image="", text="")
        except tk.TclError:
            pass
        self._details_preview_photo = None
        for card in tuple(self.cards_by_id.values()):
            try:
                card.image_label.configure(image="", text="")
            except tk.TclError:
                pass
            card.photo_image = None
        self.decoded_thumbnail_cache.clear()
        self._destroy_cards()

        # These callbacks point back to DatasetToolsApp or its readiness frame.
        # They are ordinary Python references, so Tcl widget destruction alone
        # does not break the application/browser ownership cycle.
        self.on_image_sets_changed = None
        self.on_filter_settings_changed = None
        self.on_command_state_changed = None
        self.thumbnail_executor.shutdown(wait=False, cancel_futures=True)
