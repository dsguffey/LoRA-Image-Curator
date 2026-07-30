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

from face_analyzer import (
    FaceAnalysisOptions,
    FaceBatchSummary,
    FaceProvider,
    analyze_faces,
)
from florence_analyzer import (
    BatchAnalysisSummary,
    ProgressCallback,
    StatusCallback,
    analyze_folder,
)


@dataclass(slots=True)
class PipelineSummary:
    """Combine provider results while preserving the v0.4 GUI field names."""

    florence: BatchAnalysisSummary
    face: FaceBatchSummary | None
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
    recursive: bool = True,
    face_recursive: bool = True,
    face_reference_recursive: bool = True,
    face_identity_name: str = "",
    face_reference_folder: Path | None = None,
    face_options: FaceAnalysisOptions | None = None,
    allow_face_model_download: bool = False,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    face_provider: FaceProvider | None = None,
    cancel_event: Event | None = None,
    pause_event: Event | None = None,
) -> PipelineSummary:
    """Run catalog/Florence first, then the optional face provider."""
    pipeline_start = time.perf_counter()

    if status_callback is not None:
        status_callback("Provider 1: Florence-2 caption and triage analysis")

    florence_summary = analyze_folder(
        input_folder=input_folder,
        output_folder=output_folder,
        include_triage=include_triage,
        reuse_stored_analysis=reuse_stored_analysis,
        recursive=recursive,
        progress_callback=progress_callback,
        status_callback=status_callback,
        cancel_event=cancel_event,
        pause_event=pause_event,
    )

    face_summary: FaceBatchSummary | None = None

    if run_face_analysis:
        if face_reference_folder is None:
            raise ValueError(
                "Face analysis was enabled without an identity reference folder."
            )

        if status_callback is not None:
            status_callback(
                "Provider 2: face detection, embeddings, and identity matching"
            )

        face_summary = analyze_faces(
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
        total_seconds=time.perf_counter() - pipeline_start,
    )
