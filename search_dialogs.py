"""Small dialogs supporting advanced search, history privacy, and saved views."""

from __future__ import annotations

import tkinter as tk

from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Iterable

from advanced_search import SearchClause, build_search_query


FIELD_LABELS = (
    "Any field",
    "Any tag",
    "Manual tag",
    "Active AI tag",
    "Excluded AI tag",
    "Trigger Keyword",
    "Image set",
    "Review state",
    "Identity",
    "File availability",
    "Caption",
    "Resolution",
    "Image quality",
    "Blur score",
    "Duplicate match",
)

FIELD_KEYS = {
    "Any field": "all",
    "Any tag": "tag",
    "Manual tag": "manual",
    "Active AI tag": "ai",
    "Excluded AI tag": "excluded",
    "Trigger Keyword": "trigger",
    "Image set": "set",
    "Review state": "review",
    "Identity": "identity",
    "File availability": "file",
    "Caption": "caption",
    "Resolution": "resolution",
    "Image quality": "quality",
    "Blur score": "blur",
    "Duplicate match": "duplicate",
}


class AdvancedSearchDialog(tk.Toplevel):
    """Build ordinary query text from understandable Include/Exclude rows."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Advanced Search")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result_query: str | None = None
        self.match_var = tk.StringVar(value="All conditions (AND)")
        self.rows: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = []

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "Build a search without memorizing syntax. The resulting query remains "
                "visible and editable in the Catalog Browser."
            ),
            wraplength=650,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(body, text="Condition").grid(row=1, column=0, sticky="w")
        ttk.Label(body, text="Field").grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Label(body, text="Value").grid(row=1, column=2, sticky="w", padx=(8, 0))
        for index in range(5):
            condition = tk.StringVar(value="Include")
            field = tk.StringVar(value="Any field")
            value = tk.StringVar()
            ttk.Combobox(
                body,
                textvariable=condition,
                values=("Include", "Exclude"),
                state="readonly",
                width=10,
            ).grid(row=index + 2, column=0, sticky="ew", pady=3)
            ttk.Combobox(
                body,
                textvariable=field,
                values=FIELD_LABELS,
                state="readonly",
                width=20,
            ).grid(row=index + 2, column=1, sticky="ew", padx=(8, 0), pady=3)
            entry = ttk.Entry(body, textvariable=value, width=38)
            entry.grid(row=index + 2, column=2, sticky="ew", padx=(8, 0), pady=3)
            if index == 0:
                entry.focus_set()
            self.rows.append((condition, field, value))

        options = ttk.Frame(body)
        options.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(options, text="Match:").pack(side="left")
        ttk.Combobox(
            options,
            textvariable=self.match_var,
            values=("All conditions (AND)", "Any condition (OR)"),
            state="readonly",
            width=23,
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            body,
            text=(
                "Useful values: review = keep/reject/unreviewed; file = present/missing; "
                "identity = confirmed/unconfirmed/multiple_faces; quality = analyzed/missing/error; "
                "duplicate = exact or an internal similarity threshold such as 96; "
                "set = an exact saved image-set name; "
                "use missing for an empty field."
            ),
            foreground="#5F5F5F",
            wraplength=650,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Apply Search", command=self._apply).pack(
            side="right", padx=(0, 8)
        )

        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._apply())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _apply(self) -> None:
        clauses = [
            SearchClause(
                field=FIELD_KEYS[field.get()],
                value=value.get(),
                excluded=condition.get() == "Exclude",
            )
            for condition, field, value in self.rows
            if value.get().strip()
        ]
        query = build_search_query(
            clauses,
            match_any=self.match_var.get().startswith("Any"),
        )
        if not query:
            messagebox.showinfo(
                "No conditions",
                "Enter at least one search value.",
                parent=self,
            )
            return
        self.result_query = query
        self.destroy()


class SearchHistoryDialog(tk.Toplevel):
    """Edit automatic search-history behavior without storing current text."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        enabled: bool,
        maximum: int,
        history_count: int,
    ) -> None:
        super().__init__(parent)
        self.title("Search History")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.enabled_var = tk.BooleanVar(value=enabled)
        self.maximum_var = tk.StringVar(value=str(maximum))
        self.result: tuple[bool, int, bool] | None = None
        self._clear_requested = False

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Checkbutton(
            body,
            text="Remember completed searches between sessions",
            variable=self.enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Maximum history:").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        ttk.Spinbox(body, from_=1, to=200, textvariable=self.maximum_var, width=7).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(12, 0)
        )
        ttk.Label(
            body,
            text=(
                f"{history_count} saved histor{'y entry' if history_count == 1 else 'y entries'}. "
                "Disabling history stops new searches from being stored. Clear History erases existing entries."
            ),
            wraplength=430,
            justify="left",
            foreground="#5F5F5F",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(body, text="Clear History", command=self._request_clear).grid(
            row=3, column=0, sticky="w", pady=(14, 0)
        )
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=1, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _request_clear(self) -> None:
        if messagebox.askyesno(
            "Clear search history?",
            "Erase all automatically remembered searches? Named saved searches are not affected.",
            parent=self,
        ):
            self._clear_requested = True

    def _save(self) -> None:
        try:
            maximum = int(self.maximum_var.get())
        except ValueError:
            maximum = 0
        if not 1 <= maximum <= 200:
            messagebox.showerror(
                "Invalid maximum",
                "Maximum history must be a whole number from 1 to 200.",
                parent=self,
            )
            return
        self.result = (self.enabled_var.get(), maximum, self._clear_requested)
        self.destroy()


class SavedSearchesDialog(tk.Toplevel):
    """Apply or delete searches the user explicitly saved in this catalog."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        records: Iterable[object],
        on_delete: Callable[[int], bool],
    ) -> None:
        super().__init__(parent)
        self.title("Saved Searches")
        self.geometry("640x390")
        self.transient(parent.winfo_toplevel())
        self.records = list(records)
        self.on_delete = on_delete
        self.result_query: str | None = None

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(
            body,
            text="Saved searches belong to this catalog and exist only when you explicitly create them.",
            wraplength=600,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.listbox = tk.Listbox(body, exportselection=False)
        self.listbox.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        for record in self.records:
            self.listbox.insert("end", f"{record.name}    —    {record.query}")
        if self.records:
            self.listbox.selection_set(0)
        else:
            self.listbox.insert("end", "No saved searches")
            self.listbox.configure(state="disabled")
        self.listbox.bind("<Double-Button-1>", lambda _event: self._apply())

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Delete", command=self._delete).pack(side="left")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Apply", command=self._apply).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _selected_record(self):
        selection = self.listbox.curselection()
        if not selection or not self.records:
            return None
        index = int(selection[0])
        return self.records[index] if index < len(self.records) else None

    def _apply(self) -> None:
        record = self._selected_record()
        if record is not None:
            self.result_query = record.query
            self.destroy()

    def _delete(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        if not messagebox.askyesno(
            "Delete saved search?",
            f'Delete "{record.name}"? This does not affect any images or metadata.',
            parent=self,
        ):
            return
        if self.on_delete(record.search_id):
            index = self.records.index(record)
            self.records.pop(index)
            self.listbox.configure(state="normal")
            self.listbox.delete(index)
            if not self.records:
                self.listbox.insert("end", "No saved searches")
                self.listbox.configure(state="disabled")


def ask_saved_search_name(parent: tk.Misc) -> str | None:
    """Request a compact name while keeping validation close to the repository."""
    return simpledialog.askstring(
        "Save Search",
        "Name this catalog search:",
        parent=parent,
    )
