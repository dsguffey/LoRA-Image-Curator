"""Compose the LoRA Image Curator desktop interface and workflow boundaries.

This module is the presentation/orchestration layer. It owns Tk widget
lifecycle, user confirmation, settings transfer, and background-work messages;
catalog transactions, provider execution, export, and video extraction remain
in dedicated modules. That separation is intentional: UI changes should not
quietly acquire new image-deletion, catalog-mutation, network, or subprocess
authority.

The public product name changed in v0.25.0. The historical
``DatasetToolsApp`` class name remains as a source-compatibility identifier for
the established smoke suite; visible branding comes from ``app_identity``.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from app_identity import APP_NAME, APP_VERSION, AUTHOR_NAME
from analysis_control import AnalysisCancelled
from analysis_progress import WorkflowProgressTracker, format_duration
from catalog import CATALOG_FILENAME
from catalog_browser import CatalogBrowserFrame
from body_analysis import BodyAnalysisOptions, inspect_body_setup
from body_analysis_dialog import BodyAnalysisDialog
from body_setup_dialog import BodySetupDialog
from catalog_import_dialog import CatalogImportDialog, show_catalog_import_report
from catalog_lifecycle import (
    create_catalog_database,
    delete_catalog_database,
    replace_catalog_database_with_empty,
    validate_catalog_database,
)
from dataset_readiness import (
    DEFAULT_READINESS_PROFILE_KEY,
    READINESS_PROFILES_BY_KEY,
    DatasetReadinessReport,
)
from export_dialog import DatasetExportDialog
from image_discovery import discover_supported_images
from provider_devices import inspect_provider_devices
from provider_download_dialog import ProviderDownloadDialog
from provider_registry import get_component
from provider_setup import format_download_size, inspect_florence_cache
from readiness_frame import DatasetReadinessFrame
from face_analyzer import (
    DEFAULT_DETECTION_THRESHOLD,
    DEFAULT_MODEL_NAME,
    DEFAULT_SIMILARITY_THRESHOLD,
    FaceAnalysisOptions,
    FaceSetupStatus,
    get_model_path,
    inspect_face_setup,
    model_selection_from_pack_folder,
    normalize_model_name,
    analyze_faces,
)
from provider_coverage import read_catalog_provider_coverage
from settings_manager import (
    AppSettings,
    get_default_body_model_path,
    get_settings_directory,
    load_settings,
    save_settings,
)
from third_party_notice import show_first_launch_notice
from settings_dialog import SettingsDialog
from video_extraction_dialog import (
    VideoExtractionDialog,
    show_video_extraction_report,
)
from ui_helpers import HelpIcon
from ui_fonts import MONOSPACE_FONT_FAMILY, get_ui_font
from ui_scroll import register_mousewheel_region
from ui_theme import THEMES, apply_ttk_theme, get_theme, normalize_theme_key


APPLICATION_TITLE = f"{APP_NAME} — LoRA Dataset Workspace"
WINDOW_GEOMETRY = "1400x960"
READINESS_TO_EXPORT_PROFILE = {
    "flux_character_lora": "flux_lora",
    "sdxl_character_lora": "sdxl_lora",
    "sd15_character_lora": "sd15_lora",
    "general_lora": "general_lora",
}


def configure_logging() -> Path:
    """Create a persistent log for failures hidden by a future GUI launcher."""
    log_directory = get_settings_directory()
    log_directory.mkdir(parents=True, exist_ok=True)

    log_path = log_directory / "lora_image_curator.log"

    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        encoding="utf-8",
    )

    return log_path


LOG_PATH = configure_logging()


def shutdown_logging() -> None:
    """Flush and close file handlers before Windows removes temporary folders.

    The production application benefits from a clean final flush, and GUI smoke
    tests need the handle released before ``TemporaryDirectory`` cleanup.
    ``logging.shutdown`` is idempotent and is the standard library's supported
    process-exit cleanup path.
    """
    logging.shutdown()


class DatasetToolsApp:
    """Main LoRA Image Curator desktop application."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APPLICATION_TITLE)
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(1024, 720)

        self.message_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.latest_output_csv: Path | None = None
        self.latest_face_csv: Path | None = None
        self.latest_catalog_database: Path | None = None
        self._provider_controls_locked = False
        self._quality_controls_locked = False
        self.analysis_cancel_event = threading.Event()
        self.analysis_pause_event = threading.Event()
        self._analysis_paused = False
        self._close_after_analysis_cancel = False
        self._closing = False
        self._analysis_progress_tracker: WorkflowProgressTracker | None = None
        self._analysis_has_following_face_phase = False
        self._menu_rebuild_pending = False
        self._menu_refresh_after_id: str | None = None
        self._message_queue_after_id: str | None = None
        self.images_per_page_var = tk.IntVar(value=100)

        self.settings = load_settings()
        self.theme_key_var = tk.StringVar(
            value=normalize_theme_key(self.settings.appearance_theme)
        )
        self.theme = get_theme(self.theme_key_var.get())
        apply_ttk_theme(self.root, self.theme)

        # Folder and shared-run settings.
        self.input_folder_var = tk.StringVar()
        self.output_folder_var = tk.StringVar()
        self.remember_paths_var = tk.BooleanVar(
            value=self.settings.remember_paths
        )
        self.include_triage_var = tk.BooleanVar(
            value=self.settings.include_triage
        )
        self.reuse_analysis_var = tk.BooleanVar(
            value=self.settings.reuse_stored_analysis
        )

        # Face-provider settings.  These values describe one identity profile
        # to build and compare during the current run.
        self.run_face_analysis_var = tk.BooleanVar(
            value=self.settings.run_face_analysis
        )
        self.face_identity_name_var = tk.StringVar(
            value=self.settings.face_identity_name
        )
        self.face_reference_folder_var = tk.StringVar(
            value=self.settings.face_reference_folder
        )
        self.face_model_name_var = tk.StringVar(
            value=self.settings.face_model_name or DEFAULT_MODEL_NAME
        )
        self.face_model_root_var = tk.StringVar(
            value=self.settings.face_model_root
        )
        self.face_similarity_threshold_var = tk.StringVar(
            value=str(self.settings.face_similarity_threshold)
        )
        self.face_detection_threshold_var = tk.StringVar(
            value=str(self.settings.face_detection_threshold)
        )

        self.catalog_path_var = tk.StringVar(value="Choose an output folder")
        self.status_var = tk.StringVar(value="Ready")
        self.current_work_var = tk.StringVar(
            value="Current work: No active provider"
        )
        self.progress_text_var = tk.StringVar(value="0 / 0 images")
        self.progress_detail_var = tk.StringVar(value="")
        self.progress_warning_var = tk.StringVar(value="")
        self.input_folder_count_var = tk.StringVar(
            value="Images found: choose an input folder"
        )
        self._folder_count_request = 0

        self.summary_vars = {
            "unique_images": tk.StringVar(value="—"),
            "file_locations": tk.StringVar(value="—"),
            "new_images": tk.StringVar(value="—"),
            "changed_files": tk.StringVar(value="—"),
            "florence_reused": tk.StringVar(value="—"),
            "florence_generated": tk.StringVar(value="—"),
            "faces_detected": tk.StringVar(value="—"),
            "identity_suggestions": tk.StringVar(value="—"),
        }

        self.face_setting_widgets: list[tk.Widget] = []

        self._restore_saved_paths()
        self._build_interface()
        self.images_per_page_var.set(self.catalog_browser.images_per_page)
        self._build_menu_bar()
        self._style_classic_widgets(self.root)
        self.catalog_browser.apply_theme(self.theme_key_var.get())
        self._update_catalog_path_display()
        self._toggle_face_controls()
        self._refresh_provider_coverage()
        self._refresh_input_folder_count()
        self._refresh_provider_device_status()

        if not self._closing:
            self._message_queue_after_id = self.root.after(
                100,
                self._process_message_queue,
            )
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # =========================================================================
    # Interface construction
    # =========================================================================

    def _build_interface(self) -> None:
        """Construct the complete application window."""
        # A notebook keeps the analysis workflow and the visual catalog close
        # together without forcing either one to surrender useful screen space.
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.analysis_tab = ttk.Frame(self.notebook)
        self.browser_tab = ttk.Frame(self.notebook)
        self.readiness_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.analysis_tab, text="Analyze & Update Catalog")
        self.notebook.add(self.browser_tab, text="Catalog Browser")
        self.notebook.add(self.readiness_tab, text="Finalize & Export")

        # The complete analysis workflow lives in a canvas-backed frame so a
        # smaller window never strands progress, status messages, or provider
        # controls below the visible area.  The ordinary vertical scrollbar is
        # intentionally sufficient; text/log widgets retain their own wheel
        # behavior without competing with a global mouse-wheel binding.
        self.analysis_tab.columnconfigure(0, weight=1)
        self.analysis_tab.rowconfigure(0, weight=1)
        self.analysis_canvas = tk.Canvas(
            self.analysis_tab,
            borderwidth=0,
            highlightthickness=0,
            background=self.theme.panel_background,
        )
        self.analysis_canvas.grid(row=0, column=0, sticky="nsew")
        analysis_scrollbar = ttk.Scrollbar(
            self.analysis_tab,
            orient="vertical",
            command=self.analysis_canvas.yview,
        )
        analysis_scrollbar.grid(row=0, column=1, sticky="ns")
        self.analysis_canvas.configure(yscrollcommand=analysis_scrollbar.set)

        main_frame = ttk.Frame(self.analysis_canvas, padding=18)
        self.analysis_content_window = self.analysis_canvas.create_window(
            (0, 0),
            window=main_frame,
            anchor="nw",
        )
        main_frame.bind("<Configure>", self._update_analysis_scroll_region)
        self.analysis_canvas.bind("<Configure>", self._resize_analysis_content)
        register_mousewheel_region(self.analysis_canvas)
        main_frame.columnconfigure(0, weight=1)

        title_row = ttk.Frame(main_frame)
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        title_row.columnconfigure(0, weight=1)

        ttk.Label(
            title_row,
            text="LoRA Image Curator",
            font=get_ui_font(self.root, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            main_frame,
            text=(
                "Catalog images once, run local analysis providers, reuse "
                "compatible results, and keep reviewable metadata in SQLite."
            ),
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        folder_frame = ttk.LabelFrame(
            main_frame,
            text="Catalog folders",
            padding=10,
        )
        folder_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        folder_frame.columnconfigure(1, weight=1)

        input_label = ttk.Frame(folder_frame)
        input_label.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(input_label, text="Input images:").pack(side="left")
        self.input_folder_help = HelpIcon(
            input_label,
            "Choose the folder containing source images. Analysis reads but does not alter them.",
        )
        self.input_folder_help.pack(side="left", padx=(4, 0))
        self.input_entry = ttk.Entry(
            folder_frame,
            textvariable=self.input_folder_var,
        )
        self.input_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.input_entry.bind(
            "<FocusOut>",
            lambda _event: (
                self._save_current_settings(),
                self._refresh_input_folder_count(),
            ),
        )
        self.input_browse_button = ttk.Button(
            folder_frame,
            text="Browse...",
            command=self._choose_input_folder,
        )
        self.input_browse_button.grid(
            row=0, column=2, padx=(10, 0), pady=5
        )
        ttk.Label(
            folder_frame,
            textvariable=self.input_folder_count_var,
            style="Muted.TLabel",
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 5))

        output_label = ttk.Frame(folder_frame)
        output_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(output_label, text="Catalog and reports:").pack(side="left")
        self.output_folder_help = HelpIcon(
            output_label,
            "Choose where the SQLite catalog and reports are stored.",
        )
        self.output_folder_help.pack(side="left", padx=(4, 0))
        self.output_entry = ttk.Entry(
            folder_frame,
            textvariable=self.output_folder_var,
        )
        self.output_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.output_entry.bind(
            "<KeyRelease>",
            lambda _event: self._update_catalog_path_display(),
        )
        self.output_entry.bind(
            "<FocusOut>",
            lambda _event: self._save_current_settings(),
        )
        self.output_browse_button = ttk.Button(
            folder_frame,
            text="Browse...",
            command=self._choose_output_folder,
        )
        self.output_browse_button.grid(
            row=2, column=2, padx=(10, 0), pady=5
        )

        ttk.Label(folder_frame, text="SQLite catalog:").grid(
            row=3, column=0, sticky="nw", padx=(0, 10), pady=(5, 2)
        )
        ttk.Label(
            folder_frame,
            textvariable=self.catalog_path_var,
            style="Muted.TLabel",
            wraplength=780,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(5, 2))

        catalog_actions = ttk.Frame(folder_frame)
        catalog_actions.grid(
            row=4,
            column=1,
            columnspan=2,
            sticky="w",
            pady=(8, 3),
        )
        self.new_empty_catalog_button = ttk.Button(
            catalog_actions,
            text="New Empty Catalog…",
            command=self._create_empty_catalog,
        )
        self.new_empty_catalog_button.pack(side="left")
        self.new_empty_catalog_help = HelpIcon(
            catalog_actions,
            "Create a validated empty LoRA Image Curator catalog in the selected output folder.",
        )
        self.new_empty_catalog_help.pack(side="left", padx=(3, 4))
        self.create_catalog_from_folder_button = ttk.Button(
            catalog_actions,
            text="Create from Images…",
            command=self._create_catalog_from_folder,
        )
        self.create_catalog_from_folder_button.pack(side="left", padx=(3, 0))
        self.create_catalog_from_folder_help = HelpIcon(
            catalog_actions,
            "Create or replace a catalog by indexing an image folder; source images remain unchanged.",
        )
        self.create_catalog_from_folder_help.pack(side="left", padx=(3, 4))
        self.open_catalog_button = ttk.Button(
            catalog_actions,
            text="Open Catalog…",
            command=self._open_catalog,
        )
        self.open_catalog_button.pack(side="left", padx=(3, 0))
        self.open_catalog_help = HelpIcon(
            catalog_actions,
            "Open an existing dataset_tools.db catalog.",
        )
        self.open_catalog_help.pack(side="left", padx=(3, 4))
        self.import_catalog_folder_button = ttk.Button(
            catalog_actions,
            text="Add Images…",
            command=self._import_folder_into_catalog,
            state="disabled",
        )
        self.import_catalog_folder_button.pack(side="left", padx=(3, 0))
        self.import_catalog_folder_help = HelpIcon(
            catalog_actions,
            "Add image records to the current catalog without moving source files.",
        )
        self.import_catalog_folder_help.pack(side="left", padx=(3, 4))
        self.delete_catalog_button = ttk.Button(
            catalog_actions,
            text="Delete Catalog…",
            command=self._delete_current_catalog,
            state="disabled",
        )
        self.delete_catalog_button.pack(side="left", padx=(3, 0))
        self.delete_catalog_help = HelpIcon(
            catalog_actions,
            "Delete only the validated catalog database after confirmation; source images are never deleted.",
        )
        self.delete_catalog_help.pack(side="left", padx=(3, 0))

        video_frame = ttk.LabelFrame(
            main_frame,
            text="Video Sources",
            padding=10,
        )
        video_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        video_frame.columnconfigure(0, weight=1)
        ttk.Label(
            video_frame,
            text=(
                "Extract local still-image candidates before catalog analysis. "
                "Requires a user-installed FFmpeg; the original video is unchanged."
            ),
            wraplength=920,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.video_ffmpeg_status_var = tk.StringVar(
            value=(
                "Saved FFmpeg location will be validated when opened."
                if self.settings.video_ffmpeg_path
                else "FFmpeg will be auto-detected when opened."
            )
        )
        ttk.Label(
            video_frame,
            textvariable=self.video_ffmpeg_status_var,
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        video_action = ttk.Frame(video_frame)
        video_action.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(12, 0),
        )
        self.video_source_button = ttk.Button(
            video_action,
            text="Extract Frames from Video…",
            command=self._open_video_extraction,
        )
        self.video_source_button.pack(side="left")
        self.video_source_help = HelpIcon(
            video_action,
            "Extract still-image candidates from a local video with FFmpeg. The source video is unchanged.",
        )
        self.video_source_help.pack(side="left", padx=(5, 0))

        provider_container = ttk.Frame(main_frame)
        provider_container.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        provider_container.columnconfigure(0, weight=1)
        provider_container.columnconfigure(1, weight=1)

        self._build_florence_provider(provider_container)
        self._build_face_provider(provider_container)
        self._build_body_provider(provider_container)

        controls_frame = ttk.Frame(main_frame)
        controls_frame.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        controls_frame.columnconfigure(3, weight=1)

        self.start_button = ttk.Button(
            controls_frame,
            text="Start Catalog & Providers",
            command=self._start_analysis,
        )
        self.start_button.grid(row=0, column=0, sticky="w")

        self.cancel_analysis_button = ttk.Button(
            controls_frame,
            text="Cancel Run",
            command=self._request_analysis_cancel,
            state="disabled",
        )
        self.cancel_analysis_button.grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )
        self.pause_analysis_button = ttk.Button(
            controls_frame,
            text="Pause Run",
            command=self._toggle_analysis_pause,
            state="disabled",
        )
        self.pause_analysis_button.grid(
            row=0,
            column=2,
            sticky="w",
            padx=(8, 0),
        )

        self.open_output_button = ttk.Button(
            controls_frame,
            text="Open Output Folder",
            command=self._open_report_folder,
            state="disabled",
        )
        self.open_output_button.grid(
            row=0, column=3, sticky="w", padx=(12, 0)
        )

        progress_heading = ttk.Frame(main_frame)
        progress_heading.grid(row=7, column=0, sticky="ew", pady=(0, 4))
        progress_heading.columnconfigure(0, weight=1)
        ttk.Label(
            progress_heading,
            textvariable=self.current_work_var,
            style="Accent.TLabel",
            font=get_ui_font(self.root, size=10, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Label(
            progress_heading,
            textvariable=self.progress_text_var,
            font=get_ui_font(self.root, size=10, weight="bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(
            progress_heading,
            textvariable=self.progress_detail_var,
            style="Muted.TLabel",
            anchor="e",
        ).grid(row=1, column=1, padx=(12, 0), sticky="e")
        ttk.Label(
            progress_heading,
            textvariable=self.progress_warning_var,
            style="Warning.TLabel",
            font=get_ui_font(self.root, size=9, weight="bold"),
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        self.progress_bar = ttk.Progressbar(
            main_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.grid(row=8, column=0, sticky="ew", pady=(0, 8))

        lower_pane = ttk.Panedwindow(main_frame, orient="vertical")
        lower_pane.grid(row=9, column=0, sticky="nsew")

        summary_frame = ttk.LabelFrame(
            lower_pane,
            text="Run summary",
            padding=8,
        )
        lower_pane.add(summary_frame, weight=0)

        summary_items = [
            ("Unique images", "unique_images"),
            ("File locations", "file_locations"),
            ("New images", "new_images"),
            ("Changed files", "changed_files"),
            ("Florence reused", "florence_reused"),
            ("Florence generated", "florence_generated"),
            ("Faces detected", "faces_detected"),
            ("Identity suggestions", "identity_suggestions"),
        ]

        for column in range(4):
            summary_frame.columnconfigure(column, weight=1)

        for item_index, (label_text, variable_name) in enumerate(summary_items):
            row_group = (item_index // 4) * 2
            column = item_index % 4

            ttk.Label(
                summary_frame,
                text=label_text,
                anchor="center",
            ).grid(row=row_group, column=column, sticky="ew", padx=6)
            ttk.Label(
                summary_frame,
                textvariable=self.summary_vars[variable_name],
                font=get_ui_font(self.root, size=12, weight="bold"),
                anchor="center",
            ).grid(
                row=row_group + 1,
                column=column,
                sticky="ew",
                padx=6,
                pady=(2, 7),
            )

        log_frame = ttk.LabelFrame(
            lower_pane,
            text="Status log",
            padding=8,
        )
        lower_pane.add(log_frame, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            height=13,
            state="disabled",
            font=get_ui_font(self.root, size=10, family=MONOSPACE_FONT_FAMILY),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        log_scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

        status_bar = ttk.Label(
            main_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
            anchor="w",
            padding=(6, 4),
        )
        status_bar.grid(row=10, column=0, sticky="ew", pady=(8, 0))

        initial_catalog = self._catalog_path_from_output_folder()
        if initial_catalog is None and self.settings.browser_last_catalog:
            remembered_catalog = Path(self.settings.browser_last_catalog)
            if remembered_catalog.exists():
                initial_catalog = remembered_catalog
        self.catalog_browser = CatalogBrowserFrame(self.browser_tab)
        self.catalog_browser.pack(fill="both", expand=True)
        self.dataset_readiness = DatasetReadinessFrame(
            self.readiness_tab,
            show_query=self._show_readiness_query,
            load_records=self._load_readiness_records,
            settings=self.settings,
            on_quality_running_changed=self._on_quality_running_changed,
            export_scope=self._open_readiness_export,
        )
        # Attach the writeback callback only after the frame is assigned. Some
        # Tk themes initialize Scale variables during widget construction; this
        # prevents such initialization from calling back into a half-built app.
        self.dataset_readiness.on_settings_saved = self._save_current_settings
        self.catalog_browser.on_image_sets_changed = (
            self.dataset_readiness.refresh_image_sets
        )
        self.catalog_browser.on_filter_settings_changed = (
            self._sync_readiness_interpretation_from_browser
        )
        self.catalog_browser.on_command_state_changed = (
            self._schedule_menu_state_refresh
        )
        self.dataset_readiness.pack(fill="both", expand=True)
        if initial_catalog is not None and initial_catalog.exists():
            # Remember the analysis catalog immediately, but defer thumbnail
            # work until the browser tab is actually opened.
            self.catalog_browser.set_catalog_path(
                initial_catalog,
                load=False,
                quiet=True,
            )
            self.catalog_path_var.set(str(initial_catalog.resolve()))
        self._update_catalog_management_state()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _update_analysis_scroll_region(self, _event: tk.Event) -> None:
        """Keep the analysis scrollbar synchronized with all requested content."""
        self.analysis_canvas.configure(
            scrollregion=self.analysis_canvas.bbox("all")
        )

    def _resize_analysis_content(self, event: tk.Event) -> None:
        """Make the scrollable analysis body follow the visible tab width."""
        self.analysis_canvas.itemconfigure(
            self.analysis_content_window,
            width=max(1, int(event.width)),
        )

    def _refresh_input_folder_count(self) -> None:
        """Count supported images off the Tk thread and reject stale replies."""
        raw_folder = self.input_folder_var.get().strip()
        self._folder_count_request += 1
        request_id = self._folder_count_request
        if not raw_folder:
            self.input_folder_count_var.set(
                "Images found: choose an input folder"
            )
            return
        folder = Path(raw_folder).expanduser()
        recursive = self.settings.caption_include_subfolders
        message_queue = self.message_queue
        if not folder.is_dir():
            self.input_folder_count_var.set(
                "Images found: folder is missing or inaccessible"
            )
            return
        self.input_folder_count_var.set("Images found: counting…")

        def count_images() -> None:
            try:
                count = len(
                    discover_supported_images(folder, recursive=recursive)
                )
                payload: object = (request_id, folder, count, recursive, "")
            except Exception as error:
                payload = (
                    request_id,
                    folder,
                    0,
                    recursive,
                    f"{type(error).__name__}: {error}",
                )
            message_queue.put(("folder_count", payload))

        threading.Thread(
            target=count_images,
            name="input-folder-image-count",
            daemon=True,
        ).start()

    def _refresh_provider_device_status(self) -> None:
        """Inspect accelerator availability without delaying window creation."""
        # Tk variables are owned by the main thread. Capture their plain-text
        # values before the worker starts. The worker must also avoid retaining
        # ``self``: device imports can outlive a fast close, and releasing the
        # last application reference on that worker would finalize Tk variables
        # outside the main thread. A Queue is thread-safe and is the only
        # application-owned object the worker needs.
        face_model_name = self.face_model_name_var.get().strip()
        face_model_root = self.face_model_root_var.get().strip()
        message_queue = self.message_queue

        def inspect_devices() -> None:
            try:
                devices = inspect_provider_devices(
                    face_model_name=face_model_name,
                    face_model_root=face_model_root,
                )
                message_queue.put(("provider_devices", devices))
            except Exception as error:
                logging.exception("Provider device inspection failed")
                message_queue.put(
                    (
                        "provider_devices_error",
                        f"{type(error).__name__}: {error}",
                    )
                )

        threading.Thread(
            target=inspect_devices,
            name="provider-device-inspection",
            daemon=True,
        ).start()

    def _build_body_provider(self, parent: ttk.Frame) -> None:
        """Place body analysis where it occurs in the normal catalog workflow."""
        frame = ttk.LabelFrame(
            parent,
            text="Provider 3 — MediaPipe Body / Pose",
            padding=10,
        )
        frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )
        frame.columnconfigure(0, weight=1)
        self.body_provider_status_var = tk.StringVar(value="No catalog selected.")
        self.body_provider_model_var = tk.StringVar()
        self.body_provider_device_var = tk.StringVar(value="Device: checking…")
        self._refresh_body_provider_status()
        ttk.Label(
            frame,
            text=(
                "Runs after images are cataloged. It records local pose evidence "
                "for full-body, partial-body, visible-face, and multi-pose filters."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            textvariable=self.body_provider_model_var,
            style="Muted.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            frame,
            textvariable=self.body_provider_status_var,
            style="Muted.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            frame,
            textvariable=self.body_provider_device_var,
            style="Muted.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.body_running_label = ttk.Label(
            frame,
            text="● Running — progress in dialog",
            style="Running.TLabel",
            font=get_ui_font(self.root, size=9, weight="bold"),
        )
        self.body_running_label.grid(row=4, column=0, sticky="w", pady=(5, 0))
        self.body_running_label.grid_remove()

        actions = ttk.Frame(frame)
        actions.grid(row=0, column=1, rowspan=5, sticky="e", padx=(14, 0))
        self.run_body_analysis_button = ttk.Button(
            actions,
            text="Run / Restart Body",
            command=self._run_body_analysis,
            state="disabled",
        )
        self.run_body_analysis_button.pack(side="left")
        self.check_body_setup_button = ttk.Button(
            actions,
            text="Check Setup",
            command=self._check_body_setup,
        )
        self.check_body_setup_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Settings",
            command=lambda: self._show_settings("body"),
        ).pack(side="left", padx=(8, 0))

    def _refresh_body_provider_status(self) -> None:
        """Display the configured provider/model without running compatibility work."""
        if not hasattr(self, "body_provider_model_var"):
            return
        model = self._body_model_path()
        self.body_provider_model_var.set(
            f"Google MediaPipe Pose Landmarker · model: {model.name} · {model}"
        )

    def _refresh_provider_coverage(self) -> None:
        """Show persisted checked/total counts for the currently active catalog."""
        variables = (
            getattr(self, "florence_provider_status_var", None),
            getattr(self, "face_provider_status_var", None),
            getattr(self, "body_provider_status_var", None),
        )
        if any(variable is None for variable in variables):
            return
        catalog_path = self._current_catalog_path()
        if catalog_path is None:
            for variable in variables:
                variable.set("No catalog selected.")
            return
        try:
            coverage = read_catalog_provider_coverage(catalog_path)
        except (OSError, sqlite3.Error, ValueError) as error:
            logging.exception("Could not read provider coverage")
            for variable in variables:
                variable.set(f"Coverage unavailable: {error}")
            return

        florence_text = coverage.florence.status_text()
        if coverage.florence_triage_successful:
            florence_text += (
                f" · full triage: {coverage.florence_triage_successful:,}"
            )
        self.florence_provider_status_var.set(florence_text)
        self.face_provider_status_var.set(coverage.face.status_text())
        self.body_provider_status_var.set(coverage.body.status_text())

    # ------------------------------------------------------------------
    # Workstation menu bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        """Build mode-aware menus from the same commands used by the tabs.

        Secondary Catalog Browser actions are deliberately absent from other
        tabs.  This keeps menus relevant to the active workspace while leaving
        tabs responsible for the application's three major workflow stages.
        """
        # A root menu still inherits Tk's historical ``tearoff=True`` default.
        # Windows does not present that entry as a useful application command,
        # and leaving it enabled also makes programmatic menu inspection differ
        # between Tk versions.  Every real LoRA Image Curator menu is a cascade, so
        # disable the synthetic tear-off entry explicitly.
        menu_bar = tk.Menu(self.root, tearoff=False)
        self.root.configure(menu=menu_bar)
        self.menu_bar = menu_bar

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Exit", command=self._on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)

        self.edit_menu = tk.Menu(menu_bar, tearoff=False)
        self.edit_menu.add_command(
            label="Undo",
            accelerator="Ctrl+Z",
            command=self._menu_undo,
        )
        self.edit_menu.add_command(
            label="Redo",
            accelerator="Ctrl+Y",
            command=self._menu_redo,
        )
        self.edit_menu.add_separator()
        self.edit_menu.add_command(
            label="Cut",
            accelerator="Ctrl+X",
            command=lambda: self._send_text_event("<<Cut>>"),
        )
        self.edit_menu.add_command(
            label="Copy",
            accelerator="Ctrl+C",
            command=lambda: self._send_text_event("<<Copy>>"),
        )
        self.edit_menu.add_command(
            label="Paste",
            accelerator="Ctrl+V",
            command=lambda: self._send_text_event("<<Paste>>"),
        )
        menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        browser_active = self.notebook.select() == str(self.browser_tab)
        if browser_active:
            self._add_browser_only_menus(menu_bar)

        self.catalog_menu = tk.Menu(menu_bar, tearoff=False)
        self.catalog_menu.add_command(
            label="New Empty Catalog…",
            command=self._create_empty_catalog,
        )
        self.catalog_menu.add_command(
            label="Create from Images…",
            command=self._create_catalog_from_folder,
        )
        self.catalog_menu.add_command(label="Open Catalog…", command=self._open_catalog)
        self.catalog_menu.add_separator()
        self.catalog_menu.add_command(
            label="Add Images…",
            command=self._import_folder_into_catalog,
        )
        self.catalog_menu.add_command(
            label="Delete Catalog…",
            command=self._delete_current_catalog,
        )
        menu_bar.add_cascade(label="Catalog", menu=self.catalog_menu)

        self.tools_menu = tk.Menu(menu_bar, tearoff=False)
        self.tools_menu.add_command(
            label="Extract Frames from Video…",
            command=self._open_video_extraction,
        )
        self.tools_menu.add_separator()
        self.tools_menu.add_command(
            label="Open Setup & Repair…",
            command=self._open_setup_and_repair,
        )
        self.tools_menu.add_command(
            label="Check Face Analysis Setup",
            command=self._check_face_setup,
        )
        self.tools_menu.add_command(
            label="Check Body Analysis Setup",
            command=self._check_body_setup,
        )
        self.tools_menu.add_separator()
        self.tools_menu.add_command(
            label="Start Catalog & Providers",
            command=self._start_analysis,
        )
        self.tools_menu.add_command(
            label="Run Florence Caption & Triage…",
            command=lambda: self._start_analysis(run_face_override=False),
        )
        self.tools_menu.add_command(
            label="Run Face Detection & Identity…",
            command=self._start_face_analysis,
        )
        self.tools_menu.add_command(
            label="Run Body / Pose Analysis…",
            command=self._run_body_analysis,
        )
        self.tools_menu.add_command(
            label="Cancel Active Run",
            command=self._request_analysis_cancel,
        )
        self.tools_menu.add_command(
            label="Pause / Resume Active Run",
            command=self._toggle_analysis_pause,
        )
        menu_bar.add_cascade(label="Tools", menu=self.tools_menu)

        settings_menu = tk.Menu(menu_bar, tearoff=False)
        appearance_menu = tk.Menu(settings_menu, tearoff=False)
        for theme_key, theme in THEMES.items():
            appearance_menu.add_radiobutton(
                label=theme.label,
                value=theme_key,
                variable=self.theme_key_var,
                command=self._apply_theme_setting,
            )
        settings_menu.add_cascade(label="Appearance Theme", menu=appearance_menu)
        settings_menu.add_separator()
        page_menu = tk.Menu(settings_menu, tearoff=False)
        for page_size in (25, 50, 75, 100):
            page_menu.add_radiobutton(
                label=str(page_size),
                value=page_size,
                variable=self.images_per_page_var,
                command=self._apply_images_per_page_setting,
            )
        settings_menu.add_cascade(label="Images per Browser Page", menu=page_menu)
        settings_menu.add_separator()
        settings_menu.add_command(
            label="Catalog & Paths…",
            command=lambda: self._show_settings("paths"),
        )
        settings_menu.add_command(
            label="Image Captioning…",
            command=lambda: self._show_settings("captioning"),
        )
        settings_menu.add_command(
            label="Face Scanning…",
            command=lambda: self._show_settings("face"),
        )
        settings_menu.add_command(
            label="Body / Pose Scanning…",
            command=lambda: self._show_settings("body"),
        )
        settings_menu.add_command(
            label="Video Extraction…",
            command=lambda: self._show_settings("video"),
        )
        settings_menu.add_command(
            label="Filter Settings…",
            command=lambda: self._show_settings("filters"),
        )
        settings_menu.add_command(
            label="Privacy & Diagnostics…",
            command=lambda: self._show_settings("privacy"),
        )
        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="Getting Started", command=self._show_general_help)
        help_menu.add_command(
            label="Analyze & Update Catalog",
            command=self._show_analysis_help,
        )
        help_menu.add_command(
            label="Catalog Browser",
            command=self._show_browser_help,
        )
        help_menu.add_command(
            label="Finalize & Export",
            command=self._show_finalize_help,
        )
        help_menu.add_separator()
        help_menu.add_command(
            label="Keyboard Shortcuts",
            accelerator="F1",
            command=self._show_shortcuts_help,
        )
        help_menu.add_command(
            label="Video Extraction",
            command=self._show_video_help,
        )
        help_menu.add_command(
            label="Face Analysis",
            command=self._show_face_help,
        )
        help_menu.add_command(
            label="Body Analysis & Models",
            command=self._show_body_help,
        )
        help_menu.add_command(
            label="Privacy & Third-Party Products",
            command=self._show_privacy_help,
        )
        help_menu.add_command(label="Licensing", command=self._show_license_help)
        help_menu.add_separator()
        help_menu.add_command(label="About LoRA Image Curator", command=self._show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)

        self.root.bind("<F1>", lambda _event: self._show_shortcuts_help())
        self._refresh_menu_states()

    def _add_browser_only_menus(self, menu_bar: tk.Menu) -> None:
        """Add commands that have meaning only while Catalog Browser is active."""
        self.selection_menu = tk.Menu(menu_bar, tearoff=False)
        self.selection_menu.add_command(
            label="Select All Results",
            accelerator="Ctrl+A",
            command=self.catalog_browser.select_all_results,
        )
        self.selection_menu.add_command(
            label="Select Current Page",
            accelerator="Ctrl+Shift+A",
            command=self.catalog_browser.select_current_page,
        )
        self.selection_menu.add_command(
            label="Select by Keyword…",
            command=self.catalog_browser.select_by_keyword,
        )
        self.selection_menu.add_command(
            label="Select by Image Set…",
            command=self.catalog_browser._open_image_sets,
        )
        self.selection_menu.add_separator()
        self.selection_menu.add_command(
            label="Remove Unnecessary Images…",
            accelerator="N",
            command=self.catalog_browser._remove_unnecessary_images,
        )
        self.selection_menu.add_separator()
        self.selection_menu.add_command(
            label="Deselect All",
            accelerator="Esc / Ctrl+D",
            command=self.catalog_browser.clear_selection,
        )
        self.selection_menu.add_command(
            label="Deselect Current Page",
            accelerator="Ctrl+Shift+D",
            command=self.catalog_browser.deselect_current_page,
        )
        self.selection_menu.add_command(
            label="Deselect by Keyword…",
            command=self.catalog_browser.deselect_by_keyword,
        )
        self.selection_menu.add_separator()
        self.selection_menu.add_command(
            label="Invert All Results",
            accelerator="Ctrl+I",
            command=self.catalog_browser.invert_all_results_selection,
        )
        self.selection_menu.add_command(
            label="Invert Current Page",
            accelerator="Ctrl+Shift+I",
            command=self.catalog_browser.invert_current_page_selection,
        )
        menu_bar.add_cascade(label="Selection", menu=self.selection_menu)

        self.filters_menu = tk.Menu(menu_bar, tearoff=False)
        self.filters_menu.add_command(
            label="Open Filters",
            accelerator="Ctrl+Shift+F",
            command=self.catalog_browser._open_browser_filters,
        )
        self.filters_menu.add_separator()
        scope_menu = tk.Menu(self.filters_menu, tearoff=False)
        scope_menu.add_command(
            label="Image Set & Catalog State…",
            command=lambda: self.catalog_browser._open_browser_filters("scope"),
        )
        self.filters_menu.add_cascade(label="Image Scope", menu=scope_menu)
        subject_menu = tk.Menu(self.filters_menu, tearoff=False)
        subject_menu.add_command(
            label="Face Evidence…",
            command=lambda: self.catalog_browser._open_browser_filters("face"),
        )
        subject_menu.add_command(
            label="Body / Pose Evidence…",
            command=lambda: self.catalog_browser._open_browser_filters("body"),
        )
        self.filters_menu.add_cascade(label="Subject Evidence", menu=subject_menu)
        quality_menu = tk.Menu(self.filters_menu, tearoff=False)
        quality_menu.add_command(
            label="Filter Settings Summary…",
            command=lambda: self.catalog_browser._open_browser_filters(
                "filter_settings"
            ),
        )
        quality_menu.add_command(
            label="Readiness Findings…",
            command=lambda: self.catalog_browser._open_browser_filters("readiness"),
        )
        self.filters_menu.add_cascade(label="Dataset Quality", menu=quality_menu)
        menu_bar.add_cascade(label="Filters", menu=self.filters_menu)

        self.browser_menu = tk.Menu(menu_bar, tearoff=False)
        self.browser_menu.add_command(
            label="Refresh",
            accelerator="F5",
            command=self.catalog_browser.refresh,
        )
        self.browser_menu.add_command(
            label="Image Sets…",
            command=self.catalog_browser._open_image_sets,
        )
        self.browser_menu.add_separator()
        self.browser_menu.add_command(
            label="Save Search…",
            command=self.catalog_browser._save_named_search,
        )
        self.browser_menu.add_command(
            label="Saved Searches…",
            command=self.catalog_browser._open_saved_searches,
        )
        self.browser_menu.add_command(
            label="Search History…",
            command=self.catalog_browser._open_search_history_settings,
        )
        self.browser_menu.add_separator()
        self.selected_images_menu = tk.Menu(
            self.browser_menu,
            tearoff=False,
        )
        self.selected_images_menu.add_command(
            label="Quarantine Selected…",
            accelerator="Ctrl+Shift+Q",
            command=self.catalog_browser.quarantine_selected,
        )
        self.selected_images_menu.add_command(
            label="Restore Selected from Quarantine…",
            command=self.catalog_browser.restore_selected_from_quarantine,
        )
        self.selected_images_menu.add_command(
            label="Send Selected Files to Recycle Bin…",
            accelerator="Delete",
            command=self.catalog_browser.delete_selected_to_trash,
        )
        self.selected_images_menu.add_separator()
        self.selected_images_menu.add_command(
            label="Remove Selected Records from Catalog…",
            accelerator="Ctrl+Shift+Delete",
            command=self.catalog_browser.remove_selected_from_catalog,
        )
        self.browser_menu.add_cascade(
            label="Selected Images",
            menu=self.selected_images_menu,
        )
        self.browser_menu.add_separator()
        self.browser_menu.add_command(
            label="Export Selected…",
            accelerator="Ctrl+E",
            command=self.catalog_browser._open_export_dialog,
        )
        menu_bar.add_cascade(label="Browser", menu=self.browser_menu)

    def _schedule_menu_state_refresh(self) -> None:
        """Coalesce rapid selection changes into one inexpensive menu refresh."""
        if self._menu_rebuild_pending or self._closing:
            return
        self._menu_rebuild_pending = True
        self._menu_refresh_after_id = self.root.after_idle(
            self._finish_menu_state_refresh,
        )

    def _finish_menu_state_refresh(self) -> None:
        self._menu_refresh_after_id = None
        self._menu_rebuild_pending = False
        if not self._closing:
            self._refresh_menu_states()

    def _refresh_menu_states(self) -> None:
        """Synchronize menu availability with focus, selection, and catalog state."""
        locked = self._provider_controls_locked or self._quality_controls_locked
        has_catalog = self._current_catalog_path() is not None
        normal_if = lambda condition: "normal" if condition else "disabled"
        for label in (
            "New Empty Catalog…",
            "Create from Images…",
            "Open Catalog…",
        ):
            self.catalog_menu.entryconfigure(label, state=normal_if(not locked))
        for label in ("Add Images…", "Delete Catalog…"):
            self.catalog_menu.entryconfigure(
                label,
                state=normal_if(has_catalog and not locked),
            )
        self.tools_menu.entryconfigure(
            "Extract Frames from Video…",
            state=normal_if(not locked),
        )
        for label in (
            "Open Setup & Repair…",
            "Check Face Analysis Setup",
            "Check Body Analysis Setup",
        ):
            self.tools_menu.entryconfigure(label, state=normal_if(not locked))
        provider_running = self.worker_thread is not None and self.worker_thread.is_alive()
        self.tools_menu.entryconfigure(
            "Start Catalog & Providers",
            state=normal_if(not locked),
        )
        self.tools_menu.entryconfigure(
            "Cancel Active Run",
            state=normal_if(provider_running),
        )
        self.tools_menu.entryconfigure(
            "Pause / Resume Active Run",
            state=normal_if(provider_running),
        )
        self.tools_menu.entryconfigure(
            "Run Florence Caption & Triage…",
            state=normal_if(not locked),
        )
        for label in (
            "Run Face Detection & Identity…",
            "Run Body / Pose Analysis…",
        ):
            self.tools_menu.entryconfigure(
                label,
                state=normal_if(has_catalog and not locked),
            )

        browser_active = self.notebook.select() == str(self.browser_tab)
        if not browser_active or not hasattr(self, "selection_menu"):
            self.edit_menu.entryconfigure("Undo", state="disabled")
            self.edit_menu.entryconfigure("Redo", state="disabled")
            return

        state = self.catalog_browser.command_state()
        self.edit_menu.entryconfigure(
            "Undo",
            state=normal_if(state["can_undo"]),
        )
        self.edit_menu.entryconfigure(
            "Redo",
            state=normal_if(state["can_redo"]),
        )
        self.selection_menu.entryconfigure(
            "Select All Results",
            state=normal_if(state["has_results"]),
        )
        self.selection_menu.entryconfigure(
            "Select Current Page",
            state=normal_if(state["has_page"]),
        )
        self.selection_menu.entryconfigure(
            "Select by Keyword…",
            state=normal_if(state["has_results"]),
        )
        self.selection_menu.entryconfigure(
            "Select by Image Set…",
            state=normal_if(state["has_catalog"]),
        )
        self.selection_menu.entryconfigure(
            "Deselect by Keyword…",
            state=normal_if(state["has_results"] and state["has_selection"]),
        )
        self.selection_menu.entryconfigure(
            "Remove Unnecessary Images…",
            state=normal_if(state["has_selection"]),
        )
        for label in (
            "Deselect Current Page",
            "Deselect All",
            "Invert Current Page",
            "Invert All Results",
        ):
            requirement = (
                state["has_selection"]
                if label.startswith("Deselect")
                else state["has_results"]
            )
            self.selection_menu.entryconfigure(label, state=normal_if(requirement))
        for label in (
            "Quarantine Selected…",
            "Restore Selected from Quarantine…",
            "Send Selected Files to Recycle Bin…",
            "Remove Selected Records from Catalog…",
        ):
            self.selected_images_menu.entryconfigure(
                label,
                state=normal_if(state["has_selection"]),
            )
        self.browser_menu.entryconfigure(
            "Selected Images",
            state=normal_if(state["has_catalog"]),
        )
        self.browser_menu.entryconfigure(
            "Refresh",
            state=normal_if(state["has_catalog"]),
        )
        self.browser_menu.entryconfigure(
            "Image Sets…",
            state=normal_if(state["has_catalog"] and not self._quality_controls_locked),
        )
        self.browser_menu.entryconfigure(
            "Export Selected…",
            state=normal_if(state["has_catalog"] and state["has_selection"]),
        )
        self.filters_menu.entryconfigure(
            "Open Filters",
            state=normal_if(state["has_catalog"]),
        )

    def _menu_undo(self) -> None:
        focus = self.root.focus_get()
        if (
            self.notebook.select() == str(self.browser_tab)
            and not self.catalog_browser._is_text_input(focus)
        ):
            self.catalog_browser._undo_history()
            return
        self._send_text_event("<<Undo>>")

    def _menu_redo(self) -> None:
        focus = self.root.focus_get()
        if (
            self.notebook.select() == str(self.browser_tab)
            and not self.catalog_browser._is_text_input(focus)
        ):
            self.catalog_browser._redo_history()
            return
        self._send_text_event("<<Redo>>")

    def _send_text_event(self, virtual_event: str) -> None:
        """Send a standard editing command only to the focused text-capable widget."""
        focus = self.root.focus_get()
        if focus is None or not self.catalog_browser._is_text_input(focus):
            self.root.bell()
            return
        try:
            focus.event_generate(virtual_event)
        except tk.TclError:
            self.root.bell()

    def _apply_images_per_page_setting(self) -> None:
        self.catalog_browser.set_images_per_page(self.images_per_page_var.get())
        self._refresh_menu_states()

    def _apply_theme_setting(self) -> None:
        """Apply and persist a curated visual theme immediately."""
        theme_key = normalize_theme_key(self.theme_key_var.get())
        self.theme_key_var.set(theme_key)
        self.theme = get_theme(theme_key)
        apply_ttk_theme(self.root, self.theme)
        self._style_classic_widgets(self.root)
        if hasattr(self, "catalog_browser"):
            self.catalog_browser.apply_theme(theme_key)
        self._refresh_help_icons(self.root)
        self._save_current_settings()

    def _style_classic_widgets(self, widget: tk.Misc) -> None:
        """Update classic Tk widgets that do not inherit ttk styles."""
        theme = self.theme
        for child in widget.winfo_children():
            class_name = child.winfo_class()
            try:
                if class_name in {"Frame", "Labelframe"}:
                    child.configure(background=theme.panel_background)
                elif class_name == "Text":
                    child.configure(
                        background=theme.field_background,
                        foreground=theme.text,
                        insertbackground=theme.text,
                        selectbackground=theme.accent,
                        selectforeground=theme.accent_text,
                    )
                elif class_name == "Canvas":
                    child.configure(background=theme.panel_background)
                elif class_name == "Label":
                    child.configure(background=theme.panel_background, foreground=theme.text)
            except tk.TclError:
                pass
            self._style_classic_widgets(child)

    def _refresh_help_icons(self, widget: tk.Misc) -> None:
        """Repaint canvas-drawn help affordances after a live theme change."""
        for child in widget.winfo_children():
            if isinstance(child, HelpIcon):
                child._apply_theme()
            self._refresh_help_icons(child)

    def _build_florence_provider(self, parent: ttk.Frame) -> None:
        """Build the clearly labeled Florence provider card."""
        frame = ttk.LabelFrame(
            parent,
            text="Provider 1 — Florence-2 Caption & Triage",
            padding=10,
        )
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        frame.columnconfigure(0, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.florence_provider_status_var = tk.StringVar(
            value="No catalog selected."
        )
        ttk.Label(
            header,
            textvariable=self.florence_provider_status_var,
            style="Muted.TLabel",
            wraplength=390,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.run_florence_button = ttk.Button(
            header,
            text="Run / Restart Florence",
            command=lambda: self._start_analysis(run_face_override=False),
        )
        self.run_florence_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.florence_provider_device_var = tk.StringVar(
            value="Device: checking…"
        )
        ttk.Label(
            header,
            textvariable=self.florence_provider_device_var,
            style="Muted.TLabel",
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.florence_running_label = ttk.Label(
            header,
            text="● Running — progress below",
            style="Running.TLabel",
            font=get_ui_font(self.root, size=9, weight="bold"),
        )
        self.florence_running_label.grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.florence_running_label.grid_remove()

        ttk.Label(
            frame,
            text=(
                "Creates detailed captions. Optional triage also detects "
                "objects, reads visible text (OCR), estimates person count, "
                "and flags likely screenshots or review candidates."
            ),
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(7, 8))

        triage_row = ttk.Frame(frame)
        triage_row.grid(row=2, column=0, sticky="w")
        self.triage_checkbutton = ttk.Checkbutton(
            triage_row,
            text="Add object detection and OCR triage (slower)",
            variable=self.include_triage_var,
            command=self._save_current_settings,
        )
        self.triage_checkbutton.pack(side="left")
        self.triage_help = HelpIcon(
            triage_row,
            "Add object detection, OCR, person-count, and screenshot evidence. This can substantially increase Florence processing time.",
        )
        self.triage_help.pack(side="left", padx=(4, 0))

        ttk.Label(
            frame,
            text="Files changed: none. Processing: local. GPU recommended.",
            foreground="#555555",
        ).grid(row=3, column=0, sticky="w", pady=(7, 0))
        ttk.Label(
            frame,
            text=(
                "Large collections can take much longer than cataloging. A measured "
                "ETA plus safe Pause/Resume and Cancel controls appear below. "
                "Run / Restart reuses compatible stored results by default."
            ),
            foreground="#7A4A00",
            wraplength=500,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

    def _build_face_provider(self, parent: ttk.Frame) -> None:
        """Build the optional face provider card and its first identity profile."""
        frame = ttk.LabelFrame(
            parent,
            text="Provider 2 — Face Detection & Identity Matching",
            padding=10,
        )
        frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        frame.columnconfigure(1, weight=1)

        header_frame = ttk.Frame(frame)
        header_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        header_frame.columnconfigure(0, weight=1)

        enable_row = ttk.Frame(header_frame)
        enable_row.grid(row=0, column=0, sticky="w")
        self.face_enable_checkbutton = ttk.Checkbutton(
            enable_row,
            text="Include in Run All",
            variable=self.run_face_analysis_var,
            command=self._on_face_enabled_changed,
        )
        self.face_enable_checkbutton.pack(side="left")
        self.face_enable_help = HelpIcon(
            enable_row,
            "Include local face detection and identity comparison when Start Catalog & Providers runs. Run Face remains available separately.",
        )
        self.face_enable_help.pack(side="left", padx=(4, 0))

        setup_row = ttk.Frame(header_frame)
        setup_row.grid(row=0, column=1, padx=(6, 0))
        self.run_face_analysis_button = ttk.Button(
            setup_row,
            text="Run / Restart Face",
            command=self._start_face_analysis,
        )
        self.run_face_analysis_button.pack(side="left")
        self.face_setup_button = ttk.Button(
            setup_row,
            text="Check Setup",
            command=self._check_face_setup,
        )
        self.face_setup_button.pack(side="left", padx=(8, 0))
        self.face_setup_help = HelpIcon(
            setup_row,
            "Check the installed InsightFace package, model files, and available execution providers.",
        )
        self.face_setup_help.pack(side="left", padx=(4, 0))
        self.face_running_label = ttk.Label(
            header_frame,
            text="● Running — progress below",
            style="Running.TLabel",
            font=get_ui_font(self.root, size=9, weight="bold"),
        )
        self.face_running_label.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.face_running_label.grid_remove()

        ttk.Label(
            frame,
            text=(
                "Detects faces, stores local bounding boxes and identity "
                "embeddings, then compares each face to reference images for "
                "one reference identity. Suggestions remain unconfirmed until review."
            ),
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(7, 8))

        row = 2
        self._add_face_setting_row(
            frame,
            row,
            "Trigger Keyword:",
            ttk.Entry(frame, textvariable=self.face_identity_name_var),
            help_text=(
                "Training activation text applied to this identity. Face detection "
                "does not infer or verify a person's public name."
            ),
        )
        row += 1

        reference_entry = ttk.Entry(
            frame,
            textvariable=self.face_reference_folder_var,
        )
        reference_button = ttk.Button(
            frame,
            text="Browse...",
            command=self._choose_face_reference_folder,
        )
        self._add_face_setting_row(
            frame,
            row,
            "Reference folder:",
            reference_entry,
            reference_button,
            help_text=(
                "Choose a folder of clear reference images of the same person. "
                "One visible face per image is best."
            ),
            help_attribute="face_reference_folder_help",
        )
        row += 1

        model_entry = ttk.Entry(
            frame,
            textvariable=self.face_model_name_var,
            width=18,
        )
        model_button = ttk.Button(
            frame,
            text="Browse...",
            command=self._choose_face_model_pack,
        )
        self._add_face_setting_row(
            frame,
            row,
            "Model pack:",
            model_entry,
            model_button,
            help_text=(
                "Browse lets you choose a different compatible local InsightFace "
                "model pack. Leave the default unless you intentionally use another."
            ),
            help_attribute="face_model_pack_help",
        )
        row += 1

        model_root_entry = ttk.Entry(
            frame,
            textvariable=self.face_model_root_var,
        )
        model_root_button = ttk.Button(
            frame,
            text="Browse...",
            command=self._choose_face_model_root,
        )
        self._add_face_setting_row(
            frame,
            row,
            "Model home:",
            model_root_entry,
            model_root_button,
            help_text=(
                "Optional folder containing InsightFace model packs. Leave blank "
                "to use the normal InsightFace location."
            ),
        )
        row += 1

        threshold_frame = ttk.Frame(frame)
        threshold_frame.grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(5, 0),
        )
        threshold_frame.columnconfigure(1, weight=1)
        threshold_frame.columnconfigure(3, weight=1)

        identity_threshold_label = ttk.Frame(threshold_frame)
        identity_threshold_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Label(identity_threshold_label, text="Identity threshold:").pack(
            side="left"
        )
        self.identity_threshold_help = HelpIcon(
            identity_threshold_label,
            "Higher values produce fewer, more conservative identity suggestions. See Help > Face Analysis for details.",
        )
        self.identity_threshold_help.pack(side="left", padx=(4, 0))
        similarity_entry = ttk.Entry(
            threshold_frame,
            textvariable=self.face_similarity_threshold_var,
            width=8,
        )
        similarity_entry.grid(row=0, column=1, sticky="w")

        detection_threshold_label = ttk.Frame(threshold_frame)
        detection_threshold_label.grid(
            row=0, column=2, sticky="w", padx=(18, 5)
        )
        ttk.Label(detection_threshold_label, text="Detection threshold:").pack(
            side="left"
        )
        self.detection_threshold_help = HelpIcon(
            detection_threshold_label,
            (
                "Controls whether weak face detections are retained. Higher "
                "values reduce false face positives but may miss small, cropped, "
                "or obscured faces. Run / Restart Face after changing it."
            ),
        )
        self.detection_threshold_help.pack(side="left", padx=(4, 0))
        detection_entry = ttk.Entry(
            threshold_frame,
            textvariable=self.face_detection_threshold_var,
            width=8,
        )
        detection_entry.grid(row=0, column=3, sticky="w")
        self.face_setting_widgets.extend([similarity_entry, detection_entry])

        ttk.Label(
            frame,
            text=(
                "Default InsightFace weights: non-commercial research only. "
                "Files changed: none. Processing: local."
            ),
            foreground="#555555",
            wraplength=500,
            justify="left",
        ).grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(7, 0))
        self.face_provider_status_var = tk.StringVar(value="No catalog selected.")
        ttk.Label(
            frame,
            textvariable=self.face_provider_status_var,
            style="Muted.TLabel",
            wraplength=500,
            justify="left",
        ).grid(row=row + 2, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self.face_provider_device_var = tk.StringVar(value="Device: checking…")
        ttk.Label(
            frame,
            textvariable=self.face_provider_device_var,
            style="Muted.TLabel",
            wraplength=500,
            justify="left",
        ).grid(row=row + 3, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _add_face_setting_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_text: str,
        entry: ttk.Entry,
        button: ttk.Button | None = None,
        *,
        help_text: str = "",
        help_attribute: str = "",
    ) -> None:
        """Add one aligned face-setting row and register its editable widgets."""
        label_frame = ttk.Frame(parent)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(label_frame, text=label_text).pack(side="left")
        if help_text:
            icon = HelpIcon(label_frame, help_text)
            icon.pack(side="left", padx=(4, 0))
            if help_attribute:
                setattr(self, help_attribute, icon)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        self.face_setting_widgets.append(entry)

        if button is not None:
            button.grid(row=row, column=2, padx=(7, 0), pady=3)
            self.face_setting_widgets.append(button)

    # =========================================================================
    # Folders, settings, and provider help
    # =========================================================================

    def _restore_saved_paths(self) -> None:
        if not self.settings.remember_paths:
            return

        self.input_folder_var.set(self.settings.last_input_folder)
        self.output_folder_var.set(self.settings.last_output_folder)

    @staticmethod
    def _existing_directory_or_none(path_text: str) -> str | None:
        if not path_text.strip():
            return None

        candidate = Path(path_text).expanduser()
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
        return None

    def _choose_input_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose the folder containing images",
            initialdir=self._existing_directory_or_none(
                self.input_folder_var.get()
            ),
            mustexist=True,
        )
        if selected:
            self.input_folder_var.set(selected)
            self._save_current_settings()
            self._refresh_input_folder_count()

    def _choose_output_folder(self) -> None:
        initial_directory = (
            self._existing_directory_or_none(self.output_folder_var.get())
            or self._existing_directory_or_none(self.input_folder_var.get())
        )
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose the folder for the catalog and reports",
            initialdir=initial_directory,
            mustexist=True,
        )
        if selected:
            self.output_folder_var.set(selected)
            self._update_catalog_path_display()
            self._sync_browser_to_output_folder(load=True)
            self._save_current_settings()

    # =========================================================================
    # Explicit SQLite catalog management
    # =========================================================================

    def _open_video_extraction(self) -> None:
        """Open the focused source-preparation workflow and apply its handoff.

        The dialog performs extraction and any metadata-only staged import.
        Provider analysis remains owned by the main application so the same
        Florence, face-reference, download-consent, progress, and error handling
        paths are used whether images came from still files or video.
        """
        dialog = VideoExtractionDialog(
            self.root,
            settings=self.settings,
            current_catalog=self._current_catalog_path(),
            on_settings_saved=self._save_video_settings,
        )
        self.root.wait_window(dialog)

        status = dialog.ffmpeg_status
        if status.available and status.executable is not None:
            self.video_ffmpeg_status_var.set(
                f"FFmpeg ready: {status.executable}"
            )
        elif (
            status.error
            and status.error != "FFmpeg discovery has not completed."
        ):
            self.video_ffmpeg_status_var.set(
                "FFmpeg is not configured; video extraction remains unavailable."
            )

        result = dialog.result
        if result is None:
            return

        if result.catalog_import is not None:
            self._activate_catalog(
                result.catalog_import.target_database,
                load=True,
            )

        show_video_extraction_report(self.root, result)

        if result.run_analysis_requested and result.catalog_import is not None:
            import_summary = result.catalog_import
            should_start = messagebox.askyesno(
                "Start providers now?",
                (
                    "Frame extraction and catalog import are complete.\n\n"
                    f"Supported source files: {import_summary.cataloged_files:,}\n"
                    f"New unique image contents: "
                    f"{import_summary.new_unique_images:,}\n\n"
                    "Start the currently configured caption and face providers "
                    "now? This can take a long time. You can choose No and start "
                    "them later from Analyze & Update Catalog."
                ),
                parent=self.root,
            )
            if not should_start:
                self.status_var.set(
                    "Frames cataloged; providers were left for manual start."
                )
                return

            # Reuse the ordinary provider boundary only after a second explicit
            # confirmation that includes the actual imported workload.
            self.input_folder_var.set(
                str(result.extraction.destination_folder)
            )
            self.output_folder_var.set(
                str(result.catalog_import.target_database.parent)
            )
            self._update_catalog_path_display()
            self._save_current_settings()
            self._start_analysis()

    def _current_catalog_path(self) -> Path | None:
        """Return the active, existing catalog shared by all application tabs."""
        if hasattr(self, "catalog_browser") and self.catalog_browser.catalog_path is not None:
            candidate = self.catalog_browser.catalog_path.resolve()
            if candidate.exists():
                return candidate
        implied = self._catalog_path_from_output_folder()
        if implied is not None and implied.exists():
            return implied.resolve()
        return None

    def _activate_catalog(self, database_path: Path, *, load: bool = True) -> None:
        """Make one validated catalog the shared browser/readiness target.

        Catalog lifecycle controls live on the LoRA Image Curator tab, while the
        browser and readiness tabs consume the same selected path.  Keeping the
        activation step here prevents each tab from inventing its own current
        catalog state.
        """
        target = validate_catalog_database(database_path)
        self.latest_catalog_database = target
        self.output_folder_var.set(str(target.parent))
        self.catalog_path_var.set(str(target))
        self.catalog_browser.set_catalog_path(target, load=load, quiet=True)
        self.dataset_readiness.set_records(
            self.catalog_browser.all_records if load else [],
            str(target),
        )
        self._update_catalog_management_state()
        self._save_current_settings()
        self._refresh_provider_coverage()

    def _create_empty_catalog(self) -> None:
        """Create an intentionally empty catalog from the SQLite Catalog section."""
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Create an empty LoRA Image Curator catalog",
            initialdir=(
                self._existing_directory_or_none(self.output_folder_var.get())
                or self._existing_directory_or_none(self.input_folder_var.get())
            ),
            initialfile=CATALOG_FILENAME,
            defaultextension=".db",
            filetypes=(("LoRA Image Curator catalog", "*.db"), ("All files", "*.*")),
            # The application provides a catalog-specific warning that explains
            # exactly what is and is not replaced. Suppress the generic native
            # overwrite prompt so Windows does not ask the same question twice.
            confirmoverwrite=False,
        )
        if not selected:
            return
        target = Path(selected).expanduser().resolve()
        if target.exists():
            should_overwrite = messagebox.askyesno(
                "Overwrite existing catalog?",
                (
                    f"A catalog already exists at:\n\n{target}\n\n"
                    "Continuing will permanently replace that catalog database "
                    "and its catalog-owned metadata with an empty catalog. "
                    "Source images and prior exports will not be deleted.\n\n"
                    "Overwrite the existing catalog?"
                ),
                parent=self.root,
            )
            if not should_overwrite:
                return
        try:
            if target.exists():
                replace_catalog_database_with_empty(target)
            else:
                create_catalog_database(target)
            self._activate_catalog(target, load=True)
        except Exception as error:
            logging.exception("Could not create empty catalog")
            messagebox.showerror(
                "Could not create catalog",
                f"{type(error).__name__}: {error}",
                parent=self.root,
            )

    def _create_catalog_from_folder(self) -> None:
        """Create a new catalog through the staged metadata-only import workflow."""
        initial_source = self._existing_directory_or_none(self.input_folder_var.get())
        dialog = CatalogImportDialog(
            self.root,
            mode="create",
            initial_source_folder=Path(initial_source) if initial_source else None,
        )
        self.root.wait_window(dialog)
        self._finish_catalog_import(dialog.result)

    def _open_catalog(self) -> None:
        """Open an existing validated catalog without changing its contents."""
        current = self._current_catalog_path()
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Open a LoRA Image Curator catalog",
            initialdir=str(current.parent) if current is not None else None,
            filetypes=(
                ("LoRA Image Curator catalog", "dataset_tools.db"),
                ("SQLite database", "*.db"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        try:
            self._activate_catalog(Path(selected), load=True)
        except Exception as error:
            logging.exception("Could not open catalog")
            messagebox.showerror(
                "Could not open catalog",
                f"{type(error).__name__}: {error}",
                parent=self.root,
            )

    def _import_folder_into_catalog(self) -> None:
        """Ask Replace/Merge/Cancel before adding images to the current catalog."""
        current = self._current_catalog_path()
        if current is None:
            messagebox.showinfo(
                "Open a catalog",
                "Open or create the catalog that should receive the images first.",
                parent=self.root,
            )
            return

        replace = messagebox.askyesnocancel(
            "Replace current catalog contents?",
            (
                f"Add images to this catalog?\n\n{current}\n\n"
                "Yes — replace all catalog-owned contents first, including captions, "
                "review decisions, tags, image sets, saved searches, export history, "
                "and cached quality data. Source images and exports are not deleted.\n\n"
                "No — add the images and preserve existing catalog contents.\n\n"
                "Cancel — make no changes."
            ),
            parent=self.root,
        )
        if replace is None:
            return

        initial_source = self._existing_directory_or_none(self.input_folder_var.get())
        dialog = CatalogImportDialog(
            self.root,
            mode="replace" if replace else "merge",
            target_database=current,
            initial_source_folder=Path(initial_source) if initial_source else None,
        )
        self.root.wait_window(dialog)
        self._finish_catalog_import(dialog.result)

    def _finish_catalog_import(self, summary: object | None) -> None:
        """Activate and report a completed import; cancelled dialogs return None."""
        if summary is None:
            return
        # Import here avoids coupling the rest of the application to the result
        # dataclass merely for static type narrowing.
        from catalog_import import CatalogImportSummary

        if not isinstance(summary, CatalogImportSummary):
            raise TypeError("Unexpected catalog import result.")
        self.input_folder_var.set(str(summary.source_folder))
        self._activate_catalog(summary.target_database, load=True)
        show_catalog_import_report(self.root, summary)

    def _delete_current_catalog(self) -> None:
        """Permanently delete only the explicitly named active catalog."""
        current = self._current_catalog_path()
        if current is None:
            return
        try:
            validate_catalog_database(current)
        except Exception as error:
            messagebox.showerror(
                "Cannot delete this file",
                f"The selected file is not a valid LoRA Image Curator catalog:\n\n{error}",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Permanently delete catalog?",
            (
                f"Delete this catalog permanently?\n\n{current}\n\n"
                "This removes its captions, face embeddings, review decisions, tags, "
                "image sets, saved searches, export history, and cached quality data.\n\n"
                "Source images and exported datasets are not deleted."
            ),
            parent=self.root,
        ):
            return
        try:
            delete_catalog_database(current)
        except OSError as error:
            logging.exception("Could not delete catalog")
            messagebox.showerror(
                "Could not delete catalog",
                str(error),
                parent=self.root,
            )
            return

        self.catalog_browser.clear_catalog_path()
        self.dataset_readiness.set_records([], "")
        self.latest_catalog_database = None
        self.catalog_path_var.set("No catalog selected")
        self._update_catalog_management_state()
        self._refresh_provider_coverage()

    def _update_catalog_management_state(self) -> None:
        """Disable lifecycle actions only when their prerequisites are absent."""
        if not hasattr(self, "new_empty_catalog_button"):
            return
        locked = self._provider_controls_locked or self._quality_controls_locked
        has_catalog = self._current_catalog_path() is not None
        general_state = "disabled" if locked else "normal"
        for widget in (
            self.new_empty_catalog_button,
            self.create_catalog_from_folder_button,
            self.open_catalog_button,
        ):
            widget.configure(state=general_state)
        if hasattr(self, "video_source_button"):
            self.video_source_button.configure(state=general_state)
        current_state = "normal" if has_catalog and not locked else "disabled"
        self.import_catalog_folder_button.configure(state=current_state)
        self.delete_catalog_button.configure(state=current_state)
        if hasattr(self, "run_body_analysis_button"):
            self.run_body_analysis_button.configure(state=current_state)
        if hasattr(self, "run_face_analysis_button"):
            self.run_face_analysis_button.configure(state=current_state)
        if hasattr(self, "run_florence_button"):
            self.run_florence_button.configure(state=general_state)
        if hasattr(self, "check_body_setup_button"):
            self.check_body_setup_button.configure(state=general_state)
        if hasattr(self, "menu_bar"):
            self._schedule_menu_state_refresh()

    def _choose_face_reference_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose images containing the reference identity",
            initialdir=(
                self._existing_directory_or_none(
                    self.face_reference_folder_var.get()
                )
                or self._existing_directory_or_none(
                    self.input_folder_var.get()
                )
            ),
            mustexist=True,
        )
        if selected:
            self.face_reference_folder_var.set(selected)
            self._save_current_settings()

    def _choose_face_model_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose the InsightFace home folder (contains models)",
            initialdir=self._existing_directory_or_none(
                self.face_model_root_var.get()
            ),
            mustexist=True,
        )
        if selected:
            self.face_model_root_var.set(selected)
            self._save_current_settings()

    def _choose_face_model_pack(self) -> None:
        """Select an installed pack and derive InsightFace's root/name pair."""
        current_name = (
            self.face_model_name_var.get().strip() or DEFAULT_MODEL_NAME
        )
        try:
            current_path = get_model_path(
                current_name,
                self.face_model_root_var.get().strip(),
            )
        except ValueError:
            current_path = get_model_path(DEFAULT_MODEL_NAME, "")

        initial_directory = (
            str(current_path)
            if current_path.exists()
            else str(current_path.parent)
            if current_path.parent.exists()
            else None
        )
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose an installed InsightFace model-pack folder",
            initialdir=initial_directory,
            mustexist=True,
        )
        if not selected:
            return

        try:
            model_name, model_root = model_selection_from_pack_folder(selected)
        except ValueError as error:
            messagebox.showerror(
                "Invalid InsightFace model pack",
                str(error),
                parent=self.root,
            )
            return

        self.face_model_name_var.set(model_name)
        self.face_model_root_var.set(str(model_root))
        self._save_current_settings()
        self.status_var.set(f"Selected InsightFace model pack: {model_name}")

    def _face_model_selection(self) -> tuple[str, str] | None:
        """Validate typed model settings before diagnostics or provider work."""
        try:
            model_name = normalize_model_name(
                self.face_model_name_var.get().strip() or DEFAULT_MODEL_NAME
            )
            model_root = self.face_model_root_var.get().strip()
            get_model_path(model_name, model_root)
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "Invalid InsightFace model selection",
                str(error),
                parent=self.root,
            )
            return None
        return model_name, model_root

    def _catalog_path_from_output_folder(self) -> Path | None:
        """Return the catalog implied by the analysis output folder."""
        output_text = self.output_folder_var.get().strip()
        return Path(output_text) / CATALOG_FILENAME if output_text else None

    def _update_catalog_path_display(self) -> None:
        catalog_path = self._catalog_path_from_output_folder()
        self.catalog_path_var.set(
            str(catalog_path) if catalog_path is not None else "Choose an output folder"
        )

    def _sync_browser_to_output_folder(self, *, load: bool = False) -> None:
        """Keep the browser pointed at the catalog used by the analysis tab."""
        catalog_path = self._catalog_path_from_output_folder()
        if catalog_path is None or not hasattr(self, "catalog_browser"):
            return
        if catalog_path.exists():
            self._activate_catalog(catalog_path, load=load)
        else:
            self.catalog_browser.clear_catalog_path()
            self.dataset_readiness.set_records([], "")
            self._update_catalog_management_state()
            self._refresh_provider_coverage()

    def _on_notebook_tab_changed(self, _event: tk.Event) -> None:
        """Load catalog data lazily for the browser and readiness dashboard."""
        selected_tab = self.notebook.select()
        self._build_menu_bar()
        if selected_tab not in {str(self.browser_tab), str(self.readiness_tab)}:
            return

        # Preserve a catalog chosen directly in the browser. Analysis-folder
        # changes already call ``_sync_browser_to_output_folder`` explicitly.
        catalog_path = (
            self.catalog_browser.catalog_path
            or self._catalog_path_from_output_folder()
        )
        if catalog_path is None or not catalog_path.exists():
            if selected_tab == str(self.readiness_tab):
                self.dataset_readiness.set_records([], "")
            return

        if (
            self.catalog_browser.catalog_path != catalog_path.resolve()
            or not self.catalog_browser.all_records
        ):
            self.catalog_browser.set_catalog_path(
                catalog_path,
                load=True,
                quiet=True,
            )

        if selected_tab == str(self.readiness_tab):
            self.dataset_readiness.set_records(
                self.catalog_browser.all_records,
                str(self.catalog_browser.catalog_path or catalog_path),
            )

    def _show_readiness_query(self, query: str) -> None:
        """Reveal one readiness issue using the browser's normal query UI."""
        self.catalog_browser._clear_browser_filters(apply=False)
        self.catalog_browser.apply_external_query(query, remember=False)
        self.notebook.select(self.browser_tab)

    def _load_readiness_records(self) -> tuple[list[object], str]:
        """Reload readiness data without introducing a second catalog projection."""
        if self.catalog_browser.repository is None or self.catalog_browser.catalog_path is None:
            return [], ""
        self.catalog_browser.refresh(quiet=True)
        return list(self.catalog_browser.all_records), str(self.catalog_browser.catalog_path)

    def _sync_readiness_interpretation_from_browser(
        self,
        profile_key: str,
        blur_threshold: float,
        duplicate_similarity_percent: int,
    ) -> None:
        """Keep Browser Filters and Finalize & Export on identical rules."""
        profile = READINESS_PROFILES_BY_KEY.get(
            profile_key,
            READINESS_PROFILES_BY_KEY[DEFAULT_READINESS_PROFILE_KEY],
        )
        self.settings.readiness_profile_key = profile.key
        self.settings.quality_blur_threshold = float(blur_threshold)
        self.settings.quality_duplicate_similarity_percent = int(
            duplicate_similarity_percent
        )
        self.dataset_readiness.settings = self.settings
        self.dataset_readiness.profile_var.set(profile.label)
        self.dataset_readiness.blur_threshold_var.set(f"{blur_threshold:g}")
        self.dataset_readiness.duplicate_similarity_var.set(
            duplicate_similarity_percent
        )
        self.dataset_readiness.duplicate_percent_var.set(
            self.dataset_readiness._similarity_label(
                duplicate_similarity_percent
            )
        )
        if self.dataset_readiness._records:
            self.dataset_readiness._render_current()

    def _open_readiness_export(
        self,
        image_ids: list[int],
        scope_label: str,
        report: DatasetReadinessReport,
        readiness_profile_key: str,
    ) -> None:
        """Open the shared exporter for a reviewed Finalize & Export scope."""
        if self.catalog_browser.catalog_path is None or not image_ids:
            return
        dialog = DatasetExportDialog(
            self.readiness_tab,
            database_path=self.catalog_browser.catalog_path,
            image_ids=image_ids,
            settings=self.catalog_browser.settings,
            on_settings_saved=self.catalog_browser._save_browser_settings,
            scope_label=scope_label,
            readiness_report=report,
            initial_profile_key=READINESS_TO_EXPORT_PROFILE.get(
                readiness_profile_key,
                "general_lora",
            ),
        )
        self.root.wait_window(dialog)

    def _on_quality_running_changed(self, running: bool) -> None:
        """Prevent catalog switching/mutation while quality analysis is active."""
        self._quality_controls_locked = running
        self._schedule_menu_state_refresh()
        self._update_catalog_management_state()

    def _on_face_enabled_changed(self) -> None:
        self._toggle_face_controls()
        self._save_current_settings()

    def _toggle_face_controls(self) -> None:
        # The checkbox controls only whether Face participates in Run All.
        # Independent Run Face remains configurable regardless of that choice.
        state = "disabled" if self._provider_controls_locked else "normal"
        for widget in self.face_setting_widgets:
            widget.configure(state=state)

    def _read_threshold(
        self,
        variable: tk.StringVar,
        label: str,
        default_value: float,
        *,
        show_error: bool = True,
    ) -> float | None:
        text = variable.get().strip()
        if not text:
            variable.set(str(default_value))
            return default_value

        try:
            value = float(text)
        except ValueError:
            if show_error:
                messagebox.showerror(
                    "Invalid threshold",
                    f"{label} must be a number between 0 and 1.",
                    parent=self.root,
                )
            return None

        if not 0.0 <= value <= 1.0:
            if show_error:
                messagebox.showerror(
                    "Invalid threshold",
                    f"{label} must be between 0 and 1.",
                    parent=self.root,
                )
            return None

        return value

    def _save_current_settings(self) -> None:
        remember_paths = self.remember_paths_var.get()
        similarity = self._read_threshold(
            self.face_similarity_threshold_var,
            "Identity threshold",
            DEFAULT_SIMILARITY_THRESHOLD,
            show_error=False,
        )
        detection = self._read_threshold(
            self.face_detection_threshold_var,
            "Detection threshold",
            DEFAULT_DETECTION_THRESHOLD,
            show_error=False,
        )

        # The browser owns export preferences while its dialog is open.
        # Preserve those fields when the analysis tab rewrites the shared
        # settings file; otherwise closing the application would silently reset
        # the last export directory/profile to defaults.
        browser_settings = getattr(self.catalog_browser, "settings", self.settings)

        settings = AppSettings(
            # Preserve the versioned first-launch acknowledgment. Omitting it
            # here reconstructed AppSettings with an empty default during
            # ordinary shutdown, causing the same notice to reappear on every
            # launch even though the user had already selected OK.
            third_party_notice_version=(
                browser_settings.third_party_notice_version
            ),
            remember_paths=remember_paths,
            appearance_theme=normalize_theme_key(self.theme_key_var.get()),
            catalog_import_include_subfolders=(
                browser_settings.catalog_import_include_subfolders
            ),
            caption_include_subfolders=(
                browser_settings.caption_include_subfolders
            ),
            face_include_subfolders=browser_settings.face_include_subfolders,
            face_reference_include_subfolders=(
                browser_settings.face_reference_include_subfolders
            ),
            quarantine_directory=browser_settings.quarantine_directory,
            # This preference is owned by the central Settings dialog. Omitting
            # it here reconstructed AppSettings with the conservative default
            # immediately after Save, which made the checkbox appear unable to
            # persist and prevented delete-time catalog cleanup.
            delete_catalog_record_with_file=(
                browser_settings.delete_catalog_record_with_file
            ),
            confirm_trash_deletion=browser_settings.confirm_trash_deletion,
            body_provider_key=browser_settings.body_provider_key,
            body_model_path=browser_settings.body_model_path,
            body_detection_threshold=(
                browser_settings.body_detection_threshold
            ),
            body_landmark_visibility_threshold=(
                browser_settings.body_landmark_visibility_threshold
            ),
            body_full_body_threshold_percent=(
                browser_settings.body_full_body_threshold_percent
            ),
            allow_provider_telemetry=(
                browser_settings.allow_provider_telemetry
            ),
            last_input_folder=(
                self.input_folder_var.get().strip() if remember_paths else ""
            ),
            last_output_folder=(
                self.output_folder_var.get().strip() if remember_paths else ""
            ),
            include_triage=self.include_triage_var.get(),
            reuse_stored_analysis=self.reuse_analysis_var.get(),
            video_ffmpeg_path=self.settings.video_ffmpeg_path,
            video_last_source=self.settings.video_last_source,
            video_last_destination=self.settings.video_last_destination,
            video_sampling_mode=self.settings.video_sampling_mode,
            video_interval_seconds=self.settings.video_interval_seconds,
            video_scene_threshold=self.settings.video_scene_threshold,
            video_max_frames=self.settings.video_max_frames,
            video_output_format=self.settings.video_output_format,
            run_face_analysis=self.run_face_analysis_var.get(),
            face_identity_name=self.face_identity_name_var.get().strip(),
            face_reference_folder=self.face_reference_folder_var.get().strip(),
            face_model_name=(
                self.face_model_name_var.get().strip() or DEFAULT_MODEL_NAME
            ),
            face_model_root=self.face_model_root_var.get().strip(),
            face_similarity_threshold=(
                similarity
                if similarity is not None
                else DEFAULT_SIMILARITY_THRESHOLD
            ),
            face_detection_threshold=(
                detection
                if detection is not None
                else DEFAULT_DETECTION_THRESHOLD
            ),
            browser_sort=browser_settings.browser_sort,
            browser_filter=browser_settings.browser_filter,
            browser_last_catalog=browser_settings.browser_last_catalog,
            browser_search_history_enabled=(
                browser_settings.browser_search_history_enabled
            ),
            browser_search_history_max=browser_settings.browser_search_history_max,
            browser_search_history=list(browser_settings.browser_search_history),
            browser_images_per_page=browser_settings.browser_images_per_page,
            readiness_profile_key=self.dataset_readiness._current_profile_key(),
            quality_blur_threshold=(
                self.dataset_readiness._current_blur_threshold()
                if self.dataset_readiness._current_blur_threshold() is not None
                else self.settings.quality_blur_threshold
            ),
            quality_duplicate_similarity_percent=round(
                self.dataset_readiness.duplicate_similarity_var.get()
            ),
            export_last_directory=browser_settings.export_last_directory,
            export_profile_key=browser_settings.export_profile_key,
            export_copy_images=browser_settings.export_copy_images,
            export_create_sidecars=browser_settings.export_create_sidecars,
            export_create_manifest=browser_settings.export_create_manifest,
            export_create_readme=browser_settings.export_create_readme,
            export_collision_policy=browser_settings.export_collision_policy,
            export_custom_include_trigger=(
                browser_settings.export_custom_include_trigger
            ),
            export_custom_include_manual_tags=(
                browser_settings.export_custom_include_manual_tags
            ),
            export_custom_include_ai_tags=(
                browser_settings.export_custom_include_ai_tags
            ),
            export_custom_include_raw_caption=(
                browser_settings.export_custom_include_raw_caption
            ),
        )

        try:
            save_settings(settings)
            self.settings = settings
            self.catalog_browser.settings = settings
            self.dataset_readiness.settings = settings
        except OSError as error:
            logging.exception("Could not save settings")
            self._append_log(f"Warning: settings could not be saved: {error}")

    def _save_video_settings(self, video_settings: AppSettings) -> None:
        """Merge dialog-owned video preferences into the shared settings copy."""
        self.settings.video_ffmpeg_path = video_settings.video_ffmpeg_path
        self.settings.video_last_source = video_settings.video_last_source
        self.settings.video_last_destination = (
            video_settings.video_last_destination
        )
        self.settings.video_sampling_mode = video_settings.video_sampling_mode
        self.settings.video_interval_seconds = (
            video_settings.video_interval_seconds
        )
        self.settings.video_scene_threshold = (
            video_settings.video_scene_threshold
        )
        self.settings.video_max_frames = video_settings.video_max_frames
        self.settings.video_output_format = video_settings.video_output_format
        self._save_current_settings()

    def _show_general_help(self) -> None:
        messagebox.showinfo(
            "LoRA Image Curator Help",
            (
                "Current workflow\n\n"
                "1. Start from still images, or use Video Sources to extract "
                "candidate frames with a user-installed FFmpeg.\n"
                "2. Choose an input image folder.\n"
                "3. Choose an output folder. Its dataset_tools.db is the "
                "persistent catalog.\n"
                "4. Florence creates captions and optional triage metadata.\n"
                "5. The optional face provider stores local face embeddings "
                "and identity suggestions.\n"
                "6. Review, search, group duplicates, and curate image sets in "
                "the Catalog Browser.\n"
                "7. Finalize & Export can run cached local Blur and duplicate "
                "analysis, review a catalog or image set, and export a "
                "training handoff.\n"
                "8. Reuse keeps unchanged images from being analyzed again.\n\n"
                "Safety\n\n"
                "Video extraction and analysis are local. They do not move, "
                "delete, or alter source videos/images or upload images, video, "
                "embeddings, or hashes. Catalog deletion never deletes source "
                "material. Telemetry-related permission is disabled by default.\n\n"
                "Third-party models, providers, applications, and websites are "
                "not controlled by LoRA Image Curator. Review their licenses, "
                "terms, and privacy notices separately.\n\n"
                "Use the Help menu for tab-specific guidance, shortcuts, face "
                "provider details, and licensing. Circled question marks beside "
                "technical fields provide short contextual reminders."
            ),
            parent=self.root,
        )

    def _show_analysis_help(self) -> None:
        self._show_help_text(
            "Analyze & Update Catalog Help",
            (
                "VIDEO SOURCES\n\n"
                "Extract candidate stills from local videos before cataloging. "
                "FFmpeg is user-installed and the original video is unchanged.\n\n"
                "CATALOG FOLDERS\n\n"
                "Input images are read in place. Catalog and reports chooses the "
                "folder that owns dataset_tools.db and provider reports.\n\n"
                "FLORENCE-2\n\n"
                "Creates captions. Optional triage also detects objects, reads "
                "visible text, estimates person count, and flags screenshots. "
                "Large collections can take much longer than cataloging; elapsed "
                "time and measured ETA appear during the run.\n\n"
                "FACE ANALYSIS\n\n"
                "Detects faces and can compare them to a user-supplied reference "
                "identity. Trigger Keyword is the intended LoRA activation text; "
                "it is not a claim that face detection recognized a public name.\n\n"
                "BODY / POSE ANALYSIS\n\n"
                "After cataloging, use the Body / Pose provider row to check the "
                "local MediaPipe setup or analyze the current catalog. The results "
                "power face-evidence and body/pose filters in the browser.\n\n"
                "RUN SAFETY\n\n"
                "Cancel Run stops cooperatively between images. Results already "
                "committed remain reusable, and source images are not altered."
            ),
        )

    def _show_browser_help(self) -> None:
        self._show_help_text(
            "Catalog Browser Help",
            (
                "SEARCH AND FILTER\n\n"
                "Ordinary search uses applied tags and Trigger Keywords, not "
                "filenames. Advanced Search builds explicit metadata queries. "
                "Filters combines image-set scope, general catalog state, face "
                "evidence, body/pose evidence, and readiness findings without "
                "changing selection. The highlighted Filters On button means at "
                "least one visibility constraint is active. Saved searches and "
                "search history are under Browser.\n\n"
                "PAGES AND SELECTION\n\n"
                "Each page contains at most 100 thumbnails to avoid Windows/Tk "
                "deep-canvas clipping. Ctrl+A selects all current results across "
                "pages. Explicit Current Page commands remain under Selection. "
                "Select/Deselect by Keyword also covers every result page.\n\n"
                "CURATION\n\n"
                "Use Selection > Remove Unnecessary Images or N. Its focused "
                "dialog collects curation checks, then Preview Deselection shows "
                "a report before changing selection. It never deletes files or records.\n\n"
                "UNDO AND REDO\n\n"
                "Ctrl+Z and Ctrl+Y follow one chronological history containing "
                "selection changes and durable catalog edits. The bottom status "
                "line describes the last action.\n\n"
                "FILE ACTIONS\n\n"
                "Quarantine Selected moves every present physical location for "
                "the selected catalog images into the configured reversible "
                "quarantine folder. Restore Selected returns them without "
                "overwriting occupied paths. Delete sends files to the operating "
                "system Recycle Bin and has no permanent-delete fallback."
            ),
        )

    def _show_finalize_help(self) -> None:
        self._show_help_text(
            "Finalize & Export Help",
            (
                "Choose the intended LoRA target and review readiness findings "
                "before export. Quality analysis is local and cached in the "
                "catalog. Duplicate findings are review aids, not automatic file "
                "deletion.\n\n"
                "Training-text checks use the same profile-aware builder as export. "
                "Export copies only the chosen scope and can create sidecars, a "
                "manifest, and a README. Source images and the catalog remain "
                "unchanged."
            ),
        )

    def _show_shortcuts_help(self) -> None:
        self._show_help_text(
            "Keyboard Shortcuts",
            (
                "GENERAL TEXT EDITING\n\n"
                "Ctrl+X  Cut selected text\n"
                "Ctrl+C  Copy selected text\n"
                "Ctrl+V  Paste text\n"
                "Ctrl+A  Select text when a text field has focus\n"
                "F1      Open this shortcut reference\n\n"
                "CATALOG BROWSER\n\n"
                "Ctrl+A          Select all current results across pages\n"
                "Ctrl+Shift+A    Select current thumbnail page\n"
                "Esc             Deselect all\n"
                "Ctrl+D          Deselect all\n"
                "Ctrl+Shift+D    Deselect current page\n"
                "Ctrl+I          Invert all current results\n"
                "Ctrl+Shift+I    Invert current page\n"
                "Ctrl+Z          Undo latest selection or catalog action\n"
                "Ctrl+Y          Redo\n"
                "Ctrl+Shift+Z    Alternate Redo\n"
                "Ctrl+F          Focus browser search\n"
                "Ctrl+Shift+F    Open Filters\n"
                "Ctrl+E          Export selected images\n"
                "N               Remove Unnecessary Images\n"
                "F5              Refresh the catalog browser\n"
                "Alt+Left        Previous page\n"
                "Alt+Right       Next page\n\n"
                "Browser shortcuts never replace Ctrl+A/Cut/Copy/Paste while a "
                "search box, keyword field, notes field, or other text editor has focus."
            ),
        )

    def _show_video_help(self) -> None:
        self._show_help_text(
            "Video Extraction Help",
            (
                "FFMPEG\n\n"
                "LoRA Image Curator uses a user-installed ffmpeg.exe and checks it "
                "after the extraction dialog appears. It never downloads or "
                "updates FFmpeg automatically.\n\n"
                "SAMPLING\n\n"
                "Fixed interval keeps one frame at the chosen time interval. "
                "Scene changes keeps the opening frame and frames whose visual "
                "change exceeds the threshold. Lower scene thresholds retain "
                "subtler cuts and can create many more images.\n\n"
                "OUTPUT SAFETY\n\n"
                "The source video is read only. Extracted frames are written to "
                "the chosen destination, and existing matching frame filenames "
                "are not overwritten.\n\n"
                "AFTER EXTRACTION\n\n"
                "Frames may remain as files, be added to the current catalog, "
                "or create a new catalog. Creating an image set groups the "
                "result without copying or moving the extracted files."
            ),
        )

    def _show_license_help(self) -> None:
        license_path = Path(__file__).with_name("MODEL_LICENSES.txt")
        try:
            details = license_path.read_text(encoding="utf-8")
        except OSError as error:
            details = f"Model license inventory could not be read:\n\n{error}"
        self._show_help_text(f"{APP_NAME} Licensing", details)

    def _show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            (
                f"{APP_NAME} v{APP_VERSION}\n\n"
                "A local-first desktop application for cataloging, reviewing, "
                "curating, and exporting image datasets for LoRA training.\n\n"
                f"Created by {AUTHOR_NAME}.\n"
                "License: MIT\n\n"
                "Third-party models, provider packages, applications, websites, "
                "licenses, privacy practices, outputs, and future changes remain "
                "under their respective authors' or operators' control. LoRA "
                "Image Curator cannot certify or accept responsibility for them.\n\n"
                "See Help > Licensing for application, dependency, provider, and "
                "model-license boundaries, and Help > Privacy & Third-Party "
                "Products for the complete user-facing notice."
            ),
            parent=self.root,
        )

    def _show_help_text(self, title: str, content: str) -> None:
        """Open a readable, scrollable help topic without crowding message boxes."""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("760x620")
        window.minsize(560, 420)
        window.transient(self.root)

        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap="word",
            padx=12,
            pady=10,
            font=get_ui_font(self.root, size=10),
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", content.strip())
        text.configure(state="disabled")

        ttk.Button(window, text="Close", command=window.destroy).pack(pady=(0, 12))
        window.bind("<Escape>", lambda _event: window.destroy())
        window.focus_set()

    def _show_face_help(self) -> None:
        selection = self._face_model_selection()
        if selection is None:
            return
        model_name, model_root = selection
        default_model_path = get_model_path(
            model_name,
            model_root,
        )
        messagebox.showinfo(
            "Face Provider Help",
            (
                "What this provider creates\n\n"
                "• face count and bounding boxes\n"
                "• detector confidence and landmarks\n"
                "• normalized identity embeddings stored in SQLite\n"
                "• similarity scores against one named reference identity\n"
                "• suggested identity tags that remain unconfirmed\n\n"
                "Reference folder\n\n"
                "Use several clear images of the same person. One visible face "
                "per image is best. If an image contains multiple faces, this "
                "version uses the largest face and records that choice.\n\n"
                "Thresholds\n\n"
                "A higher identity threshold creates fewer, more conservative "
                "matches. 0.48 is a starting point, not a universal truth. "
                "Detection threshold controls whether weak face detections are "
                "kept.\n\n"
                "Model home\n\n"
                "Leave blank for the normal InsightFace location:\n"
                f"{default_model_path.parent.parent}\n\n"
                "Model pack Browse selects an installed compatible pack folder "
                "inside an InsightFace models directory and updates both the "
                "pack name and model home. Model weights are not bundled.\n\n"
                "See Help > Licensing for code, dependency, and model-weight "
                "license boundaries."
            ),
            parent=self.root,
        )

    def _show_body_help(self) -> None:
        self._show_help_text(
            "Body Analysis & Models",
            (
                "CURRENT VETTED PROVIDER\n\n"
                "Google MediaPipe Pose Landmarker is the first supported body "
                "provider. It uses a local .task model and returns 33 pose "
                "landmarks with visibility evidence. Ordinary analysis is local; "
                "LoRA Image Curator does not upload images or catalog results.\n\n"
                "INSTALLATION\n\n"
                "Open Setup & Repair from the Tools menu, or run “Setup and "
                "Launch LoRA Image Curator.bat” and choose optional body/pose "
                "analysis. It installs MediaPipe and can "
                "download Google's documented Pose Landmarker Full model into "
                "the per-user model folder. Then use Settings > Body / Pose Scanning "
                "to browse to the model and run Check Compatibility.\n\n"
                "IMPORT FILTERING\n\n"
                "Create from Images and Add Images offer opt-in rules to skip "
                "files with no body/pose or no visible-face pose evidence. The "
                "face rule uses pose head landmarks; it is not face recognition "
                "and may produce false positives or negatives.\n\n"
                "BROWSER FILTERS\n\n"
                "After body analysis, Filters > Subject Evidence exposes separate "
                "Face and Body / Pose sections. Full-body evidence can also be "
                "used as the browser sort order.\n\n"
                "THRESHOLDS\n\n"
                "Settings exposes pose detection, landmark visibility, and "
                "full-body completeness. The full-body slider begins at the "
                "permissive useful edge (60%) and extends to 100%. Automated "
                "classification is review evidence, not a guarantee."
            ),
        )

    def _show_privacy_help(self) -> None:
        self._show_help_text(
            "Privacy & Third-Party Products",
            (
                "TELEMETRY\n\n"
                "LoRA Image Curator implements no application telemetry. Provider "
                "telemetry permission is disabled by default. The current local "
                "MediaPipe analysis path has no application-configured collector, "
                "data transmission, or telemetry purpose. Explicit dependency or "
                "model downloads are separate, user-started network operations. "
                "Every attempt to enable provider telemetry shows a disclosure. A "
                "future provider must identify its collector, data categories, "
                "and purpose before this permission can be used.\n\n"
                "THIRD-PARTY BOUNDARY\n\n"
                "LoRA Image Curator does not control third-party models, provider "
                "packages, applications, websites, services, licenses, terms, "
                "privacy practices, security, availability, outputs, accuracy, "
                "compatibility, or future changes. Their authors and operators "
                "remain responsible for their products. Users must review and "
                "accept applicable licenses, terms, and privacy notices.\n\n"
                "A compatibility check only tests whether the selected artifact "
                "matches the interface this release expects. It does not certify "
                "safety, provenance, legality, accuracy, or fitness for a "
                "particular purpose. This release blocks arbitrary executable "
                "provider packages and supports only vetted provider paths.\n\n"
                "GENERAL NOTICE\n\n"
                "The application is provided under the MIT License without "
                "warranty. This notice is practical product information, not "
                "legal advice. See Help > Licensing and the bundled LICENSE, "
                "MODEL_LICENSES.txt, and THIRD_PARTY_NOTICE.md files."
            ),
        )

    def _show_settings(self, section: str = "paths") -> None:
        """Open the central settings surface at the requested responsibility."""
        dialog = SettingsDialog(
            self.root,
            settings=self.settings,
            on_save=self._apply_settings_dialog,
            initial_section=section,
        )
        self.root.wait_window(dialog)

    def _apply_settings_dialog(self, updated: AppSettings) -> None:
        """Merge settings-dialog fields without losing tab-owned preferences."""
        save_settings(updated)
        self.settings = updated
        self.remember_paths_var.set(updated.remember_paths)
        self.reuse_analysis_var.set(updated.reuse_stored_analysis)
        self.include_triage_var.set(updated.include_triage)
        self.run_face_analysis_var.set(updated.run_face_analysis)
        self.face_identity_name_var.set(updated.face_identity_name)
        self.face_reference_folder_var.set(updated.face_reference_folder)
        self.face_model_name_var.set(updated.face_model_name or DEFAULT_MODEL_NAME)
        self.face_model_root_var.set(updated.face_model_root)
        self.face_similarity_threshold_var.set(
            f"{updated.face_similarity_threshold:g}"
        )
        self.face_detection_threshold_var.set(
            f"{updated.face_detection_threshold:g}"
        )
        self.catalog_browser.settings = updated
        self.dataset_readiness.settings = updated
        self.catalog_browser.apply_analysis_settings(
            profile_key=updated.readiness_profile_key,
            blur_threshold=updated.quality_blur_threshold,
            duplicate_similarity_percent=(
                updated.quality_duplicate_similarity_percent
            ),
        )
        self.dataset_readiness.blur_threshold_var.set(
            f"{updated.quality_blur_threshold:g}"
        )
        profile = READINESS_PROFILES_BY_KEY.get(
            updated.readiness_profile_key,
            READINESS_PROFILES_BY_KEY[DEFAULT_READINESS_PROFILE_KEY],
        )
        self.dataset_readiness.profile_var.set(profile.label)
        self.dataset_readiness.duplicate_similarity_var.set(
            updated.quality_duplicate_similarity_percent
        )
        self.dataset_readiness.duplicate_percent_var.set(
            self.dataset_readiness._similarity_label(
                updated.quality_duplicate_similarity_percent
            )
        )
        if self.dataset_readiness._records:
            self.dataset_readiness._render_current()
        self._refresh_body_provider_status()
        self._refresh_provider_device_status()
        self._refresh_input_folder_count()
        self._toggle_face_controls()
        self.video_ffmpeg_status_var.set(
            (
                "Saved FFmpeg location will be validated when opened."
                if updated.video_ffmpeg_path
                else "FFmpeg will be auto-detected when opened."
            )
        )
        self._save_current_settings()

    def _body_model_path(self) -> Path:
        raw = self.settings.body_model_path.strip()
        return (
            Path(raw).expanduser()
            if raw
            else get_default_body_model_path()
        )

    def _body_analysis_options(self) -> BodyAnalysisOptions:
        return BodyAnalysisOptions(
            detection_threshold=self.settings.body_detection_threshold,
            landmark_visibility_threshold=(
                self.settings.body_landmark_visibility_threshold
            ),
            full_body_threshold_percent=(
                self.settings.body_full_body_threshold_percent
            ),
        )

    def _check_body_setup(self) -> None:
        dialog = BodySetupDialog(
            self.root,
            model_path=self._body_model_path(),
        )
        self.root.wait_window(dialog)

    def _open_setup_and_repair(self, reason: str = "") -> bool:
        """Close the GUI and open the established setup assistant.

        Dependency changes never run inside the live application process.  The
        assistant remains the one owner of venv, PyTorch, optional package, and
        FFmpeg checks, so Tools and first-time setup cannot drift into two
        different installation systems.
        """
        reason_text = f"{reason}\n\n" if reason else ""
        if not messagebox.askyesno(
            "Open Setup & Repair?",
            (
                reason_text
                + "LoRA Image Curator must close before packages or runtimes are "
                "installed or repaired. Catalogs, images, settings, models, "
                "and completed provider results will not be removed.\n\n"
                "Close the application and open the existing Setup & Launch "
                "menu now?"
            ),
            parent=self.root,
        ):
            return False

        setup_batch = Path(__file__).with_name(
            "Setup and Launch LoRA Image Curator.bat"
        )
        try:
            if os.name == "nt" and hasattr(os, "startfile"):
                os.startfile(str(setup_batch))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("setup_assistant.py"))],
                    cwd=Path(__file__).resolve().parent,
                )
        except OSError as error:
            messagebox.showerror(
                "Could not open setup",
                f"{type(error).__name__}: {error}",
                parent=self.root,
            )
            return False

        self._finish_close()
        return True

    def _offer_setup_for_missing_packages(self, provider_label: str) -> None:
        """Explain a package boundary, then reuse the setup assistant action."""
        self._open_setup_and_repair(
            (
                f"{provider_label} cannot run until its optional Python "
                "packages are installed. No packages were downloaded or "
                "changed. In Setup & Launch, select the matching optional "
                "component."
            )
        )

    def _run_body_analysis(self) -> None:
        model_path = self._body_model_path()
        setup = inspect_body_setup(model_path, perform_runtime_check=False)
        if not setup.package_installed:
            self._offer_setup_for_missing_packages("Body / Pose Analysis")
            return
        if not setup.model_exists:
            default_model_path = get_default_body_model_path().resolve()
            if model_path.expanduser().resolve() != default_model_path:
                messagebox.showerror(
                    "Selected body model is missing",
                    (
                        f"The selected model file does not exist:\n\n{model_path}\n\n"
                        "Choose an existing vetted model in Settings, or clear "
                        "the custom path to use the recommended model download."
                    ),
                    parent=self.root,
                )
                return
            component = get_component("mediapipe_pose_full_v1")
            approved = messagebox.askyesno(
                "Download MediaPipe Pose model?",
                (
                    "Body / Pose Analysis needs the following local model:\n\n"
                    f"{component['artifact']}\n"
                    f"Publisher: {component['publisher']}\n"
                    f"Download: {format_download_size(component['approx_download_bytes'])}\n"
                    f"License: {component['license']}\n"
                    f"Source: {component['source_url']}\n"
                    f"Save to: {model_path}\n\n"
                    "The pinned file will be checked against its expected size "
                    "and SHA-256 before it replaces anything. Download it now?"
                ),
                parent=self.root,
            )
            if not approved:
                return
            dialog = ProviderDownloadDialog(
                self.root,
                title="Downloading MediaPipe Pose model",
                status_text=(
                    "Downloading the approved model from Google and verifying "
                    "its exact size and SHA-256…"
                ),
                download_action=lambda: self._download_body_model(model_path),
                on_complete=self._run_body_analysis,
            )
            self.root.wait_window(dialog)
            return
        if not setup.model_filename_vetted:
            messagebox.showerror(
                "Body model is not vetted",
                "Use Settings to choose a vetted MediaPipe Pose Landmarker "
                "lite, full, or heavy .task file, then run Check Setup.",
                parent=self.root,
            )
            return

        catalog_path = self._current_catalog_path()
        if catalog_path is None:
            messagebox.showinfo(
                "Choose a catalog",
                "Open or create a catalog before running body analysis.",
                parent=self.root,
            )
            return
        image_ids: list[int] | None = None
        selected = sorted(self.catalog_browser.selected_image_ids)
        if selected:
            choice = messagebox.askyesnocancel(
                "Choose body-analysis scope",
                (
                    f"{len(selected):,} catalog images are selected.\n\n"
                    "Yes: analyze only the selected images.\n"
                    "No: analyze every image with a present source file.\n"
                    "Cancel: do not start."
                ),
                parent=self.root,
            )
            if choice is None:
                return
            if choice:
                image_ids = selected
        self._set_running_provider("body")
        try:
            dialog = BodyAnalysisDialog(
                self.root,
                database_path=catalog_path,
                model_path=self._body_model_path(),
                options=self._body_analysis_options(),
                image_ids=image_ids,
                on_complete=lambda _summary: self._body_analysis_completed(),
            )
            self.root.wait_window(dialog)
        finally:
            self._set_running_provider(None)

    @staticmethod
    def _download_body_model(model_path: Path) -> None:
        """Call the registry-pinned atomic downloader only after GUI approval."""
        from install_body_dependencies import download_model

        download_model(model_path)

    def _body_analysis_completed(self) -> None:
        """Refresh browser evidence and provider coverage after a body pass."""
        self.catalog_browser.refresh(quiet=True)
        self._refresh_provider_coverage()

    def _check_face_setup(self) -> None:
        selection = self._face_model_selection()
        if selection is None:
            return
        model_name, model_root = selection
        status = inspect_face_setup(
            model_name=model_name,
            model_root=model_root,
        )
        providers = (
            ", ".join(status.available_execution_providers)
            if status.available_execution_providers
            else "none"
        )
        notes = "\n".join(f"• {note}" for note in status.notes) or "• No warnings"

        messagebox.showinfo(
            "Face Analysis Setup",
            (
                f"InsightFace: {status.insightface_version}\n"
                f"ONNX Runtime: {status.onnxruntime_version}\n"
                f"Execution providers: {providers}\n"
                f"Recommended provider: {status.recommended_execution_provider}\n\n"
                f"Model path:\n{status.model_path}\n"
                f"Model installed: {'yes' if status.model_installed else 'no'}\n\n"
                f"Notes\n{notes}"
            ),
            parent=self.root,
        )

    def _confirm_florence_download_if_needed(self) -> bool | None:
        """Return download authority, or ``None`` when the run was cancelled."""
        cache = inspect_florence_cache()
        if cache.model_ready:
            return False
        component = get_component("florence_model")
        approved = messagebox.askyesno(
            "Download Florence-2 model?",
            (
                "Florence Caption & Triage needs the following model, and the "
                "exact reviewed revision is not complete in the local cache:\n\n"
                f"{component['artifact']}\n"
                f"Publisher: {component['publisher']}\n"
                f"Download: {format_download_size(component['approx_download_bytes'])}\n"
                f"License: {component['license']}\n"
                f"Source: {component['source_url']}\n"
                f"Cache: {cache.cache_root}\n\n"
                "Download and cache this pinned revision, then run Florence?"
            ),
            parent=self.root,
        )
        return True if approved else None

    def _confirm_face_model_download(
        self,
        setup: FaceSetupStatus,
        model_name: str,
    ) -> bool | None:
        """Ask before InsightFace acquires restricted pretrained weights."""
        if setup.model_installed:
            return False
        if model_name != DEFAULT_MODEL_NAME:
            messagebox.showerror(
                "Selected face model is missing",
                (
                    f"The selected model pack is not installed:\n\n{setup.model_path}"
                    "\n\nAutomatic download is available only for the reviewed "
                    f"{DEFAULT_MODEL_NAME} pack. Choose an installed/licensed "
                    "pack in Settings, or select the default pack and try again."
                ),
                parent=self.root,
            )
            return None

        component = get_component("insightface_buffalo_l")
        approved = messagebox.askyesno(
            "Download InsightFace buffalo_l?",
            (
                f"The selected model pack is not installed:\n\n{setup.model_path}\n\n"
                f"Model: {component['artifact']}\n"
                f"Publisher: {component['publisher']}\n"
                f"Download: {format_download_size(component['approx_download_bytes'])}\n"
                f"Source: {component['source_url']}\n"
                f"Save to: {setup.model_path}\n\n"
                "IMPORTANT LICENSE: InsightFace's distributed pretrained models "
                "are restricted to non-commercial research use unless you "
                "obtain a separate license. LoRA Image Curator does not bundle "
                "or relicense these weights.\n\nDownload and use this model?"
            ),
            parent=self.root,
        )
        return True if approved else None

    # =========================================================================
    # Background work
    # =========================================================================

    def _start_face_analysis(self) -> None:
        """Run InsightFace alone against the active catalog and input folder."""
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showinfo(
                "Analysis already running",
                "Please wait for the current run to finish.",
                parent=self.root,
            )
            return

        catalog_path = self._current_catalog_path()
        input_text = self.input_folder_var.get().strip()
        output_text = self.output_folder_var.get().strip()
        if catalog_path is None:
            messagebox.showinfo(
                "Choose a catalog",
                "Open or create the catalog that should receive face results.",
                parent=self.root,
            )
            return
        if not input_text or not output_text:
            messagebox.showerror(
                "Folders required",
                "Choose the input image folder and catalog/report folder.",
                parent=self.root,
            )
            return
        input_folder = Path(input_text)
        output_folder = Path(output_text)
        if not input_folder.exists() or not input_folder.is_dir():
            messagebox.showerror(
                "Invalid input folder",
                f"The input folder is not valid:\n\n{input_folder}",
                parent=self.root,
            )
            return
        if catalog_path.resolve() != (output_folder / CATALOG_FILENAME).resolve():
            messagebox.showerror(
                "Provider catalog filename mismatch",
                (
                    "Face analysis currently writes to the standard catalog in "
                    "the selected report folder:\n\n"
                    f"{output_folder / CATALOG_FILENAME}\n\n"
                    f"The active catalog is:\n\n{catalog_path}"
                ),
                parent=self.root,
            )
            return

        identity_name = " ".join(self.face_identity_name_var.get().split())
        reference_text = self.face_reference_folder_var.get().strip()
        if not identity_name:
            messagebox.showerror(
                "Trigger Keyword required",
                "Enter the training keyword represented by the reference folder.",
                parent=self.root,
            )
            return
        if not reference_text:
            messagebox.showerror(
                "Reference folder required",
                "Choose a folder containing reference images of one person.",
                parent=self.root,
            )
            return
        reference_folder = Path(reference_text)
        if not reference_folder.exists() or not reference_folder.is_dir():
            messagebox.showerror(
                "Invalid reference folder",
                f"The identity reference folder is not valid:\n\n{reference_folder}",
                parent=self.root,
            )
            return

        similarity_threshold = self._read_threshold(
            self.face_similarity_threshold_var,
            "Identity threshold",
            DEFAULT_SIMILARITY_THRESHOLD,
        )
        detection_threshold = self._read_threshold(
            self.face_detection_threshold_var,
            "Detection threshold",
            DEFAULT_DETECTION_THRESHOLD,
        )
        if similarity_threshold is None or detection_threshold is None:
            return
        selection = self._face_model_selection()
        if selection is None:
            return
        model_name, model_root = selection
        setup = inspect_face_setup(model_name, model_root)
        if not setup.insightface_installed or not setup.onnxruntime_installed:
            self._offer_setup_for_missing_packages("Face Analysis")
            return

        download_choice = self._confirm_face_model_download(setup, model_name)
        if download_choice is None:
            return
        allow_model_download = download_choice

        options = FaceAnalysisOptions(
            model_name=model_name,
            model_root=model_root,
            similarity_threshold=similarity_threshold,
            detection_threshold=detection_threshold,
        )
        self._save_current_settings()
        self._clear_log()
        self._reset_summary()
        self.analysis_cancel_event.clear()
        self.analysis_pause_event.clear()
        self._analysis_paused = False
        self._close_after_analysis_cancel = False
        self.progress_bar["value"] = 0
        self.progress_text_var.set("Preparing face detection and identity matching…")
        self.progress_detail_var.set("")
        self.progress_warning_var.set("")
        self.status_var.set("Starting face provider…")
        self._set_running_provider("face")
        self._set_controls_enabled(False)
        self.cancel_analysis_button.configure(state="normal")
        self.pause_analysis_button.configure(text="Pause Run", state="normal")
        self._analysis_progress_tracker = WorkflowProgressTracker(("Face analysis",))
        self._analysis_has_following_face_phase = False

        self.worker_thread = threading.Thread(
            target=self._face_analysis_worker,
            args=(
                input_folder,
                output_folder,
                identity_name,
                reference_folder,
                options,
                allow_model_download,
                self.reuse_analysis_var.get(),
                self.settings.face_include_subfolders,
                self.settings.face_reference_include_subfolders,
            ),
            name="face-provider-analysis",
            daemon=True,
        )
        self.worker_thread.start()
        self._schedule_menu_state_refresh()

    def _face_analysis_worker(
        self,
        input_folder: Path,
        output_folder: Path,
        identity_name: str,
        reference_folder: Path,
        options: FaceAnalysisOptions,
        allow_model_download: bool,
        reuse_stored_analysis: bool,
        recursive: bool,
        reference_recursive: bool,
    ) -> None:
        """Execute the independent face provider outside Tk's event thread."""
        try:
            summary = analyze_faces(
                input_folder=input_folder,
                output_folder=output_folder,
                identity_name=identity_name,
                reference_folder=reference_folder,
                options=options,
                reuse_stored_analysis=reuse_stored_analysis,
                recursive=recursive,
                reference_recursive=reference_recursive,
                allow_model_download=allow_model_download,
                progress_callback=self._queue_progress,
                status_callback=self._queue_status,
                cancel_event=self.analysis_cancel_event,
                pause_event=self.analysis_pause_event,
            )
        except AnalysisCancelled as error:
            self.message_queue.put(("cancelled", error))
        except Exception as error:
            logging.exception("Face provider run failed")
            self.message_queue.put(("error", error))
        else:
            self.message_queue.put(("face_complete", summary))

    def _start_analysis(self, *, run_face_override: bool | None = None) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showinfo(
                "Analysis already running",
                "Please wait for the current run to finish.",
                parent=self.root,
            )
            return

        input_text = self.input_folder_var.get().strip()
        output_text = self.output_folder_var.get().strip()

        if not input_text or not output_text:
            messagebox.showerror(
                "Folders required",
                "Choose both the input image folder and output/catalog folder.",
                parent=self.root,
            )
            return

        input_folder = Path(input_text)
        output_folder = Path(output_text)

        if not input_folder.exists() or not input_folder.is_dir():
            messagebox.showerror(
                "Invalid input folder",
                f"The input folder is not valid:\n\n{input_folder}",
                parent=self.root,
            )
            return

        if not output_folder.exists() or not output_folder.is_dir():
            messagebox.showerror(
                "Invalid output folder",
                f"The output folder is not valid:\n\n{output_folder}",
                parent=self.root,
            )
            return

        # The provider pipeline currently derives its catalog filename from the
        # output folder.  Refuse a mismatched custom-named active catalog rather
        # than silently analyzing into a second dataset_tools.db beside it.
        implied_catalog = (output_folder / CATALOG_FILENAME).resolve()
        active_catalog = self._current_catalog_path()
        if (
            active_catalog is not None
            and active_catalog.resolve() != implied_catalog
        ):
            messagebox.showerror(
                "Provider catalog filename mismatch",
                (
                    "The active catalog is:\n\n"
                    f"{active_catalog}\n\n"
                    "The provider pipeline writes to:\n\n"
                    f"{implied_catalog}\n\n"
                    f"To avoid splitting results between two catalogs, use "
                    f"{CATALOG_FILENAME} in the selected output folder before "
                    "starting providers."
                ),
                parent=self.root,
            )
            return

        florence_download_choice = self._confirm_florence_download_if_needed()
        if florence_download_choice is None:
            return
        allow_florence_model_download = florence_download_choice

        run_face_analysis = (
            self.run_face_analysis_var.get()
            if run_face_override is None
            else bool(run_face_override)
        )
        allow_model_download = False
        face_options: FaceAnalysisOptions | None = None
        reference_folder: Path | None = None
        identity_name = ""

        if run_face_analysis:
            identity_name = " ".join(self.face_identity_name_var.get().split())
            reference_text = self.face_reference_folder_var.get().strip()

            if not identity_name:
                messagebox.showerror(
                    "Trigger Keyword required",
                    "Enter the training keyword represented by the reference folder.",
                    parent=self.root,
                )
                return

            if not reference_text:
                messagebox.showerror(
                    "Reference folder required",
                    "Choose a folder containing reference images of one person.",
                    parent=self.root,
                )
                return

            reference_folder = Path(reference_text)
            if not reference_folder.exists() or not reference_folder.is_dir():
                messagebox.showerror(
                    "Invalid reference folder",
                    f"The identity reference folder is not valid:\n\n{reference_folder}",
                    parent=self.root,
                )
                return

            similarity_threshold = self._read_threshold(
                self.face_similarity_threshold_var,
                "Identity threshold",
                DEFAULT_SIMILARITY_THRESHOLD,
            )
            detection_threshold = self._read_threshold(
                self.face_detection_threshold_var,
                "Detection threshold",
                DEFAULT_DETECTION_THRESHOLD,
            )
            if similarity_threshold is None or detection_threshold is None:
                return

            selection = self._face_model_selection()
            if selection is None:
                return
            model_name, model_root = selection
            setup = inspect_face_setup(model_name, model_root)

            if not setup.insightface_installed or not setup.onnxruntime_installed:
                self._offer_setup_for_missing_packages("Face Analysis")
                return

            download_choice = self._confirm_face_model_download(setup, model_name)
            if download_choice is None:
                return
            allow_model_download = download_choice

            face_options = FaceAnalysisOptions(
                model_name=model_name,
                model_root=model_root,
                similarity_threshold=similarity_threshold,
                detection_threshold=detection_threshold,
            )

        self._save_current_settings()
        self._clear_log()
        self._reset_summary()
        self.analysis_cancel_event.clear()
        self.analysis_pause_event.clear()
        self._analysis_paused = False
        self._close_after_analysis_cancel = False

        self.latest_output_csv = None
        self.latest_face_csv = None
        self.latest_catalog_database = None

        self.progress_bar["value"] = 0
        self.progress_text_var.set("Preparing catalog and provider workflow…")
        self.progress_detail_var.set("")
        self.progress_warning_var.set("")
        self.status_var.set("Starting catalog and providers...")
        self._set_running_provider("florence")
        self.open_output_button.configure(state="disabled")
        self._set_controls_enabled(False)
        self.cancel_analysis_button.configure(state="normal")
        self.pause_analysis_button.configure(text="Pause Run", state="normal")

        include_triage = self.include_triage_var.get()
        reuse_analysis = self.reuse_analysis_var.get()
        phases = ["Cataloging", "Florence analysis"]
        phase_weights = [0.05, 0.95]
        if run_face_analysis:
            phases.append("Face analysis")
            phase_weights = [0.05, 0.65, 0.30]
        self._analysis_progress_tracker = WorkflowProgressTracker(
            phases,
            weights=phase_weights,
        )
        self._analysis_has_following_face_phase = run_face_analysis

        self._append_log(f"Input folder: {input_folder}")
        self._append_log(
            "Image Captioning subfolders: "
            + (
                "included"
                if self.settings.caption_include_subfolders
                else "excluded"
            )
        )
        self._append_log(f"Output folder: {output_folder}")
        self._append_log(f"Catalog: {output_folder / CATALOG_FILENAME}")
        self._append_log(
            "Florence triage: " + ("enabled" if include_triage else "disabled")
        )
        self._append_log(
            "Stored-result reuse: "
            + ("enabled" if reuse_analysis else "disabled")
        )
        self._append_log(
            "Face provider: " + ("enabled" if run_face_analysis else "disabled")
        )
        if run_face_analysis and face_options is not None:
            self._append_log(
                "Face input/reference subfolders: "
                f"{'included' if self.settings.face_include_subfolders else 'excluded'} / "
                f"{'included' if self.settings.face_reference_include_subfolders else 'excluded'}"
            )
            self._append_log(f"Trigger Keyword: {identity_name}")
            self._append_log(f"Reference folder: {reference_folder}")
            self._append_log(f"Face model: {face_options.model_name}")
            self._append_log(
                f"Identity threshold: {face_options.similarity_threshold:.3f}"
            )

        self.worker_thread = threading.Thread(
            target=self._analysis_worker,
            args=(
                input_folder,
                output_folder,
                include_triage,
                reuse_analysis,
                run_face_analysis,
                allow_florence_model_download,
                identity_name,
                reference_folder,
                face_options,
                allow_model_download,
                self.settings.caption_include_subfolders,
                self.settings.face_include_subfolders,
                self.settings.face_reference_include_subfolders,
            ),
            daemon=True,
        )
        self.worker_thread.start()
        self._schedule_menu_state_refresh()

    def _analysis_worker(
        self,
        input_folder: Path,
        output_folder: Path,
        include_triage: bool,
        reuse_analysis: bool,
        run_face_analysis: bool,
        allow_florence_model_download: bool,
        identity_name: str,
        reference_folder: Path | None,
        face_options: FaceAnalysisOptions | None,
        allow_model_download: bool,
        recursive: bool,
        face_recursive: bool,
        face_reference_recursive: bool,
    ) -> None:
        try:
            from analysis_pipeline import run_pipeline

            summary = run_pipeline(
                input_folder=input_folder,
                output_folder=output_folder,
                include_triage=include_triage,
                reuse_stored_analysis=reuse_analysis,
                run_face_analysis=run_face_analysis,
                allow_florence_model_download=allow_florence_model_download,
                recursive=recursive,
                face_recursive=face_recursive,
                face_reference_recursive=face_reference_recursive,
                face_identity_name=identity_name,
                face_reference_folder=reference_folder,
                face_options=face_options,
                allow_face_model_download=allow_model_download,
                progress_callback=self._queue_progress,
                status_callback=self._queue_status,
                cancel_event=self.analysis_cancel_event,
                pause_event=self.analysis_pause_event,
            )
            self.message_queue.put(("complete", summary))

        except AnalysisCancelled as error:
            self.message_queue.put(("cancelled", error))

        except Exception as error:
            logging.exception("Catalog/provider run failed")
            self.message_queue.put(("error", error))

    def _queue_progress(
        self,
        phase: str,
        completed: int,
        total: int,
        current_path: Path,
    ) -> None:
        self.message_queue.put(
            (
                "progress",
                {
                    "phase": phase,
                    "completed": completed,
                    "total": total,
                    "current_path": current_path,
                },
            )
        )

    def _queue_status(self, message: str) -> None:
        self.message_queue.put(("status", message))

    def _process_message_queue(self) -> None:
        self._message_queue_after_id = None
        try:
            while True:
                message_type, payload = self.message_queue.get_nowait()

                if message_type == "status":
                    message = str(payload)
                    self._append_log(message)
                    self.status_var.set(message)
                    self._promote_major_status(message)

                elif message_type == "folder_count":
                    request_id, folder, count, recursive, error = payload
                    if int(request_id) != self._folder_count_request:
                        continue
                    if error:
                        self.input_folder_count_var.set(
                            f"Images found: count failed — {error}"
                        )
                    else:
                        self.input_folder_count_var.set(
                            f"Images found: {int(count):,} supported files "
                            f"({'including' if recursive else 'excluding'} "
                            f"subfolders) · {Path(folder)}"
                        )

                elif message_type == "provider_devices":
                    self.florence_provider_device_var.set(payload.florence)
                    self.face_provider_device_var.set(payload.face)
                    self.body_provider_device_var.set(payload.body)

                elif message_type == "provider_devices_error":
                    message = f"Device check unavailable: {payload}"
                    self.florence_provider_device_var.set(message)
                    self.face_provider_device_var.set(message)
                    self.body_provider_device_var.set(message)

                elif message_type == "progress":
                    phase = str(payload["phase"])
                    if phase == "Face analysis":
                        self._set_running_provider("face")
                    else:
                        self._set_running_provider("florence")
                    completed = int(payload["completed"])
                    total = int(payload["total"])
                    tracker = self._analysis_progress_tracker
                    if tracker is None:
                        tracker = WorkflowProgressTracker((phase,))
                        self._analysis_progress_tracker = tracker
                    snapshot = tracker.update(phase, completed, total)
                    self.progress_bar["value"] = snapshot.overall_percent
                    phase_suffix = (
                        " remaining images"
                        if phase == "Florence analysis"
                        else " images"
                    )
                    self.progress_text_var.set(
                        f"{phase}: {completed:,} / {total:,}{phase_suffix}"
                    )
                    elapsed = format_duration(snapshot.phase_elapsed_seconds)
                    eta = format_duration(snapshot.estimated_remaining_seconds)
                    detail = f"Elapsed: {elapsed} · Remaining: {eta}"
                    if (
                        phase == "Florence analysis"
                        and self._analysis_has_following_face_phase
                    ):
                        detail += " · Face analysis follows"
                    self.progress_detail_var.set(detail)
                    if (
                        snapshot.estimated_remaining_seconds is not None
                        and snapshot.estimated_remaining_seconds >= 600
                    ):
                        self.progress_warning_var.set(
                            "Long run detected. You can safely use Cancel Run; "
                            "completed results remain reusable."
                        )
                    else:
                        self.progress_warning_var.set("")

                elif message_type == "complete":
                    self._handle_completion(payload)

                elif message_type == "face_complete":
                    self._handle_face_completion(payload)

                elif message_type == "error":
                    self._handle_error(payload)

                elif message_type == "cancelled":
                    self._handle_cancelled(payload)

        except queue.Empty:
            pass

        if not self._closing:
            self._message_queue_after_id = self.root.after(
                100,
                self._process_message_queue,
            )

    def _promote_major_status(self, message: str) -> None:
        """Mirror meaningful phase transitions above the progress bar.

        Per-image diagnostics remain in the status log.  Only transitions that
        explain a pause or a new kind of work take over the prominent heading.
        """
        promoted: dict[str, str] = {
            "Cataloging files and checking content hashes...": (
                "Cataloging images and checking content hashes…"
            ),
            "Catalog registration complete.": (
                "Cataloging complete; preparing Florence analysis…"
            ),
            "Loading Florence-2...": "Loading the Florence-2 model…",
            "Loading Florence-2 from the local cache...": (
                "Loading the Florence-2 model from the local cache…"
            ),
            "Downloading the approved Florence-2 model and loading it...": (
                "Downloading the approved Florence-2 model…"
            ),
            "Downloading the approved InsightFace model pack...": (
                "Downloading the approved InsightFace model pack…"
            ),
            "Florence-2 loaded successfully.": (
                "Florence-2 loaded; starting image analysis…"
            ),
            "Provider 2: face detection, embeddings, and identity matching": (
                "Preparing face detection and identity matching…"
            ),
        }
        heading = promoted.get(message)
        if heading is not None:
            self.progress_text_var.set(heading)

    def _set_running_provider(self, provider: str | None) -> None:
        """Show one temporary provider marker and identify the shared progress owner."""
        labels = {
            "florence": getattr(self, "florence_running_label", None),
            "face": getattr(self, "face_running_label", None),
            "body": getattr(self, "body_running_label", None),
        }
        for label in labels.values():
            if label is not None:
                label.grid_remove()

        current_work = {
            "florence": "Current work: Image Captioning / Florence-2",
            "face": "Current work: Face Scanning / InsightFace",
            "body": "Current work: Body / Pose Scanning / MediaPipe",
        }
        active_label = labels.get(provider or "")
        if active_label is not None:
            active_label.grid()
        self.current_work_var.set(
            current_work.get(provider or "", "Current work: No active provider")
        )

    def _handle_completion(self, summary: Any) -> None:
        self.worker_thread = None
        self._set_running_provider(None)
        self.cancel_analysis_button.configure(state="disabled")
        self._reset_analysis_pause_state()
        self.latest_output_csv = Path(summary.output_csv)
        self.latest_catalog_database = Path(summary.catalog_database)
        self.catalog_browser.set_catalog_path(
            self.latest_catalog_database,
            load=True,
            quiet=True,
        )
        self.latest_face_csv = (
            Path(summary.face.output_csv) if summary.face is not None else None
        )

        self.progress_bar["value"] = 100
        self.progress_text_var.set(f"Complete: {summary.total_images} images")
        self.progress_detail_var.set(
            f"Total elapsed: {format_duration(summary.total_seconds)}"
        )
        self.progress_warning_var.set("")
        self.status_var.set("Catalog and providers complete")
        self._set_controls_enabled(True)
        self.open_output_button.configure(state="normal")
        self._refresh_provider_coverage()

        self.summary_vars["unique_images"].set(
            str(summary.catalog_unique_images)
        )
        self.summary_vars["file_locations"].set(
            str(summary.catalog_file_locations)
        )
        self.summary_vars["new_images"].set(str(summary.new_unique_images))
        self.summary_vars["changed_files"].set(str(summary.changed_files))
        self.summary_vars["florence_reused"].set(
            str(summary.reused_analyses)
        )
        self.summary_vars["florence_generated"].set(
            str(summary.generated_analyses)
        )
        self.summary_vars["faces_detected"].set(
            str(summary.face.faces_detected) if summary.face is not None else "—"
        )
        self.summary_vars["identity_suggestions"].set(
            str(summary.face.suggestions_created)
            if summary.face is not None
            else "—"
        )

        self._append_log("")
        self._append_log("=" * 72)
        self._append_log("Catalog and provider run complete")
        self._append_log("=" * 72)
        self._append_log(f"Files discovered: {summary.total_images}")
        self._append_log(f"New unique images: {summary.new_unique_images}")
        self._append_log(
            "New paths to existing image content: "
            f"{summary.new_locations_existing_images}"
        )
        self._append_log(f"Unchanged files: {summary.unchanged_files}")
        self._append_log(f"Changed files: {summary.changed_files}")
        self._append_log(
            f"Florence analyses reused: {summary.reused_analyses}"
        )
        self._append_log(
            f"Florence analyses generated: {summary.generated_analyses}"
        )

        if summary.face is not None:
            face = summary.face
            self._append_log("")
            self._append_log("Face provider")
            self._append_log(f"Execution provider: {face.execution_provider}")
            self._append_log(
                f"Reference faces used: {face.reference_faces_used}"
            )
            self._append_log(f"Face analyses reused: {face.reused_images}")
            self._append_log(
                f"Face analyses generated: {face.generated_images}"
            )
            self._append_log(f"Faces detected: {face.faces_detected}")
            self._append_log(
                f"Identity suggestions: {face.suggestions_created}"
            )
            self._append_log(f"Face CSV report: {face.output_csv}")

        self._append_log("")
        self._append_log(f"Total time: {summary.total_seconds:.2f} seconds")
        self._append_log(f"Database: {summary.catalog_database}")
        self._append_log(f"Florence CSV report: {summary.output_csv}")

        if self._close_after_analysis_cancel:
            self._finish_close()
            return

        face_message = ""
        if summary.face is not None:
            face_message = (
                f"\nFaces detected: {summary.face.faces_detected}"
                f"\nIdentity suggestions: {summary.face.suggestions_created}"
                f"\n\nFace report:\n{summary.face.output_csv}\n"
            )

        messagebox.showinfo(
            "Provider run complete",
            (
                f"Processed {summary.total_images} files.\n\n"
                f"New unique images: {summary.new_unique_images}\n"
                f"Florence reused: {summary.reused_analyses}\n"
                f"Florence generated: {summary.generated_analyses}\n"
                f"{face_message}\n"
                f"Catalog:\n{summary.catalog_database}\n\n"
                f"Florence report:\n{summary.output_csv}"
            ),
            parent=self.root,
        )

    def _handle_face_completion(self, summary: Any) -> None:
        """Restore the UI and report an independently completed face pass."""
        self.worker_thread = None
        self._set_running_provider(None)
        self.cancel_analysis_button.configure(state="disabled")
        self._reset_analysis_pause_state()
        self.latest_face_csv = Path(summary.output_csv)
        self.latest_catalog_database = Path(summary.catalog_database)
        self.catalog_browser.set_catalog_path(
            self.latest_catalog_database,
            load=True,
            quiet=True,
        )
        self.progress_bar["value"] = 100
        self.progress_text_var.set(
            f"Face analysis complete: {summary.total_images:,} images"
        )
        self.progress_detail_var.set(
            f"Total elapsed: {format_duration(summary.total_seconds)}"
        )
        self.progress_warning_var.set("")
        self.status_var.set("Face provider complete")
        self._set_controls_enabled(True)
        self.open_output_button.configure(state="normal")
        self.summary_vars["faces_detected"].set(str(summary.faces_detected))
        self.summary_vars["identity_suggestions"].set(
            str(summary.suggestions_created)
        )
        self._refresh_provider_coverage()

        self._append_log("")
        self._append_log("=" * 72)
        self._append_log("Face provider run complete")
        self._append_log("=" * 72)
        self._append_log(f"Images checked: {summary.total_images:,}")
        self._append_log(f"Results reused: {summary.reused_images:,}")
        self._append_log(f"Results generated: {summary.generated_images:,}")
        self._append_log(f"Failed: {summary.failed_images:,}")
        self._append_log(f"Faces detected: {summary.faces_detected:,}")
        self._append_log(
            f"Identity suggestions: {summary.suggestions_created:,}"
        )
        self._append_log(f"Report: {summary.output_csv}")

        messagebox.showinfo(
            "Face provider complete",
            (
                f"Checked {summary.total_images:,} images.\n\n"
                f"Reused: {summary.reused_images:,}\n"
                f"Generated: {summary.generated_images:,}\n"
                f"Failed: {summary.failed_images:,}\n"
                f"Faces detected: {summary.faces_detected:,}\n"
                f"Identity suggestions: {summary.suggestions_created:,}\n\n"
                f"Report:\n{summary.output_csv}"
            ),
            parent=self.root,
        )

    def _handle_error(self, error: Exception) -> None:
        self.worker_thread = None
        self._set_running_provider(None)
        self.cancel_analysis_button.configure(state="disabled")
        self._reset_analysis_pause_state()
        self.status_var.set("Catalog/provider run failed")
        self.progress_warning_var.set("")
        self.progress_detail_var.set("Stopped because of an error")
        self._set_controls_enabled(True)
        self._refresh_provider_coverage()

        error_message = f"{type(error).__name__}: {error}"
        self._append_log("")
        self._append_log(f"ERROR: {error_message}")
        self._append_log(f"Diagnostic log: {LOG_PATH}")

        if self._close_after_analysis_cancel:
            self._finish_close()
            return

        messagebox.showerror(
            "Catalog/provider run failed",
            f"{error_message}\n\nDiagnostic log:\n{LOG_PATH}",
            parent=self.root,
        )

    def _request_analysis_cancel(self) -> None:
        """Request a safe stop after the provider's current image."""
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.cancel_analysis_button.configure(state="disabled")
            return
        self.analysis_cancel_event.set()
        # A paused worker must be released so it can observe cancellation at
        # the same safe image boundary.
        self.analysis_pause_event.clear()
        self._analysis_paused = False
        self.cancel_analysis_button.configure(state="disabled")
        self.pause_analysis_button.configure(text="Pause Run", state="disabled")
        self.status_var.set(
            "Cancellation requested; finishing the current image safely…"
        )
        self.progress_warning_var.set(
            "Cancellation requested. The current model operation must reach a safe stop."
        )
        self._append_log(
            "Cancellation requested. The current model operation may need to "
            "finish before the run stops."
        )

    def _toggle_analysis_pause(self) -> None:
        """Pause or resume the active Florence/face workflow between images."""
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self._reset_analysis_pause_state()
            return
        if self._analysis_paused:
            self.analysis_pause_event.clear()
            self._analysis_paused = False
            self.pause_analysis_button.configure(text="Pause Run")
            self.status_var.set("Resuming provider run…")
            self.progress_warning_var.set("")
            self._append_log(
                "Provider run resumed. The loaded model remains in memory."
            )
        else:
            self.analysis_pause_event.set()
            self._analysis_paused = True
            self.pause_analysis_button.configure(text="Resume Run")
            self.status_var.set(
                "Pause requested; finishing the current image safely…"
            )
            self.progress_warning_var.set(
                "Pause requested. Resume keeps the current provider model loaded."
            )
            self._append_log(
                "Pause requested. The current model operation will finish "
                "before the run waits."
            )

    def _reset_analysis_pause_state(self) -> None:
        """Return shared pause controls to their idle state."""
        self.analysis_pause_event.clear()
        self._analysis_paused = False
        if hasattr(self, "pause_analysis_button"):
            self.pause_analysis_button.configure(
                text="Pause Run",
                state="disabled",
            )

    def _handle_cancelled(self, error: Exception) -> None:
        """Restore the UI after a deliberate cooperative cancellation."""
        self.worker_thread = None
        self._set_running_provider(None)
        self.cancel_analysis_button.configure(state="disabled")
        self._reset_analysis_pause_state()
        self.status_var.set("Catalog/provider run cancelled")
        self.progress_text_var.set("Cancelled")
        self.progress_detail_var.set("Completed results remain stored and reusable")
        self.progress_warning_var.set("")
        self._set_controls_enabled(True)
        self._refresh_provider_coverage()
        if self._current_catalog_path() is not None:
            self.open_output_button.configure(state="normal")
        self._append_log("")
        self._append_log(str(error))

        if self._close_after_analysis_cancel:
            self._finish_close()
            return
        messagebox.showinfo(
            "Provider run cancelled",
            (
                f"{error}\n\nThe source images were not changed. The catalog "
                "keeps results completed before cancellation."
            ),
            parent=self.root,
        )

    # =========================================================================
    # General GUI helpers
    # =========================================================================

    def _reset_summary(self) -> None:
        for variable in self.summary_vars.values():
            variable.set("—")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._provider_controls_locked = not enabled

        for widget in (
            self.start_button,
            self.run_florence_button,
            self.run_face_analysis_button,
            self.input_entry,
            self.output_entry,
            self.input_browse_button,
            self.output_browse_button,
            self.triage_checkbutton,
            self.face_enable_checkbutton,
            self.face_setup_button,
        ):
            widget.configure(state=state)

        if enabled:
            self._toggle_face_controls()
        else:
            for widget in self.face_setting_widgets:
                widget.configure(state="disabled")
        self._update_catalog_management_state()

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_report_folder(self) -> None:
        folder: Path | None = None

        if self.latest_catalog_database is not None:
            folder = self.latest_catalog_database.parent
        elif self.output_folder_var.get().strip():
            folder = Path(self.output_folder_var.get().strip())

        if folder is None:
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=True)
            else:
                subprocess.run(["xdg-open", str(folder)], check=True)
        except (OSError, subprocess.SubprocessError) as error:
            logging.exception("Could not open output folder")
            messagebox.showerror(
                "Could not open folder",
                str(error),
                parent=self.root,
            )

    def _on_close(self) -> None:
        provider_running = self.worker_thread is not None and self.worker_thread.is_alive()
        quality_running = self.dataset_readiness.is_running
        if provider_running or quality_running:
            should_close = messagebox.askyesno(
                "A run is active",
                (
                    "Closing now will stop the active analysis after its current "
                    "item. Catalog results already committed and any partial CSV "
                    "should remain usable.\n\nClose anyway?"
                ),
                parent=self.root,
            )
            if not should_close:
                return
            if provider_running:
                self._close_after_analysis_cancel = True
                self._request_analysis_cancel()
                if quality_running:
                    self.dataset_readiness.shutdown()
                return

        self._finish_close()

    def _finish_close(self) -> None:
        """Release persistent and background resources, then close Tk."""
        if self._closing:
            return
        self._closing = True
        self._save_current_settings()
        for after_id in (self._menu_refresh_after_id, self._message_queue_after_id):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._menu_refresh_after_id = None
        self._message_queue_after_id = None
        self.dataset_readiness.shutdown()
        self.catalog_browser.shutdown()
        self.root.destroy()
        shutdown_logging()


def main() -> None:
    """Create and run the application."""
    root = tk.Tk()
    root.withdraw()

    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass

    if not show_first_launch_notice(root):
        root.destroy()
        shutdown_logging()
        return
    root.deiconify()
    DatasetToolsApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Fatal GUI startup error")
        raise
