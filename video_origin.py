"""Persist and recover the source-video location of extracted still frames.

FFmpeg normally writes ordinary JPG/PNG files without enough portable metadata
to recover the originating clip position.  LoRA Image Curator therefore writes
one small JSON manifest beside an extraction run. Catalog import reads that
manifest and stores the origin against the exact file record, so the browser
details pane can later point a useful frame back to its scene.

Fixed-interval timestamps are deterministic. Scene-change timestamps remain
unknown in this release because the existing quiet FFmpeg command does not
capture presentation timestamps; the manifest still retains source identity
and extraction mode rather than inventing a time.
"""

from __future__ import annotations

import json
import os
import tempfile

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MANIFEST_FILENAME = ".lora_image_curator_video_origin.json"
MANIFEST_VERSION = 1


@dataclass(slots=True, frozen=True)
class VideoFrameOrigin:
    """Trace one extracted image back to its source-video context."""

    source_video: str
    sampling_mode: str
    timestamp_seconds: float | None
    frame_number: int | None
    interval_seconds: float | None


class VideoOriginManifestCache:
    """Read at most one manifest per image directory during a catalog run."""

    def __init__(self) -> None:
        self._directories: dict[Path, dict[str, object]] = {}

    def origin_for(self, image_path: Path) -> VideoFrameOrigin | None:
        """Return validated origin data for one image filename when available."""
        directory = image_path.expanduser().resolve().parent
        if directory not in self._directories:
            self._directories[directory] = _read_manifest(directory)
        manifest = self._directories[directory]
        frames = manifest.get("frames", {})
        if not isinstance(frames, dict):
            return None
        raw = frames.get(image_path.name)
        if not isinstance(raw, dict):
            return None
        timestamp = raw.get("timestamp_seconds")
        frame_number = raw.get("frame_number")
        interval = raw.get("interval_seconds")
        return VideoFrameOrigin(
            source_video=str(raw.get("source_video", "")),
            sampling_mode=str(raw.get("sampling_mode", "")),
            timestamp_seconds=(
                float(timestamp) if timestamp is not None else None
            ),
            frame_number=(
                int(frame_number) if frame_number is not None else None
            ),
            interval_seconds=(
                float(interval) if interval is not None else None
            ),
        )


def update_video_origin_manifest(
    *,
    destination_folder: Path,
    source_video: Path,
    sampling_mode: str,
    interval_seconds: float,
    output_files: Iterable[Path],
    replace_names: Iterable[str] = (),
) -> Path:
    """Atomically merge origin facts for successfully published output files."""
    destination = destination_folder.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(destination)
    frames = manifest.get("frames")
    if not isinstance(frames, dict):
        frames = {}
    for name in replace_names:
        frames.pop(str(name), None)

    resolved_source = str(source_video.expanduser().resolve())
    for output in output_files:
        path = output.expanduser().resolve()
        if not path.is_file():
            continue
        frame_number = _frame_number(path)
        timestamp = (
            max(0.0, (frame_number - 1) * float(interval_seconds))
            if sampling_mode == "interval" and frame_number is not None
            else None
        )
        frames[path.name] = {
            "source_video": resolved_source,
            "sampling_mode": str(sampling_mode),
            "timestamp_seconds": timestamp,
            "frame_number": frame_number,
            "interval_seconds": (
                float(interval_seconds)
                if sampling_mode == "interval"
                else None
            ),
        }

    payload = {
        "manifest_version": MANIFEST_VERSION,
        "frames": frames,
    }
    final_path = destination / MANIFEST_FILENAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_FILENAME}.",
        suffix=".tmp",
        dir=destination,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(final_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return final_path


def format_video_timestamp(seconds: float | None) -> str:
    """Format seconds as a useful clip-navigation timestamp."""
    if seconds is None:
        return "Timestamp unavailable"
    tenths = max(0, int(round(float(seconds) * 10)))
    whole_seconds, tenth = divmod(tenths, 10)
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, second = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{second:02d}.{tenth}"


def _read_manifest(directory: Path) -> dict[str, object]:
    path = directory / MANIFEST_FILENAME
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if (
        not isinstance(payload, dict)
        or int(payload.get("manifest_version", 0)) != MANIFEST_VERSION
    ):
        return {}
    return payload


def _frame_number(path: Path) -> int | None:
    """Read the final underscore-delimited integer from an extraction name."""
    stem = path.stem
    _prefix, separator, raw_number = stem.rpartition("_")
    if not separator or not raw_number.isdigit():
        return None
    number = int(raw_number)
    return number if number >= 1 else None
