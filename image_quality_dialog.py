"""Read-only explanation popup for stored image-analysis evidence."""

from __future__ import annotations

import tkinter as tk

from tkinter import ttk
from typing import Iterable

from ui_fonts import MONOSPACE_FONT_FAMILY, get_ui_font


class ImageQualityDialog(tk.Toplevel):
    """Present dense filter evidence without crowding Browser Image Details."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        filename: str,
        fields: Iterable[tuple[str, str]],
    ) -> None:
        super().__init__(parent)
        self.title(f"Image Quality — {filename}")
        self.geometry("650x650")
        self.minsize(520, 430)
        self.transient(parent.winfo_toplevel())

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(
            body,
            text="Image Quality and Detection Evidence",
            font=get_ui_font(self, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=(
                "Read-only explanation of the measurements used by Browser "
                "filters. These values are evidence, not automatic deletion decisions."
            ),
            wraplength=600,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 10))

        text_frame = ttk.Frame(body)
        text_frame.grid(row=2, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        text = tk.Text(
            text_frame,
            wrap="word",
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=8,
            font=get_ui_font(self, size=10),
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=text.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.tag_configure(
            "heading",
            font=get_ui_font(self, size=10, weight="bold"),
            spacing1=8,
        )
        text.tag_configure(
            "technical",
            font=get_ui_font(self, size=9, family=MONOSPACE_FONT_FAMILY),
            foreground="#5F5F5F",
        )
        for label, value in fields:
            text.insert("end", f"{label}\n", "heading")
            text.insert("end", f"{value or '—'}\n")
        text.configure(state="disabled")

        ttk.Button(body, text="Close", command=self.destroy).grid(
            row=3, column=0, sticky="e", pady=(12, 0)
        )
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
