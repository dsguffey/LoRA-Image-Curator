"""Tk progress dialog for running local body analysis on a catalog scope."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Iterable

from body_analysis import BodyAnalysisOptions, inspect_body_setup
from body_analysis_runner import (
    BodyAnalysisCancelled,
    BodyAnalysisSummary,
    analyze_catalog_bodies,
)


class BodyAnalysisDialog(tk.Toplevel):
    """Run optional body analysis without freezing the main Tk event loop."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        database_path: Path,
        model_path: Path,
        options: BodyAnalysisOptions,
        image_ids: Iterable[int] | None = None,
        on_complete: Callable[[BodyAnalysisSummary], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Run Body Analysis")
        self.geometry("650x270")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.database_path = database_path
        self.model_path = model_path
        self.options = options.normalized()
        self.image_ids = tuple(image_ids) if image_ids is not None else None
        self.on_complete = on_complete
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._close_after_cancel = False
        self.status_var = tk.StringVar(value="Checking provider and model…")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_interface()
        self.after_idle(self._start)
        self.grab_set()

    def _build_interface(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                "MediaPipe analyzes local image files with the selected local "
                "model. Images, landmarks, and catalog data are not uploaded."
            ),
            wraplength=610,
            justify="left",
        ).pack(anchor="w")
        ttk.Progressbar(
            frame,
            variable=self.progress_var,
            maximum=100.0,
        ).pack(fill="x", pady=(18, 8))
        ttk.Label(
            frame,
            textvariable=self.status_var,
            wraplength=610,
            justify="left",
        ).pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        self.cancel_button = ttk.Button(
            buttons,
            text="Stop",
            command=self._request_close,
        )
        self.cancel_button.pack(side="right")
        self.pause_button = ttk.Button(
            buttons,
            text="Pause",
            command=self._toggle_pause,
        )
        self.pause_button.pack(side="right", padx=(0, 8))

    def _start(self) -> None:
        self._worker = threading.Thread(
            target=self._run_worker,
            name="body-analysis",
            daemon=True,
        )
        self._worker.start()
        self._poll_after_id = self.after(75, self._poll)

    def _run_worker(self) -> None:
        def progress(completed: int, total: int, path: Path) -> None:
            self._messages.put(("progress", (completed, total, path)))

        try:
            status = inspect_body_setup(
                self.model_path,
                perform_runtime_check=True,
            )
            if not status.ready:
                self._messages.put(
                    (
                        "setup_error",
                        "\n".join(status.notes)
                        or "Provider/model compatibility failed.",
                    )
                )
                return
            summary = analyze_catalog_bodies(
                self.database_path,
                self.model_path,
                self.options,
                image_ids=self.image_ids,
                progress_callback=progress,
                cancel_event=self._cancel,
                pause_event=self._pause,
            )
        except BodyAnalysisCancelled as error:
            self._messages.put(("cancelled", error))
        except Exception as error:
            logging.exception("Body analysis failed")
            self._messages.put(("error", error))
        else:
            self._messages.put(("complete", summary))

    def _poll(self) -> None:
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
                    f"Analyzing {min(completed + 1, total):,} of {total:,}: "
                    f"{Path(path).name}"
                )
            elif kind == "complete":
                terminal = True
                self._pause.clear()
                summary = payload  # type: ignore[assignment]
                self.progress_var.set(100.0)
                if self.on_complete is not None:
                    self.on_complete(summary)
                messagebox.showinfo(
                    "Body analysis complete",
                    (
                        f"Requested: {summary.requested_images:,}\n"
                        f"Analyzed: {summary.analyzed_images:,}\n"
                        f"Reused: {summary.reused_images:,}\n"
                        f"Failed: {summary.failed_images:,}\n"
                        f"Bodies detected: {summary.bodies_detected:,}\n"
                        f"Full-body matches: {summary.full_body_images:,}"
                    ),
                    parent=self,
                )
                self.destroy()
            elif kind == "cancelled":
                terminal = True
                self._pause.clear()
                self.status_var.set("Body analysis stopped between images.")
                self.cancel_button.configure(text="Close", state="normal")
                self.pause_button.configure(state="disabled")
                if self._close_after_cancel:
                    self.destroy()
            elif kind == "error":
                terminal = True
                self._pause.clear()
                error = payload
                messagebox.showerror(
                    "Body analysis failed",
                    f"{type(error).__name__}: {error}",
                    parent=self,
                )
                self.cancel_button.configure(text="Close", state="normal")
                self.pause_button.configure(state="disabled")
            elif kind == "setup_error":
                terminal = True
                messagebox.showerror(
                    "Body analysis is not ready",
                    str(payload),
                    parent=self,
                )
                self.destroy()

        if not terminal and self._worker is not None and self._worker.is_alive():
            self._poll_after_id = self.after(75, self._poll)

    def _request_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._close_after_cancel = True
            self._cancel.set()
            self._pause.clear()
            self.cancel_button.configure(state="disabled")
            self.pause_button.configure(state="disabled")
            self.status_var.set("Stopping after the current image…")
            return
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        self.destroy()

    def _toggle_pause(self) -> None:
        """Pause or resume at an image boundary without unloading the model."""
        if self._worker is None or not self._worker.is_alive():
            return
        if self._pause.is_set():
            self._pause.clear()
            self.pause_button.configure(text="Pause")
            self.status_var.set("Resuming body analysis…")
        else:
            self._pause.set()
            self.pause_button.configure(text="Resume")
            self.status_var.set(
                "Pause requested; finishing the current image safely…"
            )
