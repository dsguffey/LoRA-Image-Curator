"""
analysis_pipeline.py

Thin orchestration layer for LoRA Image Curator analysis providers.

The GUI talks to this module rather than directly to Florence or InsightFace.
That boundary is intentionally modest in version 0.5, but it prevents the GUI
from becoming the place where provider-specific logic accumulates.  Future
caption, embedding, quality, or export providers can be added here (and later
moved into a fuller provider registry) without rewriting the user interface.
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

from analysis_control import AnalysisCancelled
from quality_analysis import QualityAnalysisSummary, analyze_catalog_quality


ProgressCallback = Callable[[str, int, int, Path], None]
StatusCallback = Callable[[str], None]


@dataclass(slots=True)
class PipelineSummary:
    """Combine provider results while preserving the v0.4 GUI field names."""

    florence: Any
    face: Any | None
    quality: QualityAnalysisSummary | None
    total_seconds: float

    # These properties let the existing completion UI keep using familiar names
    # while new face-specific fields remain explicitly grouped under ``face``.
    @property
    def total_images(self) -> int:
        return self.florence.total_images

    @property
    def successful_images(self) -> int:
        return self.florence.successful_images

    @property
    def failed_images(self) -> int:
        return self.florence.failed_images

    @property
    def output_csv(self) -> Path:
        return self.florence.output_csv

    @property
    def catalog_database(self) -> Path:
        return self.florence.catalog_database

    @property
    def new_unique_images(self) -> int:
        return self.florence.new_unique_images

    @property
    def new_locations_existing_images(self) -> int:
        return self.florence.new_locations_existing_images

    @property
    def unchanged_files(self) -> int:
        return self.florence.unchanged_files

    @property
    def changed_files(self) -> int:
        return self.florence.changed_files

    @property
    def missing_files_marked(self) -> int:
        return self.florence.missing_files_marked

    @property
    def reused_analyses(self) -> int:
        return self.florence.reused_analyses

    @property
    def generated_analyses(self) -> int:
        return self.florence.generated_analyses

    @property
    def catalog_unique_images(self) -> int:
        return self.florence.catalog_unique_images

    @property
    def catalog_file_locations(self) -> int:
        return self.florence.catalog_file_locations


def run_pipeline(
    *,
    input_folder: Path,
    output_folder: Path,
    include_triage: bool,
    reuse_stored_analysis: bool,
    run_face_analysis: bool,
    run_quality_analysis: bool = True,
    allow_florence_model_download: bool = False,
    recursive: bool = True,
    face_recursive: bool = True,
    face_reference_recursive: bool = True,
    face_identity_name: str = "",
    face_reference_folder: Path | None = None,
    face_options: Any | None = None,
    allow_face_model_download: bool = False,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    face_provider: Any | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
    florence_runner: Callable[..., Any] | None = None,
    face_runner: Callable[..., Any] | None = None,
    quality_runner: Callable[..., QualityAnalysisSummary] | None = None,
) -> PipelineSummary:
    """Update the catalog, run quality, then run the selected providers."""
    pipeline_start = time.perf_counter()
    quality_summary: QualityAnalysisSummary | None = None
    if florence_runner is None:
        from florence_analyzer import analyze_folder

        florence_runner = analyze_folder
    if quality_runner is None:
        quality_runner = analyze_catalog_quality

    def run_quality(catalog_database: Path) -> None:
        nonlocal quality_summary
        if not run_quality_analysis:
            return
        if status_callback is not None:
            status_callback("Quality analysis: local sharpness and duplicate evidence")
        quality_summary = quality_runner(
            catalog_database,
            progress_callback=(
                lambda progress: progress_callback(
                    "Quality analysis",
                    progress.completed,
                    progress.total,
                    progress.current_path or catalog_database,
                )
                if progress_callback is not None
                else None
            ),
            cancel_event=cancel_event,
            pause_event=pause_event,
        )
        if quality_summary.cancelled:
            raise AnalysisCancelled(
                "The analysis run was cancelled during Quality Analysis. "
                "Completed measurements remain stored in the catalog."
            )

    if status_callback is not None:
        status_callback("Updating catalog before local analysis providers")

    florence_summary = florence_runner(
        input_folder=input_folder,
        output_folder=output_folder,
        include_triage=include_triage,
        reuse_stored_analysis=reuse_stored_analysis,
        recursive=recursive,
        allow_model_download=allow_florence_model_download,
        progress_callback=progress_callback,
        status_callback=status_callback,
        catalog_ready_callback=run_quality,
        cancel_event=cancel_event,
        pause_event=pause_event,
    )

    face_summary: Any | None = None

    if run_face_analysis:
        if face_runner is None:
            from face_analyzer import analyze_faces

            face_runner = analyze_faces
        if status_callback is not None:
            status_callback(
                "Provider 2: face detection and optional identity matching"
            )

        face_summary = face_runner(
            input_folder=input_folder,
            output_folder=output_folder,
            identity_name=face_identity_name,
            reference_folder=face_reference_folder,
            options=face_options,
            reuse_stored_analysis=reuse_stored_analysis,
            recursive=face_recursive,
            reference_recursive=face_reference_recursive,
            allow_model_download=allow_face_model_download,
            progress_callback=progress_callback,
            status_callback=status_callback,
            provider=face_provider,
            cancel_event=cancel_event,
            pause_event=pause_event,
        )

    return PipelineSummary(
        florence=florence_summary,
        face=face_summary,
        quality=quality_summary,
        total_seconds=time.perf_counter() - pipeline_start,
    )
