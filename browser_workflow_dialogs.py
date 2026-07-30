"""Tk dialogs for unified browser filters and result-wide keyword selection."""

from __future__ import annotations

import tkinter as tk

from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Iterable

from browser_workflow import (
    ALL_IMAGE_SETS_LABEL,
    BODY_STATE_FILTERS,
    FACE_STATE_FILTERS,
    GENERAL_STATE_FILTERS,
    READINESS_ISSUE_LABELS,
    BrowserFilterState,
    parse_keyword_terms,
)
from dataset_readiness import READINESS_PROFILES, READINESS_PROFILES_BY_KEY
from image_sets import ImageSetSummary
from quality_analysis import duplicate_similarity_description
from ui_fonts import get_ui_font


PROFILE_LABEL_TO_KEY = {
    profile.label: profile.key
    for profile in READINESS_PROFILES
}


class BrowserFiltersDialog(tk.Toplevel):
    """Compose browser-only visibility constraints in organized categories.

    Face, body/pose, and general catalog state are independent so users can
    combine evidence such as ``Has face`` and ``Full body``. The dialog owns
    only visibility; selection-changing curation remains a separate workflow.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_state: BrowserFilterState,
        image_sets: Iterable[ImageSetSummary],
        initial_section: str = "scope",
    ) -> None:
        super().__init__(parent)
        self.title("Filters")
        self.geometry("770x650")
        self.minsize(700, 580)
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.result: BrowserFilterState | None = None
        self._initial_section = initial_section
        self._image_sets = tuple(image_sets)
        self._sets_by_label = {
            self._image_set_label(summary): summary
            for summary in self._image_sets
        }
        normalized = initial_state.normalized()
        selected_set = next(
            (
                summary
                for summary in self._image_sets
                if summary.set_id == normalized.image_set_id
            ),
            None,
        )
        profile = READINESS_PROFILES_BY_KEY.get(
            normalized.profile_key,
            READINESS_PROFILES[0],
        )

        self.image_set_var = tk.StringVar(
            value=(
                self._image_set_label(selected_set)
                if selected_set is not None
                else ALL_IMAGE_SETS_LABEL
            )
        )
        self.catalog_state_var = tk.StringVar(value=normalized.catalog_state)
        self.face_state_var = tk.StringVar(value=normalized.face_state)
        self.body_state_var = tk.StringVar(value=normalized.body_state)
        self.match_var = tk.StringVar(
            value=(
                "All selected checks"
                if normalized.readiness_match == "all"
                else "Any selected check"
            )
        )
        self.profile_var = tk.StringVar(value=profile.label)
        self.blur_threshold = normalized.blur_threshold
        self.duplicate_var = tk.StringVar(
            value=duplicate_similarity_description(
                normalized.duplicate_similarity_percent
            )
        )
        self.issue_vars = {
            label: tk.BooleanVar(value=label in normalized.readiness_issues)
            for label in READINESS_ISSUE_LABELS
        }

        self._build_interface()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    @staticmethod
    def _image_set_label(summary: ImageSetSummary) -> str:
        """Show the saved name and useful scope size without changing identity."""
        return f"{summary.name} ({summary.image_count:,})"

    def _build_interface(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(
            body,
            text=(
                "Choose what appears in the Catalog Browser. Face, body/pose, "
                "catalog-state, and readiness filters can be combined. Filters "
                "affect every page and never change selection, files, or catalog data."
            ),
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        notebook = ttk.Notebook(body)
        notebook.grid(row=1, column=0, sticky="nsew")
        self.notebook = notebook

        scope = ttk.Frame(notebook, padding=12)
        face = ttk.Frame(notebook, padding=12)
        body_pose = ttk.Frame(notebook, padding=12)
        filter_settings = ttk.Frame(notebook, padding=12)
        readiness = ttk.Frame(notebook, padding=12)
        notebook.add(scope, text="Scope")
        notebook.add(face, text="Face")
        notebook.add(body_pose, text="Body / Pose")
        notebook.add(filter_settings, text="Filter Settings")
        notebook.add(readiness, text="Readiness")
        self._tabs = {
            "scope": scope,
            "face": face,
            "body": body_pose,
            # Keep the old internal route so historical callers still focus
            # the renamed tab.
            "quality": filter_settings,
            "filters": filter_settings,
            "filter_settings": filter_settings,
            "readiness": readiness,
        }

        scope.columnconfigure(1, weight=1)
        ttk.Label(
            scope,
            text=(
                "Limit the browser to a saved image set and, optionally, one "
                "general catalog state."
            ),
            wraplength=680,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Combobox(
            scope,
            textvariable=self.image_set_var,
            values=(
                ALL_IMAGE_SETS_LABEL,
                *(self._image_set_label(summary) for summary in self._image_sets),
            ),
            state="readonly",
            width=42,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(scope, text="Image set:").grid(row=1, column=0, sticky="w")
        ttk.Label(scope, text="Catalog state:").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(7, 0),
        )
        ttk.Combobox(
            scope,
            textvariable=self.catalog_state_var,
            values=GENERAL_STATE_FILTERS,
            state="readonly",
            width=28,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(7, 0))

        self._build_radio_section(
            face,
            heading="Face evidence",
            explanation=(
                "Face detection and pose-based face visibility are different "
                "evidence sources. “No face” means InsightFace accepted no face "
                "under the detection threshold used for that run; it does not "
                "prove that no person is present."
            ),
            variable=self.face_state_var,
            choices=FACE_STATE_FILTERS,
        )
        self._build_radio_section(
            body_pose,
            heading="Body / pose evidence",
            explanation=(
                "“Has / No body” asks whether MediaPipe accepted enough human-pose "
                "evidence under the Body / pose detection strictness saved in "
                "Settings. Full/partial body describes completeness after a pose "
                "is accepted. Combine No body with No face to triage likely "
                "non-person images, but review false positives before bulk deletion."
            ),
            variable=self.body_state_var,
            choices=BODY_STATE_FILTERS,
        )

        filter_settings.columnconfigure(1, weight=1)
        ttk.Label(
            filter_settings,
            text="Shared filter interpretation",
            font=get_ui_font(self, size=10, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            filter_settings,
            text=(
                "The values below explain the current rules; they do not turn "
                "filters on. Change them under Settings > Filter Settings. "
                "On/off readiness checkboxes stay on the Readiness tab here."
            ),
            wraplength=680,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 14))
        ttk.Label(filter_settings, text="Target:").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(
            filter_settings,
            textvariable=self.profile_var,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0))
        ttk.Label(filter_settings, text="Blur below:").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(
            filter_settings,
            text=f"{self.blur_threshold:g}  (Settings > Filter Settings)",
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(filter_settings, text="Duplicate similarity:").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(
            filter_settings,
            textvariable=self.duplicate_var,
        ).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(
            filter_settings,
            text=(
                "Duplicate similarity is off as a Browser visibility filter "
                "unless Possible Duplicates is checked on Readiness. Quality "
                "analysis may still report duplicate warnings."
            ),
            foreground="#5F5F5F",
            wraplength=680,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 0))

        readiness.columnconfigure((0, 1), weight=1)
        ttk.Label(
            readiness,
            text=(
                "Show images matching any or all selected readiness findings. "
                "These checks use the Filter Settings interpretation."
            ),
            wraplength=680,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        for index, label in enumerate(READINESS_ISSUE_LABELS):
            visible_label = (
                "Possible Duplicates "
                f"(uses {self.duplicate_var.get().split('%', 1)[0]}% setting)"
                if label == "Possible Duplicates"
                else label
            )
            ttk.Checkbutton(
                readiness,
                text=visible_label,
                variable=self.issue_vars[label],
            ).grid(
                row=index // 2 + 1,
                column=index % 2,
                sticky="w",
                padx=(0, 18),
                pady=2,
            )

        readiness_actions = ttk.Frame(readiness)
        readiness_actions.grid(
            row=(len(READINESS_ISSUE_LABELS) + 1) // 2 + 1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(
            readiness_actions,
            text="Select All Checks",
            command=lambda: self._set_all_issues(True),
        ).pack(side="left")
        ttk.Button(
            readiness_actions,
            text="Clear Checks",
            command=lambda: self._set_all_issues(False),
        ).pack(side="left", padx=(7, 0))
        ttk.Label(readiness_actions, text="Match:").pack(side="left", padx=(22, 6))
        ttk.Combobox(
            readiness_actions,
            textvariable=self.match_var,
            values=("Any selected check", "All selected checks"),
            state="readonly",
            width=20,
        ).pack(side="left")

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(
            buttons,
            text="Clear Filters",
            command=self._clear_and_apply,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Show Likely Non-Person",
            command=self._show_likely_non_person,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Apply Filters", command=self._apply).pack(
            side="right",
            padx=(0, 8),
        )
        selected = self._tabs.get(self._initial_section, scope)
        notebook.select(selected)

    @staticmethod
    def _build_radio_section(
        parent: ttk.Frame,
        *,
        heading: str,
        explanation: str,
        variable: tk.StringVar,
        choices: tuple[str, ...],
    ) -> None:
        """Build one mutually exclusive evidence category with plain wording."""
        ttk.Label(
            parent,
            text=heading,
            font=get_ui_font(parent, size=10, weight="bold"),
        ).pack(anchor="w")
        ttk.Label(
            parent,
            text=explanation,
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        for choice in choices:
            ttk.Radiobutton(
                parent,
                text=choice,
                value=choice,
                variable=variable,
            ).pack(anchor="w", pady=2)

    def _set_all_issues(self, selected: bool) -> None:
        for variable in self.issue_vars.values():
            variable.set(selected)

    def _clear_controls(self) -> None:
        """Reset visibility constraints while retaining interpretation settings."""
        self.image_set_var.set(ALL_IMAGE_SETS_LABEL)
        self.catalog_state_var.set("All images")
        self.face_state_var.set("Any face evidence")
        self.body_state_var.set("Any body / pose evidence")
        self._set_all_issues(False)
        self.match_var.set("Any selected check")

    def _clear_and_apply(self) -> None:
        """Clear every visibility constraint and publish the result immediately.

        ``Clear Filters`` is an action, not a form-editing convenience.  Closing
        the dialog through the normal apply boundary makes the result available
        to the browser at once and ensures the complete state becomes one
        reversible Undo/Redo operation.
        """
        self._clear_controls()
        self._apply()

    def _show_likely_non_person(self) -> None:
        """Apply the conservative combined triage view without changing data."""
        self.face_state_var.set("No face")
        self.body_state_var.set("No body / pose")
        self._apply()

    def _apply(self) -> None:
        try:
            duplicate_similarity = int(
                self.duplicate_var.get().split("%", 1)[0]
            )
        except (ValueError, IndexError):
            messagebox.showerror(
                "Invalid filter threshold",
                "Duplicate similarity must be a whole percentage.",
                parent=self,
            )
            return
        if not 96 <= duplicate_similarity <= 100:
            messagebox.showerror(
                "Invalid duplicate similarity",
                "Duplicate similarity must be between 96 and 100 percent.",
                parent=self,
            )
            return

        summary = self._sets_by_label.get(self.image_set_var.get())
        self.result = BrowserFilterState(
            catalog_state=self.catalog_state_var.get(),
            face_state=self.face_state_var.get(),
            body_state=self.body_state_var.get(),
            image_set_id=summary.set_id if summary is not None else None,
            image_set_name=summary.name if summary is not None else "",
            readiness_issues=frozenset(
                label
                for label, variable in self.issue_vars.items()
                if variable.get()
            ),
            readiness_match=(
                "all"
                if self.match_var.get().startswith("All")
                else "any"
            ),
            profile_key=PROFILE_LABEL_TO_KEY.get(
                self.profile_var.get(),
                READINESS_PROFILES[0].key,
            ),
            blur_threshold=self.blur_threshold,
            duplicate_similarity_percent=duplicate_similarity,
        ).normalized()
        self.destroy()


@dataclass(slots=True, frozen=True)
class CurationOptions:
    """Session-only choices for explainable selection pruning."""

    already_rejected: bool = True
    missing_or_unreadable: bool = True
    low_resolution: bool = True
    blur: bool = True
    screenshot_or_ui: bool = True
    no_person_or_face: bool = False
    subject_too_small: bool = True
    multiple_prominent_faces: bool = True
    any_multiple_people_or_faces: bool = False
    near_duplicates: bool = True
    small_face_percent: float = 0.25
    prominence_percent: float = 45.0


class CurationOptionsDialog(tk.Toplevel):
    """Collect selection-pruning criteria without occupying browser width."""

    CHECKS = (
        ("already_rejected", "Already marked Reject"),
        ("missing_or_unreadable", "Missing or unreadable source"),
        ("low_resolution", "Below readiness resolution"),
        ("blur", "Blur score below threshold"),
        ("screenshot_or_ui", "Screenshot / webpage / UI"),
        ("no_person_or_face", "No person and no face detected"),
        ("subject_too_small", "Main face too small"),
        ("multiple_prominent_faces", "Multiple similarly prominent faces"),
        ("any_multiple_people_or_faces", "Any multiple people or faces"),
        ("near_duplicates", "Near-duplicate of stronger image"),
    )

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial: CurationOptions,
    ) -> None:
        super().__init__(parent)
        self.title("Remove Unnecessary Images")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result: CurationOptions | None = None
        self.vars = {
            key: tk.BooleanVar(value=bool(getattr(initial, key)))
            for key, _label in self.CHECKS
        }
        self.small_face_var = tk.StringVar(value=f"{initial.small_face_percent:g}")
        self.prominence_var = tk.StringVar(value=f"{initial.prominence_percent:g}")
        self._build_interface()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _build_interface(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                "Choose evidence used to deselect images from the current "
                "selection. A preview explains every proposed change before "
                "anything is deselected. Files and catalog data are unchanged."
            ),
            wraplength=590,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        checks = ttk.LabelFrame(frame, text="Curation checks", padding=10)
        checks.grid(row=1, column=0, columnspan=2, sticky="ew")
        checks.columnconfigure((0, 1), weight=1)
        for index, (key, label) in enumerate(self.CHECKS):
            ttk.Checkbutton(
                checks,
                text=label,
                variable=self.vars[key],
            ).grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=(0, 14),
                pady=2,
            )

        thresholds = ttk.LabelFrame(frame, text="Subject thresholds", padding=10)
        thresholds.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Label(thresholds, text="Small face below:").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Spinbox(
            thresholds,
            from_=0.05,
            to=10.0,
            increment=0.05,
            width=9,
            textvariable=self.small_face_var,
        ).grid(row=0, column=1, sticky="w", padx=(8, 4))
        ttk.Label(thresholds, text="% of image").grid(row=0, column=2, sticky="w")
        ttk.Label(thresholds, text="Second face at least:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Spinbox(
            thresholds,
            from_=10,
            to=100,
            increment=5,
            width=9,
            textvariable=self.prominence_var,
        ).grid(row=1, column=1, sticky="w", padx=(8, 4), pady=(8, 0))
        ttk.Label(thresholds, text="% of largest").grid(
            row=1, column=2, sticky="w", pady=(8, 0)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(
            buttons,
            text="Preview Deselection",
            command=self._accept,
        ).pack(side="right", padx=(0, 8))

    def _accept(self) -> None:
        if not any(variable.get() for variable in self.vars.values()):
            messagebox.showinfo(
                "Choose curation checks",
                "Select at least one curation check before previewing deselection.",
                parent=self,
            )
            return
        try:
            small_face = float(self.small_face_var.get())
            prominence = float(self.prominence_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid curation threshold",
                "Enter numeric percentages for face size and face prominence.",
                parent=self,
            )
            return
        if not 0.05 <= small_face <= 10.0:
            messagebox.showerror(
                "Invalid small-face threshold",
                "Small face percentage must be between 0.05 and 10.",
                parent=self,
            )
            return
        if not 10.0 <= prominence <= 100.0:
            messagebox.showerror(
                "Invalid prominence threshold",
                "Second-face prominence must be between 10 and 100 percent.",
                parent=self,
            )
            return
        self.result = CurationOptions(
            **{key: variable.get() for key, variable in self.vars.items()},
            small_face_percent=small_face,
            prominence_percent=prominence,
        )
        self.destroy()


class KeywordSelectionDialog(tk.Toplevel):
    """Collect one or more keywords for a result-wide selection operation."""

    def __init__(self, parent: tk.Misc, *, action: str) -> None:
        super().__init__(parent)
        self.action = "Deselect" if action.casefold() == "deselect" else "Select"
        self.title(f"{self.action} by Keyword")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.keyword_var = tk.StringVar()
        self.match_var = tk.StringVar(value="Any keyword")
        self.result: tuple[tuple[str, ...], bool] | None = None

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                f"{self.action} matching images across every page of the current "
                "browser results. Enter applied tags or Trigger Keywords separated "
                "by commas."
            ),
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(body, text="Keywords:").grid(row=1, column=0, sticky="w")
        entry = ttk.Entry(body, textvariable=self.keyword_var, width=55)
        entry.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        entry.focus_set()
        ttk.Label(body, text="Match:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            body,
            textvariable=self.match_var,
            values=("Any keyword", "All keywords"),
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(
            body,
            text="Example: close-up, interview, computer foreground",
            foreground="#5F5F5F",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(
            buttons,
            text=f"{self.action} Matches",
            command=self._apply,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._apply())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _apply(self) -> None:
        terms = parse_keyword_terms(self.keyword_var.get())
        if not terms:
            messagebox.showinfo(
                "Enter a keyword",
                "Enter at least one tag or Trigger Keyword.",
                parent=self,
            )
            return
        self.result = (terms, self.match_var.get().startswith("All"))
        self.destroy()
