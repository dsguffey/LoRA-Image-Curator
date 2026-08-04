"""Visible, responsive feedback for an explicitly approved model download."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from tkinter import messagebox, ttk
from typing import Callable


class ProviderDownloadDialog(tk.Toplevel):
    """Run one approved download outside Tk and report its terminal result.

    Confirmation belongs to the caller so this class never grants network
    authority itself.  It starts only after approval and exists to make slow or
    large provider transfers visibly active while keeping Tk responsive.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        status_text: str,
        download_action: Callable[[], None],
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("660x230")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.download_action = download_action
        self.on_complete = on_complete
        self._messages: queue.Queue[Exception | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self.status_var = tk.StringVar(value=status_text)

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            textvariable=self.status_var,
            wraplength=620,
            justify="left",
        ).pack(anchor="w", fill="x")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(18, 12))
        self.progress.start(12)
        self.close_button = ttk.Button(
            frame,
            text="Close",
            command=self._request_close,
            state="disabled",
        )
        self.close_button.pack(side="right")

        self.after_idle(self._start)
        self.grab_set()

    def _start(self) -> None:
        def worker() -> None:
            try:
                self.download_action()
            except Exception as error:
                logging.exception("Approved provider download failed")
                self._messages.put(error)
            else:
                self._messages.put(None)

        self._worker = threading.Thread(
            target=worker,
            name="provider-model-download",
            daemon=True,
        )
        self._worker.start()
        self._poll_after_id = self.after(75, self._poll)

    def _poll(self) -> None:
        self._poll_after_id = None
        try:
            result = self._messages.get_nowait()
        except queue.Empty:
            if self._worker is not None and self._worker.is_alive():
                self._poll_after_id = self.after(75, self._poll)
            return

        self.progress.stop()
        if isinstance(result, Exception):
            self.status_var.set("Download failed. The previous local file was preserved.")
            self.close_button.configure(state="normal")
            messagebox.showerror(
                "Model download failed",
                f"{type(result).__name__}: {result}",
                parent=self,
            )
            return

        self.status_var.set("Download completed and verification passed.")
        callback = self.on_complete
        self.destroy()
        if callback is not None:
            self.master.after_idle(callback)

    def _request_close(self) -> None:
        """Do not pretend a library-controlled transfer can be cancelled safely."""
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo(
                "Download in progress",
                "Please wait for the current verified download to finish.",
                parent=self,
            )
            return
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        self.destroy()
