"""Focused Tkinter workflow for video-source frame extraction.

The first application tab exposes only a compact launcher.  This dialog owns
the less frequently used FFmpeg path, sampling, output, and catalog-handoff
choices so the normal catalog/provider controls remain readable.

Extraction and staged catalog import run on one worker thread.  The original
video is read only, existing matching frame names are never overwritten, and a
failed catalog import cannot publish a partial SQLite catalog.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Literal

from catalog import CATALOG_FILENAME
from catalog_import import (
    CatalogImportCancelled,
    CatalogImportOptions,
    CatalogImportSummary,
    default_image_set_name,
    format_import_summary,
    import_catalog_folder,
)
from settings_manager import AppSettings
from video_extraction import (
    FFmpegStatus,
    VideoExtractionCancelled,
    VideoExtractionError,
    VideoExtractionOptions,
    VideoExtractionSummary,
    discover_ffmpeg,
    format_command,
    format_extraction_summary,
    estimate_interval_frame_count,
    normalize_filename_prefix,
    output_glob,
    probe_ffmpeg,
    probe_video_duration,
    run_video_extraction,
    validate_extraction_options,
)
from ui_fonts import MONOSPACE_FONT_FAMILY, get_ui_font
from ui_helpers import HelpIcon


PostAction = Literal["save", "merge", "create"]
SettingsSavedCallback = Callable[[AppSettings], None]

SAMPLING_LABEL_TO_KEY = {
    "Fixed interval": "interval",
    "Scene changes": "scene",
}
FORMAT_LABEL_TO_KEY = {
    "JPEG — high quality": "jpg",
    "PNG — lossless": "png",
}
POST_ACTION_LABEL_TO_KEY = {
    "Save frames only": "save",
    "Add frames to the current catalog": "merge",
    "Create a new catalog from the frames": "create",
}


@dataclass(slots=True, frozen=True)
class VideoSourceResult:
    """Completed extraction plus any deliberately requested catalog handoff."""

    extraction: VideoExtractionSummary
    catalog_import: CatalogImportSummary | None
    run_analysis_requested: bool


class VideoExtractionDialog(tk.Toplevel):
    """Configure FFmpeg and run a cancellable frame-extraction workflow."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        settings: AppSettings,
        current_catalog: Path | None,
        on_settings_saved: SettingsSavedCallback | None = None,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.settings = settings
        self.current_catalog = (
            current_catalog.expanduser().resolve()
            if current_catalog is not None
            else None
        )
        self.on_settings_saved = on_settings_saved
        self.result: VideoSourceResult | None = None

        self.title("Extract Frames from Video")
        self.geometry("900x720")
        self.minsize(800, 650)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._request_close)

        # The dialog is constructed before any external executable is probed.
        # FFmpeg discovery starts from the Tk event loop after this window has
        # mapped, then runs in a daemon worker so even a slow/broken executable
        # cannot delay or freeze the interface.
        self.ffmpeg_status = FFmpegStatus(
            False,
            None,
            None,
            "",
            "FFmpeg discovery has not completed.",
        )
        self.ffmpeg_path_var = tk.StringVar(
            value=settings.video_ffmpeg_path
        )
        self.ffmpeg_status_var = tk.StringVar()
        self.source_var = tk.StringVar(value=settings.video_last_source)
        self.destination_var = tk.StringVar(value=settings.video_last_destination)
        self.sampling_var = tk.StringVar(
            value=(
                "Scene changes"
                if settings.video_sampling_mode == "scene"
                else "Fixed interval"
            )
        )
        self.interval_var = tk.StringVar(value=f"{settings.video_interval_seconds:g}")
        self.scene_threshold_var = tk.StringVar(
            value=f"{settings.video_scene_threshold:g}"
        )
        self.maximum_var = tk.StringVar(value=str(settings.video_max_frames))
        self.format_var = tk.StringVar(
            value=(
                "PNG — lossless"
                if settings.video_output_format == "png"
                else "JPEG — high quality"
            )
        )
        self.prefix_var = tk.StringVar(value="frame")
        default_post_action = "merge" if self.current_catalog is not None else "save"
        self.post_action_var = tk.StringVar(
            value=next(
                label
                for label, key in POST_ACTION_LABEL_TO_KEY.items()
                if key == default_post_action
            )
        )
        self.catalog_target_var = tk.StringVar(value="")
        self.create_set_var = tk.BooleanVar(value=True)
        self.set_name_var = tk.StringVar(value="")
        self.run_analysis_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a video and extraction settings.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.estimate_var = tk.StringVar(
            value="Estimated total: choose a video to read its duration."
        )

        self._automatic_prefix = "frame"
        self._automatic_set_name = ""
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._close_after_cancel = False
        self._ffmpeg_discovery_results: queue.Queue[FFmpegStatus] = queue.Queue()
        self._ffmpeg_discovery_thread: threading.Thread | None = None
        self._ffmpeg_discovery_after_id: str | None = None
        self._duration_results: queue.Queue[tuple[Path, float | None]] = queue.Queue()
        self._duration_thread: threading.Thread | None = None
        self._duration_after_id: str | None = None
        self._video_duration_seconds: float | None = None

        self._build_interface()
        self._show_ffmpeg_checking_state()
        self._update_sampling_fields()
        self._update_post_action_fields()
        self.grab_set()
        self.source_entry.focus_set()
        self._ffmpeg_discovery_after_id = self.after(
            75,
            self._begin_ffmpeg_discovery,
        )

    def _build_interface(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        ttk.Label(
            container,
            text=(
                "Create still-image candidates from a local video. LoRA Image Curator "
                "does not bundle or download FFmpeg, upload the video, alter the "
                "source, or overwrite matching frame files."
            ),
            wraplength=850,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

        ffmpeg_frame = ttk.LabelFrame(container, text="FFmpeg", padding=9)
        ffmpeg_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ffmpeg_frame.columnconfigure(1, weight=1)
        ffmpeg_label = ttk.Frame(ffmpeg_frame)
        ffmpeg_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(ffmpeg_label, text="Executable:").pack(side="left")
        self.ffmpeg_help = HelpIcon(
            ffmpeg_label,
            "LoRA Image Curator uses a user-installed ffmpeg.exe. It never downloads or updates FFmpeg automatically.",
        )
        self.ffmpeg_help.pack(side="left", padx=(4, 0))
        self.ffmpeg_entry = ttk.Entry(
            ffmpeg_frame,
            textvariable=self.ffmpeg_path_var,
            state="readonly",
        )
        self.ffmpeg_entry.grid(row=0, column=1, sticky="ew")
        self.ffmpeg_browse_button = ttk.Button(
            ffmpeg_frame,
            text="Choose…",
            command=self._choose_ffmpeg,
        )
        self.ffmpeg_browse_button.grid(row=0, column=2, padx=(8, 0))
        self.ffmpeg_detect_button = ttk.Button(
            ffmpeg_frame,
            text="Auto-detect",
            command=self._auto_detect_ffmpeg,
        )
        self.ffmpeg_detect_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(
            ffmpeg_frame,
            textvariable=self.ffmpeg_status_var,
            foreground="#555555",
            wraplength=820,
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))

        source_label = ttk.Frame(container)
        source_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(source_label, text="Source video:").pack(side="left")
        self.source_video_help = HelpIcon(
            source_label,
            "Choose a local video to read. The original video is never altered.",
        )
        self.source_video_help.pack(side="left", padx=(4, 0))
        self.source_entry = ttk.Entry(container, textvariable=self.source_var)
        self.source_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        self.source_entry.bind(
            "<FocusOut>",
            lambda _event: self._start_duration_probe(),
        )
        self.source_entry.bind(
            "<Return>",
            lambda _event: self._start_duration_probe(),
        )
        self.source_button = ttk.Button(
            container,
            text="Browse…",
            command=self._choose_source,
        )
        self.source_button.grid(row=2, column=3, padx=(10, 0), pady=5)

        destination_label = ttk.Frame(container)
        destination_label.grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Label(destination_label, text="Destination folder:").pack(side="left")
        self.destination_help = HelpIcon(
            destination_label,
            "Extracted frame files are created here. Existing matching filenames are not overwritten.",
        )
        self.destination_help.pack(side="left", padx=(4, 0))
        self.destination_entry = ttk.Entry(
            container,
            textvariable=self.destination_var,
        )
        self.destination_entry.grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=5
        )
        self.destination_button = ttk.Button(
            container,
            text="Browse…",
            command=self._choose_destination,
        )
        self.destination_button.grid(row=3, column=3, padx=(10, 0), pady=5)

        options = ttk.LabelFrame(container, text="Extraction", padding=9)
        options.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 10))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="Sampling:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.sampling_combo = ttk.Combobox(
            options,
            textvariable=self.sampling_var,
            values=tuple(SAMPLING_LABEL_TO_KEY),
            state="readonly",
            width=22,
        )
        self.sampling_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.sampling_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._sampling_changed()
        )

        ttk.Label(options, text="Output:").grid(
            row=0, column=2, sticky="w", padx=(18, 8), pady=4
        )
        self.format_combo = ttk.Combobox(
            options,
            textvariable=self.format_var,
            values=tuple(FORMAT_LABEL_TO_KEY),
            state="readonly",
            width=22,
        )
        self.format_combo.grid(row=0, column=3, sticky="w", pady=4)

        self.interval_label = ttk.Label(options, text="Seconds between frames:")
        self.interval_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.interval_entry = ttk.Entry(
            options,
            textvariable=self.interval_var,
            width=12,
        )
        self.interval_entry.grid(row=1, column=1, sticky="w", pady=4)
        self.interval_entry.bind(
            "<KeyRelease>",
            lambda _event: self._update_estimate_display(),
        )

        self.scene_label = ttk.Label(options, text="Scene threshold:")
        self.scene_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        self.scene_entry = ttk.Entry(
            options,
            textvariable=self.scene_threshold_var,
            width=12,
        )
        self.scene_entry.grid(row=2, column=1, sticky="w", pady=4)
        self.scene_help = ttk.Label(
            options,
            text="Lower values keep subtler cuts; 0.35 is a conservative start.",
            foreground="#555555",
        )
        self.scene_help.grid(row=2, column=2, columnspan=2, sticky="w", padx=(18, 0))

        ttk.Label(options, text="Maximum frames:").grid(
            row=1, column=2, sticky="w", padx=(18, 8), pady=4
        )
        self.maximum_entry = ttk.Entry(
            options,
            textvariable=self.maximum_var,
            width=12,
        )
        self.maximum_entry.grid(row=1, column=3, sticky="w", pady=4)
        self.maximum_entry.bind(
            "<KeyRelease>",
            lambda _event: self._update_estimate_display(),
        )

        ttk.Label(options, text="Filename prefix:").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.prefix_entry = ttk.Entry(options, textvariable=self.prefix_var)
        self.prefix_entry.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(
            options,
            text="Output example: prefix_000001.jpg",
            foreground="#555555",
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=(18, 0), pady=4)
        ttk.Label(
            options,
            textvariable=self.estimate_var,
            foreground="#555555",
            wraplength=800,
            justify="left",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

        handoff = ttk.LabelFrame(container, text="After extraction", padding=9)
        handoff.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        handoff.columnconfigure(1, weight=1)

        ttk.Label(handoff, text="Next step:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.post_action_combo = ttk.Combobox(
            handoff,
            textvariable=self.post_action_var,
            values=tuple(
                label
                for label, key in POST_ACTION_LABEL_TO_KEY.items()
                if key != "merge" or self.current_catalog is not None
            ),
            state="readonly",
            width=38,
        )
        self.post_action_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.post_action_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_post_action_fields(),
        )

        ttk.Label(handoff, text="Catalog:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.catalog_target_entry = ttk.Entry(
            handoff,
            textvariable=self.catalog_target_var,
        )
        self.catalog_target_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.catalog_target_button = ttk.Button(
            handoff,
            text="Choose…",
            command=self._choose_catalog_target,
        )
        self.catalog_target_button.grid(row=1, column=2, padx=(8, 0), pady=4)

        self.create_set_check = ttk.Checkbutton(
            handoff,
            text="Create an image set from the extracted frames",
            variable=self.create_set_var,
            command=self._update_post_action_fields,
        )
        self.create_set_check.grid(
            row=2, column=1, columnspan=2, sticky="w", pady=4
        )
        ttk.Label(handoff, text="Image set name:").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.set_name_entry = ttk.Entry(handoff, textvariable=self.set_name_var)
        self.set_name_entry.grid(row=3, column=1, sticky="ew", pady=4)
        self.run_analysis_check = ttk.Checkbutton(
            handoff,
            text=(
                "Run the currently configured caption/face providers after import"
            ),
            variable=self.run_analysis_var,
        )
        self.run_analysis_check.grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(5, 2)
        )

        self.progress = ttk.Progressbar(
            container,
            variable=self.progress_var,
            maximum=100.0,
        )
        self.progress.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Label(
            container,
            textvariable=self.status_var,
            wraplength=850,
            justify="left",
        ).grid(row=7, column=0, columnspan=4, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(container)
        buttons.grid(row=8, column=0, columnspan=4, sticky="e", pady=(14, 0))
        self.start_button = ttk.Button(
            buttons,
            text="Extract Frames",
            command=self._start,
        )
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(
            buttons,
            text="Cancel",
            command=self._request_close,
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

    def _display_ffmpeg_status(self) -> None:
        if self.ffmpeg_status.available:
            source_label = {
                "saved": "saved location",
                "path": "automatic PATH detection",
                "manual": "manually selected",
            }.get(self.ffmpeg_status.source, "validated")
            self.ffmpeg_status_var.set(
                f"Ready ({source_label}): {self.ffmpeg_status.version_line}"
            )
            self.start_button.configure(state="normal")
        else:
            self.ffmpeg_status_var.set(
                "Not configured. Choose ffmpeg.exe or install FFmpeg on PATH. "
                + self.ffmpeg_status.error
            )
            self.start_button.configure(state="disabled")

    def _show_ffmpeg_checking_state(self) -> None:
        """Acknowledge the open command before external FFmpeg probing starts."""
        self.ffmpeg_status_var.set("Checking FFmpeg…")
        self.status_var.set(
            "The dialog is ready. FFmpeg availability is being checked in the background."
        )
        self.start_button.configure(state="disabled")
        self.ffmpeg_browse_button.configure(state="disabled")
        self.ffmpeg_detect_button.configure(state="disabled")

    def _begin_ffmpeg_discovery(self) -> None:
        """Probe the remembered executable without blocking Tk's event loop."""
        self._ffmpeg_discovery_after_id = None
        if not self.winfo_exists():
            return

        saved_path = self.settings.video_ffmpeg_path

        def worker() -> None:
            try:
                status = discover_ffmpeg(saved_path)
            except Exception as error:
                logging.exception("Unexpected FFmpeg discovery failure")
                status = FFmpegStatus(
                    False,
                    None,
                    None,
                    "",
                    f"{type(error).__name__}: {error}",
                )
            self._ffmpeg_discovery_results.put(status)

        self._ffmpeg_discovery_thread = threading.Thread(
            target=worker,
            name="dataset-ffmpeg-discovery",
            daemon=True,
        )
        self._ffmpeg_discovery_thread.start()
        self._ffmpeg_discovery_after_id = self.after(
            40,
            self._poll_ffmpeg_discovery,
        )

    def _poll_ffmpeg_discovery(self) -> None:
        """Apply a completed read-only executable probe on the Tk thread."""
        self._ffmpeg_discovery_after_id = None
        try:
            status = self._ffmpeg_discovery_results.get_nowait()
        except queue.Empty:
            if (
                self.winfo_exists()
                and self._ffmpeg_discovery_thread is not None
                and self._ffmpeg_discovery_thread.is_alive()
            ):
                self._ffmpeg_discovery_after_id = self.after(
                    40,
                    self._poll_ffmpeg_discovery,
                )
            return

        self.ffmpeg_status = status
        self.ffmpeg_path_var.set(
            str(status.executable or self.settings.video_ffmpeg_path)
        )
        self.ffmpeg_browse_button.configure(state="normal")
        self.ffmpeg_detect_button.configure(state="normal")
        self._display_ffmpeg_status()
        self.status_var.set("Choose a video and extraction settings.")
        self._start_duration_probe()

    def _choose_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose the FFmpeg executable",
            initialdir=(
                str(Path(self.ffmpeg_path_var.get()).expanduser().parent)
                if self.ffmpeg_path_var.get().strip()
                else None
            ),
            filetypes=(
                ("FFmpeg executable", "ffmpeg.exe"),
                ("Executable files", "*.exe"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        self.status_var.set("Validating the selected executable…")
        self.update_idletasks()
        status = probe_ffmpeg(selected, source="manual")
        self.ffmpeg_status = status
        self.ffmpeg_path_var.set(str(status.executable or selected))
        if status.available:
            self.settings.video_ffmpeg_path = str(status.executable)
            self._persist_settings()
        else:
            messagebox.showerror(
                "Not a valid FFmpeg executable",
                status.error,
                parent=self,
            )
        self._display_ffmpeg_status()
        self.status_var.set("Choose a video and extraction settings.")
        self._start_duration_probe()

    def _auto_detect_ffmpeg(self) -> None:
        self.status_var.set("Looking for FFmpeg on PATH…")
        self.update_idletasks()
        status = discover_ffmpeg("")
        self.ffmpeg_status = status
        self.ffmpeg_path_var.set(str(status.executable or ""))
        if status.available:
            self.settings.video_ffmpeg_path = str(status.executable)
            self._persist_settings()
        else:
            messagebox.showinfo(
                "FFmpeg not found",
                (
                    "FFmpeg was not found automatically. Install it and add it "
                    "to PATH, or click Choose and select ffmpeg.exe directly."
                ),
                parent=self,
            )
        self._display_ffmpeg_status()
        self.status_var.set("Choose a video and extraction settings.")
        self._start_duration_probe()

    def _choose_source(self) -> None:
        current = self.source_var.get().strip()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose a source video",
            initialdir=str(Path(current).expanduser().parent) if current else None,
            filetypes=(
                (
                    "Video files",
                    "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mpg *.mpeg *.ts",
                ),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        previous_prefix = self._automatic_prefix
        previous_set_name = self._automatic_set_name
        source = Path(selected)
        self.source_var.set(selected)
        self._automatic_prefix = normalize_filename_prefix(source.stem)
        self._automatic_set_name = default_image_set_name(
            source.with_name(f"{source.stem} frames")
        )
        if not self.prefix_var.get().strip() or self.prefix_var.get() == previous_prefix:
            self.prefix_var.set(self._automatic_prefix)
        if (
            not self.set_name_var.get().strip()
            or self.set_name_var.get() == previous_set_name
        ):
            self.set_name_var.set(self._automatic_set_name)
        if not self.destination_var.get().strip():
            self.destination_var.set(str(source.parent / f"{source.stem}_frames"))
        self._start_duration_probe()

    def _choose_destination(self) -> None:
        current = self.destination_var.get().strip()
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the folder that will receive extracted frames",
            initialdir=(
                current
                if current and Path(current).expanduser().is_dir()
                else (
                    str(Path(self.source_var.get()).expanduser().parent)
                    if self.source_var.get().strip()
                    else None
                )
            ),
            mustexist=True,
        )
        if selected:
            self.destination_var.set(selected)

    def _choose_catalog_target(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Create a catalog for the extracted frames",
            initialdir=self.destination_var.get().strip() or None,
            initialfile="dataset_tools.db",
            defaultextension=".db",
            filetypes=(("LoRA Image Curator catalog", "*.db"), ("All files", "*.*")),
        )
        if selected:
            self.catalog_target_var.set(selected)

    def _update_sampling_fields(self) -> None:
        interval_mode = SAMPLING_LABEL_TO_KEY.get(self.sampling_var.get()) == "interval"
        self.interval_entry.configure(state="normal" if interval_mode else "disabled")
        self.scene_entry.configure(state="disabled" if interval_mode else "normal")

    def _sampling_changed(self) -> None:
        """Refresh both editable fields and the honest total-estimate label."""
        self._update_sampling_fields()
        self._update_estimate_display()

    def _start_duration_probe(self) -> None:
        """Read source duration off the Tk thread and ignore stale probe results."""
        source_text = self.source_var.get().strip()
        if (
            not source_text
            or not self.ffmpeg_status.available
            or self.ffmpeg_status.executable is None
        ):
            self._video_duration_seconds = None
            self._update_estimate_display()
            return
        source = Path(source_text).expanduser().resolve()
        if not source.is_file():
            self._video_duration_seconds = None
            self._update_estimate_display()
            return
        self.estimate_var.set("Estimated total: reading video duration…")
        ffmpeg_path = self.ffmpeg_status.executable

        def worker() -> None:
            duration = probe_video_duration(ffmpeg_path, source)
            self._duration_results.put((source, duration))

        self._duration_thread = threading.Thread(
            target=worker,
            name="video-duration-probe",
            daemon=True,
        )
        self._duration_thread.start()
        if self._duration_after_id is None:
            self._duration_after_id = self.after(50, self._poll_duration_probe)

    def _poll_duration_probe(self) -> None:
        """Apply only the duration result for the source still shown in the form."""
        self._duration_after_id = None
        current_text = self.source_var.get().strip()
        current = Path(current_text).expanduser().resolve() if current_text else None
        while True:
            try:
                source, duration = self._duration_results.get_nowait()
            except queue.Empty:
                break
            if current == source:
                self._video_duration_seconds = duration
                self._update_estimate_display()
        if (
            self.winfo_exists()
            and self._duration_thread is not None
            and (
                self._duration_thread.is_alive()
                or not self._duration_results.empty()
            )
        ):
            self._duration_after_id = self.after(50, self._poll_duration_probe)

    def _update_estimate_display(self) -> None:
        """Show an up-front fixed-interval estimate or a clear unavailable state."""
        if SAMPLING_LABEL_TO_KEY.get(self.sampling_var.get()) != "interval":
            self.estimate_var.set(
                "Estimated total: unavailable for scene-change sampling."
            )
            return
        if self._video_duration_seconds is None:
            self.estimate_var.set(
                "Estimated total: unavailable until video duration is read."
            )
            return
        try:
            interval = float(self.interval_var.get().strip())
            maximum = int(self.maximum_var.get().strip())
            estimate = estimate_interval_frame_count(
                self._video_duration_seconds,
                interval,
                maximum,
            )
        except (TypeError, ValueError):
            self.estimate_var.set(
                "Estimated total: enter a valid interval and maximum."
            )
            return
        duration = self._video_duration_seconds
        hours, remainder = divmod(round(duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.estimate_var.set(
            f"Estimated total for complete video: {estimate:,} images "
            f"(duration {hours:d}:{minutes:02d}:{seconds:02d}; cap {maximum:,})."
        )

    def _post_action(self) -> PostAction:
        return POST_ACTION_LABEL_TO_KEY.get(self.post_action_var.get(), "save")

    def _update_post_action_fields(self) -> None:
        action = self._post_action()
        imports = action in {"merge", "create"}
        if action == "merge" and self.current_catalog is not None:
            self.catalog_target_var.set(str(self.current_catalog))
        elif action == "save":
            self.catalog_target_var.set("")

        self.catalog_target_entry.configure(
            state="normal" if action == "create" else "readonly"
        )
        self.catalog_target_button.configure(
            state="normal" if action == "create" else "disabled"
        )
        self.create_set_check.configure(state="normal" if imports else "disabled")
        set_state = "normal" if imports and self.create_set_var.get() else "disabled"
        self.set_name_entry.configure(state=set_state)
        self.run_analysis_check.configure(state="normal" if imports else "disabled")
        if not imports:
            self.run_analysis_var.set(False)

    def _read_options(
        self,
    ) -> tuple[VideoExtractionOptions, PostAction, Path | None]:
        if not self.ffmpeg_status.available or self.ffmpeg_status.executable is None:
            raise ValueError("Choose and validate FFmpeg before extracting frames.")
        if not self.source_var.get().strip():
            raise ValueError("Choose a source video.")
        if not self.destination_var.get().strip():
            raise ValueError("Choose a destination folder.")

        try:
            interval = float(self.interval_var.get().strip())
            threshold = float(self.scene_threshold_var.get().strip())
            maximum = int(self.maximum_var.get().strip())
        except ValueError as error:
            raise ValueError(
                "Interval and scene threshold must be numbers; maximum frames "
                "must be a whole number."
            ) from error

        action = self._post_action()
        target: Path | None = None
        if action == "merge":
            if self.current_catalog is None:
                raise ValueError("Open a catalog before adding extracted frames.")
            target = self.current_catalog
        elif action == "create":
            if not self.catalog_target_var.get().strip():
                raise ValueError("Choose a filename for the new catalog.")
            target = Path(self.catalog_target_var.get()).expanduser().resolve()

        if (
            action in {"merge", "create"}
            and self.create_set_var.get()
            and not self.set_name_var.get().strip()
        ):
            raise ValueError("Enter a name for the extracted-frame image set.")
        if (
            action in {"merge", "create"}
            and self.run_analysis_var.get()
            and target is not None
            and target.name.casefold() != CATALOG_FILENAME.casefold()
        ):
            raise ValueError(
                "Automatic provider analysis requires the catalog filename "
                f"{CATALOG_FILENAME!r}. Choose that filename or turn off the "
                "provider-analysis option; the extracted frames and staged "
                "catalog import work with other .db names."
            )

        options = VideoExtractionOptions(
            ffmpeg_path=self.ffmpeg_status.executable,
            source_video=Path(self.source_var.get()),
            destination_folder=Path(self.destination_var.get()),
            sampling_mode=SAMPLING_LABEL_TO_KEY.get(
                self.sampling_var.get(), "interval"
            ),
            interval_seconds=interval,
            scene_threshold=threshold,
            max_frames=maximum,
            output_format=FORMAT_LABEL_TO_KEY.get(self.format_var.get(), "jpg"),
            filename_prefix=self.prefix_var.get(),
        )
        return options, action, target

    def _start(self) -> None:
        try:
            # Re-probe at the action boundary.  The saved executable may have
            # been moved or replaced while the dialog was open.
            current_probe = probe_ffmpeg(
                self.ffmpeg_path_var.get(),
                source=self.ffmpeg_status.source or "manual",
            )
            self.ffmpeg_status = current_probe
            self._display_ffmpeg_status()
            if not current_probe.available:
                raise ValueError(current_probe.error)
            options, action, target = self._read_options()
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot start extraction", str(error), parent=self)
            return

        options = validate_extraction_options(options)
        existing_frames = sorted(
            options.destination_folder.glob(output_glob(options))
        )
        if existing_frames:
            collision_choice = messagebox.askyesnocancel(
                "Matching frame files already exist",
                (
                    f"{len(existing_frames):,} file(s) match the output pattern:\n\n"
                    f"{output_glob(options)}\n\n"
                    "Yes — overwrite matching generated frame files.\n"
                    "No — keep existing files and add only missing names.\n"
                    "Cancel — make no changes."
                ),
                parent=self,
            )
            if collision_choice is None:
                return
            options = replace(
                options,
                collision_policy=(
                    "overwrite" if collision_choice else "skip"
                ),
            )

        overwrite_catalog = False
        if action == "create" and target is not None and target.exists():
            catalog_choice = messagebox.askyesnocancel(
                "Catalog already exists",
                (
                    f"A catalog already exists at:\n\n{target}\n\n"
                    "Yes — replace its catalog-owned contents after extraction.\n"
                    "No — merge new frame records and keep existing contents.\n"
                    "Cancel — make no changes."
                ),
                parent=self,
            )
            if catalog_choice is None:
                return
            if catalog_choice:
                overwrite_catalog = True
            else:
                action = "merge"

        expected_total = options.max_frames
        if (
            options.sampling_mode == "interval"
            and self._video_duration_seconds is not None
        ):
            expected_total = estimate_interval_frame_count(
                self._video_duration_seconds,
                options.interval_seconds,
                options.max_frames,
            )

        self._remember_form_settings(options)
        self._cancel_event.clear()
        self._set_running(True)
        self.progress_var.set(0.0)
        self.status_var.set("Starting FFmpeg…")
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(
                options,
                action,
                target,
                self.create_set_var.get(),
                self.set_name_var.get().strip(),
                self.run_analysis_var.get(),
                overwrite_catalog,
                expected_total,
            ),
            name="video-frame-extraction",
            daemon=True,
        )
        self._worker.start()
        self._poll_after_id = self.after(75, self._poll_messages)

    def _run_worker(
        self,
        options: VideoExtractionOptions,
        action: PostAction,
        target: Path | None,
        create_image_set: bool,
        image_set_name: str,
        run_analysis_requested: bool,
        overwrite_catalog: bool,
        expected_total: int,
    ) -> None:
        def extraction_progress(completed: int, maximum: int) -> None:
            self._messages.put(
                (
                    "extract_progress",
                    (completed, expected_total or maximum),
                )
            )

        try:
            extraction = run_video_extraction(
                options,
                progress_callback=extraction_progress,
                cancel_event=self._cancel_event,
            )
            catalog_summary: CatalogImportSummary | None = None
            if action in {"merge", "create"}:
                assert target is not None
                self._messages.put(("phase", "Adding extracted frames to the catalog…"))

                def import_progress(completed: int, total: int, path: Path) -> None:
                    self._messages.put(
                        ("import_progress", (completed, total, path))
                    )

                catalog_summary = import_catalog_folder(
                    CatalogImportOptions(
                        source_folder=extraction.destination_folder,
                        target_database=target,
                        mode="merge" if action == "merge" else "create",
                        overwrite_existing=overwrite_catalog,
                        recursive=False,
                        create_image_set=create_image_set,
                        image_set_name=image_set_name,
                    ),
                    progress_callback=import_progress,
                    cancel_event=self._cancel_event,
                )
        except (VideoExtractionCancelled, CatalogImportCancelled) as error:
            self._messages.put(("cancelled", error))
        except Exception as error:
            logging.exception("Video source extraction failed")
            self._messages.put(("error", error))
        else:
            self._messages.put(
                (
                    "complete",
                    VideoSourceResult(
                        extraction=extraction,
                        catalog_import=catalog_summary,
                        run_analysis_requested=(
                            action in {"merge", "create"}
                            and run_analysis_requested
                        ),
                    ),
                )
            )

    def _poll_messages(self) -> None:
        self._poll_after_id = None
        terminal = False
        while True:
            try:
                message_type, payload = self._messages.get_nowait()
            except queue.Empty:
                break

            if message_type == "extract_progress":
                completed, maximum = payload
                maximum_value = max(1, int(maximum))
                self.progress_var.set(min(80.0, (int(completed) / maximum_value) * 80.0))
                self.status_var.set(
                    f"Extracting frames: {int(completed):,} written "
                    f"(estimated total {maximum_value:,})"
                )
            elif message_type == "phase":
                self.progress_var.set(80.0)
                self.status_var.set(str(payload))
            elif message_type == "import_progress":
                completed, total, path = payload
                percentage = 80.0 + (
                    (int(completed) / max(1, int(total))) * 20.0
                )
                self.progress_var.set(min(100.0, percentage))
                self.status_var.set(
                    f"Cataloging {int(completed):,} / {int(total):,}: "
                    f"{Path(path).name}"
                )
            elif message_type == "complete":
                terminal = True
                self.result = payload
                self.progress_var.set(100.0)
                self._set_running(False)
                self.destroy()
            elif message_type == "cancelled":
                terminal = True
                self._set_running(False)
                self.status_var.set(str(payload))
                if self._close_after_cancel:
                    self.destroy()
            elif message_type == "error":
                terminal = True
                self._set_running(False)
                error = payload
                detail = f"{type(error).__name__}: {error}"
                if isinstance(error, VideoExtractionError) and error.command:
                    detail += (
                        f"\n\nPartial frames: {error.partial_output_count:,}"
                        f"\n\nCommand:\n{format_command(error.command)}"
                    )
                self.status_var.set("Extraction/import failed. Review the error and retry.")
                messagebox.showerror(
                    "Video source import failed",
                    detail,
                    parent=self,
                )

        if not terminal and self._worker is not None and self._worker.is_alive():
            self._poll_after_id = self.after(75, self._poll_messages)

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (
            self.ffmpeg_browse_button,
            self.ffmpeg_detect_button,
            self.source_entry,
            self.source_button,
            self.destination_entry,
            self.destination_button,
            self.sampling_combo,
            self.interval_entry,
            self.scene_entry,
            self.maximum_entry,
            self.format_combo,
            self.prefix_entry,
            self.post_action_combo,
            self.catalog_target_entry,
            self.catalog_target_button,
            self.create_set_check,
            self.set_name_entry,
            self.run_analysis_check,
        ):
            widget.configure(state=state)

        if running:
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(text="Cancel Extraction")
        else:
            self.cancel_button.configure(text="Close")
            self._display_ffmpeg_status()
            self._update_sampling_fields()
            self._update_post_action_fields()

    def _request_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not self._cancel_event.is_set():
                self._cancel_event.set()
                self._close_after_cancel = True
                self.status_var.set("Cancelling after FFmpeg stops safely…")
                self.cancel_button.configure(state="disabled")
            return
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        if self._ffmpeg_discovery_after_id is not None:
            self.after_cancel(self._ffmpeg_discovery_after_id)
            self._ffmpeg_discovery_after_id = None
        if self._duration_after_id is not None:
            self.after_cancel(self._duration_after_id)
            self._duration_after_id = None
        self.destroy()

    def _remember_form_settings(self, options: VideoExtractionOptions) -> None:
        """Persist only reusable local conveniences, never video contents."""
        self.settings.video_ffmpeg_path = str(options.ffmpeg_path)
        self.settings.video_last_source = str(options.source_video)
        self.settings.video_last_destination = str(options.destination_folder)
        self.settings.video_sampling_mode = options.sampling_mode
        self.settings.video_interval_seconds = options.interval_seconds
        self.settings.video_scene_threshold = options.scene_threshold
        self.settings.video_max_frames = options.max_frames
        self.settings.video_output_format = options.output_format
        self._persist_settings()

    def _persist_settings(self) -> None:
        if self.on_settings_saved is not None:
            # Pass the dialog-owned object explicitly.  The main application
            # rebuilds its shared AppSettings instance after each save, so a
            # zero-argument callback would otherwise lose later form changes
            # held by this still-open dialog.
            self.on_settings_saved(self.settings)


class VideoExtractionReportDialog(tk.Toplevel):
    """Scrollable report preserving the complete FFmpeg command and import audit."""

    def __init__(self, parent: tk.Misc, result: VideoSourceResult) -> None:
        super().__init__(parent)
        self.title("Video Source Import Report")
        self.geometry("860x620")
        self.minsize(700, 500)
        self.transient(parent)

        report = format_extraction_summary(result.extraction)
        if result.catalog_import is not None:
            report += "\n\n" + "=" * 72 + "\n\n"
            report += format_import_summary(result.catalog_import)
        report += (
            "\n\nMulti-person action frames remain review-needed candidates. "
            "Use the Catalog Browser, Remove Unnecessary Images, and manual "
            "review before treating them as single-subject training images."
        )
        if result.run_analysis_requested:
            report += (
                "\n\nThe configured providers will start after this report closes."
            )

        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        text = tk.Text(container, wrap="word", font=get_ui_font(self, size=10, family=MONOSPACE_FONT_FAMILY))
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", report)
        text.configure(state="disabled")
        ttk.Button(container, text="Close", command=self.destroy).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=(10, 0)
        )
        self.grab_set()


def show_video_extraction_report(
    parent: tk.Misc,
    result: VideoSourceResult,
) -> None:
    """Show the required complete, scrollable post-extraction report."""
    dialog = VideoExtractionReportDialog(parent, result)
    parent.wait_window(dialog)
