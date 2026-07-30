"""Tkinter management dialog for explicitly saved catalog image sets."""

from __future__ import annotations

import logging
import tkinter as tk

from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Iterable

from image_sets import ImageSetRepository, ImageSetSummary
from ui_fonts import get_ui_font


class ImageSetManagerDialog(tk.Toplevel):
    """Create and manage named sets without persisting transient selection state."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        database_path: Path,
        selected_image_ids: Iterable[int],
        on_select_images: Callable[[tuple[int, ...]], None],
        on_sets_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Image Sets")
        self.geometry("650x470")
        self.minsize(560, 400)
        self.transient(parent.winfo_toplevel())
        self.repository = ImageSetRepository(database_path)
        self.selected_image_ids = tuple(sorted({int(value) for value in selected_image_ids}))
        self.on_select_images = on_select_images
        self.on_sets_changed = on_sets_changed
        self._sets_by_tree_id: dict[str, ImageSetSummary] = {}
        self.status_var = tk.StringVar()

        self._build_interface()
        self._reload_sets()
        self.grab_set()

    def _build_interface(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(
            body,
            text="Saved Image Sets",
            font=get_ui_font(self, size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=(
                f"Current browser selection: {len(self.selected_image_ids):,} image"
                f"{'s' if len(self.selected_image_ids) != 1 else ''}. "
                "Sets are stored only when you explicitly create or change one."
            ),
            wraplength=610,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(3, 10))

        list_frame = ttk.Frame(body)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("count",),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("count", text="Images", anchor="e")
        self.tree.column("#0", width=420, minwidth=220, stretch=True)
        self.tree.column("count", width=90, minwidth=70, stretch=False, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._update_button_states)
        self.tree.bind("<Double-1>", lambda _event: self._select_set_in_browser())

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.new_button = ttk.Button(buttons, text="New Set…", command=self._create_set)
        self.new_button.grid(row=0, column=0)
        self.update_button = ttk.Button(
            buttons,
            text="Update Image Set",
            command=self._update_image_set,
        )
        self.update_button.grid(row=0, column=1, padx=(6, 0))
        self.rename_button = ttk.Button(buttons, text="Rename…", command=self._rename_set)
        self.rename_button.grid(row=0, column=2, padx=(6, 0))
        self.delete_button = ttk.Button(buttons, text="Delete…", command=self._delete_set)
        self.delete_button.grid(row=0, column=3, padx=(6, 0))

        bottom = ttk.Frame(body)
        bottom.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(
            bottom,
            textvariable=self.status_var,
            foreground="#5F5F5F",
        ).grid(row=0, column=0, sticky="w")
        self.select_button = ttk.Button(
            bottom,
            text="Select Image Set in Browser",
            command=self._select_set_in_browser,
        )
        self.select_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bottom, text="Close", command=self.destroy).grid(
            row=0, column=2, padx=(7, 0)
        )
        self.bind("<Escape>", lambda _event: self.destroy())

    def _reload_sets(self, *, select_set_id: int | None = None) -> None:
        try:
            summaries = self.repository.list_sets()
        except Exception as error:
            self._show_error("Could not load image sets", error)
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._sets_by_tree_id.clear()
        selected_tree_id = ""
        for summary in summaries:
            tree_id = self.tree.insert(
                "",
                "end",
                text=summary.name,
                values=(f"{summary.image_count:,}",),
            )
            self._sets_by_tree_id[tree_id] = summary
            if summary.set_id == select_set_id:
                selected_tree_id = tree_id
        if selected_tree_id:
            self.tree.selection_set(selected_tree_id)
            self.tree.focus(selected_tree_id)
            self.tree.see(selected_tree_id)
        self.status_var.set(
            f"{len(summaries):,} saved set{'s' if len(summaries) != 1 else ''}"
        )
        self._update_button_states()

    def _selected_set(self) -> ImageSetSummary | None:
        selected = self.tree.selection()
        return self._sets_by_tree_id.get(selected[0]) if selected else None

    def _update_button_states(self, _event: tk.Event | None = None) -> None:
        has_set = self._selected_set() is not None
        self.update_button.configure(state="normal" if has_set else "disabled")
        for button in (self.rename_button, self.delete_button, self.select_button):
            button.configure(state="normal" if has_set else "disabled")

    def _create_set(self) -> None:
        name = simpledialog.askstring(
            "New Image Set",
            (
                "Name the new image set. The current browser selection will be added."
                if self.selected_image_ids
                else "Name the new empty image set."
            ),
            parent=self,
        )
        if name is None:
            return
        try:
            created = self.repository.create_set(name, self.selected_image_ids)
        except Exception as error:
            self._show_error("Could not create image set", error)
            return
        self.status_var.set(
            f'Created "{created.name}" with {created.image_count:,} image'
            f"{'s' if created.image_count != 1 else ''}."
        )
        self._changed(created.set_id)

    def _update_image_set(self) -> None:
        """Replace the chosen membership with the exact browser selection."""
        summary = self._selected_set()
        if summary is None:
            return
        if not self.selected_image_ids and not messagebox.askyesno(
            "Make image set empty?",
            (
                f'Replace every member of "{summary.name}" with the current empty '
                "browser selection?\n\nCatalog images and source files are not deleted."
            ),
            parent=self,
        ):
            return
        try:
            updated = self.repository.replace_images(
                summary.set_id,
                self.selected_image_ids,
            )
        except Exception as error:
            self._show_error("Could not update image set", error)
            return
        self.status_var.set(
            f'Updated "{updated.name}" to exactly {updated.image_count:,} image'
            f"{'s' if updated.image_count != 1 else ''}."
        )
        self._changed(summary.set_id)

    def _rename_set(self) -> None:
        summary = self._selected_set()
        if summary is None:
            return
        name = simpledialog.askstring(
            "Rename Image Set",
            "New name:",
            initialvalue=summary.name,
            parent=self,
        )
        if name is None:
            return
        try:
            renamed = self.repository.rename_set(summary.set_id, name)
        except Exception as error:
            self._show_error("Could not rename image set", error)
            return
        self.status_var.set(f'Renamed image set to "{renamed.name}".')
        self._changed(renamed.set_id)

    def _delete_set(self) -> None:
        summary = self._selected_set()
        if summary is None:
            return
        if not messagebox.askyesno(
            "Delete image set?",
            (
                f'Delete the image set "{summary.name}"?\n\n'
                "This removes only the saved set and its memberships. Catalog images, "
                "source files, review decisions, and quality data are not deleted."
            ),
            parent=self,
        ):
            return
        try:
            self.repository.delete_set(summary.set_id)
        except Exception as error:
            self._show_error("Could not delete image set", error)
            return
        self.status_var.set(f'Deleted image set "{summary.name}".')
        self._changed(None)

    def _select_set_in_browser(self) -> None:
        summary = self._selected_set()
        if summary is None:
            return
        try:
            image_ids = self.repository.get_image_ids(summary.set_id)
        except Exception as error:
            self._show_error("Could not load image set", error)
            return
        self.on_select_images(image_ids)
        self.destroy()

    def _changed(self, set_id: int | None) -> None:
        status_message = self.status_var.get()
        if self.on_sets_changed is not None:
            self.on_sets_changed()
        self._reload_sets(select_set_id=set_id)
        self.status_var.set(status_message)

    def _show_error(self, title: str, error: Exception) -> None:
        logging.exception(title)
        messagebox.showerror(
            title,
            f"{type(error).__name__}: {error}",
            parent=self,
        )
