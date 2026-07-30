"""Central Settings window for paths, analysis, filtering, and privacy.

The dialog is the durable home for values that interpret provider and quality
results. Browser filter checkboxes remain in the Browser because they decide
which records are visible; shared thresholds live here so Browser and Finalize
cannot quietly drift onto different rules.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk

from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from body_analysis import BodyProviderStatus
from body_setup_dialog import BodySetupDialog
from dataset_readiness import READINESS_PROFILES, READINESS_PROFILES_BY_KEY
from quality_analysis import duplicate_similarity_description
from settings_manager import (
    AppSettings,
    get_default_body_model_path,
    get_default_quarantine_directory,
    get_settings_directory,
)
from ui_fonts import get_ui_font


class SettingsDialog(tk.Toplevel):
    """Edit durable application preferences without touching catalog data."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        settings: AppSettings,
        on_save: Callable[[AppSettings], None],
        initial_section: str = "paths",
    ) -> None:
        super().__init__(parent)
        self.title("LoRA Image Curator Settings")
        self.geometry("820x650")
        self.minsize(740, 560)
        self.transient(parent.winfo_toplevel())
        self._original = settings
        self._on_save = on_save
        self._initial_section = initial_section

        self.quarantine_var = tk.StringVar(
            value=settings.quarantine_directory
            or str(get_default_quarantine_directory())
        )
        self.catalog_subfolders_var = tk.BooleanVar(
            value=settings.catalog_import_include_subfolders
        )
        self.caption_subfolders_var = tk.BooleanVar(
            value=settings.caption_include_subfolders
        )
        self.face_subfolders_var = tk.BooleanVar(
            value=settings.face_include_subfolders
        )
        self.face_reference_subfolders_var = tk.BooleanVar(
            value=settings.face_reference_include_subfolders
        )
        self.include_triage_var = tk.BooleanVar(value=settings.include_triage)
        self.run_face_analysis_var = tk.BooleanVar(
            value=settings.run_face_analysis
        )
        self.face_identity_var = tk.StringVar(value=settings.face_identity_name)
        self.face_reference_var = tk.StringVar(
            value=settings.face_reference_folder
        )
        self.face_model_name_var = tk.StringVar(value=settings.face_model_name)
        self.face_model_root_var = tk.StringVar(value=settings.face_model_root)
        self.face_similarity_var = tk.StringVar(
            value=f"{settings.face_similarity_threshold:.2f}"
        )
        self.face_detection_var = tk.StringVar(
            value=f"{settings.face_detection_threshold:.2f}"
        )
        self.ffmpeg_path_var = tk.StringVar(value=settings.video_ffmpeg_path)
        self.body_provider_var = tk.StringVar(value="Google MediaPipe Pose Landmarker")
        self.body_model_var = tk.StringVar(
            value=settings.body_model_path
            or str(get_default_body_model_path())
        )
        self.detection_percent_var = tk.IntVar(
            value=round(settings.body_detection_threshold * 100)
        )
        self.visibility_percent_var = tk.IntVar(
            value=round(settings.body_landmark_visibility_threshold * 100)
        )
        self.full_body_percent_var = tk.IntVar(
            value=settings.body_full_body_threshold_percent
        )
        self.blur_threshold_var = tk.IntVar(
            value=round(settings.quality_blur_threshold)
        )
        selected_profile = READINESS_PROFILES_BY_KEY.get(
            settings.readiness_profile_key,
            READINESS_PROFILES[0],
        )
        self.readiness_profile_var = tk.StringVar(value=selected_profile.label)
        self.duplicate_similarity_var = tk.IntVar(
            value=int(settings.quality_duplicate_similarity_percent)
        )
        self.duplicate_description_var = tk.StringVar()
        self.telemetry_var = tk.BooleanVar(
            value=settings.allow_provider_telemetry
        )
        self.delete_catalog_record_var = tk.BooleanVar(
            value=settings.delete_catalog_record_with_file
        )
        self.remember_paths_var = tk.BooleanVar(value=settings.remember_paths)
        self.reuse_analysis_var = tk.BooleanVar(
            value=settings.reuse_stored_analysis
        )
        self.setup_status_var = tk.StringVar(value="Not checked")
        self._slider_description_vars: list[tk.StringVar] = []
        self.blur_description_var = tk.StringVar()

        self._build_interface()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _build_interface(self) -> None:
        # Grid reserves a permanent footer row. The previous pack order let a
        # tall Notebook claim the complete Windows client area at some DPI and
        # font combinations, leaving the Save/Cancel row below the visible
        # window even though both buttons existed.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self)
        notebook.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=12,
            pady=(12, 8),
        )
        self.notebook = notebook

        paths = ttk.Frame(notebook, padding=14)
        captioning = ttk.Frame(notebook, padding=14)
        face = ttk.Frame(notebook, padding=14)
        body = ttk.Frame(notebook, padding=14)
        video = ttk.Frame(notebook, padding=14)
        filters = ttk.Frame(notebook, padding=14)
        privacy = ttk.Frame(notebook, padding=14)
        notebook.add(paths, text="Catalog & Paths")
        notebook.add(captioning, text="Image Captioning")
        notebook.add(face, text="Face Scanning")
        notebook.add(body, text="Body / Pose")
        notebook.add(video, text="Video")
        notebook.add(filters, text="Filter Settings")
        notebook.add(privacy, text="Privacy & Diagnostics")
        tabs = {
            "paths": paths,
            "catalog": paths,
            "captioning": captioning,
            "image_captioning": captioning,
            "face": face,
            "face_scanning": face,
            "analysis": body,
            "body": body,
            "body_scanning": body,
            "video": video,
            "video_extraction": video,
            "filters": filters,
            "filter_settings": filters,
            "privacy": privacy,
            "diagnostics": privacy,
        }
        notebook.select(tabs.get(self._initial_section, paths))

        paths.columnconfigure(1, weight=1)
        ttk.Label(
            paths,
            text=(
                "Quarantine is reversible and separate from the Windows Recycle "
                "Bin. Choose a drive with enough room for large image batches."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Label(paths, text="Quarantine folder:").grid(
            row=1, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(paths, textvariable=self.quarantine_var).grid(
            row=1, column=1, sticky="ew"
        )
        ttk.Button(
            paths,
            text="Browse…",
            command=self._browse_quarantine,
        ).grid(row=1, column=2, padx=(8, 0))
        ttk.Button(
            paths,
            text="Open Settings Folder",
            command=lambda: self._open_folder(get_settings_directory()),
        ).grid(row=2, column=1, sticky="w", pady=(12, 0))
        ttk.Checkbutton(
            paths,
            text="Remember the last input and catalog/report folders",
            variable=self.remember_paths_var,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Checkbutton(
            paths,
            text="Include subfolders by default when creating or adding to a catalog",
            variable=self.catalog_subfolders_var,
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            paths,
            text=(
                "Default: on. Create from Images and Add Images still show this "
                "choice for each import and remember the most recent choice."
            ),
            wraplength=650,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=5, column=1, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(
            paths,
            text=(
                "Also remove the complete catalog record when deleting an "
                "image file (default: off)"
            ),
            variable=self.delete_catalog_record_var,
        ).grid(row=6, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            paths,
            text=(
                "When off, the record remains available under the “No image "
                "file found” filter. When on, a catalog backup is created first "
                "and all database data for successfully deleted images is removed."
            ),
            wraplength=650,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=7, column=1, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(
            paths,
            text=(
                "Blank provider/tool paths use their documented automatic "
                "locations. Open the function-specific Settings pages to review "
                "Florence, InsightFace, MediaPipe, and FFmpeg choices."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(18, 0))

        self._build_captioning_page(captioning)
        self._build_face_page(face)
        self._build_video_page(video)

        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Provider:").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            body,
            textvariable=self.body_provider_var,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(body, text="Pose model:").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(body, textvariable=self.body_model_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(
            body,
            text="Browse…",
            command=self._browse_body_model,
        ).grid(row=1, column=2, padx=(8, 0), pady=(10, 0))

        threshold_frame = ttk.LabelFrame(
            body,
            text="Detection and completeness thresholds",
            padding=10,
        )
        threshold_frame.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(16, 0),
        )
        threshold_frame.columnconfigure(1, weight=1)
        self._slider_row(
            threshold_frame,
            0,
            "Body / pose detection strictness:",
            self.detection_percent_var,
            30,
            100,
            (
                "This is the main false-positive control for body/pose presence. "
                "Higher values require stronger human-pose evidence. Changing it "
                "requires Run / Restart Body to create results under the new rule."
            ),
            self._body_detection_description,
        )
        self._slider_row(
            threshold_frame,
            1,
            "Landmark visibility:",
            self.visibility_percent_var,
            30,
            100,
            (
                "Controls whether individual landmarks count as clearly visible. "
                "It affects close-up/partial/full-body interpretation, not whether "
                "MediaPipe initially finds a pose."
            ),
            self._landmark_visibility_description,
        )
        self._slider_row(
            threshold_frame,
            2,
            "Full-body completeness:",
            self.full_body_percent_var,
            60,
            100,
            (
                "60% is the permissive edge of useful full-body evidence; "
                "100% requires exceptionally complete visibility."
            ),
            self._full_body_description,
        )
        ttk.Checkbutton(
            body,
            text="Reuse compatible stored provider results for unchanged images",
            variable=self.reuse_analysis_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Label(
            body,
            textvariable=self.setup_status_var,
            wraplength=700,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))
        button_row = ttk.Frame(body)
        button_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(
            button_row,
            text="Check Compatibility",
            command=self._check_body_setup,
        ).pack(side="left")
        ttk.Button(
            button_row,
            text="Open Model Folder",
            command=self._open_body_model_folder,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            body,
            text=(
                "Only the vetted MediaPipe lite, full, and heavy .task bundle "
                "names are accepted in this release. No arbitrary Python code "
                "providers are installed."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(18, 0))

        filters.columnconfigure(1, weight=1)
        ttk.Label(
            filters,
            text="Shared filter interpretation",
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            filters,
            text=(
                "These values define how Browser filters and Finalize & Export "
                "interpret stored analysis. They do not turn a filter on. "
                "Visibility checkboxes remain under Browser > Filters."
            ),
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 16))
        ttk.Label(filters, text="Dataset target:").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Combobox(
            filters,
            textvariable=self.readiness_profile_var,
            values=tuple(profile.label for profile in READINESS_PROFILES),
            state="readonly",
            width=30,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            filters,
            text=(
                "The target changes resolution and dataset-readiness "
                "interpretation; it never changes stored images."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 14))

        ttk.Label(filters, text="Blur threshold:").grid(
            row=4, column=0, sticky="w"
        )
        blur_spinbox = ttk.Spinbox(
            filters,
            from_=0,
            to=10000,
            increment=1,
            width=10,
            textvariable=self.blur_threshold_var,
            command=self._update_blur_description,
        )
        blur_spinbox.grid(row=4, column=1, sticky="w", padx=(8, 0))
        blur_spinbox.bind("<KeyRelease>", self._update_blur_description)
        blur_spinbox.bind("<FocusOut>", self._update_blur_description)
        ttk.Label(
            filters,
            text=(
                "Whole-number local sharpness score. Images below the chosen "
                "value appear under Blur in Filters and Finalize & Export."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(
            filters,
            textvariable=self.blur_description_var,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(3, 16))

        ttk.Label(filters, text="Duplicate similarity:").grid(
            row=7, column=0, sticky="w"
        )
        duplicate_scale = ttk.Scale(
            filters,
            from_=96,
            to=100,
            variable=self.duplicate_similarity_var,
            command=self._update_duplicate_description,
        )
        duplicate_scale.grid(row=7, column=1, sticky="ew", padx=(8, 8))
        duplicate_scale.bind(
            "<KeyRelease>",
            lambda _event: self._update_duplicate_description(
                self.duplicate_similarity_var.get()
            ),
        )
        ttk.Label(
            filters,
            textvariable=self.duplicate_similarity_var,
            width=4,
        ).grid(row=7, column=2, sticky="e")
        ttk.Label(
            filters,
            textvariable=self.duplicate_description_var,
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(5, 6))
        ttk.Label(
            filters,
            text=(
                "To show only matching images, check Possible Duplicates under "
                "Browser > Filters > Readiness. Clear that checkbox to turn the "
                "duplicate filter off. Quality analysis and warnings may still "
                "report possible duplicates without hiding other images."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._update_blur_description()
        self._update_duplicate_description(self.duplicate_similarity_var.get())

        ttk.Label(
            privacy,
            text="Issue source",
            font=get_ui_font(self, size=10, weight="bold"),
        ).pack(anchor="w")
        ttk.Label(
            privacy,
            text=(
                "App issue: navigation, selection, scrolling, saved settings, "
                "catalog integrity, or a LoRA Image Curator error dialog.\n"
                "Provider/tool issue: model accuracy, model-length warnings, missing "
                "CUDA/ONNX execution providers, model loading, or FFmpeg behavior.\n\n"
                "The Status log records both. Provider messages are labeled so a "
                "third-party limitation is not presented as an application defect."
            ),
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(6, 16))
        ttk.Separator(privacy).pack(fill="x", pady=(0, 16))
        ttk.Label(
            privacy,
            text="Telemetry and online diagnostics",
            font=get_ui_font(self, size=10, weight="bold"),
        ).pack(anchor="w")
        ttk.Checkbutton(
            privacy,
            text="Allow an installed provider to use its disclosed telemetry",
            variable=self.telemetry_var,
            command=self._telemetry_toggled,
        ).pack(anchor="w", pady=(8, 4))
        ttk.Label(
            privacy,
            text=(
                "Disabled by default. LoRA Image Curator implements no telemetry. "
                "The current MediaPipe image-analysis path is local and has no "
                "application-configured collector, data transmission, or telemetry "
                "purpose. Explicit model downloads are separate network actions. "
                "A future provider with telemetry must name the collector, data "
                "categories, and purpose and request permission again."
            ),
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))
        ttk.Separator(privacy).pack(fill="x", pady=(0, 16))
        ttk.Label(
            privacy,
            text="Third-party responsibility",
            font=get_ui_font(self, size=10, weight="bold"),
        ).pack(anchor="w")
        ttk.Label(
            privacy,
            text=(
                "LoRA Image Curator does not control third-party models, provider "
                "packages, applications, websites, licenses, privacy practices, "
                "outputs, compatibility, availability, or future changes. Their "
                "authors and operators are responsible for those products. You "
                "are responsible for reviewing applicable terms, licenses, and "
                "privacy notices before installation or use. Compatibility checks "
                "reduce accidental mismatch but cannot certify safety, accuracy, "
                "legality, or fitness for a particular purpose."
            ),
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.cancel_button = ttk.Button(
            actions,
            text="Cancel",
            command=self.destroy,
        )
        self.cancel_button.pack(side="right")
        self.save_button = ttk.Button(
            actions,
            text="Save",
            command=self._save,
        )
        self.save_button.pack(
            side="right", padx=(0, 8)
        )

    def _build_captioning_page(self, page: ttk.Frame) -> None:
        """Build settings owned by the image-captioning function."""
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            text="Provider: Microsoft Florence-2",
            font=get_ui_font(self, size=11, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            page,
            text=(
                "Florence creates detailed captions and optional object-detection "
                "and OCR triage. Processing is local; source images are read only."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))
        ttk.Checkbutton(
            page,
            text="Include images in subfolders",
            variable=self.caption_subfolders_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(
            page,
            text="Default: on. This also defines the source scope for Start Catalog & Providers.",
            wraplength=680,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 12))
        ttk.Checkbutton(
            page,
            text="Add object detection and OCR triage (slower)",
            variable=self.include_triage_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            page,
            text="Reuse compatible stored provider results for unchanged images",
            variable=self.reuse_analysis_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            page,
            text="Provider diagnostic",
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(22, 4))
        ttk.Label(
            page,
            text=(
                "Florence's official object-detection example requests 1,024 new "
                "tokens. Some Transformers builds warn that this may exceed the "
                "model's predefined 1,024-token length. The app already runs one "
                "image and one task at a time; this is a provider/runtime warning, "
                "not evidence that the catalog job itself is too large. Keep an eye "
                "on failed-image counts while compatibility testing continues."
            ),
            wraplength=700,
            foreground="#7A4A00",
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w")

    def _build_face_page(self, page: ttk.Frame) -> None:
        """Build settings owned by the face-scanning function."""
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            text="Provider: InsightFace with ONNX Runtime",
            font=get_ui_font(self, size=11, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            page,
            text=(
                "Detects faces and compares local embeddings with one reference "
                "identity. Suggestions remain reviewable provider evidence."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 12))
        ttk.Checkbutton(
            page,
            text="Include Face Scanning in Run All",
            variable=self.run_face_analysis_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            page,
            text="Include input-folder subfolders",
            variable=self.face_subfolders_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            page,
            text="Include reference-folder subfolders",
            variable=self.face_reference_subfolders_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 12))
        self._settings_entry_row(
            page,
            5,
            "Trigger Keyword:",
            self.face_identity_var,
        )
        self._settings_entry_row(
            page,
            6,
            "Reference folder:",
            self.face_reference_var,
            browse=self._browse_face_reference,
        )
        self._settings_entry_row(
            page,
            7,
            "Model pack:",
            self.face_model_name_var,
        )
        self._settings_entry_row(
            page,
            8,
            "Model home:",
            self.face_model_root_var,
            browse=self._browse_face_model_root,
        )
        threshold_row = ttk.Frame(page)
        threshold_row.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(threshold_row, text="Identity threshold:").pack(side="left")
        ttk.Entry(
            threshold_row,
            textvariable=self.face_similarity_var,
            width=8,
        ).pack(side="left", padx=(6, 18))
        ttk.Label(threshold_row, text="Detection threshold:").pack(side="left")
        ttk.Entry(
            threshold_row,
            textvariable=self.face_detection_var,
            width=8,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            page,
            text=(
                "Blank Model home uses InsightFace's standard local model location. "
                "Default model pack: buffalo_l. Distributed pretrained weights may "
                "have non-commercial research restrictions."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _build_video_page(self, page: ttk.Frame) -> None:
        """Build settings owned by local video-frame extraction."""
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            text="Tool: FFmpeg",
            font=get_ui_font(self, size=11, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            page,
            text=(
                "FFmpeg extracts still-image candidates from local videos. The "
                "original video is unchanged and FFmpeg is never downloaded automatically."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 16))
        ttk.Label(page, text="FFmpeg executable:").grid(
            row=2, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(page, textvariable=self.ffmpeg_path_var).grid(
            row=2, column=1, sticky="ew"
        )
        ttk.Button(
            page,
            text="Browse…",
            command=self._browse_ffmpeg,
        ).grid(row=2, column=2, padx=(8, 0))
        ttk.Label(
            page,
            text=(
                "Logical default: blank. The extraction dialog then checks the "
                "operating-system PATH. Choose a file only when you want a specific "
                "local FFmpeg build."
            ),
            wraplength=700,
            foreground="#5F5F5F",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    @staticmethod
    def _settings_entry_row(
        page: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        browse: Callable[[], None] | None = None,
    ) -> None:
        ttk.Label(page, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Entry(page, textvariable=variable).grid(
            row=row, column=1, sticky="ew", pady=3
        )
        if browse is not None:
            ttk.Button(page, text="Browse…", command=browse).grid(
                row=row, column=2, padx=(8, 0), pady=3
            )

    def _slider_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.IntVar,
        minimum: int,
        maximum: int,
        explanation: str,
        describe: Callable[[int], str],
    ) -> None:
        minimum = int(minimum)
        maximum = int(maximum)
        display_var = tk.StringVar()
        self._slider_description_vars.append(display_var)

        def update(raw_value: str | float) -> None:
            try:
                value = round(float(raw_value))
            except (TypeError, ValueError):
                value = variable.get()
            value = max(minimum, min(maximum, int(value)))
            if variable.get() != value:
                variable.set(value)
            display_var.set(describe(value))

        ttk.Label(parent, text=label).grid(row=row * 2, column=0, sticky="w")
        scale = ttk.Scale(
            parent,
            from_=minimum,
            to=maximum,
            variable=variable,
            command=update,
        )
        scale.grid(row=row * 2, column=1, sticky="ew", padx=(8, 8))
        scale.bind("<KeyRelease>", lambda _event: update(variable.get()))
        ttk.Label(parent, textvariable=variable, width=4).grid(
            row=row * 2, column=2, sticky="e"
        )
        ttk.Label(
            parent,
            textvariable=display_var,
            wraplength=570,
            foreground="#5F5F5F",
            justify="left",
        ).grid(
            row=row * 2 + 1,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(0, 8),
        )
        update(variable.get())
        # The hover text preserves the longer design explanation without
        # crowding the live, value-specific description.
        scale.bind(
            "<Enter>",
            lambda _event: display_var.set(
                f"{describe(variable.get())}  {explanation}"
            ),
        )
        scale.bind(
            "<Leave>",
            lambda _event: display_var.set(describe(variable.get())),
        )

    @staticmethod
    def _strictness_word(value: int) -> str:
        if value <= 39:
            return "Very permissive"
        if value <= 49:
            return "Permissive"
        if value == 50:
            return "Balanced — provider default"
        if value <= 64:
            return "Moderately strict"
        if value <= 79:
            return "Strict"
        return "Very strict"

    @classmethod
    def _body_detection_description(cls, value: int) -> str:
        return (
            f"{value}% — {cls._strictness_word(value)} body/pose detection; "
            "higher values reduce false positives but may miss cropped or obscured people."
        )

    @classmethod
    def _landmark_visibility_description(cls, value: int) -> str:
        return (
            f"{value}% — {cls._strictness_word(value)} landmark visibility; "
            "higher values require clearer shoulders, hips, knees, feet, and head points."
        )

    @staticmethod
    def _full_body_description(value: int) -> str:
        if value <= 64:
            wording = "Permissive full-body judgment"
        elif value <= 74:
            wording = "Balanced full-body judgment"
        elif value <= 89:
            wording = "Strict full-body judgment"
        else:
            wording = "Very strict full-body judgment"
        return (
            f"{value}% — {wording}; visible head and foot evidence are still required."
        )

    def _update_blur_description(self, _event: tk.Event | None = None) -> None:
        try:
            value = int(self.blur_threshold_var.get())
        except (tk.TclError, ValueError):
            self.blur_description_var.set(
                "Enter a whole-number sharpness score from 0 to 10,000."
            )
            return
        self.blur_description_var.set(
            f"{value:,} — images with a lower sharpness score are flagged as Blur."
        )

    def _update_duplicate_description(self, raw_value: str | float) -> None:
        """Snap similarity to a described 96–100 whole-number choice."""
        try:
            value = round(float(raw_value))
        except (TypeError, ValueError, tk.TclError):
            value = int(self.duplicate_similarity_var.get())
        value = max(96, min(100, value))
        if self.duplicate_similarity_var.get() != value:
            self.duplicate_similarity_var.set(value)
        self.duplicate_description_var.set(
            duplicate_similarity_description(value)
        )

    def _browse_quarantine(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the quarantine folder",
            initialdir=self.quarantine_var.get().strip() or None,
        )
        if selected:
            self.quarantine_var.set(selected)

    def _browse_face_reference(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the face-reference folder",
            initialdir=self.face_reference_var.get().strip() or None,
        )
        if selected:
            self.face_reference_var.set(selected)

    def _browse_face_model_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the InsightFace model home",
            initialdir=self.face_model_root_var.get().strip() or None,
        )
        if selected:
            self.face_model_root_var.set(selected)

    def _browse_ffmpeg(self) -> None:
        current = self.ffmpeg_path_var.get().strip()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose the FFmpeg executable",
            initialdir=str(Path(current).parent) if current else None,
            filetypes=(
                ("FFmpeg executable", "ffmpeg.exe"),
                ("Executable files", "*.exe"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            self.ffmpeg_path_var.set(selected)

    def _browse_body_model(self) -> None:
        current = self.body_model_var.get().strip()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose a vetted MediaPipe Pose Landmarker model",
            initialdir=str(Path(current).parent) if current else None,
            filetypes=(
                ("MediaPipe task bundle", "*.task"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            self.body_model_var.set(selected)
            self.setup_status_var.set("Model changed; compatibility not checked.")

    def _check_body_setup(self) -> None:
        raw_path = self.body_model_var.get().strip()
        if not raw_path:
            self.setup_status_var.set("Choose a Pose Landmarker .task model.")
            return
        dialog = BodySetupDialog(
            self,
            model_path=Path(raw_path),
            on_complete=self._body_setup_completed,
        )
        self.wait_window(dialog)
        if self.winfo_exists():
            self.grab_set()

    def _body_setup_completed(self, status: BodyProviderStatus) -> None:
        """Retain a compact result after the responsive detail dialog closes."""
        lines = [
            f"MediaPipe package: {status.package_version}",
            f"Model exists: {'yes' if status.model_exists else 'no'}",
            (
                "Vetted model name: "
                f"{'yes' if status.model_filename_vetted else 'no'}"
            ),
            f"Runtime compatible: {'yes' if status.model_compatible else 'no'}",
        ]
        lines.extend(status.notes)
        self.setup_status_var.set("\n".join(lines))

    def _telemetry_toggled(self) -> None:
        if not self.telemetry_var.get():
            return
        approved = messagebox.askyesno(
            "Provider telemetry permission",
            (
                "Telemetry remains disabled unless you approve.\n\n"
                "Current collector: none.\n"
                "Current data collected: none.\n"
                "Current purpose: local pose/body analysis only.\n\n"
                "LoRA Image Curator does not implement telemetry, and the current "
                "MediaPipe path does not use this permission. A future provider "
                "with a different collector, data, or purpose must show another "
                "disclosure before use.\n\n"
                "Record this permission as enabled?"
            ),
            parent=self,
        )
        if not approved:
            self.telemetry_var.set(False)

    def _open_body_model_folder(self) -> None:
        raw = self.body_model_var.get().strip()
        folder = Path(raw).expanduser().parent if raw else get_default_body_model_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        self._open_folder(folder)

    @staticmethod
    def _open_folder(folder: Path) -> None:
        path = folder.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _save(self) -> None:
        quarantine = self.quarantine_var.get().strip()
        model = self.body_model_var.get().strip()
        if not quarantine:
            messagebox.showerror(
                "Choose a quarantine folder",
                "Quarantine requires a destination folder.",
                parent=self,
            )
            return
        try:
            blur_threshold = int(self.blur_threshold_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid blur threshold",
                "Blur threshold must be a whole number from 0 to 10,000.",
                parent=self,
            )
            return
        if not 0 <= blur_threshold <= 10000:
            messagebox.showerror(
                "Invalid blur threshold",
                "Blur threshold must be between 0 and 10,000.",
                parent=self,
            )
            return
        try:
            face_similarity = float(self.face_similarity_var.get())
            face_detection = float(self.face_detection_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid face threshold",
                "Face identity and detection thresholds must be numbers from 0 to 1.",
                parent=self,
            )
            return
        if not (
            0.0 <= face_similarity <= 1.0
            and 0.0 <= face_detection <= 1.0
        ):
            messagebox.showerror(
                "Invalid face threshold",
                "Face identity and detection thresholds must be between 0 and 1.",
                parent=self,
            )
            return
        duplicate_similarity = max(
            96,
            min(100, int(self.duplicate_similarity_var.get())),
        )
        profile = next(
            (
                candidate
                for candidate in READINESS_PROFILES
                if candidate.label == self.readiness_profile_var.get()
            ),
            READINESS_PROFILES[0],
        )
        updated = replace(
            self._original,
            remember_paths=self.remember_paths_var.get(),
            reuse_stored_analysis=self.reuse_analysis_var.get(),
            include_triage=self.include_triage_var.get(),
            catalog_import_include_subfolders=self.catalog_subfolders_var.get(),
            caption_include_subfolders=self.caption_subfolders_var.get(),
            face_include_subfolders=self.face_subfolders_var.get(),
            face_reference_include_subfolders=(
                self.face_reference_subfolders_var.get()
            ),
            quarantine_directory=str(Path(quarantine).expanduser()),
            delete_catalog_record_with_file=(
                self.delete_catalog_record_var.get()
            ),
            body_provider_key="mediapipe_pose",
            body_model_path=str(Path(model).expanduser()) if model else "",
            body_detection_threshold=self.detection_percent_var.get() / 100.0,
            body_landmark_visibility_threshold=(
                self.visibility_percent_var.get() / 100.0
            ),
            body_full_body_threshold_percent=self.full_body_percent_var.get(),
            run_face_analysis=self.run_face_analysis_var.get(),
            face_identity_name=" ".join(self.face_identity_var.get().split()),
            face_reference_folder=(
                str(Path(self.face_reference_var.get().strip()).expanduser())
                if self.face_reference_var.get().strip()
                else ""
            ),
            face_model_name=(
                self.face_model_name_var.get().strip() or "buffalo_l"
            ),
            face_model_root=(
                str(Path(self.face_model_root_var.get().strip()).expanduser())
                if self.face_model_root_var.get().strip()
                else ""
            ),
            face_similarity_threshold=face_similarity,
            face_detection_threshold=face_detection,
            video_ffmpeg_path=(
                str(Path(self.ffmpeg_path_var.get().strip()).expanduser())
                if self.ffmpeg_path_var.get().strip()
                else ""
            ),
            allow_provider_telemetry=self.telemetry_var.get(),
            readiness_profile_key=profile.key,
            quality_blur_threshold=float(blur_threshold),
            quality_duplicate_similarity_percent=duplicate_similarity,
        )
        try:
            self._on_save(updated)
        except OSError as error:
            messagebox.showerror(
                "Could not save settings",
                str(error),
                parent=self,
            )
            return
        self.destroy()
