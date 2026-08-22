"""Tkinter presentation for final checks, local quality, and export handoff."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Iterable

from dataset_readiness import (
    DEFAULT_READINESS_PROFILE_KEY,
    READINESS_PROFILES,
    READINESS_PROFILES_BY_KEY,
    DatasetReadinessReport,
    build_readiness_report,
)
from quality_analysis import (
    DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
    QualityAnalysisSummary,
    QualityCancellationToken,
    QualityProgress,
    analyze_catalog_quality,
    duplicate_similarity_description,
)
from image_sets import ImageSetRepository, ImageSetSummary
from settings_manager import AppSettings, save_settings
from ui_fonts import get_ui_font
from ui_scroll import register_mousewheel_region


PROFILE_LABEL_TO_KEY = {profile.label: profile.key for profile in READINESS_PROFILES}
ALL_CATALOG_IMAGES_LABEL = "All catalog images"


def _image_set_scope_label(summary: ImageSetSummary) -> str:
    """Keep saved-set choices visually distinct from the built-in all scope."""
    return f"Set: {summary.name}"


def eligible_export_records(records: Iterable[object]) -> list[object]:
    """Exclude deliberate non-training statuses from a final export scope."""
    return [
        record
        for record in records
        if str(getattr(record, "review_status", "")).casefold()
        not in {"reject", "quarantined"}
    ]


class Tooltip:
    """A restrained hover tooltip for compact labels with nuanced meaning."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: tk.Event) -> None:
        if self.window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            window,
            text=self.text,
            background="#FFFCE8",
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=5,
            wraplength=420,
            justify="left",
        ).pack()
        self.window = window

    def _hide(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class DatasetReadinessFrame(ttk.Frame):
    """Show readiness, composition, and manually started quality analysis.

    The frame owns only transient run state. Measurements are committed to the
    selected catalog because decoding every image is expensive; progress,
    cancellation, and the reanalyze checkbox disappear when the app closes.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        show_query: Callable[[str], None],
        load_records: Callable[[], tuple[Iterable[object], str]] | None = None,
        settings: AppSettings | None = None,
        on_settings_saved: Callable[[], None] | None = None,
        on_quality_running_changed: Callable[[bool], None] | None = None,
        export_scope: (
            Callable[[list[int], str, DatasetReadinessReport, str], None] | None
        ) = None,
    ) -> None:
        super().__init__(parent, padding=10)
        self.show_query = show_query
        self.load_records = load_records
        self.settings = settings or AppSettings()
        self.on_settings_saved = on_settings_saved
        self.on_quality_running_changed = on_quality_running_changed
        self.export_scope = export_scope
        self.catalog_var = tk.StringVar(value="No catalog selected")
        self.quality_status_var = tk.StringVar(value="Quality analysis has not been started.")
        self.quality_progress_var = tk.DoubleVar(value=0.0)
        self.reanalyze_all_var = tk.BooleanVar(value=False)
        self._external_quality_run_button: ttk.Button | None = None
        self._external_quality_cancel_button: ttk.Button | None = None
        self._records: list[object] = []
        self._catalog_path: Path | None = None
        self._tooltips: list[Tooltip] = []
        self._worker: threading.Thread | None = None
        self._cancellation: QualityCancellationToken | None = None
        self._quality_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._quality_after_id: str | None = None
        self._image_sets_by_label: dict[str, ImageSetSummary] = {}
        self._selected_image_set_id: int | None = None
        self._current_report: DatasetReadinessReport | None = None
        self._score_value_var: tk.StringVar | None = None
        self._score_status_var: tk.StringVar | None = None
        self._score_progress: ttk.Progressbar | None = None
        self._target_summary_var: tk.StringVar | None = None
        self._issue_result_vars: dict[str, tk.StringVar] = {}
        self._issue_buttons: dict[str, ttk.Button] = {}
        self._handoff_scope_var: tk.StringVar | None = None
        self._handoff_profile_var: tk.StringVar | None = None
        self._handoff_count_var: tk.StringVar | None = None
        self._handoff_status_var: tk.StringVar | None = None
        self._export_scope_button: ttk.Button | None = None

        profile = READINESS_PROFILES_BY_KEY.get(
            self.settings.readiness_profile_key,
            READINESS_PROFILES_BY_KEY[DEFAULT_READINESS_PROFILE_KEY],
        )
        self.profile_var = tk.StringVar(value=profile.label)
        self.image_set_var = tk.StringVar(value=ALL_CATALOG_IMAGES_LABEL)
        self.blur_threshold_var = tk.StringVar(
            value=f"{self.settings.quality_blur_threshold:g}"
        )
        self.duplicate_similarity_var = tk.DoubleVar(
            value=float(self.settings.quality_duplicate_similarity_percent)
        )
        self.duplicate_percent_var = tk.StringVar(
            value=self._similarity_label(
                self.settings.quality_duplicate_similarity_percent
            )
        )

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_header()
        self._build_quality_controls()
        self._build_scrolling_content()
        self._render_empty()
        self._quality_after_id = self.after(100, self._process_quality_queue)

    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Catalog:").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.catalog_var).grid(
            row=0, column=1, sticky="ew", padx=(6, 8)
        )
        ttk.Label(header, text="Image set:").grid(row=0, column=2, sticky="e")
        self.image_set_combo = ttk.Combobox(
            header,
            textvariable=self.image_set_var,
            values=(ALL_CATALOG_IMAGES_LABEL,),
            state="readonly",
            width=26,
        )
        self.image_set_combo.grid(row=0, column=3, padx=(6, 12))
        self.image_set_combo.bind("<<ComboboxSelected>>", self._on_image_set_changed)
        ttk.Label(header, text="Target:").grid(row=0, column=4, sticky="e")
        self.profile_combo = ttk.Combobox(
            header,
            textvariable=self.profile_var,
            values=tuple(profile.label for profile in READINESS_PROFILES),
            state="readonly",
            width=24,
        )
        self.profile_combo.grid(row=0, column=5, padx=(6, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_changed)
        ttk.Button(header, text="Refresh", command=self.refresh).grid(row=0, column=6)

    def _build_quality_controls(self) -> None:
        controls = ttk.LabelFrame(self, text="Local Image Quality", padding=9)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(5, weight=1)

        self.run_button = ttk.Button(
            controls,
            text="Run Quality Analysis",
            command=self._start_quality_analysis,
        )
        self.run_button.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self.cancel_button = ttk.Button(
            controls,
            text="Cancel",
            command=self._cancel_quality_analysis,
            state="disabled",
        )
        self.cancel_button.grid(row=0, column=1, rowspan=2, sticky="nsw", padx=(6, 14))

        blur_label = ttk.Label(controls, text="Blur threshold:")
        blur_label.grid(row=0, column=2, sticky="w")
        self._tooltips.append(
            Tooltip(
                blur_label,
                "Images scoring below this local sharpness threshold appear under Blur. "
                "Change the shared value under Settings > Filter Settings.",
            )
        )
        ttk.Label(
            controls,
            textvariable=self.blur_threshold_var,
            style="Accent.TLabel",
        ).grid(row=0, column=3, sticky="w", padx=(6, 4))
        duplicate_label = ttk.Label(controls, text="Similarity match:")
        duplicate_label.grid(row=0, column=4, sticky="w")
        self._tooltips.append(
            Tooltip(
                duplicate_label,
                "The shared Possible Duplicates strictness is configured under "
                "Settings > Filter Settings. Lower values catch more near-matches; "
                "100% is strictest. Matches never change review status.",
            )
        )
        ttk.Label(
            controls,
            textvariable=self.duplicate_percent_var,
            width=52,
        ).grid(row=0, column=5, columnspan=2, sticky="w", padx=(6, 0))

        ttk.Checkbutton(
            controls,
            text="Reanalyze cached images",
            variable=self.reanalyze_all_var,
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(7, 0))
        self.quality_progress = ttk.Progressbar(
            controls,
            maximum=100,
            variable=self.quality_progress_var,
        )
        self.quality_progress.grid(
            row=1, column=4, columnspan=3, sticky="ew", pady=(7, 0)
        )
        ttk.Label(
            controls,
            textvariable=self.quality_status_var,
            foreground="#5F5F5F",
            wraplength=950,
            justify="left",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(7, 0))

    def _build_scrolling_content(self) -> None:
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.content_canvas = canvas
        canvas.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(canvas, padding=(6, 2, 12, 12))
        window_id = canvas.create_window((0, 0), anchor="nw", window=self.content)
        self.content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window_id, width=max(1, event.width)),
        )
        register_mousewheel_region(canvas)

    def set_records(self, records: Iterable[object], catalog_path: str) -> None:
        """Replace the dashboard source with the browser's current projection."""
        self._records = list(records)
        self.catalog_var.set(catalog_path or "No catalog selected")
        self._catalog_path = Path(catalog_path).resolve() if catalog_path else None
        self.refresh_image_sets()
        self._render_current()

    def refresh(self) -> None:
        """Reload the common browser projection and recalculate the dashboard."""
        if self.load_records is not None:
            try:
                records, catalog_path = self.load_records()
                self._records = list(records)
                self.catalog_var.set(catalog_path or "No catalog selected")
                self._catalog_path = Path(catalog_path).resolve() if catalog_path else None
            except Exception as error:
                messagebox.showerror(
                    "Could not refresh readiness",
                    str(error),
                    parent=self,
                )
                return
        self.refresh_image_sets()
        self._render_current()

    def refresh_image_sets(self) -> None:
        """Reload deliberate saved scopes while preserving the current set by ID."""
        summaries: list[ImageSetSummary] = []
        if self._catalog_path is not None and self._catalog_path.exists():
            try:
                summaries = ImageSetRepository(self._catalog_path).list_sets()
            except Exception:
                logging.exception("Could not load image sets for Dataset Readiness")
        self._image_sets_by_label = {
            _image_set_scope_label(summary): summary for summary in summaries
        }
        values = (
            ALL_CATALOG_IMAGES_LABEL,
            *(_image_set_scope_label(summary) for summary in summaries),
        )
        self.image_set_combo.configure(values=values)
        selected = next(
            (
                summary
                for summary in summaries
                if summary.set_id == self._selected_image_set_id
            ),
            None,
        )
        if selected is None:
            self._selected_image_set_id = None
            self.image_set_var.set(ALL_CATALOG_IMAGES_LABEL)
        else:
            self.image_set_var.set(_image_set_scope_label(selected))

    def _on_image_set_changed(self, _event: tk.Event | None = None) -> None:
        """Apply one session-only readiness scope selected by the user."""
        summary = self._image_sets_by_label.get(self.image_set_var.get())
        self._selected_image_set_id = summary.set_id if summary is not None else None
        self._render_current()

    def _scoped_records(self) -> list[object]:
        if self._selected_image_set_id is None:
            return list(self._records)
        if self._catalog_path is None:
            return []
        try:
            member_ids = set(
                ImageSetRepository(self._catalog_path).get_image_ids(
                    self._selected_image_set_id
                )
            )
        except Exception:
            logging.exception("Could not load the selected readiness image set")
            return []
        return [
            record
            for record in self._records
            if int(getattr(record, "image_id")) in member_ids
        ]

    def _eligible_scoped_records(self) -> list[object]:
        """Return intended training records while preserving review visibility."""
        return eligible_export_records(self._scoped_records())

    def _active_scope_label(self) -> str:
        """Return a stable human-readable label for export reports and README."""
        if self._selected_image_set_id is None:
            return ALL_CATALOG_IMAGES_LABEL
        summary = next(
            (
                item
                for item in self._image_sets_by_label.values()
                if item.set_id == self._selected_image_set_id
            ),
            None,
        )
        return f'Image set "{summary.name}"' if summary is not None else self.image_set_var.get()

    def _current_profile_key(self) -> str:
        return PROFILE_LABEL_TO_KEY.get(
            self.profile_var.get(),
            DEFAULT_READINESS_PROFILE_KEY,
        )

    def _current_blur_threshold(self, *, show_error: bool = False) -> float | None:
        try:
            value = float(self.blur_threshold_var.get())
        except ValueError:
            value = -1.0
        if 0.0 <= value <= 10000.0:
            return value
        if show_error:
            messagebox.showerror(
                "Invalid blur threshold",
                "Blur threshold must be a number from 0 to 10,000.",
                parent=self,
            )
        return None

    def _render_current(self) -> None:
        blur_threshold = self._current_blur_threshold()
        if blur_threshold is None:
            return
        self._render(
            build_readiness_report(
                self._scoped_records(),
                profile_key=self._current_profile_key(),
                blur_threshold=blur_threshold,
                duplicate_similarity_percent=round(self.duplicate_similarity_var.get()),
                overlay_coverage_threshold_percent=(
                    self.settings.overlay_coverage_threshold_percent
                ),
                overlay_spatial_mode=(
                    self.settings.overlay_spatial_mode
                ),
            )
        )

    def _on_profile_changed(self, _event: tk.Event | None = None) -> None:
        """Update every readiness check affected by the selected LoRA profile."""
        self.settings.readiness_profile_key = self._current_profile_key()
        self._save_preferences()
        report = self._build_current_report()
        if report is None or not self._update_score_and_issue(report, "Low Resolution"):
            self._render_current()
            return
        self._update_issue_result(report, "No Training Text")
        self._update_issue_result(report, "Repeated Training Text")
        self._current_report = report
        if self._target_summary_var is not None:
            self._target_summary_var.set(f"Target profile: {report.profile.label}")

    def _on_blur_changed(self, _event: tk.Event | None = None) -> None:
        """Commit a blur interpretation and update only its dependent widgets."""
        blur_threshold = self._current_blur_threshold(show_error=True)
        if blur_threshold is None:
            return
        self.settings.quality_blur_threshold = blur_threshold
        self._save_preferences()
        report = self._build_current_report()
        if report is None or not self._update_score_and_issue(report, "Blur"):
            self._render_current()
            return
        self._current_report = report

    def _on_duplicate_slider(self, value: str) -> None:
        """Snap and describe the drag without recalculating dashboard content."""
        percentage = self._nearest_similarity_step(value)
        if round(self.duplicate_similarity_var.get()) != percentage:
            self.duplicate_similarity_var.set(percentage)
        self.duplicate_percent_var.set(self._similarity_label(percentage))

    def _commit_duplicate_slider(self, _event: tk.Event | None = None) -> None:
        """Apply similarity only after mouse/key release and update one issue row."""
        percentage = self._nearest_similarity_step(self.duplicate_similarity_var.get())
        self.duplicate_similarity_var.set(percentage)
        self.duplicate_percent_var.set(self._similarity_label(percentage))
        self.settings.quality_duplicate_similarity_percent = percentage
        self._save_preferences()
        report = self._build_current_report()
        if report is None or not self._update_issue_result(report, "Possible Duplicates"):
            self._render_current()
            return
        self._current_report = report

    def _build_current_report(self) -> DatasetReadinessReport | None:
        blur_threshold = self._current_blur_threshold()
        if blur_threshold is None:
            return None
        return build_readiness_report(
            self._scoped_records(),
            profile_key=self._current_profile_key(),
            blur_threshold=blur_threshold,
            duplicate_similarity_percent=round(self.duplicate_similarity_var.get()),
            overlay_coverage_threshold_percent=(
                self.settings.overlay_coverage_threshold_percent
            ),
            overlay_spatial_mode=self.settings.overlay_spatial_mode,
        )

    def _update_score_and_issue(
        self,
        report: DatasetReadinessReport,
        issue_label: str,
    ) -> bool:
        if (
            self._score_value_var is None
            or self._score_status_var is None
            or self._score_progress is None
        ):
            return False
        self._score_value_var.set(f"{report.score}%")
        self._score_status_var.set(report.status)
        self._score_progress.configure(value=report.score)
        self._update_handoff_summary(report)
        return self._update_issue_result(report, issue_label)

    def _update_issue_result(
        self,
        report: DatasetReadinessReport,
        issue_label: str,
    ) -> bool:
        result_var = self._issue_result_vars.get(issue_label)
        issue = next((item for item in report.issues if item.label == issue_label), None)
        if result_var is None or issue is None:
            return False
        result_var.set(self._format_issue_result(issue))
        button = self._issue_buttons.get(issue_label)
        if button is not None:
            button.configure(command=lambda query=issue.query: self._show_issue(query))
            button.configure(state="normal" if issue.query.strip() else "disabled")
        self._update_handoff_summary(report)
        return True

    @staticmethod
    def _nearest_similarity_step(value: str | float) -> int:
        """Clamp the perceptual-match control to whole-number 96-100 steps."""
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = DEFAULT_DUPLICATE_SIMILARITY_PERCENT
        return max(96, min(100, round(numeric_value)))

    @staticmethod
    def _similarity_label(percentage: int) -> str:
        """Return the exact integer step plus its plain-language meaning."""
        return duplicate_similarity_description(percentage)

    def _save_preferences(self) -> None:
        try:
            if self.on_settings_saved is not None:
                self.on_settings_saved()
            else:
                save_settings(self.settings)
        except OSError:
            logging.exception("Could not save readiness preferences")

    def bind_external_quality_controls(
        self,
        *,
        run_button: ttk.Button,
        cancel_button: ttk.Button,
        progressbar: ttk.Progressbar,
    ) -> None:
        """Share one quality worker with the primary Analyze & Update controls."""
        self._external_quality_run_button = run_button
        self._external_quality_cancel_button = cancel_button
        progressbar.configure(variable=self.quality_progress_var, maximum=100)

    def _set_quality_button_states(self, *, running: bool) -> None:
        run_state = "disabled" if running else "normal"
        cancel_state = "normal" if running else "disabled"
        self.run_button.configure(state=run_state)
        self.cancel_button.configure(state=cancel_state)
        if self._external_quality_run_button is not None:
            self._external_quality_run_button.configure(state=run_state)
        if self._external_quality_cancel_button is not None:
            self._external_quality_cancel_button.configure(state=cancel_state)

    def _start_quality_analysis(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        if self._catalog_path is None or not self._catalog_path.exists():
            messagebox.showinfo(
                "Choose a catalog",
                "Open or create a LoRA Image Curator catalog before running quality analysis.",
                parent=self,
            )
            return

        self._cancellation = QualityCancellationToken()
        if self.on_quality_running_changed is not None:
            self.on_quality_running_changed(True)
        self._set_quality_button_states(running=True)
        self.quality_progress_var.set(0)
        self.quality_status_var.set("Starting local quality analysis…")
        database_path = self._catalog_path
        reanalyze_all = self.reanalyze_all_var.get()
        token = self._cancellation

        def worker() -> None:
            try:
                summary = analyze_catalog_quality(
                    database_path,
                    reanalyze_all=reanalyze_all,
                    cancellation=token,
                    progress_callback=lambda progress: self._quality_queue.put(
                        ("progress", progress)
                    ),
                )
                self._quality_queue.put(("complete", summary))
            except Exception as error:
                logging.exception("Quality analysis failed")
                self._quality_queue.put(("error", error))

        self._worker = threading.Thread(
            target=worker,
            daemon=True,
            name="dataset-quality-analysis",
        )
        self._worker.start()

    def _cancel_quality_analysis(self) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()
            self.cancel_button.configure(state="disabled")
            if self._external_quality_cancel_button is not None:
                self._external_quality_cancel_button.configure(state="disabled")
            self.quality_status_var.set("Cancelling after the current image…")

    def _process_quality_queue(self) -> None:
        try:
            while True:
                message_type, payload = self._quality_queue.get_nowait()
                if message_type == "progress":
                    self._show_quality_progress(payload)  # type: ignore[arg-type]
                elif message_type == "complete":
                    self._finish_quality_analysis(payload)  # type: ignore[arg-type]
                elif message_type == "error":
                    self._fail_quality_analysis(payload)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._quality_after_id = self.after(100, self._process_quality_queue)

    def _show_quality_progress(self, progress: QualityProgress) -> None:
        percentage = (progress.completed / progress.total) * 100 if progress.total else 0
        self.quality_progress_var.set(percentage)
        name = progress.current_path.name if progress.current_path is not None else "Unavailable file"
        self.quality_status_var.set(
            f"{progress.completed:,} / {progress.total:,}: {name}  •  "
            f"analyzed {progress.analyzed:,}, reused {progress.reused:,}, failed {progress.failed:,}"
        )

    def _finish_quality_analysis(self, summary: QualityAnalysisSummary) -> None:
        self._set_quality_button_states(running=False)
        if self.on_quality_running_changed is not None:
            self.on_quality_running_changed(False)
        if summary.total_images:
            completed = summary.analyzed_images + summary.reused_images + summary.failed_images
            self.quality_progress_var.set((completed / summary.total_images) * 100)
        state = "Cancelled" if summary.cancelled else "Complete"
        self.quality_status_var.set(
            f"{state}: analyzed {summary.analyzed_images:,}, reused {summary.reused_images:,}, "
            f"failed {summary.failed_images:,} in {summary.total_seconds:.1f} seconds."
        )
        self.refresh()

    def _fail_quality_analysis(self, error: Exception) -> None:
        self._set_quality_button_states(running=False)
        if self.on_quality_running_changed is not None:
            self.on_quality_running_changed(False)
        self.quality_status_var.set("Quality analysis failed.")
        messagebox.showerror(
            "Quality analysis failed",
            f"{type(error).__name__}: {error}",
            parent=self,
        )

    def _clear(self) -> None:
        # Tooltips attached to persistent header controls stay alive; rendered
        # issue tooltips are removed alongside the content widgets they explain.
        persistent_tooltips = self._tooltips[:2]
        for tooltip in self._tooltips[2:]:
            tooltip._hide()
        for child in self.content.winfo_children():
            child.destroy()
        self._tooltips = persistent_tooltips
        self._current_report = None
        self._score_value_var = None
        self._score_status_var = None
        self._score_progress = None
        self._target_summary_var = None
        self._issue_result_vars = {}
        self._issue_buttons = {}
        self._handoff_scope_var = None
        self._handoff_profile_var = None
        self._handoff_count_var = None
        self._handoff_status_var = None
        self._export_scope_button = None

    def _render_empty(self) -> None:
        self._clear()
        if self._catalog_path is not None and self._selected_image_set_id is not None:
            summary = next(
                (
                    item
                    for item in self._image_sets_by_label.values()
                    if item.set_id == self._selected_image_set_id
                ),
                None,
            )
            set_name = summary.name if summary is not None else self.image_set_var.get()
            message = (
                f'The image set "{set_name}" is empty.\n\n'
                "Add images from the Catalog Browser, then return to Finalize & Export."
            )
        else:
            message = (
                "Open or create a LoRA Image Curator catalog to calculate readiness.\n\n"
                "Quality analysis starts only when you click Run Quality Analysis."
            )
        ttk.Label(
            self.content,
            text=message,
            justify="center",
        ).pack(fill="x", pady=80)

    def _render(self, report: DatasetReadinessReport) -> None:
        self._clear()
        if report.total_images == 0:
            self._render_empty()
            return
        self._current_report = report

        title = ttk.Frame(self.content)
        title.pack(fill="x", pady=(0, 10))
        ttk.Label(title, text="Finalize & Export", font=get_ui_font(self, size=18, weight="bold")).pack(
            side="left"
        )
        self._target_summary_var = tk.StringVar(
            value=f"Target profile: {report.profile.label}"
        )
        ttk.Label(title, textvariable=self._target_summary_var).pack(
            side="right", pady=(7, 0)
        )

        score = ttk.LabelFrame(self.content, text="Overall Readiness", padding=12)
        score.pack(fill="x", pady=(0, 10))
        score.columnconfigure(1, weight=1)
        self._score_value_var = tk.StringVar(value=f"{report.score}%")
        self._score_status_var = tk.StringVar(value=report.status)
        ttk.Label(
            score,
            textvariable=self._score_value_var,
            font=get_ui_font(self, size=24, weight="bold"),
        ).grid(
            row=0, column=0, rowspan=2, sticky="w", padx=(0, 14)
        )
        self._score_progress = ttk.Progressbar(
            score, maximum=100, value=report.score
        )
        self._score_progress.grid(row=0, column=1, sticky="ew", pady=(3, 3))
        ttk.Label(
            score,
            textvariable=self._score_status_var,
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(
            row=1, column=1, sticky="w"
        )
        ttk.Label(
            score,
            text=(
                "This is a preparation checklist, not a prediction of model quality. "
                "Every deduction is shown below."
            ),
            foreground="#5F5F5F",
            wraplength=850,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        checks_and_handoff = ttk.Frame(self.content)
        checks_and_handoff.pack(fill="x", pady=(0, 10))
        checks_and_handoff.columnconfigure(0, weight=3)
        checks_and_handoff.columnconfigure(1, weight=2)

        issues = ttk.LabelFrame(
            checks_and_handoff,
            text="Readiness Checks",
            padding=10,
        )
        issues.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        issues.columnconfigure(1, weight=1)
        ttk.Label(issues, text="Check", font=get_ui_font(self, size=9, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(issues, text="Result", font=get_ui_font(self, size=9, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        for row_index, issue in enumerate(report.issues, start=1):
            has_query = bool(issue.query.strip())
            button = ttk.Button(
                issues,
                text=issue.label,
                command=lambda query=issue.query: self._show_issue(query),
                state="normal" if has_query else "disabled",
                width=24,
            )
            button.grid(row=row_index, column=0, sticky="w", pady=2)
            self._issue_buttons[issue.label] = button
            self._tooltips.append(Tooltip(button, issue.explanation))
            result_var = tk.StringVar(value=self._format_issue_result(issue))
            self._issue_result_vars[issue.label] = result_var
            ttk.Label(
                issues,
                textvariable=result_var,
            ).grid(row=row_index, column=1, sticky="w", padx=(10, 0))

        handoff = ttk.LabelFrame(
            checks_and_handoff,
            text="Training Handoff",
            padding=12,
        )
        handoff.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        handoff.columnconfigure(0, weight=1)
        self._handoff_scope_var = tk.StringVar()
        self._handoff_profile_var = tk.StringVar()
        self._handoff_count_var = tk.StringVar()
        self._handoff_status_var = tk.StringVar()
        ttk.Label(
            handoff,
            textvariable=self._handoff_scope_var,
            font=get_ui_font(self, size=10, weight="bold"),
            wraplength=390,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            handoff,
            textvariable=self._handoff_profile_var,
            wraplength=390,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(
            handoff,
            textvariable=self._handoff_count_var,
            wraplength=390,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Separator(handoff, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=10
        )
        ttk.Label(
            handoff,
            textvariable=self._handoff_status_var,
            wraplength=390,
            justify="left",
        ).grid(row=4, column=0, sticky="w")
        ttk.Label(
            handoff,
            text=(
                "Export copies the eligible images in this scope. Reject and "
                "Quarantined records remain visible in readiness totals but are excluded."
            ),
            foreground="#5F5F5F",
            wraplength=390,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(10, 12))
        self._export_scope_button = ttk.Button(
            handoff,
            text="Export Training Data…",
            command=self._export_current_scope,
        )
        self._export_scope_button.grid(row=6, column=0, sticky="ew")
        self._update_handoff_summary(report)

        composition = ttk.LabelFrame(self.content, text="Dataset Composition", padding=10)
        composition.pack(fill="x", pady=(0, 10))
        composition.columnconfigure((0, 1, 2, 3), weight=1)
        self._stat_column(
            composition,
            0,
            "Images",
            (
                ("Total", report.total_images),
                ("Eligible", report.eligible_images),
                ("Available", report.file_counts.get("present", 0)),
                ("Missing", report.file_counts.get("missing", 0)),
            ),
        )
        self._stat_column(
            composition,
            1,
            "Review State",
            (
                ("Keep", report.review_counts.get("keep", 0)),
                ("Needs follow-up", report.review_counts.get("review", 0)),
                ("Reject", report.review_counts.get("reject", 0)),
                ("Unreviewed", report.review_counts.get("unreviewed", 0)),
            ),
        )
        self._stat_column(
            composition,
            2,
            "Short Side",
            (
                ("Below 512", report.resolution_counts.get("below_512", 0)),
                ("512–767", report.resolution_counts.get("512_to_767", 0)),
                ("768–1023", report.resolution_counts.get("768_to_1023", 0)),
                ("1024+", report.resolution_counts.get("1024_plus", 0)),
                ("Unknown", report.resolution_counts.get("unknown", 0)),
            ),
        )
        self._stat_column(
            composition,
            3,
            "Quality Cache",
            (
                ("Analyzed", report.quality_counts.get("success", 0)),
                ("Errors", report.quality_counts.get("error", 0)),
                ("Not analyzed", report.quality_counts.get("not_analyzed", 0)),
            ),
        )

        tags = ttk.LabelFrame(self.content, text="Common Vocabulary", padding=10)
        tags.pack(fill="x")
        tags.columnconfigure((0, 1, 2, 3), weight=1)
        self._value_column(tags, 0, "Trigger Keywords", report.top_trigger_keywords)
        self._value_column(tags, 1, "Manual Tags", report.top_manual_tags)
        self._value_column(tags, 2, "Active AI Tags", report.top_ai_tags)
        self._value_column(tags, 3, "Most Excluded", report.top_excluded_tags)

    def _update_handoff_summary(self, report: DatasetReadinessReport) -> None:
        """Keep the export card synchronized with profile and threshold changes."""
        if (
            self._handoff_scope_var is None
            or self._handoff_profile_var is None
            or self._handoff_count_var is None
            or self._handoff_status_var is None
        ):
            return
        excluded_count = report.total_images - report.eligible_images
        unresolved = [issue for issue in report.issues if issue.count]
        blocking = sum(issue.count for issue in unresolved if issue.severity == "blocking")
        review = sum(issue.count for issue in unresolved if issue.severity == "review")
        advisory = sum(issue.count for issue in unresolved if issue.severity == "advisory")
        self._handoff_scope_var.set(self._active_scope_label())
        self._handoff_profile_var.set(f"LoRA target: {report.profile.label}")
        self._handoff_count_var.set(
            f"{report.eligible_images:,} eligible image"
            f"{'s' if report.eligible_images != 1 else ''}; "
            f"{excluded_count:,} excluded by review status."
        )
        self._handoff_status_var.set(
            f"{report.status} ({report.score}%).\n"
            f"Blocking findings: {blocking:,}\n"
            f"Review findings: {review:,}\n"
            f"Advisory findings: {advisory:,}"
        )
        if self._export_scope_button is not None:
            self._export_scope_button.configure(
                state=(
                    "normal"
                    if report.eligible_images > 0
                    and self._catalog_path is not None
                    and self.export_scope is not None
                    else "disabled"
                )
            )

    def _export_current_scope(self) -> None:
        """Open the shared exporter for the active catalog or image-set scope."""
        report = self._build_current_report()
        if report is None or self._catalog_path is None or self.export_scope is None:
            return
        eligible = self._eligible_scoped_records()
        if not eligible:
            messagebox.showinfo(
                "No eligible images",
                "This scope contains no images eligible for export.",
                parent=self,
            )
            return
        self.export_scope(
            [int(getattr(record, "image_id")) for record in eligible],
            self._active_scope_label(),
            report,
            self._current_profile_key(),
        )

    @staticmethod
    def _format_issue_result(issue) -> str:
        deduction = (
            f"  (−{issue.deduction:.1f}, up to {issue.maximum_deduction})"
            if issue.maximum_deduction
            else "  (advisory)"
        )
        return f"{issue.count:,} image{'s' if issue.count != 1 else ''}{deduction}"

    def _show_issue(self, query: str) -> None:
        """Keep a readiness issue inside the currently selected saved set."""
        if not query.strip():
            return
        if self._selected_image_set_id is None:
            self.show_query(query)
            return
        summary = next(
            (
                item
                for item in self._image_sets_by_label.values()
                if item.set_id == self._selected_image_set_id
            ),
            None,
        )
        set_name = summary.name if summary is not None else self.image_set_var.get()
        escaped_name = set_name.replace("\\", "\\\\").replace('"', '\\"')
        self.show_query(f'set:"{escaped_name}" AND ({query})')

    @staticmethod
    def _stat_column(parent: ttk.Frame, column: int, heading: str, rows) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 14, 0))
        ttk.Label(
            frame,
            text=heading,
            font=get_ui_font(parent, size=10, weight="bold"),
        ).pack(anchor="w")
        for label, count in rows:
            ttk.Label(frame, text=f"{label}: {count:,}").pack(anchor="w", pady=1)

    @staticmethod
    def _value_column(parent: ttk.Frame, column: int, heading: str, rows) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 14, 0))
        ttk.Label(
            frame,
            text=heading,
            font=get_ui_font(parent, size=10, weight="bold"),
        ).pack(anchor="w")
        if not rows:
            ttk.Label(frame, text="None", foreground="#5F5F5F").pack(anchor="w")
            return
        for label, count in rows:
            ttk.Label(frame, text=f"{label}  ({count:,})", wraplength=220).pack(anchor="w")

    def shutdown(self) -> None:
        """Request cooperative cancellation before the root window closes."""
        if self._cancellation is not None:
            self._cancellation.cancel()
        if self._quality_after_id is not None:
            try:
                self.after_cancel(self._quality_after_id)
            except tk.TclError:
                pass
            self._quality_after_id = None

        # The application owns this frame, while these bound callbacks point
        # back to the application. Remove the Python-level cycle explicitly so
        # Tk variables cannot be left for collection on an unrelated worker.
        self.show_query = lambda _query: None
        self.load_records = None
        self.on_settings_saved = None
        self.on_quality_running_changed = None
        self.export_scope = None

    @property
    def is_running(self) -> bool:
        """Return whether a quality worker is still active."""
        return self._worker is not None and self._worker.is_alive()
