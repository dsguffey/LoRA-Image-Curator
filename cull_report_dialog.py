"""Preview dialog for the non-destructive Remove Unnecessary Images action."""

from __future__ import annotations

import tkinter as tk

from tkinter import ttk

from selection_culling import CullPlan
from ui_fonts import get_ui_font


class CullReportDialog(tk.Toplevel):
    """Show exactly what will be deselected and wait for user confirmation."""

    def __init__(self, parent: tk.Misc, plan: CullPlan) -> None:
        super().__init__(parent)
        self.plan = plan
        self.apply_requested = False

        self.title("Remove Unnecessary Images")
        self.geometry("920x680")
        self.minsize(720, 520)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        removed_count = len(plan.decisions)
        kept_count = len(plan.kept_image_ids)
        ttk.Label(
            body,
            text=(
                f"Preview: {removed_count:,} of "
                f"{len(plan.considered_image_ids):,} selected images would be "
                f"deselected; {kept_count:,} would remain selected."
            ),
            font=get_ui_font(self, size=12, weight="bold"),
            wraplength=870,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        criteria_text = _format_enabled_checks(plan)
        ttk.Label(
            body,
            text=criteria_text,
            wraplength=870,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 9))

        table_frame = ttk.LabelFrame(
            body,
            text="Proposed deselections and reasons",
            padding=8,
        )
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.decision_tree = ttk.Treeview(
            table_frame,
            columns=("filename", "reasons"),
            show="headings",
            selectmode="browse",
        )
        self.decision_tree.heading("filename", text="Image")
        self.decision_tree.heading("reasons", text="Why it would be deselected")
        self.decision_tree.column("filename", width=230, minwidth=140, stretch=False)
        self.decision_tree.column("reasons", width=590, minwidth=320, stretch=True)
        self.decision_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.decision_tree.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.decision_tree.configure(yscrollcommand=scrollbar.set)

        for decision in plan.decisions:
            self.decision_tree.insert(
                "",
                "end",
                values=(decision.filename, " • ".join(decision.reasons)),
            )

        if not plan.decisions:
            self.decision_tree.insert(
                "",
                "end",
                values=(
                    "No changes",
                    "No selected image met the available removal criteria.",
                ),
            )

        summary = _format_summary(plan)
        ttk.Label(
            body,
            text=summary,
            wraplength=870,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(9, 0))

        ttk.Label(
            body,
            text=(
                "Not automatically judged: identity correctness, anatomy, aesthetic "
                "quality, or pose/outfit/expression/lighting balance. Images lacking "
                "optional analysis are left selected unless another concrete problem "
                "is available. No files, catalog records, review decisions, tags, or "
                "image sets will be changed."
            ),
            wraplength=870,
            justify="left",
            foreground="#5F5F5F",
        ).grid(row=4, column=0, sticky="w", pady=(7, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="e", pady=(12, 0))
        if plan.decisions:
            ttk.Button(
                buttons,
                text="Cancel",
                command=self._cancel,
            ).grid(row=0, column=0, padx=(0, 7))
            apply_button = ttk.Button(
                buttons,
                text=f"Deselect {removed_count:,} Images",
                command=self._apply,
            )
            apply_button.grid(row=0, column=1)
            apply_button.focus_set()
        else:
            close_button = ttk.Button(
                buttons,
                text="Close",
                command=self._cancel,
            )
            close_button.grid(row=0, column=0)
            close_button.focus_set()

        self.bind("<Escape>", lambda _event: self._cancel())
        self.grab_set()

    def _apply(self) -> None:
        self.apply_requested = True
        self.destroy()

    def _cancel(self) -> None:
        self.apply_requested = False
        self.destroy()


def _format_summary(plan: CullPlan) -> str:
    reason_text = ", ".join(
        f"{label}: {count:,}" for label, count in plan.reason_counts
    )
    unavailable_text = ", ".join(
        f"{label}: {count:,}" for label, count in plan.unavailable_counts
    )
    lines = [
        "Reasons counted (an image may have more than one): "
        + (reason_text or "none"),
    ]
    if unavailable_text:
        lines.append(
            "Checks unavailable for some images (these absences do not cause "
            f"deselection): {unavailable_text}."
        )
    return "\n".join(lines)


def _format_enabled_checks(plan: CullPlan) -> str:
    """Describe the exact curation choices behind this preview."""
    checks = plan.criteria.checks
    enabled: list[str] = []
    pairs = (
        ("already rejected", checks.already_rejected),
        ("missing/unreadable", checks.missing_or_unreadable),
        ("low resolution", checks.low_resolution),
        ("Blur", checks.blur),
        ("screenshot/UI", checks.screenshot_or_ui),
        ("no person/face", checks.no_person_or_face),
        ("small main face", checks.subject_too_small),
        ("multiple prominent faces", checks.multiple_prominent_faces),
        ("any multiple people/faces", checks.any_multiple_people_or_faces),
        ("near-duplicates", checks.near_duplicates),
    )
    enabled.extend(label for label, active in pairs if active)
    threshold_notes: list[str] = []
    if checks.low_resolution:
        threshold_notes.append(
            f"{plan.criteria.profile_label} minimum short side "
            f"{plan.criteria.minimum_short_side:,} px"
        )
    if checks.subject_too_small:
        threshold_notes.append(
            f"small face below {plan.criteria.small_face_area_ratio * 100:g}%"
        )
    if checks.multiple_prominent_faces:
        threshold_notes.append(
            "second prominent face at least "
            f"{plan.criteria.prominent_face_relative_ratio * 100:g}% of largest"
        )
    if checks.blur:
        threshold_notes.append(f"Blur threshold {plan.criteria.blur_threshold:g}")
    if checks.near_duplicates:
        threshold_notes.append(
            "near-duplicate similarity "
            f"{plan.criteria.duplicate_similarity_percent:g}%"
        )
    threshold_text = (
        " Thresholds: " + "; ".join(threshold_notes) + "."
        if threshold_notes
        else ""
    )
    return "Enabled checks: " + ", ".join(enabled) + "." + threshold_text
