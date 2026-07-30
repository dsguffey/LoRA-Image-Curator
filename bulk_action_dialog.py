"""Responsive modal progress for long-running destructive catalog actions.

The dialog is modal in the UI sense: while an action is active, the rest of the
application is deliberately unavailable.  The work itself runs on a daemon
thread and communicates through a queue, so Tk's main event loop remains free
to repaint the progress bar and accept a cooperative cancellation request.

Worker functions must never access Tk objects.  They receive a plain progress
callback plus a ``threading.Event`` and return an ordinary Python result.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Generic, TypeVar

from ui_fonts import get_ui_font


ResultT = TypeVar("ResultT")
ProgressReporter = Callable[[int, int, str], None]
BulkWorker = Callable[[ProgressReporter, threading.Event], ResultT]


@dataclass(slots=True, frozen=True)
class BulkProgress:
    completed: int
    total: int
    detail: str


class BulkActionDialog(tk.Toplevel, Generic[ResultT]):
    """Run one worker with modal, determinate progress and safe cancellation."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        heading: str,
        total: int,
        worker: BulkWorker[ResultT],
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("570x220")
        self.minsize(500, 205)
        self.resizable(True, False)
        self.transient(parent.winfo_toplevel())

        self.result: ResultT | None = None
        self.error: BaseException | None = None
        self._worker_function = worker
        self._cancel_event = threading.Event()
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._poll_after_id: str | None = None
        self._finished = False

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text=heading,
            font=get_ui_font(self, size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.count_var = tk.StringVar(value=f"0 of {max(0, total):,}")
        ttk.Label(body, textvariable=self.count_var).grid(
            row=0, column=1, sticky="e", padx=(12, 0)
        )

        self.progress = ttk.Progressbar(
            body,
            mode="determinate",
            maximum=max(1, total),
            value=0,
        )
        self.progress.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(14, 8),
        )
        self.detail_var = tk.StringVar(value="Preparing operation…")
        ttk.Label(
            body,
            textvariable=self.detail_var,
            wraplength=520,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        self.notice_var = tk.StringVar(
            value="The rest of the application is locked until this action finishes."
        )
        ttk.Label(
            body,
            textvariable=self.notice_var,
            foreground="#5F5F5F",
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.cancel_button = ttk.Button(
            body,
            text="Cancel",
            command=self._request_cancel,
        )
        self.cancel_button.grid(row=3, column=1, sticky="e", padx=(10, 0), pady=(12, 0))

        self.protocol("WM_DELETE_WINDOW", self._request_cancel)
        self.bind("<Escape>", lambda _event: self._request_cancel())
        self.grab_set()
        self.update_idletasks()
        self._center_over_parent()
        self.after_idle(self._start_worker)

    def _center_over_parent(self) -> None:
        try:
            parent = self.master.winfo_toplevel()
            x = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - self.winfo_width()) // 2
            )
            y = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - self.winfo_height()) // 2
            )
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            return

    def _start_worker(self) -> None:
        def report(completed: int, total: int, detail: str = "") -> None:
            self._queue.put(
                (
                    "progress",
                    BulkProgress(
                        max(0, int(completed)),
                        max(0, int(total)),
                        str(detail),
                    ),
                )
            )

        def run() -> None:
            try:
                result = self._worker_function(report, self._cancel_event)
            except BaseException as error:
                self._queue.put(("error", error))
            else:
                self._queue.put(("done", result))

        threading.Thread(
            target=run,
            name="catalog-bulk-action",
            daemon=True,
        ).start()
        self._poll_queue()

    def _request_cancel(self) -> None:
        if self._finished or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.notice_var.set(
            "Cancelling after the current file or database batch finishes…"
        )

    def _poll_queue(self) -> None:
        self._poll_after_id = None
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self._render_progress(payload)  # type: ignore[arg-type]
            elif kind == "error":
                self.error = payload  # type: ignore[assignment]
                self._finish()
                return
            elif kind == "done":
                self.result = payload  # type: ignore[assignment]
                self._finish()
                return
        self._poll_after_id = self.after(75, self._poll_queue)

    def _render_progress(self, progress: BulkProgress) -> None:
        maximum = max(1, progress.total)
        completed = min(maximum, progress.completed)
        self.progress.configure(maximum=maximum, value=completed)
        self.count_var.set(f"{progress.completed:,} of {progress.total:,}")
        if progress.detail:
            self.detail_var.set(progress.detail)

    def _finish(self) -> None:
        self._finished = True
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
