"""Tkinter workflow for explicit folder-to-catalog imports.

The dialog owns only transient form/progress state.  Durable work is delegated
to :mod:`catalog_import`, whose staging strategy guarantees that closing or
cancelling the dialog cannot publish half of an import.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from catalog_import import (
    CatalogImportCancelled,
    CatalogImportOptions,
    CatalogImportSummary,
    ImportMode,
    default_image_set_name,
    format_import_summary,
    import_catalog_folder,
)
from settings_manager import get_default_body_model_path, load_settings, save_settings


class CatalogImportDialog(tk.Toplevel):
    """Collect import choices and run the staged import without freezing Tk."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        mode: ImportMode,
        target_database: Path | None = None,
        initial_source_folder: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.mode = mode
        self.fixed_target = (
            target_database.expanduser().resolve()
            if target_database is not None
            else None
        )
        self.result: CatalogImportSummary | None = None

        self.title(self._title_for_mode())
        self.geometry("760x540")
        self.minsize(680, 500)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._request_close)

        source_text = str(initial_source_folder or "")
        self.source_var = tk.StringVar(value=source_text)
        self.target_var = tk.StringVar(value=str(self.fixed_target or ""))
        settings = load_settings()
        self.recursive_var = tk.BooleanVar(
            value=settings.catalog_import_include_subfolders
        )
        self.create_set_var = tk.BooleanVar(value=True)
        self.set_name_var = tk.StringVar(
            value=(
                default_image_set_name(initial_source_folder)
                if initial_source_folder is not None
                else ""
            )
        )
        self.status_var = tk.StringVar(value="Choose the import options, then click Start.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.skip_without_body_var = tk.BooleanVar(value=False)
        self.skip_without_face_var = tk.BooleanVar(value=False)
        self.body_model_path = (
            settings.body_model_path
            or str(get_default_body_model_path())
        )
        self.body_detection_threshold = settings.body_detection_threshold
        self.body_landmark_visibility_threshold = (
            settings.body_landmark_visibility_threshold
        )
        self.body_full_body_threshold_percent = (
            settings.body_full_body_threshold_percent
        )

        self._automatic_set_name = self.set_name_var.get()
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._close_after_cancel = False

        self._build_interface()
        self._set_set_name_state()
        self.grab_set()
        self.source_entry.focus_set()

    def _title_for_mode(self) -> str:
        return {
            "create": "Create Catalog from Images",
            "merge": "Add Images to Catalog — Merge",
            "replace": "Add Images to Catalog — Replace Contents",
        }[self.mode]

    def _build_interface(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        explanation = {
            "create": (
                "Create a new catalog and register the supported images in a folder. "
                "AI caption and face analysis can be run later."
            ),
            "merge": (
                "Add the supported images in this folder to the selected catalog. "
                "Existing catalog content "
                "is preserved, and exact SHA-256 copies remain one catalog image."
            ),
            "replace": (
                "Replace the selected catalog's owned contents with this folder. "
                "The original is left untouched unless the complete staged import succeeds."
            ),
        }[self.mode]
        ttk.Label(container, text=explanation, wraplength=710, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )

        ttk.Label(container, text="Image folder:").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.source_entry = ttk.Entry(container, textvariable=self.source_var)
        self.source_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.source_button = ttk.Button(
            container,
            text="Browse…",
            command=self._choose_source_folder,
        )
        self.source_button.grid(row=1, column=2, padx=(10, 0), pady=5)

        ttk.Label(container, text="SQLite catalog:").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.target_entry = ttk.Entry(
            container,
            textvariable=self.target_var,
            state="readonly" if self.fixed_target is not None else "normal",
        )
        self.target_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.target_button = ttk.Button(
            container,
            text="Browse…",
            command=self._choose_target_database,
            state="disabled" if self.fixed_target is not None else "normal",
        )
        self.target_button.grid(row=2, column=2, padx=(10, 0), pady=5)

        self.recursive_check = ttk.Checkbutton(
            container,
            text="Include images in subfolders",
            variable=self.recursive_var,
        )
        self.recursive_check.grid(row=3, column=1, sticky="w", pady=(10, 4))

        self.create_set_check = ttk.Checkbutton(
            container,
            text="Create an image set from the imported images",
            variable=self.create_set_var,
            command=self._set_set_name_state,
        )
        self.create_set_check.grid(row=4, column=1, sticky="w", pady=4)

        ttk.Label(container, text="Image set name:").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.set_name_entry = ttk.Entry(container, textvariable=self.set_name_var)
        self.set_name_entry.grid(row=5, column=1, sticky="ew", pady=5)

        import_filter = ttk.LabelFrame(
            container,
            text="Optional import filtering — local MediaPipe analysis",
            padding=8,
        )
        import_filter.grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(12, 0),
        )
        self.skip_without_body_check = ttk.Checkbutton(
            import_filter,
            text="Do not catalog images with no detected body / pose",
            variable=self.skip_without_body_var,
        )
        self.skip_without_body_check.pack(anchor="w")
        self.skip_without_face_check = ttk.Checkbutton(
            import_filter,
            text="Do not catalog images with no visible-face pose evidence",
            variable=self.skip_without_face_var,
        )
        self.skip_without_face_check.pack(anchor="w", pady=(3, 0))
        ttk.Label(
            import_filter,
            text=(
                "Filtering is opt-in and slower. It uses the model and thresholds "
                "configured in Settings. “Face” here means visible MediaPipe head/"
                "face landmarks, not identity recognition."
            ),
            wraplength=690,
            foreground="#5F5F5F",
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        ttk.Separator(container).grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(14, 10)
        )
        self.progress = ttk.Progressbar(
            container,
            variable=self.progress_var,
            maximum=100.0,
        )
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew")
        ttk.Label(
            container,
            textvariable=self.status_var,
            wraplength=710,
            justify="left",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(container)
        buttons.grid(row=10, column=0, columnspan=3, sticky="e", pady=(18, 0))
        self.start_button = ttk.Button(buttons, text="Start", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(
            buttons,
            text="Cancel",
            command=self._request_close,
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

    def _choose_source_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the folder containing images",
            initialdir=self.source_var.get().strip() or None,
        )
        if not selected:
            return
        previous_automatic_name = self._automatic_set_name
        self.source_var.set(selected)
        self._automatic_set_name = default_image_set_name(Path(selected))
        if not self.set_name_var.get().strip() or self.set_name_var.get() == previous_automatic_name:
            self.set_name_var.set(self._automatic_set_name)

    def _choose_target_database(self) -> None:
        initial_directory = None
        current = self.target_var.get().strip()
        if current:
            initial_directory = str(Path(current).expanduser().parent)
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Create the LoRA Image Curator catalog",
            initialdir=initial_directory,
            initialfile="dataset_tools.db",
            defaultextension=".db",
            filetypes=(("LoRA Image Curator catalog", "*.db"), ("All files", "*.*")),
            confirmoverwrite=False,
        )
        if selected:
            self.target_var.set(selected)

    def _set_set_name_state(self) -> None:
        state = "normal" if self.create_set_var.get() else "disabled"
        if hasattr(self, "set_name_entry"):
            self.set_name_entry.configure(state=state)

    def _start(self) -> None:
        try:
            source = Path(self.source_var.get().strip()).expanduser().resolve()
            target = Path(self.target_var.get().strip()).expanduser().resolve()
        except (OSError, ValueError) as error:
            messagebox.showerror("Invalid path", str(error), parent=self)
            return

        if not self.source_var.get().strip() or not source.is_dir():
            messagebox.showerror(
                "Choose an image folder",
                "Choose an existing folder containing the images to catalog.",
                parent=self,
            )
            return
        if not self.target_var.get().strip():
            messagebox.showerror(
                "Choose a catalog file",
                "Choose where the new dataset_tools.db catalog should be saved.",
                parent=self,
            )
            return
        overwrite_existing = False
        if self.mode == "create" and target.exists():
            overwrite_existing = messagebox.askyesno(
                "Overwrite existing catalog?",
                (
                    f"A catalog already exists at:\n\n{target}\n\n"
                    "Continuing will permanently replace that catalog database "
                    "and its catalog-owned metadata. Source images and prior "
                    "exports will not be deleted.\n\nOverwrite the existing catalog?"
                ),
                parent=self,
            )
            if not overwrite_existing:
                return
        if self.create_set_var.get() and not self.set_name_var.get().strip():
            self.set_name_var.set(default_image_set_name(source))

        options = CatalogImportOptions(
            source_folder=source,
            target_database=target,
            mode=self.mode,
            recursive=self.recursive_var.get(),
            create_image_set=self.create_set_var.get(),
            image_set_name=self.set_name_var.get().strip(),
            overwrite_existing=overwrite_existing,
            skip_without_body=self.skip_without_body_var.get(),
            skip_without_face=self.skip_without_face_var.get(),
            body_model_path=self.body_model_path,
            body_detection_threshold=self.body_detection_threshold,
            body_landmark_visibility_threshold=(
                self.body_landmark_visibility_threshold
            ),
            body_full_body_threshold_percent=(
                self.body_full_body_threshold_percent
            ),
        )
        settings = load_settings()
        settings.catalog_import_include_subfolders = self.recursive_var.get()
        try:
            save_settings(settings)
        except OSError:
            logging.exception("Could not save the catalog-import subfolder default")
        self._cancel_event.clear()
        self._set_running(True)
        self.status_var.set("Discovering supported image files…")
        self.progress_var.set(0.0)
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(options,),
            name="catalog-folder-import",
            daemon=True,
        )
        self._worker.start()
        self._poll_after_id = self.after(75, self._poll_messages)

    def _run_worker(self, options: CatalogImportOptions) -> None:
        def report_progress(completed: int, total: int, path: Path) -> None:
            self._messages.put(("progress", (completed, total, path)))

        try:
            summary = import_catalog_folder(
                options,
                progress_callback=report_progress,
                cancel_event=self._cancel_event,
            )
        except CatalogImportCancelled as error:
            self._messages.put(("cancelled", error))
        except Exception as error:
            logging.exception("Catalog folder import failed")
            self._messages.put(("error", error))
        else:
            self._messages.put(("complete", summary))

    def _poll_messages(self) -> None:
        self._poll_after_id = None
        terminal = False
        while True:
            try:
                kind, payload = self._messages.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                completed, total, path = payload  # type: ignore[misc]
                self.progress_var.set((completed / total * 100.0) if total else 100.0)
                self.status_var.set(
                    f"Cataloging {completed + 1:,} of {total:,}: {Path(path).name}"
                )
            elif kind == "complete":
                terminal = True
                self.result = payload  # type: ignore[assignment]
                self.progress_var.set(100.0)
                self._set_running(False)
                self.destroy()
            elif kind == "cancelled":
                terminal = True
                self._set_running(False)
                self.status_var.set("Import cancelled. The original catalog was not changed.")
                if self._close_after_cancel:
                    self.destroy()
            elif kind == "error":
                terminal = True
                self._set_running(False)
                self.status_var.set("Import failed. The original catalog was not changed.")
                error = payload
                messagebox.showerror(
                    "Catalog import failed",
                    f"{type(error).__name__}: {error}",
                    parent=self,
                )

        if not terminal and self._worker is not None and self._worker.is_alive():
            self._poll_after_id = self.after(75, self._poll_messages)

    def _set_running(self, running: bool) -> None:
        ordinary_state = "disabled" if running else "normal"
        for widget in (
            self.source_entry,
            self.source_button,
            self.recursive_check,
            self.create_set_check,
            self.skip_without_body_check,
            self.skip_without_face_check,
            self.start_button,
        ):
            widget.configure(state=ordinary_state)
        if self.fixed_target is None:
            self.target_entry.configure(state=ordinary_state)
            self.target_button.configure(state=ordinary_state)
        self.cancel_button.configure(text="Stop" if running else "Close")
        if running:
            self.set_name_entry.configure(state="disabled")
        else:
            self._set_set_name_state()

    def _request_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._close_after_cancel = True
            self._cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set(
                "Stopping after the current file. The original catalog will remain unchanged…"
            )
            return
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        self.destroy()


def show_catalog_import_report(
    parent: tk.Misc,
    summary: CatalogImportSummary,
) -> None:
    """Show the complete, selectable import report without truncating hashes."""
    dialog = tk.Toplevel(parent)
    dialog.title("Catalog Import Complete")
    dialog.geometry("780x560")
    dialog.minsize(640, 420)
    dialog.transient(parent)

    container = ttk.Frame(dialog, padding=12)
    container.pack(fill="both", expand=True)
    container.columnconfigure(0, weight=1)
    container.rowconfigure(1, weight=1)

    ttk.Label(
        container,
        text=f"Catalog ready: {summary.target_database}",
        wraplength=740,
        justify="left",
    ).grid(row=0, column=0, sticky="w", pady=(0, 8))

    report_frame = ttk.Frame(container)
    report_frame.grid(row=1, column=0, sticky="nsew")
    report_frame.columnconfigure(0, weight=1)
    report_frame.rowconfigure(0, weight=1)
    text = tk.Text(report_frame, wrap="word", padx=8, pady=8)
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(report_frame, orient="vertical", command=text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scrollbar.set)
    text.insert("1.0", format_import_summary(summary))
    text.configure(state="disabled")

    ttk.Button(container, text="Close", command=dialog.destroy).grid(
        row=2, column=0, sticky="e", pady=(10, 0)
    )
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.grab_set()
    dialog.wait_window()
