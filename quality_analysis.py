"""Local, cached image-quality measurements for LoRA Image Curator.

Milestone 8B deliberately keeps measurement separate from judgment.  This
module calculates two reproducible facts for each unique catalog image:

* a variance-of-Laplacian sharpness score used by the UI's short **Blur** label
* a 64-bit difference hash used to suggest visually similar images

Neither result changes a review decision or a source file.  Exact duplicates
are already represented by the catalog's SHA-256 content identity: one image
row can have several file locations.  Perceptual-hash similarity is advisory
because crops, poses, and visually simple images can create false positives.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np

from catalog import Catalog, utc_now_text


QUALITY_ALGORITHM_VERSION = 2
DEFAULT_BLUR_THRESHOLD = 100.0
DEFAULT_DUPLICATE_SIMILARITY_PERCENT = 96
SHARPNESS_MAX_SIDE = 512
HASH_WIDTH = 9
HASH_HEIGHT = 8
HASH_BITS = (HASH_WIDTH - 1) * HASH_HEIGHT
OVERLAY_MAX_SIDE = 256
OVERLAY_MIN_RUN_FRACTION = 0.25
OVERLAY_MIN_THICKNESS_FRACTION = 0.025
OVERLAY_MIN_ASPECT_RATIO = 2.0


def duplicate_similarity_description(percentage: int) -> str:
    """Describe every valid whole-number duplicate threshold."""
    normalized = max(96, min(100, int(percentage)))
    labels = {
        96: "Looser match — catches more near-duplicates",
        97: "Similar — allows a small visual difference",
        98: "Moderate — requires a very close visual match",
        99: "Strong — only effectively equal 64-bit hashes qualify",
        100: "Exact hash match — strictest",
    }
    return f"{normalized}% — {labels[normalized]}"


@dataclass(slots=True, frozen=True)
class QualityTarget:
    """One catalog image and its preferred present file location."""

    image_id: int
    source_file_id: int | None
    source_path: Path | None
    cached: bool


@dataclass(slots=True, frozen=True)
class QualityMeasurement:
    """Provider-independent measurements derived from decoded pixels."""

    sharpness_score: float
    perceptual_hash: str
    overlay_regions_json: str


@dataclass(slots=True, frozen=True)
class QualityProgress:
    """Immutable progress snapshot safe to pass across a GUI thread boundary."""

    completed: int
    total: int
    current_path: Path | None
    analyzed: int
    reused: int
    failed: int


@dataclass(slots=True, frozen=True)
class QualityAnalysisSummary:
    """Final counts for one manual-start quality-analysis run."""

    total_images: int
    analyzed_images: int
    reused_images: int
    failed_images: int
    cancelled: bool
    total_seconds: float


ProgressCallback = Callable[[QualityProgress], None]


class QualityCancellationToken:
    """Thread-safe cooperative cancellation checked between images."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


def measure_image_quality(image_path: Path) -> QualityMeasurement:
    """Decode one image and calculate stable, dependency-light measurements.

    Pillow is already a core LoRA Image Curator dependency.  Using it here avoids a
    second computer-vision stack merely to obtain a blur heuristic.  The
    sharpness calculation samples a maximum 512-pixel side so scores remain
    practical to compute for large source files and broadly comparable across
    resolutions.
    """
    try:
        with Image.open(image_path) as source:
            oriented = ImageOps.exif_transpose(source)
            grayscale = oriented.convert("L")
            grayscale.thumbnail(
                (SHARPNESS_MAX_SIDE, SHARPNESS_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            sharpness = _variance_of_laplacian(grayscale)
            perceptual_hash = _difference_hash(oriented)
            overlay_regions_json = _detect_overlay_regions(oriented)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"Could not decode image: {error}") from error

    return QualityMeasurement(
        sharpness_score=sharpness,
        perceptual_hash=perceptual_hash,
        overlay_regions_json=overlay_regions_json,
    )


def _detect_overlay_regions(image: Image.Image) -> str:
    """Return conservative normalized rectangles for obvious neutral bars.

    This intentionally does not claim to recognize arbitrary occlusion. It
    finds long, dark or mid-gray, low-saturation runs typical of censor bars,
    title banners, and other synthetic overlays. OCR boxes remain a separate
    evidence source and are combined later with face/body geometry.
    """
    sample = image.convert("RGB")
    sample.thumbnail((OVERLAY_MAX_SIDE, OVERLAY_MAX_SIDE), Image.Resampling.LANCZOS)
    pixels = np.asarray(sample, dtype=np.int16)
    height, width = pixels.shape[:2]
    if width < 8 or height < 8:
        return "[]"

    channel_range = pixels.max(axis=2) - pixels.min(axis=2)
    luminance = (
        pixels[:, :, 0] * 299
        + pixels[:, :, 1] * 587
        + pixels[:, :, 2] * 114
    ) / 1000.0
    neutral_overlay = (channel_range <= 30) & (luminance <= 190)

    rectangles = _long_overlay_runs(neutral_overlay, transpose=False)
    rectangles.extend(_long_overlay_runs(neutral_overlay, transpose=True))
    rectangles = _deduplicate_rectangles(rectangles)
    payload = [
        {
            "kind": "bar",
            "x1": x1 / width,
            "y1": y1 / height,
            "x2": x2 / width,
            "y2": y2 / height,
        }
        for x1, y1, x2, y2 in rectangles
    ]
    return json.dumps(payload, separators=(",", ":"))


def _long_overlay_runs(
    mask: np.ndarray,
    *,
    transpose: bool,
) -> list[tuple[int, int, int, int]]:
    """Group long same-tone runs into horizontal or vertical bar rectangles."""
    working = mask.T if transpose else mask
    row_count, column_count = working.shape
    minimum_run = max(4, round(column_count * OVERLAY_MIN_RUN_FRACTION))
    maximum_gap = max(2, round(row_count * 0.035))
    rows: list[tuple[int, int, int]] = []
    for row_index in range(row_count):
        padded = np.pad(working[row_index], (1, 1), constant_values=False)
        transitions = np.flatnonzero(padded[1:] != padded[:-1])
        if transitions.size < 2:
            continue
        starts = transitions[0::2]
        ends = transitions[1::2]
        lengths = ends - starts
        longest_index = int(np.argmax(lengths))
        if int(lengths[longest_index]) >= minimum_run:
            rows.append(
                (row_index, int(starts[longest_index]), int(ends[longest_index]))
            )

    groups: list[list[tuple[int, int, int]]] = []
    for candidate in rows:
        if not groups:
            groups.append([candidate])
            continue
        previous = groups[-1][-1]
        overlap = min(previous[2], candidate[2]) - max(previous[1], candidate[1])
        shorter = min(previous[2] - previous[1], candidate[2] - candidate[1])
        if candidate[0] - previous[0] <= maximum_gap and overlap >= shorter * 0.5:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    rectangles: list[tuple[int, int, int, int]] = []
    minimum_thickness = max(
        2,
        round(row_count * OVERLAY_MIN_THICKNESS_FRACTION),
    )
    for group in groups:
        first_row = group[0][0]
        last_row = group[-1][0] + 1
        if last_row - first_row < minimum_thickness:
            continue
        left = min(item[1] for item in group)
        right = max(item[2] for item in group)
        run_length = right - left
        thickness = last_row - first_row
        if run_length / max(1, thickness) < OVERLAY_MIN_ASPECT_RATIO:
            continue
        rectangle = (
            (first_row, left, last_row, right)
            if transpose
            else (left, first_row, right, last_row)
        )
        rectangles.append(rectangle)
    return rectangles


def _deduplicate_rectangles(
    rectangles: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Drop near-contained bar fragments while retaining distinct overlays."""
    ordered = sorted(
        rectangles,
        key=lambda item: (item[2] - item[0]) * (item[3] - item[1]),
        reverse=True,
    )
    kept: list[tuple[int, int, int, int]] = []
    for candidate in ordered:
        area = max(1, (candidate[2] - candidate[0]) * (candidate[3] - candidate[1]))
        if any(
            max(0, min(candidate[2], existing[2]) - max(candidate[0], existing[0]))
            * max(0, min(candidate[3], existing[3]) - max(candidate[1], existing[1]))
            >= area * 0.8
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def perceptual_hash_similarity(first_hash: str, second_hash: str) -> float:
    """Return the equal-bit percentage for two compatible 64-bit hashes."""
    first = _parse_hash(first_hash)
    second = _parse_hash(second_hash)
    return _integer_hash_similarity(first, second)


def _integer_hash_similarity(first: int, second: int) -> float:
    """Compare already-validated hashes without reparsing inside pairwise loops."""
    differing_bits = (first ^ second).bit_count()
    return ((HASH_BITS - differing_bits) / HASH_BITS) * 100.0


def nearest_duplicate_candidates(
    records: Iterable[object],
) -> dict[int, tuple[int, float]]:
    """Return each analyzed image's closest perceptual-hash neighbor.

    A metric BK-tree keeps this exact while avoiding the quadratic all-pairs
    comparison that made a 14,000-image Browser refresh perform roughly
    98 million comparisons. Duplicate hashes share one tree node, and
    deterministic image-ID tie breaking keeps reports stable.
    """
    hashed = _parsed_record_hashes(records)
    if len(hashed) < 2:
        return {}

    root = _BKHashNode(hashed[0][1], hashed[0][0])
    for image_id, hash_value in hashed[1:]:
        root.insert(hash_value, image_id)

    nearest: dict[int, tuple[int, float]] = {}
    for image_id, hash_value in hashed:
        candidate = root.nearest(hash_value, exclude_image_id=image_id)
        if candidate is None:
            continue
        candidate_id, distance = candidate
        nearest[image_id] = (
            candidate_id,
            ((HASH_BITS - distance) / HASH_BITS) * 100.0,
        )
    return nearest


def nearest_duplicate_candidate(
    image_id: int,
    perceptual_hash: str,
    records: Iterable[object],
) -> tuple[int, float] | None:
    """Find one image's closest neighbor without enriching the whole catalog."""
    try:
        target_hash = _parse_hash(perceptual_hash)
    except ValueError:
        return None

    best: tuple[int, int] | None = None
    for candidate_id, candidate_hash in _parsed_record_hashes(records):
        if candidate_id == int(image_id):
            continue
        distance = (target_hash ^ candidate_hash).bit_count()
        if (
            best is None
            or distance < best[1]
            or (distance == best[1] and candidate_id < best[0])
        ):
            best = (candidate_id, distance)
    if best is None:
        return None
    return (
        best[0],
        ((HASH_BITS - best[1]) / HASH_BITS) * 100.0,
    )


def duplicate_candidates_at_threshold(
    records: Iterable[object],
    similarity_threshold: float,
) -> dict[int, tuple[int, float]]:
    """Return one qualifying direct neighbor per image at a bounded threshold.

    The application exposes 96-100%, where at most two differing bits qualify.
    Segment buckets generate only plausible pairs rather than enumerating 2,081
    bit-flip variants for every hash or scanning all pairs.
    """
    hashed = _parsed_record_hashes(records)
    threshold = max(0.0, min(100.0, float(similarity_threshold)))
    maximum_distance = int(
        ((100.0 - threshold) * HASH_BITS / 100.0) + 1e-12
    )
    if maximum_distance > 2:
        nearest = nearest_duplicate_candidates(
            _HashRecordProxy(image_id, hash_value)
            for image_id, hash_value in hashed
        )
        return {
            image_id: candidate
            for image_id, candidate in nearest.items()
            if candidate[1] >= threshold
        }

    candidates: dict[int, tuple[int, float]] = {}
    for first, second in _candidate_pairs_within_distance(
        hashed,
        maximum_distance,
    ):
        first_id, first_hash = first
        second_id, second_hash = second
        distance = (first_hash ^ second_hash).bit_count()
        if distance > maximum_distance:
            continue
        similarity = ((HASH_BITS - distance) / HASH_BITS) * 100.0
        _retain_better_candidate(candidates, first_id, second_id, similarity)
        _retain_better_candidate(candidates, second_id, first_id, similarity)
    return candidates


def duplicate_candidate_clusters(
    records: Iterable[object],
    similarity_threshold: float,
) -> tuple[tuple[int, ...], ...]:
    """Return non-overlapping perceptual-similarity review groups.

    A duplicate-review screen needs complete comparison groups rather than one
    nearest-neighbor pointer per image.  This function therefore treats every
    threshold-meeting pair as an undirected relationship and returns the
    connected components containing at least two images.  For example, when A
    resembles B and B resembles C, all three belong to one review group even
    if A and C do not directly meet the threshold.

    The result is advisory.  It does not change review decisions, image-set
    membership, catalog rows, or source files.  Integer image IDs keep the
    function independent of the GUI and make it straightforward to regression
    test.
    """
    hashed = _parsed_record_hashes(records)

    if len(hashed) < 2:
        return ()

    parent = {image_id: image_id for image_id, _value in hashed}

    def find(image_id: int) -> int:
        while parent[image_id] != image_id:
            parent[image_id] = parent[parent[image_id]]
            image_id = parent[image_id]
        return image_id

    def union(first_id: int, second_id: int) -> None:
        first_root = find(first_id)
        second_root = find(second_id)
        if first_root != second_root:
            # Deterministic roots make test output and UI grouping stable.
            smaller, larger = sorted((first_root, second_root))
            parent[larger] = smaller

    threshold = max(0.0, min(100.0, float(similarity_threshold)))
    maximum_distance = int(
        ((100.0 - threshold) * HASH_BITS / 100.0) + 1e-12
    )
    if maximum_distance <= 2:
        # At the bounded UI thresholds, segment buckets generate only plausible
        # Hamming-neighborhood pairs. This stays close to linear on large,
        # varied catalogs instead of performing 29 million dictionary probes
        # for 14,000 radius-two hashes.
        for first, second in _candidate_pairs_within_distance(
            hashed,
            maximum_distance,
        ):
            first_id, first_hash = first
            second_id, second_hash = second
            if (first_hash ^ second_hash).bit_count() <= maximum_distance:
                union(first_id, second_id)
    else:
        # Broader programmatic thresholds remain compatible, although the UI
        # deliberately does not offer them.
        for index, (first_id, first_hash) in enumerate(hashed):
            for second_id, second_hash in hashed[index + 1 :]:
                if _integer_hash_similarity(first_hash, second_hash) >= threshold:
                    union(first_id, second_id)

    components: dict[int, list[int]] = {}
    for image_id, _value in hashed:
        components.setdefault(find(image_id), []).append(image_id)

    groups = [tuple(sorted(members)) for members in components.values() if len(members) >= 2]
    return tuple(sorted(groups, key=lambda members: (members[0], members)))


def _parsed_record_hashes(
    records: Iterable[object],
) -> list[tuple[int, int]]:
    """Normalize valid record hashes once for indexed comparison helpers."""
    hashed: list[tuple[int, int]] = []
    for record in records:
        image_id = int(getattr(record, "image_id"))
        value = str(getattr(record, "perceptual_hash", "") or "")
        try:
            parsed = _parse_hash(value)
        except ValueError:
            continue
        hashed.append((image_id, parsed))
    return hashed


def _candidate_pairs_within_distance(
    hashed: list[tuple[int, int]],
    maximum_distance: int,
) -> Iterable[tuple[tuple[int, int], tuple[int, int]]]:
    """Yield possible close pairs once using the pigeonhole principle."""
    if maximum_distance <= 0:
        ids_by_hash: dict[int, list[tuple[int, int]]] = {}
        for item in hashed:
            ids_by_hash.setdefault(item[1], []).append(item)
        for items in ids_by_hash.values():
            for index, first in enumerate(items):
                for second in items[index + 1 :]:
                    yield first, second
        return

    segment_count = maximum_distance + 1
    base_width, remainder = divmod(HASH_BITS, segment_count)
    segments: list[tuple[int, int]] = []
    offset = 0
    for index in range(segment_count):
        width = base_width + (1 if index < remainder else 0)
        segments.append((offset, (1 << width) - 1))
        offset += width

    buckets: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for item in hashed:
        _image_id, hash_value = item
        for segment_index, (shift, mask) in enumerate(segments):
            key = (segment_index, (hash_value >> shift) & mask)
            buckets.setdefault(key, []).append(item)

    emitted: set[tuple[int, int]] = set()
    for items in buckets.values():
        for index, first in enumerate(items):
            for second in items[index + 1 :]:
                pair_key = tuple(sorted((first[0], second[0])))
                if pair_key in emitted:
                    continue
                emitted.add(pair_key)
                yield first, second


def _retain_better_candidate(
    candidates: dict[int, tuple[int, float]],
    image_id: int,
    candidate_id: int,
    similarity: float,
) -> None:
    current = candidates.get(image_id)
    if (
        current is None
        or similarity > current[1]
        or (similarity == current[1] and candidate_id < current[0])
    ):
        candidates[image_id] = (candidate_id, similarity)


class _HashRecordProxy:
    """Adapter for reusing the exact BK-tree path with parsed integer hashes."""

    __slots__ = ("image_id", "perceptual_hash")

    def __init__(self, image_id: int, hash_value: int) -> None:
        self.image_id = image_id
        self.perceptual_hash = f"{hash_value:016x}"


class _BKHashNode:
    """Metric-tree node for exact nearest-neighbor Hamming lookup."""

    __slots__ = ("hash_value", "image_ids", "children")

    def __init__(self, hash_value: int, image_id: int) -> None:
        self.hash_value = hash_value
        self.image_ids = [image_id]
        self.children: dict[int, _BKHashNode] = {}

    def insert(self, hash_value: int, image_id: int) -> None:
        node = self
        while True:
            distance = (node.hash_value ^ hash_value).bit_count()
            if distance == 0:
                node.image_ids.append(image_id)
                node.image_ids.sort()
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKHashNode(hash_value, image_id)
                return
            node = child

    def nearest(
        self,
        hash_value: int,
        *,
        exclude_image_id: int,
    ) -> tuple[int, int] | None:
        best_id: int | None = None
        best_distance = HASH_BITS + 1
        pending = [self]
        while pending:
            node = pending.pop()
            distance = (node.hash_value ^ hash_value).bit_count()
            candidate_id = next(
                (
                    image_id
                    for image_id in node.image_ids
                    if image_id != exclude_image_id
                ),
                None,
            )
            if candidate_id is not None and (
                distance < best_distance
                or (
                    distance == best_distance
                    and (best_id is None or candidate_id < best_id)
                )
            ):
                best_id = candidate_id
                best_distance = distance

            lower = max(0, distance - best_distance)
            upper = min(HASH_BITS, distance + best_distance)
            pending.extend(
                child
                for edge_distance, child in node.children.items()
                if lower <= edge_distance <= upper
            )
        if best_id is None:
            return None
        return best_id, best_distance


def analyze_catalog_quality(
    database_path: Path,
    *,
    reanalyze_all: bool = False,
    cancellation: QualityCancellationToken | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
) -> QualityAnalysisSummary:
    """Analyze one catalog in the calling worker thread.

    Successful results from the current algorithm version are reused unless
    the user explicitly requests a full rerun.  Each new result is committed
    independently, so cancellation or one corrupt image cannot discard work
    already completed.
    """
    started = time.perf_counter()
    token = cancellation or QualityCancellationToken()
    database_path = database_path.expanduser().resolve()

    # Opening through Catalog applies the additive schema migration before the
    # worker begins short, isolated result transactions.
    with Catalog(database_path):
        pass

    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        targets = _load_targets(connection)
        analyzed = 0
        reused = 0
        failed = 0
        completed = 0

        for target in targets:
            while pause_event is not None and pause_event.is_set():
                if token.cancelled or (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    break
                time.sleep(0.10)
            if token.cancelled or (
                cancel_event is not None and cancel_event.is_set()
            ):
                break

            if target.cached and not reanalyze_all:
                reused += 1
            elif target.source_path is None:
                failed += 1
                _store_error(
                    connection,
                    target,
                    "No present source file is available for quality analysis.",
                )
            else:
                try:
                    measurement = measure_image_quality(target.source_path)
                    _store_success(connection, target, measurement)
                    analyzed += 1
                except (OSError, ValueError) as error:
                    failed += 1
                    _store_error(connection, target, str(error))

            completed += 1
            if progress_callback is not None:
                progress_callback(
                    QualityProgress(
                        completed=completed,
                        total=len(targets),
                        current_path=target.source_path,
                        analyzed=analyzed,
                        reused=reused,
                        failed=failed,
                    )
                )

        return QualityAnalysisSummary(
            total_images=len(targets),
            analyzed_images=analyzed,
            reused_images=reused,
            failed_images=failed,
            cancelled=(
                token.cancelled
                or (cancel_event is not None and cancel_event.is_set())
            ),
            total_seconds=time.perf_counter() - started,
        )
    finally:
        connection.close()


def _load_targets(connection: sqlite3.Connection) -> list[QualityTarget]:
    """Project one preferred present path and current cache state per image."""
    rows = connection.execute(
        """
        SELECT
            i.id AS image_id,
            preferred.file_id,
            preferred.absolute_path,
            CASE
                WHEN quality.status = 'success'
                 AND quality.algorithm_version = ?
                THEN 1 ELSE 0
            END AS cached
        FROM images AS i
        LEFT JOIN (
            SELECT f.image_id, f.id AS file_id, f.absolute_path
            FROM files AS f
            WHERE f.status = 'present'
              AND f.id = (
                  SELECT f2.id
                  FROM files AS f2
                  WHERE f2.image_id = f.image_id
                    AND f2.status = 'present'
                  ORDER BY f2.last_seen_at DESC, f2.id DESC
                  LIMIT 1
              )
        ) AS preferred ON preferred.image_id = i.id
        LEFT JOIN image_quality_results AS quality ON quality.image_id = i.id
        ORDER BY i.id
        """,
        (QUALITY_ALGORITHM_VERSION,),
    ).fetchall()

    targets: list[QualityTarget] = []
    for row in rows:
        raw_path = str(row["absolute_path"] or "")
        source_path: Path | None = None
        if raw_path:
            # Windows catalogs can still be inspected on Linux during tests.
            # Do not reinterpret a Windows drive path as a local relative path.
            candidate = Path(raw_path)
            if not PureWindowsPath(raw_path).drive or candidate.exists():
                source_path = candidate
        targets.append(
            QualityTarget(
                image_id=int(row["image_id"]),
                source_file_id=(int(row["file_id"]) if row["file_id"] is not None else None),
                source_path=source_path,
                cached=bool(row["cached"]),
            )
        )
    return targets


def _store_success(
    connection: sqlite3.Connection,
    target: QualityTarget,
    measurement: QualityMeasurement,
) -> None:
    connection.execute(
        """
        INSERT INTO image_quality_results(
            image_id, source_file_id, algorithm_version, sharpness_score,
            perceptual_hash, overlay_regions_json, status, error, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'success', '', ?)
        ON CONFLICT(image_id) DO UPDATE SET
            source_file_id = excluded.source_file_id,
            algorithm_version = excluded.algorithm_version,
            sharpness_score = excluded.sharpness_score,
            perceptual_hash = excluded.perceptual_hash,
            overlay_regions_json = excluded.overlay_regions_json,
            status = excluded.status,
            error = excluded.error,
            analyzed_at = excluded.analyzed_at
        """,
        (
            target.image_id,
            target.source_file_id,
            QUALITY_ALGORITHM_VERSION,
            measurement.sharpness_score,
            measurement.perceptual_hash,
            measurement.overlay_regions_json,
            utc_now_text(),
        ),
    )
    connection.commit()


def _store_error(
    connection: sqlite3.Connection,
    target: QualityTarget,
    error: str,
) -> None:
    connection.execute(
        """
        INSERT INTO image_quality_results(
            image_id, source_file_id, algorithm_version, sharpness_score,
            perceptual_hash, overlay_regions_json, status, error, analyzed_at
        ) VALUES (?, ?, ?, NULL, '', '[]', 'error', ?, ?)
        ON CONFLICT(image_id) DO UPDATE SET
            source_file_id = excluded.source_file_id,
            algorithm_version = excluded.algorithm_version,
            sharpness_score = NULL,
            perceptual_hash = '',
            overlay_regions_json = '[]',
            status = 'error',
            error = excluded.error,
            analyzed_at = excluded.analyzed_at
        """,
        (
            target.image_id,
            target.source_file_id,
            QUALITY_ALGORITHM_VERSION,
            error[:2000],
            utc_now_text(),
        ),
    )
    connection.commit()


def _variance_of_laplacian(image: Image.Image) -> float:
    """Calculate the population variance of a four-neighbor Laplacian."""
    width, height = image.size
    if width < 3 or height < 3:
        return 0.0

    pixels = image.tobytes()
    total = 0.0
    squared_total = 0.0
    count = (width - 2) * (height - 2)
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            index = row + x
            value = (
                4 * pixels[index]
                - pixels[index - 1]
                - pixels[index + 1]
                - pixels[index - width]
                - pixels[index + width]
            )
            total += value
            squared_total += value * value

    mean = total / count
    return max(0.0, (squared_total / count) - (mean * mean))


def _difference_hash(image: Image.Image) -> str:
    """Return a 16-character hexadecimal horizontal difference hash."""
    sample = image.convert("L").resize(
        (HASH_WIDTH, HASH_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    # ``Image.getdata()`` is deprecated in newer Pillow releases.  The sample
    # is guaranteed to be 8-bit grayscale, so ``tobytes`` provides the same
    # flat integer sequence without a version-specific compatibility branch.
    pixels = sample.tobytes()
    value = 0
    for y in range(HASH_HEIGHT):
        row = y * HASH_WIDTH
        for x in range(HASH_WIDTH - 1):
            value <<= 1
            if pixels[row + x] > pixels[row + x + 1]:
                value |= 1
    return f"{value:016x}"


def _parse_hash(value: str) -> int:
    normalized = value.strip().casefold()
    if len(normalized) != HASH_BITS // 4:
        raise ValueError("Perceptual hash must contain exactly 16 hexadecimal characters.")
    try:
        return int(normalized, 16)
    except ValueError as error:
        raise ValueError("Perceptual hash is not hexadecimal.") from error
