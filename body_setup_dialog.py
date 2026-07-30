"""Responsive body-provider compatibility checking for Tk interfaces.

MediaPipe performs native model initialization during a complete compatibility
check. That work can take long enough to make a command appear unresponsive if
it runs before Tk paints any feedback. This dialog acknowledges the command
immediately, performs the read-only check on a worker thread, and applies the
result only on Tk's owning thread.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk

from pathlib import Path
from tkinter import ttk
from typing import Callable

from body_analysis import BodyProviderStatus, inspect_body_setup


class BodySetupDialog(tk.Toplevel):
    """Show progress and a complete local provider/model compatibility result."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        model_path: Path,
        on_complete: Callable[[BodyProviderStatus], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Body Analysis Setup")
        self.geometry("680x390")
        self.minsize(620, 330)
        self.transient(parent.winfo_toplevel())
        self.model_path = model_path.expanduser()
        self.on_complete = on_complete
        self._results: queue.Queue[BodyProviderStatus | Exception] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self.status_var = tk.StringVar(
            value=(
                "Checking the MediaPipe package and initializing the selected "
                "local model…"
            )
        )
        self._build_interface()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        # after_idle lets the window map before native model initialization
        # begins. The initialization itself still runs outside Tk's thread.
        self.after_idle(self._start)
        self.grab_set()

    def _build_interface(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                "Compatibility check only. No images, landmarks, captions, "
                "hashes, or catalog records are uploaded."
            ),
            wraplength=640,
            justify="left",
        ).pack(anchor="w")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(16, 12))
        self.progress.start(12)
        ttk.Label(
            frame,
            textvariable=self.status_var,
            wraplength=640,
            justify="left",
        ).pack(anchor="w", fill="x")
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(16, 0))
        self.close_button = ttk.Button(
            buttons,
            text="Close",
            command=self._close,
            state="disabled",
        )
        self.close_button.pack(side="right")

    def _start(self) -> None:
        if not self.winfo_exists():
            return

        def worker() -> None:
            try:
                result = inspect_body_setup(
                    self.model_path,
                    perform_runtime_check=True,
                )
            except Exception as error:
                logging.exception("Unexpected body setup check failure")
                self._results.put(error)
            else:
                self._results.put(result)

        self._worker = threading.Thread(
            target=worker,
            name="body-setup-check",
            daemon=True,
        )
        self._worker.start()
        self._poll_after_id = self.after(50, self._poll)

    def _poll(self) -> None:
        self._poll_after_id = None
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            if (
                self.winfo_exists()
                and self._worker is not None
                and self._worker.is_alive()
            ):
                self._poll_after_id = self.after(50, self._poll)
            return

        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.close_button.configure(state="normal")
        if isinstance(result, Exception):
            self.status_var.set(
                f"Compatibility check failed:\n\n{type(result).__name__}: {result}"
            )
            return

        notes = "\n".join(f"• {note}" for note in result.notes) or "• No warnings"
        self.status_var.set(
            f"Provider: {result.provider_label}\n"
            f"MediaPipe: {result.package_version}\n"
            f"Model path: {result.model_path}\n"
            f"Model exists: {'yes' if result.model_exists else 'no'}\n"
            f"Vetted model name: "
            f"{'yes' if result.model_filename_vetted else 'no'}\n"
            f"Runtime compatible: {'yes' if result.model_compatible else 'no'}\n\n"
            f"Notes\n{notes}"
        )
        if self.on_complete is not None:
            self.on_complete(result)

    def _close(self) -> None:
        """Close feedback safely; a daemon check may finish without touching Tk."""
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        self.progress.stop()
        self.destroy()
