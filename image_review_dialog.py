"""Large Browser image review with a compact on-image control strip.

The thumbnail exposes one maximize icon; this dialog then has enough visual
space for floating previous/next, zoom, fit, actual-size, and return controls.
It opens in fit-to-window mode, supports panning, and retains an external-viewer
escape hatch. No selection, catalog data, review decision, or file is changed.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tkinter as tk

from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Iterable

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from ui_fonts import get_ui_font
from ui_helpers import Tooltip
from video_origin import format_video_timestamp


MINIMUM_ZOOM = 0.05
MAXIMUM_ZOOM = 8.0


class ImageReviewDialog(tk.Toplevel):
    """Review one current-result sequence in a web-gallery-style viewer."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        records: Iterable[object],
        initial_image_id: int,
        on_delete: Callable[[object], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Enlarged Image Review")
        self.geometry("1120x820")
        self.minsize(720, 520)
        self.transient(parent.winfo_toplevel())

        self.records = tuple(records)
        self._on_delete = on_delete
        self.index = next(
            (
                index
                for index, record in enumerate(self.records)
                if int(getattr(record, "image_id")) == int(initial_image_id)
            ),
            0,
        )
        self._source_image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._zoom = 1.0
        self._fit_to_window = True
        # Configure events and image changes share one delayed redraw slot.
        # Keeping the Tcl callback identifier lets ``destroy()`` cancel it
        # before the widget command disappears; otherwise Tk reports an
        # ``invalid command name ..._redraw`` error after a fast close.
        self._redraw_after_id: str | None = None
        self._destroying = False

        self.canvas = tk.Canvas(
            self,
            background="#161616",
            highlightthickness=0,
            cursor="fleur",
        )
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="center")
        self.message_item = self.canvas.create_text(
            0,
            0,
            text="",
            fill="#F0F0F0",
            font=get_ui_font(self, size=12),
            justify="center",
        )

        # The strip deliberately floats over the large viewer instead of
        # permanently consuming space above every small Browser thumbnail.
        toolbar = ttk.Frame(self.canvas, padding=(7, 5))
        toolbar.place(relx=0.5, rely=1.0, y=-14, anchor="s")
        self.control_bar = toolbar
        self.previous_button = ttk.Button(
            toolbar,
            text="← Previous",
            command=lambda: self._navigate(-1),
        )
        self.previous_button.pack(side="left")
        self.next_button = ttk.Button(
            toolbar,
            text="Next →",
            command=lambda: self._navigate(1),
        )
        self.next_button.pack(side="left", padx=(6, 18))

        ttk.Button(toolbar, text="−", width=3, command=self._zoom_out).pack(
            side="left"
        )
        self.zoom_var = tk.StringVar(value="Fit")
        ttk.Label(
            toolbar,
            textvariable=self.zoom_var,
            width=10,
            anchor="center",
        ).pack(side="left", padx=4)
        ttk.Button(toolbar, text="+", width=3, command=self._zoom_in).pack(
            side="left"
        )
        ttk.Button(toolbar, text="Fit", command=self._fit).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="100%", command=self._actual_size).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            toolbar,
            text="Open Externally",
            command=self._open_externally,
        ).pack(side="left", padx=(10, 0))
        if self._on_delete is not None:
            delete_button = ttk.Button(
                toolbar,
                text="🗑",
                width=3,
                command=self._delete_current,
            )
            delete_button.pack(side="left", padx=(6, 0))
            Tooltip(delete_button, "Send this image to the Recycle Bin")
        return_button = ttk.Button(
            toolbar,
            text="↙",
            width=3,
            command=self.destroy,
        )
        return_button.pack(side="left", padx=(6, 0))
        Tooltip(return_button, "Return to the Browser (Esc)")

        details = ttk.Frame(self, padding=(10, 7))
        details.pack(fill="x")
        self.position_var = tk.StringVar()
        self.filename_var = tk.StringVar()
        self.origin_var = tk.StringVar()
        ttk.Label(
            details,
            textvariable=self.filename_var,
            font=get_ui_font(self, size=10, weight="bold"),
        ).pack(side="left")
        ttk.Label(
            details,
            textvariable=self.origin_var,
            foreground="#5F5F5F",
        ).pack(side="left", padx=(16, 0))
        ttk.Label(details, textvariable=self.position_var).pack(side="right")

        self.canvas.bind("<Configure>", self._schedule_redraw)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda _event: self._zoom_in())
        self.canvas.bind("<Button-5>", lambda _event: self._zoom_out())
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.bind("<Left>", lambda _event: self._navigate(-1))
        self.bind("<Right>", lambda _event: self._navigate(1))
        self.bind("<plus>", lambda _event: self._zoom_in())
        self.bind("<equal>", lambda _event: self._zoom_in())
        self.bind("<minus>", lambda _event: self._zoom_out())
        self.bind("<Key-0>", lambda _event: self._fit())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._load_current()
        self.focus_set()

    @property
    def current_record(self) -> object | None:
        if not self.records:
            return None
        return self.records[self.index]

    def _navigate(self, offset: int) -> None:
        if not self.records:
            return
        new_index = max(0, min(len(self.records) - 1, self.index + offset))
        if new_index == self.index:
            return
        self.index = new_index
        self._load_current()

    def _load_current(self) -> None:
        record = self.current_record
        self._source_image = None
        self._photo = None
        if record is None:
            self._show_message("No images are available in the current Browser results.")
            return

        source_path = getattr(record, "source_path", None)
        path = Path(source_path) if source_path is not None else None
        self.filename_var.set(str(getattr(record, "filename", "Image")))
        self.position_var.set(f"{self.index + 1:,} of {len(self.records):,}")
        source_video = str(getattr(record, "source_video", "") or "")
        timestamp = getattr(record, "video_timestamp_seconds", None)
        self.origin_var.set(
            (
                f"{Path(source_video).name} · {format_video_timestamp(timestamp)}"
                if source_video
                else ""
            )
        )
        self.previous_button.configure(
            state="normal" if self.index > 0 else "disabled"
        )
        self.next_button.configure(
            state="normal" if self.index + 1 < len(self.records) else "disabled"
        )

        if path is None or not path.is_file():
            self._show_message(
                "Image file not found.\n\n"
                f"{path or '(no preferred file location stored)'}"
            )
            return
        try:
            with Image.open(path) as source:
                self._source_image = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, UnidentifiedImageError) as error:
            logging.exception("Could not load enlarged image %s", path)
            self._show_message(f"Image could not be decoded.\n\n{error}")
            return

        self._fit_to_window = True
        self._queue_redraw(idle=True)

    def _show_message(self, message: str) -> None:
        self.canvas.itemconfigure(self.image_item, image="")
        self.canvas.itemconfigure(self.message_item, text=message)
        self.canvas.coords(
            self.message_item,
            self.canvas.winfo_width() / 2,
            self.canvas.winfo_height() / 2,
        )
        self.zoom_var.set("Unavailable")

    def _schedule_redraw(self, _event: tk.Event) -> None:
        self._queue_redraw(idle=False)

    def _queue_redraw(self, *, idle: bool) -> None:
        """Replace the pending redraw while retaining cancellation ownership."""
        if self._destroying:
            return
        self._cancel_pending_redraw()
        if idle:
            self._redraw_after_id = self.after_idle(self._run_scheduled_redraw)
        else:
            self._redraw_after_id = self.after(80, self._run_scheduled_redraw)

    def _cancel_pending_redraw(self) -> None:
        """Remove the one owned Tcl timer without losing its identifier."""
        callback_id = self._redraw_after_id
        self._redraw_after_id = None
        if callback_id is None:
            return
        try:
            self.after_cancel(callback_id)
        except tk.TclError:
            # The interpreter or callback may already be gone during shutdown.
            pass

    def _run_scheduled_redraw(self) -> None:
        """Consume the owned timer before rendering its requested frame."""
        self._redraw_after_id = None
        if self._destroying:
            return
        self._render()

    def _redraw_now(self) -> None:
        """Render synchronously after cancelling any superseded timer.

        Zoom, Fit, and 100% actions used to call the renderer directly while a
        delayed Configure redraw was still queued. The renderer then cleared
        the only stored timer ID without cancelling Tcl's pending ``after``
        script. A subsequent close could no longer remove that script, which
        produced ``invalid command name ..._redraw`` on Windows.
        """
        if self._destroying:
            return
        self._cancel_pending_redraw()
        self._render()

    def _render(self) -> None:
        """Draw the current image without changing callback ownership."""
        image = self._source_image
        if image is None:
            self._center_items()
            return
        available_width = max(1, self.canvas.winfo_width() - 24)
        available_height = max(1, self.canvas.winfo_height() - 24)
        if self._fit_to_window:
            self._zoom = min(
                available_width / image.width,
                available_height / image.height,
                1.0,
            )
        width = max(1, round(image.width * self._zoom))
        height = max(1, round(image.height * self._zoom))
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.itemconfigure(self.image_item, image=self._photo)
        self.canvas.itemconfigure(self.message_item, text="")
        content_width = max(width + 24, self.canvas.winfo_width())
        content_height = max(height + 24, self.canvas.winfo_height())
        self.canvas.configure(
            scrollregion=(0, 0, content_width, content_height)
        )
        self.canvas.coords(
            self.image_item,
            content_width / 2,
            content_height / 2,
        )
        if self._fit_to_window:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self.zoom_var.set(
            "Fit" if self._fit_to_window else f"{self._zoom * 100:.0f}%"
        )

    def _center_items(self) -> None:
        self.canvas.coords(
            self.message_item,
            self.canvas.winfo_width() / 2,
            self.canvas.winfo_height() / 2,
        )

    def _zoom_in(self) -> str:
        self._fit_to_window = False
        self._zoom = min(MAXIMUM_ZOOM, self._zoom * 1.25)
        self._redraw_now()
        return "break"

    def _zoom_out(self) -> str:
        self._fit_to_window = False
        self._zoom = max(MINIMUM_ZOOM, self._zoom / 1.25)
        self._redraw_now()
        return "break"

    def _fit(self) -> str:
        self._fit_to_window = True
        self._redraw_now()
        return "break"

    def _actual_size(self) -> str:
        """Show one source pixel per screen pixel and enable panning."""
        self._fit_to_window = False
        self._zoom = 1.0
        self._redraw_now()
        return "break"

    def _on_mousewheel(self, event: tk.Event) -> str:
        return self._zoom_in() if event.delta > 0 else self._zoom_out()

    def _pan_start(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event: tk.Event) -> None:
        if not self._fit_to_window:
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _open_externally(self) -> None:
        record = self.current_record
        source_path = getattr(record, "source_path", None) if record is not None else None
        path = Path(source_path) if source_path is not None else None
        if path is None or not path.is_file():
            messagebox.showerror(
                "Image file not found",
                str(path or "(no preferred file location stored)"),
                parent=self,
            )
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=True)
            else:
                subprocess.run(["xdg-open", str(path)], check=True)
        except (OSError, subprocess.SubprocessError) as error:
            messagebox.showerror("Could not open image", str(error), parent=self)

    def _delete_current(self) -> None:
        """Delegate one-image deletion to the Browser's normal action path."""
        record = self.current_record
        if record is None or self._on_delete is None:
            return
        self._on_delete(record)
        self.destroy()

    def destroy(self) -> None:
        """Cancel Tcl callbacks before removing the viewer widget command."""
        if self._destroying:
            return
        self._destroying = True
        self._cancel_pending_redraw()
        self._photo = None
        self._source_image = None
        super().destroy()
