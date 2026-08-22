"""
export_dialog.py

Tkinter workflow for Milestone 7D dataset export and caption assembly.

The dialog keeps filesystem choices separate from the browser details pane. It
loads the current selection once, displays the exact derived text for a sample,
can preview every destination name before writing, and delegates all actual
copying to ``dataset_export.execute_export`` on a worker thread. Tk widgets are
updated only on the GUI thread.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from dataset_export import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    DatasetExportRepository,
    ExportCancellationToken,
    ExportOptions,
    ExportPlan,
    ExportProgress,
    ExportResult,
    build_export_plan,
    execute_export,
    format_export_preview,
)
from dataset_readiness import DatasetReadinessReport
from settings_manager import AppSettings, save_settings
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    PROFILE_LABEL_TO_KEY,
    TrainingTextProfile,
    build_training_text,
    custom_training_profile,
    find_repeated_training_text_groups,
    get_training_profile,
)
from ui_fonts import get_ui_font


PROFILE_LABELS = tuple(
    profile.label for profile in BUILTIN_TRAINING_PROFILES.values()
) + ("Custom",)
COLLISION_LABEL_TO_KEY = {
    "Rename safely": COLLISION_RENAME,
    "Skip existing": COLLISION_SKIP,
}
COLLISION_KEY_TO_LABEL = {value: key for key, value in COLLISION_LABEL_TO_KEY.items()}


class ExportPlanPreviewDialog(tk.Toplevel):
    """Read-only preview of counts, paths, and one sidecar example."""

    def __init__(self, parent: tk.Misc, plan: ExportPlan) -> None:
        super().__init__(parent)
        self.title("Preview Export")
        self.geometry("760x620")
        self.minsize(600, 420)
        self.transient(parent.winfo_toplevel())

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(
            body,
            text="No files have been written. This is the exact current plan.",
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        text = tk.Text(body, wrap="word", padx=8, pady=8)
        text.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", format_export_preview(plan))
        text.configure(state="disabled")

        ttk.Button(body, text="Close", command=self.destroy).grid(
            row=2, column=0, columnspan=2, sticky="e", pady=(10, 0)
        )
        self.bind("<Escape>", lambda _event: self.destroy())


class ExportProgressDialog(tk.Toplevel):
    """Run one export in the background and display a durable completion report."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        plan: ExportPlan,
        repository: DatasetExportRepository,
    ) -> None:
        super().__init__(parent)
        self.title("Export Dataset")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._on_close_requested)

        self.plan = plan
        self.repository = repository
        self.cancellation = ExportCancellationToken()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.result: ExportResult | None = None

        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.heading_var = tk.StringVar(value="Preparing export…")
        ttk.Label(
            body,
            textvariable=self.heading_var,
            font=get_ui_font(self, size=11, weight="bold"),
            wraplength=560,
        ).grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(
            body,
            orient="horizontal",
            mode="determinate",
            maximum=max(1, plan.requested_count),
            length=560,
        )
        self.progress.grid(row=1, column=0, sticky="ew", pady=(10, 5))

        self.detail_var = tk.StringVar(value="Starting…")
        ttk.Label(
            body,
            textvariable=self.detail_var,
            wraplength=560,
            justify="left",
        ).grid(row=2, column=0, sticky="w")

        self.summary_var = tk.StringVar(value="")
        ttk.Label(
            body,
            textvariable=self.summary_var,
            wraplength=560,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e", pady=(12, 0))
        self.open_folder_button = ttk.Button(
            buttons,
            text="Open Folder",
            command=self._open_destination,
            state="disabled",
        )
        self.open_folder_button.grid(row=0, column=0)
        self.cancel_button = ttk.Button(
            buttons,
            text="Cancel",
            command=self._cancel,
        )
        self.cancel_button.grid(row=0, column=1, padx=(7, 0))
        self.close_button = ttk.Button(
            buttons,
            text="Close",
            command=self.destroy,
            state="disabled",
        )
        self.close_button.grid(row=0, column=2, padx=(7, 0))

        self.grab_set()
        self.worker = threading.Thread(
            target=self._run_worker,
            name="dataset-export",
            daemon=True,
        )
        self.worker.start()
        self.after(75, self._poll_events)

    def _run_worker(self) -> None:
        try:
            result = execute_export(
                self.plan,
                repository=self.repository,
                cancellation=self.cancellation,
                progress_callback=lambda progress: self.events.put(("progress", progress)),
            )
        except Exception as error:
            self.events.put(("error", error))
        else:
            self.events.put(("done", result))

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.events.get_nowait()
                if event_type == "progress":
                    self._show_progress(payload)  # type: ignore[arg-type]
                elif event_type == "done":
                    self._show_result(payload)  # type: ignore[arg-type]
                    return
                elif event_type == "error":
                    self._show_error(payload)  # type: ignore[arg-type]
                    return
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(75, self._poll_events)

    def _show_progress(self, progress: ExportProgress) -> None:
        self.progress.configure(value=progress.processed_count)
        self.heading_var.set(
            f"Exporting {progress.processed_count:,} of {progress.total_count:,}"
        )
        self.detail_var.set(f"{progress.filename}\n{progress.message}")

    def _show_result(self, result: ExportResult) -> None:
        self.result = result
        self.progress.configure(value=result.requested_count)
        heading = {
            "complete": "Export complete",
            "partial": "Export completed with skipped or failed items",
            "cancelled": "Export cancelled",
        }.get(result.status, "Export finished")
        self.heading_var.set(heading)
        self.detail_var.set(str(self.plan.options.destination))

        lines = [
            f"Exported: {result.exported_count:,}",
            f"Skipped: {result.skipped_count:,}",
            f"Failed: {result.failed_count:,}",
        ]
        if result.manifest_path is not None:
            lines.append(f"Manifest: {result.manifest_path.name}")
        if result.readme_path is not None:
            lines.append(f"Training handoff: {result.readme_path.name}")
        if result.error_report_path is not None:
            lines.append(f"Error report: {result.error_report_path.name}")
        self.summary_var.set("\n".join(lines))

        self.cancel_button.configure(state="disabled")
        self.close_button.configure(state="normal")
        self.open_folder_button.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _show_error(self, error: Exception) -> None:
        self.heading_var.set("Export failed")
        self.detail_var.set(f"{type(error).__name__}: {error}")
        self.summary_var.set(
            "LoRA Image Curator did not overwrite any existing destination files. "
            "Already completed files, if any, remain in the export folder."
        )
        self.cancel_button.configure(state="disabled")
        self.close_button.configure(state="normal")
        if self.plan.options.destination.exists():
            self.open_folder_button.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _cancel(self) -> None:
        if self.result is not None:
            return
        self.cancellation.cancel()
        self.cancel_button.configure(state="disabled")
        self.heading_var.set("Cancelling after the current file…")

    def _on_close_requested(self) -> None:
        if self.result is not None:
            self.destroy()
            return
        if messagebox.askyesno(
            "Cancel export?",
            "The export is still running. Cancel after the current file?",
            parent=self,
        ):
            self._cancel()

    def _open_destination(self) -> None:
        _open_folder(self.plan.options.destination, parent=self)


class DatasetExportDialog(tk.Toplevel):
    """Configure, preview, and launch one non-destructive training handoff."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        database_path: Path,
        image_ids: list[int],
        settings: AppSettings,
        on_settings_saved: Callable[[], None] | None = None,
        scope_label: str = "Browser selection",
        readiness_report: DatasetReadinessReport | None = None,
        initial_profile_key: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Export Training Handoff")
        self.geometry("740x840")
        self.minsize(680, 740)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.database_path = database_path
        self.image_ids = list(image_ids)
        self.settings = settings
        self.on_settings_saved = on_settings_saved
        self.scope_label = " ".join(str(scope_label).split()).strip() or "Browser selection"
        self.readiness_report = readiness_report
        self.repository = DatasetExportRepository(database_path)

        try:
            self.records = self.repository.fetch_records(self.image_ids)
        except Exception as error:
            self.destroy()
            messagebox.showerror(
                "Could not prepare export",
                f"{type(error).__name__}: {error}",
                parent=parent,
            )
            return

        default_destination = (
            Path(settings.export_last_directory)
            if settings.export_last_directory
            else database_path.parent / "exports"
        )
        self.destination_var = tk.StringVar(value=str(default_destination))
        self.profile_var = tk.StringVar(
            value=self._label_for_profile_key(
                initial_profile_key or settings.export_profile_key
            )
        )
        self.copy_images_var = tk.BooleanVar(value=settings.export_copy_images)
        self.sidecars_var = tk.BooleanVar(value=settings.export_create_sidecars)
        self.manifest_var = tk.BooleanVar(value=settings.export_create_manifest)
        self.readme_var = tk.BooleanVar(value=settings.export_create_readme)
        self.collision_var = tk.StringVar(
            value=COLLISION_KEY_TO_LABEL.get(
                settings.export_collision_policy,
                "Rename safely",
            )
        )
        self.custom_trigger_var = tk.BooleanVar(
            value=settings.export_custom_include_trigger
        )
        self.custom_manual_var = tk.BooleanVar(
            value=settings.export_custom_include_manual_tags
        )
        self.custom_ai_var = tk.BooleanVar(
            value=settings.export_custom_include_ai_tags
        )
        self.custom_caption_var = tk.BooleanVar(
            value=settings.export_custom_include_raw_caption
        )
        self.profile_description_var = tk.StringVar()
        self.sample_var = tk.StringVar()
        self.preflight_var = tk.StringVar()

        self._build_interface()
        self._on_profile_changed()
        self.grab_set()

    def _build_interface(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(7, weight=1)

        ttk.Label(
            body,
            text=(
                f"Export {len(self.records):,} eligible image"
                f"{'s' if len(self.records) != 1 else ''} from {self.scope_label}"
            ),
            font=get_ui_font(self, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=(
                "Exports copy data into a new folder. Original images and catalog "
                "curation are never moved, renamed, deleted, or overwritten."
            ),
            wraplength=650,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(3, 10))

        preflight_frame = ttk.LabelFrame(body, text="Pre-export Check", padding=9)
        preflight_frame.grid(row=2, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(
            preflight_frame,
            textvariable=self.preflight_var,
            wraplength=650,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        destination_frame = ttk.LabelFrame(body, text="Destination", padding=9)
        destination_frame.grid(row=3, column=0, sticky="ew", pady=(0, 9))
        destination_frame.columnconfigure(0, weight=1)
        ttk.Entry(destination_frame, textvariable=self.destination_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(
            destination_frame,
            text="Browse…",
            command=self._choose_destination,
        ).grid(row=0, column=1, padx=(7, 0))

        output_frame = ttk.LabelFrame(body, text="Outputs", padding=9)
        output_frame.grid(row=4, column=0, sticky="ew", pady=(0, 9))
        output_frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            output_frame,
            text="Copy images",
            variable=self.copy_images_var,
            command=self._refresh_sample,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            output_frame,
            text="Create same-name .txt sidecars",
            variable=self.sidecars_var,
            command=self._refresh_sample,
        ).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            output_frame,
            text="Create manifest.csv",
            variable=self.manifest_var,
            command=self._refresh_sample,
        ).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(
            output_frame,
            text="Create training-handoff README.txt",
            variable=self.readme_var,
            command=self._refresh_sample,
        ).grid(row=3, column=0, sticky="w")
        ttk.Label(output_frame, text="Filename collisions:").grid(
            row=4, column=0, sticky="w", pady=(7, 0)
        )
        collision_combo = ttk.Combobox(
            output_frame,
            textvariable=self.collision_var,
            values=tuple(COLLISION_LABEL_TO_KEY),
            state="readonly",
            width=18,
        )
        collision_combo.grid(row=4, column=1, sticky="w", pady=(7, 0))

        profile_frame = ttk.LabelFrame(body, text="Training Text Profile", padding=9)
        profile_frame.grid(row=5, column=0, sticky="ew", pady=(0, 9))
        profile_frame.columnconfigure(0, weight=1)
        profile_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.profile_var,
            values=PROFILE_LABELS,
            state="readonly",
        )
        profile_combo.grid(row=0, column=0, sticky="ew")
        profile_combo.bind("<<ComboboxSelected>>", self._on_profile_changed)
        ttk.Label(
            profile_frame,
            textvariable=self.profile_description_var,
            wraplength=620,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 7))

        self.custom_frame = ttk.Frame(profile_frame)
        self.custom_frame.grid(row=2, column=0, sticky="ew")
        self.custom_checkbuttons = [
            ttk.Checkbutton(
                self.custom_frame,
                text="Trigger Keyword",
                variable=self.custom_trigger_var,
                command=self._refresh_sample,
            ),
            ttk.Checkbutton(
                self.custom_frame,
                text="Manual tags",
                variable=self.custom_manual_var,
                command=self._refresh_sample,
            ),
            ttk.Checkbutton(
                self.custom_frame,
                text="Active AI tags",
                variable=self.custom_ai_var,
                command=self._refresh_sample,
            ),
            ttk.Checkbutton(
                self.custom_frame,
                text="Raw provider caption",
                variable=self.custom_caption_var,
                command=self._refresh_sample,
            ),
        ]
        for index, checkbox in enumerate(self.custom_checkbuttons):
            checkbox.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 16))

        preview_frame = ttk.LabelFrame(body, text="Live Training Preview", padding=9)
        preview_frame.grid(row=6, column=0, sticky="ew", pady=(0, 9))
        preview_frame.columnconfigure(0, weight=1)
        ttk.Label(
            preview_frame,
            textvariable=self.sample_var,
            wraplength=620,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        note = ttk.Label(
            body,
            text=(
                "Preview resolves collision-safe filenames before writing. Export runs "
                "in the background, can be cancelled, and writes an error report for "
                "item-level failures."
            ),
            wraplength=650,
            justify="left",
        )
        note.grid(row=7, column=0, sticky="nw")

        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=0)
        ttk.Button(buttons, text="Preview", command=self._preview).grid(
            row=0, column=1, padx=(7, 0)
        )
        ttk.Button(buttons, text="Export", command=self._export).grid(
            row=0, column=2, padx=(7, 0)
        )

        self.bind("<Escape>", lambda _event: self.destroy())

    def _choose_destination(self) -> None:
        current = Path(self.destination_var.get()).expanduser()
        initial = current if current.exists() else current.parent
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose export destination",
            initialdir=str(initial),
        )
        if selected:
            self.destination_var.set(selected)

    def _on_profile_changed(self, _event: tk.Event | None = None) -> None:
        profile = self._current_profile()
        self.profile_description_var.set(profile.description)
        custom = self.profile_var.get() == "Custom"
        for checkbox in self.custom_checkbuttons:
            checkbox.configure(state="normal" if custom else "disabled")
        self._refresh_sample()

    def _refresh_sample(self) -> None:
        if not self.records:
            self.sample_var.set("No selected images.")
            return
        profile = self._current_profile()
        sample = self.records[0]
        text = build_training_text(sample.layers, profile)
        prefix = (
            f"Example from {sample.filename}:\n"
            if len(self.records) > 1
            else "Exact sidecar text:\n"
        )
        self.sample_var.set(prefix + (text or "(empty)"))
        self.preflight_var.set("\n".join(self._preflight_lines()))

    def _current_profile(self) -> TrainingTextProfile:
        if self.profile_var.get() == "Custom":
            return custom_training_profile(
                include_trigger=self.custom_trigger_var.get(),
                include_manual_tags=self.custom_manual_var.get(),
                include_ai_tags=self.custom_ai_var.get(),
                include_raw_caption=self.custom_caption_var.get(),
            )
        key = PROFILE_LABEL_TO_KEY.get(self.profile_var.get(), "flux_lora")
        return get_training_profile(key)

    def _current_options(self) -> ExportOptions:
        destination_text = self.destination_var.get().strip()
        if not destination_text:
            raise ValueError("Choose an export destination folder.")
        return ExportOptions(
            destination=Path(destination_text),
            profile=self._current_profile(),
            copy_images=self.copy_images_var.get(),
            create_sidecars=self.sidecars_var.get(),
            create_manifest=self.manifest_var.get(),
            create_readme=self.readme_var.get(),
            collision_policy=COLLISION_LABEL_TO_KEY.get(
                self.collision_var.get(),
                COLLISION_RENAME,
            ),
            handoff_scope=self.scope_label,
            handoff_notes=tuple(self._preflight_lines()),
        ).validated()

    def _build_plan(self) -> ExportPlan | None:
        try:
            return build_export_plan(self.records, self._current_options())
        except Exception as error:
            messagebox.showerror(
                "Cannot prepare export",
                f"{type(error).__name__}: {error}",
                parent=self,
            )
            return None

    def _preview(self) -> None:
        plan = self._build_plan()
        if plan is None:
            return
        ExportPlanPreviewDialog(self, plan)

    def _export(self) -> None:
        plan = self._build_plan()
        if plan is None:
            return
        warnings = self._preflight_warnings()
        if warnings and not messagebox.askyesno(
            "Export with unresolved checks?",
            (
                "LoRA Image Curator found items worth reviewing before training:\n\n"
                + "\n".join(f"• {warning}" for warning in warnings)
                + "\n\nContinue with this non-destructive export?"
            ),
            parent=self,
        ):
            return
        self._save_preferences(plan.options)
        progress = ExportProgressDialog(
            self,
            plan=plan,
            repository=self.repository,
        )
        self.wait_window(progress)
        if progress.result is not None:
            # Keep the configuration dialog open after cancellation/partial
            # failure so the user can adjust the destination or policy. Close
            # automatically only after a clean successful export.
            if progress.result.status == "complete":
                self.destroy()
            elif self.winfo_exists():
                self.grab_set()

    def _save_preferences(self, options: ExportOptions) -> None:
        self.settings.export_last_directory = str(options.destination)
        self.settings.export_profile_key = options.profile.key
        self.settings.export_copy_images = options.copy_images
        self.settings.export_create_sidecars = options.create_sidecars
        self.settings.export_create_manifest = options.create_manifest
        self.settings.export_create_readme = options.create_readme
        self.settings.export_collision_policy = options.collision_policy
        self.settings.export_custom_include_trigger = self.custom_trigger_var.get()
        self.settings.export_custom_include_manual_tags = self.custom_manual_var.get()
        self.settings.export_custom_include_ai_tags = self.custom_ai_var.get()
        self.settings.export_custom_include_raw_caption = self.custom_caption_var.get()
        try:
            save_settings(self.settings)
        except OSError:
            pass
        if self.on_settings_saved is not None:
            self.on_settings_saved()

    @staticmethod
    def _label_for_profile_key(key: str) -> str:
        if key == "custom":
            return "Custom"
        profile = get_training_profile(key)
        return profile.label

    def _preflight_lines(self) -> list[str]:
        """Summarize the current scope without blocking deliberate export."""
        lines = [f"Scope: {self.scope_label}"]
        if self.readiness_report is not None:
            report = self.readiness_report
            lines.append(
                f"Readiness ({report.profile.label}): "
                f"{report.score}% — {report.status}; "
                f"{report.eligible_images:,} eligible of {report.total_images:,} total."
            )
            issue_text = self._readiness_issue_summary()
            lines.append(
                f"Unresolved checks: {issue_text}"
                if issue_text
                else "Unresolved checks: none detected."
            )

        if self.sidecars_var.get():
            profile = self._current_profile()
            empty_count = sum(
                not build_training_text(record.layers, profile)
                for record in self.records
            )
            repeated_count = self._repeated_training_text_count(profile)
            lines.append(
                f"Training text: {empty_count:,} empty sidecar"
                f"{'s' if empty_count != 1 else ''} with {profile.label}."
                if empty_count
                else f"Training text: all sidecars contain text with {profile.label}."
            )
            lines.append(
                f"Repeated training text: {repeated_count:,} image"
                f"{'s' if repeated_count != 1 else ''} share exact sidecar text "
                f"with {profile.label}."
                if repeated_count
                else f"Repeated training text: none with {profile.label}."
            )
        else:
            lines.append("Training text: TXT sidecars are disabled.")
        return lines

    def _preflight_warnings(self) -> list[str]:
        """Return concise warnings that require one explicit Continue choice."""
        warnings: list[str] = []
        if self.readiness_report is not None:
            for issue in self.readiness_report.issues:
                if issue.count and issue.label in {
                    "Missing Files",
                    "Missing Trigger Keyword",
                    "Unreviewed",
                    "Low Resolution",
                    "Quality Not Analyzed",
                    "Blur",
                    "Prominent Overlay",
                    "Possible Duplicates",
                }:
                    warnings.append(
                        f"{issue.count:,} image"
                        f"{'s' if issue.count != 1 else ''}: {issue.label}"
                    )
        if self.sidecars_var.get():
            profile = self._current_profile()
            empty_count = sum(
                not build_training_text(record.layers, profile)
                for record in self.records
            )
            if empty_count:
                warnings.append(
                    f"{empty_count:,} empty training-text sidecar"
                    f"{'s' if empty_count != 1 else ''}"
                )
            repeated_count = self._repeated_training_text_count(profile)
            if repeated_count:
                warnings.append(
                    f"{repeated_count:,} image"
                    f"{'s' if repeated_count != 1 else ''}: "
                    "Repeated Training Text"
                )
        return warnings

    def _readiness_issue_summary(self) -> str:
        if self.readiness_report is None:
            return ""
        return ", ".join(
            f"{issue.label} {issue.count:,}"
            for issue in self.readiness_report.issues
            if issue.count
            and issue.label not in {"No Training Text", "Repeated Training Text"}
        )

    def _repeated_training_text_count(
        self,
        profile: TrainingTextProfile,
    ) -> int:
        """Use the selected export profile for the dialog's live validation."""
        groups = find_repeated_training_text_groups(
            (
                (int(record.image_id), record.layers)
                for record in self.records
            ),
            profile,
        )
        return sum(len(group) for group in groups)


def _open_folder(path: Path, *, parent: tk.Misc) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True)
        else:
            subprocess.run(["xdg-open", str(path)], check=True)
    except (OSError, subprocess.SubprocessError) as error:
        messagebox.showerror("Could not open folder", str(error), parent=parent)
