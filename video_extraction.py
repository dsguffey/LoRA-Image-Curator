"""Local FFmpeg discovery and non-destructive video-frame extraction.

This module deliberately has no Tkinter dependency.  The GUI collects choices
and reports progress, while this layer owns command construction, validation,
process cancellation, output accounting, and the boundary around the external
FFmpeg executable.

LoRA Image Curator does not bundle or download FFmpeg.  A user-approved executable
or an executable found on ``PATH`` is probed with ``-version`` before use.  The
video path and output pattern are passed as separate process arguments with
``shell=False``; filenames are never interpolated into a shell command.
"""

from __future__ import annotations

import os
import math
import queue
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Callable, Literal, Sequence

from video_origin import update_video_origin_manifest

SamplingMode = Literal["interval", "scene"]
OutputFormat = Literal["jpg", "png"]
CollisionPolicy = Literal["error", "overwrite", "skip"]
FFmpegSource = Literal["saved", "path", "manual"]
ProgressCallback = Callable[[int, int], None]

DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_MAX_FRAMES = 500
MAX_FRAME_LIMIT = 100_000
DEFAULT_FILENAME_PREFIX = "frame"


@dataclass(slots=True, frozen=True)
class FFmpegStatus:
    """Result of resolving and harmlessly probing one FFmpeg executable."""

    available: bool
    executable: Path | None
    source: FFmpegSource | None
    version_line: str
    error: str


@dataclass(slots=True, frozen=True)
class VideoExtractionOptions:
    """Validated user choices for one local extraction run."""

    ffmpeg_path: Path
    source_video: Path
    destination_folder: Path
    sampling_mode: SamplingMode = "interval"
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD
    max_frames: int = DEFAULT_MAX_FRAMES
    output_format: OutputFormat = "jpg"
    filename_prefix: str = DEFAULT_FILENAME_PREFIX
    collision_policy: CollisionPolicy = "error"


@dataclass(slots=True, frozen=True)
class VideoExtractionSummary:
    """Complete, user-reportable outcome of a successful FFmpeg command."""

    source_video: Path
    destination_folder: Path
    output_files: tuple[Path, ...]
    sampling_description: str
    output_format: OutputFormat
    maximum_frames: int
    command: tuple[str, ...]
    elapsed_seconds: float
    origin_manifest: Path | None = None
    skipped_existing_files: int = 0
    failed_outputs: int = 0

    @property
    def output_count(self) -> int:
        return len(self.output_files)

    @property
    def command_text(self) -> str:
        return format_command(self.command)


class VideoExtractionError(RuntimeError):
    """FFmpeg failed after the extraction request was validated."""

    def __init__(
        self,
        message: str,
        *,
        command: Sequence[str] = (),
        partial_output_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.command = tuple(command)
        self.partial_output_count = int(partial_output_count)


class VideoExtractionCancelled(VideoExtractionError):
    """The user cancelled an active extraction process."""


def probe_ffmpeg(
    executable: Path | str,
    *,
    source: FFmpegSource = "manual",
    timeout_seconds: float = 5.0,
) -> FFmpegStatus:
    """Validate an executable using FFmpeg's read-only ``-version`` command."""
    raw_value = str(executable).strip()
    if not raw_value:
        return FFmpegStatus(False, None, source, "", "No executable was selected.")

    resolved_text = shutil.which(raw_value)
    candidate = (
        Path(resolved_text).expanduser().resolve()
        if resolved_text
        else Path(raw_value).expanduser().resolve()
    )
    if not candidate.exists() or not candidate.is_file():
        return FFmpegStatus(
            False,
            candidate,
            source,
            "",
            f"Executable not found: {candidate}",
        )

    try:
        completed = subprocess.run(
            [str(candidate), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return FFmpegStatus(
            False,
            candidate,
            source,
            "",
            f"{type(error).__name__}: {error}",
        )

    combined = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    first_line = next((line.strip() for line in combined.splitlines() if line.strip()), "")
    if completed.returncode != 0:
        return FFmpegStatus(
            False,
            candidate,
            source,
            first_line,
            f"Version probe exited with code {completed.returncode}.",
        )
    if "ffmpeg version" not in combined.casefold():
        return FFmpegStatus(
            False,
            candidate,
            source,
            first_line,
            "The selected program did not identify itself as FFmpeg.",
        )

    return FFmpegStatus(True, candidate, source, first_line, "")


def discover_ffmpeg(saved_path: str = "") -> FFmpegStatus:
    """Try a remembered user choice, then the operating system ``PATH``.

    An invalid remembered path is not a permanent blocker.  LoRA Image Curator falls
    back to normal PATH discovery, while the dialog still displays enough
    detail for the user to replace the stale setting.
    """
    remembered_error = ""
    if saved_path.strip():
        remembered = probe_ffmpeg(saved_path, source="saved")
        if remembered.available:
            return remembered
        remembered_error = remembered.error

    path_match = shutil.which("ffmpeg")
    if path_match:
        automatic = probe_ffmpeg(path_match, source="path")
        if automatic.available:
            return automatic
        path_error = automatic.error
    else:
        path_error = "FFmpeg was not found on PATH."

    error_parts = [part for part in (remembered_error, path_error) if part]
    return FFmpegStatus(False, None, None, "", " ".join(error_parts))


def probe_video_duration(
    ffmpeg_path: Path,
    source_video: Path,
    *,
    timeout_seconds: float = 15.0,
) -> float | None:
    """Read video duration with the matching FFprobe executable when available.

    FFprobe is normally distributed beside FFmpeg.  Failure is intentionally
    non-fatal: extraction can proceed, but the UI labels the total estimate as
    unavailable instead of inventing a moving total from current progress.
    """
    executable_name = "ffprobe.exe" if ffmpeg_path.suffix.casefold() == ".exe" else "ffprobe"
    sibling = ffmpeg_path.expanduser().resolve().with_name(executable_name)
    discovered = shutil.which("ffprobe")
    probe = sibling if sibling.exists() else Path(discovered).resolve() if discovered else None
    if probe is None or not source_video.expanduser().is_file():
        return None
    try:
        completed = subprocess.run(
            [
                str(probe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source_video.expanduser().resolve()),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        duration = float(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def estimate_interval_frame_count(
    duration_seconds: float,
    interval_seconds: float,
    maximum_frames: int,
) -> int:
    """Estimate fixed-grid FFmpeg output from duration and the user's cap."""
    if duration_seconds <= 0 or interval_seconds <= 0:
        return 0
    return min(
        max(0, int(maximum_frames)),
        max(1, math.ceil(float(duration_seconds) / float(interval_seconds))),
    )


def normalize_filename_prefix(value: str) -> str:
    """Return a filesystem-safe prefix without silently changing an empty value."""
    collapsed = "_".join(value.strip().split())
    sanitized = "".join(
        character
        for character in collapsed
        if character.isalnum() or character in {"-", "_"}
    )
    if not sanitized:
        raise ValueError("Filename prefix must contain at least one letter or number.")
    if len(sanitized) > 80:
        raise ValueError("Filename prefix must be 80 characters or fewer.")
    return sanitized


def validate_extraction_options(
    options: VideoExtractionOptions,
) -> VideoExtractionOptions:
    """Resolve paths and reject unsafe or internally inconsistent choices."""
    ffmpeg_path = options.ffmpeg_path.expanduser().resolve()
    source_video = options.source_video.expanduser().resolve()
    destination = options.destination_folder.expanduser().resolve()
    prefix = normalize_filename_prefix(options.filename_prefix)

    if not ffmpeg_path.exists() or not ffmpeg_path.is_file():
        raise FileNotFoundError(f"FFmpeg executable not found: {ffmpeg_path}")
    if not source_video.exists() or not source_video.is_file():
        raise FileNotFoundError(f"Source video not found: {source_video}")
    if source_video == ffmpeg_path:
        raise ValueError("The source video cannot be the FFmpeg executable.")
    if options.sampling_mode not in {"interval", "scene"}:
        raise ValueError(f"Unsupported sampling mode: {options.sampling_mode}")
    if not 0.05 <= float(options.interval_seconds) <= 86_400.0:
        raise ValueError("Frame interval must be between 0.05 and 86,400 seconds.")
    if not 0.01 <= float(options.scene_threshold) <= 1.0:
        raise ValueError("Scene threshold must be between 0.01 and 1.00.")
    if not 1 <= int(options.max_frames) <= MAX_FRAME_LIMIT:
        raise ValueError(
            f"Maximum frames must be between 1 and {MAX_FRAME_LIMIT:,}."
        )
    if options.output_format not in {"jpg", "png"}:
        raise ValueError(f"Unsupported output format: {options.output_format}")
    if options.collision_policy not in {"error", "overwrite", "skip"}:
        raise ValueError(
            f"Unsupported output collision policy: {options.collision_policy}"
        )

    return VideoExtractionOptions(
        ffmpeg_path=ffmpeg_path,
        source_video=source_video,
        destination_folder=destination,
        sampling_mode=options.sampling_mode,
        interval_seconds=float(options.interval_seconds),
        scene_threshold=float(options.scene_threshold),
        max_frames=int(options.max_frames),
        output_format=options.output_format,
        filename_prefix=prefix,
        collision_policy=options.collision_policy,
    )


def build_ffmpeg_command(options: VideoExtractionOptions) -> tuple[str, ...]:
    """Build one deterministic command without invoking a shell."""
    checked = validate_extraction_options(options)
    extension = checked.output_format
    output_pattern = checked.destination_folder / (
        f"{checked.filename_prefix}_%06d.{extension}"
    )

    if checked.sampling_mode == "interval":
        # ``fps`` samples on a fixed time grid.  The reciprocal form avoids
        # rounding a long interval into an imprecise decimal frame rate.
        video_filter = f"fps=1/{checked.interval_seconds:.6f}"
    else:
        # The comma belongs to FFmpeg's expression grammar and must be escaped
        # for the filter parser.  It is not shell escaping; shell=False remains
        # authoritative for process safety.
        # Always include the opening frame so a valid one-scene video cannot
        # complete with an empty result merely because it contains no cut.
        video_filter = (
            "select=eq(n\\,0)+"
            f"gt(scene\\,{checked.scene_threshold:.6f})"
        )

    command = [
        str(checked.ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(checked.source_video),
        "-vf",
        video_filter,
        "-frames:v",
        str(checked.max_frames),
        "-fps_mode",
        "vfr",
    ]
    if checked.output_format == "jpg":
        command.extend(("-q:v", "2"))
    else:
        command.extend(("-compression_level", "4"))
    command.extend(("-n", str(output_pattern)))
    return tuple(command)


def format_command(command: Sequence[str]) -> str:
    """Format a command for the report without changing how it was executed."""
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def sampling_description(options: VideoExtractionOptions) -> str:
    """Return a concise human explanation of the active sampling rule."""
    if options.sampling_mode == "interval":
        return f"One frame every {options.interval_seconds:g} seconds"
    return f"Scene changes at threshold {options.scene_threshold:.2f}"


def output_glob(options: VideoExtractionOptions) -> str:
    """Return the exact glob owned by one extraction prefix and format."""
    return f"{options.filename_prefix}_*.{options.output_format}"


def run_video_extraction(
    options: VideoExtractionOptions,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> VideoExtractionSummary:
    """Run FFmpeg, report frame progress, and account for produced files.

    The caller must explicitly choose how a detected collision is handled.
    ``overwrite`` and ``skip`` first extract into a same-drive temporary folder.
    A successful overwrite then replaces only files owned by the same
    normalized prefix/format; skip merges only missing names.  Staging preserves
    deterministic numbering and keeps the prior run intact if FFmpeg fails.
    """
    checked = validate_extraction_options(options)
    checked.destination_folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(checked.destination_folder.glob(output_glob(checked)))
    if existing and checked.collision_policy == "error":
        raise FileExistsError(
            "The destination already contains "
            f"{len(existing):,} file(s) matching {output_glob(checked)!r}. "
            "Choose another folder or filename prefix; LoRA Image Curator will not "
            "overwrite or mix extraction runs."
        )
    temporary_destination: Path | None = None
    execution_options = checked
    if existing and checked.collision_policy in {"overwrite", "skip"}:
        temporary_destination = Path(
            tempfile.mkdtemp(
                prefix=".lora_image_curator_resume_",
                dir=checked.destination_folder,
            )
        )
        execution_options = replace(
            checked,
            destination_folder=temporary_destination,
            collision_policy="error",
        )

    command = build_ffmpeg_command(execution_options)
    started = time.perf_counter()
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creation_flags,
        )
    except OSError as error:
        raise VideoExtractionError(
            f"Could not start FFmpeg: {type(error).__name__}: {error}",
            command=command,
        ) from error

    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_parts: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_queue.put(line)
        stdout_queue.put(None)

    def read_stderr() -> None:
        assert process.stderr is not None
        stderr_parts.extend(process.stderr.readlines())

    stdout_thread = threading.Thread(
        target=read_stdout,
        name="ffmpeg-progress-reader",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stderr,
        name="ffmpeg-error-reader",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    last_frame = 0
    stdout_finished = False
    cancelled = False
    while process.poll() is None or not stdout_finished:
        if cancel_event is not None and cancel_event.is_set() and not cancelled:
            cancelled = True
            process.terminate()

        try:
            line = stdout_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            stdout_finished = True
            continue

        key, separator, value = line.strip().partition("=")
        if separator and key == "frame":
            try:
                frame_count = max(0, int(value))
            except ValueError:
                continue
            if frame_count != last_frame:
                last_frame = frame_count
                if progress_callback is not None:
                    progress_callback(
                        min(frame_count, checked.max_frames),
                        checked.max_frames,
                    )

    if cancelled and process.poll() is None:
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()

    return_code = process.wait()
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()
    execution_outputs = tuple(
        sorted(
            execution_options.destination_folder.glob(output_glob(execution_options)),
            key=lambda path: path.name.casefold(),
        )
    )
    error_text = "".join(stderr_parts).strip()

    if cancelled:
        location = temporary_destination or checked.destination_folder
        raise VideoExtractionCancelled(
            (
                "Extraction was cancelled. Existing destination frames were "
                "preserved. Partial frames remain for review at:\n"
                f"{location}\n\nFiles: {len(execution_outputs):,}"
            ),
            command=command,
            partial_output_count=len(execution_outputs),
        )
    if return_code != 0:
        detail = error_text or f"FFmpeg exited with code {return_code}."
        if temporary_destination is not None:
            detail += (
                "\n\nExisting destination frames were preserved. Staged partial "
                f"frames remain at:\n{temporary_destination}"
            )
        raise VideoExtractionError(
            detail,
            command=command,
            partial_output_count=len(execution_outputs),
        )
    if not execution_outputs:
        raise VideoExtractionError(
            (
                "FFmpeg completed without producing any frames. The interval "
                "may exceed the video length, or the scene threshold may be too strict."
            ),
            command=command,
        )

    skipped_existing = 0
    if temporary_destination is not None:
        published_outputs: list[Path] = []
        if checked.collision_policy == "overwrite":
            # Do not remove the prior complete run until FFmpeg has produced a
            # successful replacement in staging.
            for path in existing:
                path.unlink()
        for temporary_output in execution_outputs:
            destination_output = checked.destination_folder / temporary_output.name
            if (
                checked.collision_policy == "skip"
                and destination_output.exists()
            ):
                skipped_existing += 1
                temporary_output.unlink()
                continue
            shutil.move(str(temporary_output), str(destination_output))
            published_outputs.append(destination_output)
        temporary_destination.rmdir()
        outputs = tuple(published_outputs)
    else:
        outputs = execution_outputs

    if progress_callback is not None:
        progress_callback(
            min(last_frame, checked.max_frames),
            checked.max_frames,
        )
    origin_manifest = update_video_origin_manifest(
        destination_folder=checked.destination_folder,
        source_video=checked.source_video,
        sampling_mode=checked.sampling_mode,
        interval_seconds=checked.interval_seconds,
        output_files=outputs,
        replace_names=(
            (path.name for path in existing)
            if checked.collision_policy == "overwrite"
            else ()
        ),
    )
    return VideoExtractionSummary(
        source_video=checked.source_video,
        destination_folder=checked.destination_folder,
        output_files=outputs,
        sampling_description=sampling_description(checked),
        output_format=checked.output_format,
        maximum_frames=checked.max_frames,
        command=command,
        elapsed_seconds=time.perf_counter() - started,
        origin_manifest=origin_manifest,
        skipped_existing_files=skipped_existing,
    )


def format_extraction_summary(summary: VideoExtractionSummary) -> str:
    """Return the complete success report shown after extraction/import."""
    return "\n".join(
        (
            "Video frame extraction complete",
            "",
            f"Source: {summary.source_video}",
            f"Destination: {summary.destination_folder}",
            f"Sampling: {summary.sampling_description}",
            f"Output format: {summary.output_format.upper()}",
            f"Maximum requested: {summary.maximum_frames:,}",
            f"Frames written: {summary.output_count:,}",
            f"Existing files skipped: {summary.skipped_existing_files:,}",
            f"Failed outputs: {summary.failed_outputs:,}",
            (
                f"Video-origin manifest: {summary.origin_manifest}"
                if summary.origin_manifest is not None
                else "Video-origin manifest: unavailable"
            ),
            f"Elapsed time: {summary.elapsed_seconds:.2f} seconds",
            "",
            "Exact FFmpeg command:",
            summary.command_text,
        )
    )
