"""
ui_theme.py

Centralized visual themes for LoRA Image Curator.

Tkinter uses two styling systems at once: themed ``ttk`` widgets and classic
``tk`` widgets such as Canvas, Text, and image-card Frames.  Keeping all palette
choices in this module prevents the application from drifting back into a pile
of hard-coded grays as new workflow surfaces are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import ttk
import tkinter as tk

from ui_fonts import get_ui_font

DEFAULT_THEME_KEY = "clean_gray"


@dataclass(frozen=True, slots=True)
class AppTheme:
    """One restrained application palette."""

    key: str
    label: str
    window_background: str
    panel_background: str
    raised_background: str
    field_background: str
    text: str
    muted_text: str
    border: str
    accent: str
    accent_text: str
    success: str
    warning: str
    manual: str
    card_background: str
    card_border: str
    browser_background: str
    handle_background: str
    handle_active_background: str
    handle_foreground: str
    ai_tag_foreground: str
    ai_tag_background: str
    manual_tag_foreground: str
    manual_tag_background: str
    excluded_tag_foreground: str
    excluded_tag_background: str
    duplicate_background: str
    duplicate_border: str
    duplicate_heading: str
    tooltip_background: str
    tooltip_foreground: str


THEMES: dict[str, AppTheme] = {
    "clean_gray": AppTheme(
        key="clean_gray",
        label="Clean Gray",
        window_background="#EEF1F4",
        panel_background="#F7F8FA",
        raised_background="#FFFFFF",
        field_background="#FFFFFF",
        text="#1E252D",
        muted_text="#5D6975",
        border="#C8D0D8",
        accent="#2F6B9A",
        accent_text="#FFFFFF",
        success="#217A3C",
        warning="#9A5A00",
        manual="#B65F00",
        card_background="#FFFFFF",
        card_border="#C9D2DB",
        browser_background="#F3F6F8",
        handle_background="#E3EAF1",
        handle_active_background="#D3E1EE",
        handle_foreground="#263F59",
        ai_tag_foreground="#174A7E",
        ai_tag_background="#D7E9FF",
        manual_tag_foreground="#7A3D00",
        manual_tag_background="#F2C38B",
        excluded_tag_foreground="#5F6670",
        excluded_tag_background="#E0E3E7",
        duplicate_background="#EAF0F7",
        duplicate_border="#7890A8",
        duplicate_heading="#263F59",
        tooltip_background="#FFF8D8",
        tooltip_foreground="#222222",
    ),
    "soft_light": AppTheme(
        key="soft_light",
        label="Soft Light",
        window_background="#F4F1EC",
        panel_background="#FBFAF7",
        raised_background="#FFFFFF",
        field_background="#FFFFFF",
        text="#26231F",
        muted_text="#6B6258",
        border="#D7CEC2",
        accent="#5D7893",
        accent_text="#FFFFFF",
        success="#2C7440",
        warning="#8A5600",
        manual="#A85F1B",
        card_background="#FFFFFF",
        card_border="#DDD3C6",
        browser_background="#F7F4EE",
        handle_background="#ECE5DB",
        handle_active_background="#DED4C6",
        handle_foreground="#4B4237",
        ai_tag_foreground="#2A557A",
        ai_tag_background="#DDECF8",
        manual_tag_foreground="#704109",
        manual_tag_background="#F0D0A5",
        excluded_tag_foreground="#6A6259",
        excluded_tag_background="#E7E1D8",
        duplicate_background="#ECEFF2",
        duplicate_border="#8A99A7",
        duplicate_heading="#33485B",
        tooltip_background="#FFF5D7",
        tooltip_foreground="#27231E",
    ),
    "dark_workstation": AppTheme(
        key="dark_workstation",
        label="Dark Workstation",
        window_background="#1F2328",
        panel_background="#282D33",
        raised_background="#323842",
        field_background="#111418",
        text="#EEF2F6",
        muted_text="#AAB4BF",
        border="#46515C",
        accent="#6EA8D9",
        accent_text="#0D141B",
        success="#71D28A",
        warning="#F0B15F",
        manual="#E29A56",
        card_background="#2B3138",
        card_border="#4C5965",
        browser_background="#1A1D21",
        handle_background="#303946",
        handle_active_background="#3D4B5B",
        handle_foreground="#DCE8F4",
        ai_tag_foreground="#D9ECFF",
        ai_tag_background="#254761",
        manual_tag_foreground="#FFE2C0",
        manual_tag_background="#65401D",
        excluded_tag_foreground="#C8CDD2",
        excluded_tag_background="#42484F",
        duplicate_background="#26313B",
        duplicate_border="#5E7890",
        duplicate_heading="#D8E6F4",
        tooltip_background="#2F3338",
        tooltip_foreground="#F2F4F6",
    ),
    "high_contrast": AppTheme(
        key="high_contrast",
        label="High Contrast",
        window_background="#FFFFFF",
        panel_background="#FFFFFF",
        raised_background="#FFFFFF",
        field_background="#FFFFFF",
        text="#000000",
        muted_text="#333333",
        border="#000000",
        accent="#005A9E",
        accent_text="#FFFFFF",
        success="#006B2E",
        warning="#8B0000",
        manual="#8A3B00",
        card_background="#FFFFFF",
        card_border="#000000",
        browser_background="#FFFFFF",
        handle_background="#E6F2FF",
        handle_active_background="#CBE5FF",
        handle_foreground="#000000",
        ai_tag_foreground="#003B73",
        ai_tag_background="#D8ECFF",
        manual_tag_foreground="#4F2500",
        manual_tag_background="#FFD7A3",
        excluded_tag_foreground="#222222",
        excluded_tag_background="#DDDDDD",
        duplicate_background="#F0F7FF",
        duplicate_border="#005A9E",
        duplicate_heading="#000000",
        tooltip_background="#FFFFCC",
        tooltip_foreground="#000000",
    ),
}


def normalize_theme_key(value: str | None) -> str:
    """Return a known theme key, preserving compatibility with old settings."""
    key = str(value or DEFAULT_THEME_KEY)
    return key if key in THEMES else DEFAULT_THEME_KEY


def get_theme(value: str | None) -> AppTheme:
    """Return the selected theme, falling back to the polished default."""
    return THEMES[normalize_theme_key(value)]


def apply_ttk_theme(root: tk.Misc, theme: AppTheme) -> None:
    """Apply the palette to the themed-widget styles used by the application."""
    # Lightweight classic-Tk helpers (tooltips and canvas-drawn help icons)
    # cannot read ttk's configured colors back reliably across platforms.
    # Retaining the active immutable palette on the root gives those helpers a
    # single theme source without coupling them to the main application class.
    setattr(root._root(), "_dataset_tools_theme", theme)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # The option database receives a named Tk font object.  Passing a
    # description such as "Segoe UI 9" would reintroduce the Python 3.14/Tk
    # multiword-family parsing failure fixed in the v0.23.0 compatibility
    # rebuild.
    root.option_add("*Font", get_ui_font(root, size=9))
    root.option_add("*Menu.background", theme.raised_background)
    root.option_add("*Menu.foreground", theme.text)
    root.option_add("*Menu.activeBackground", theme.accent)
    root.option_add("*Menu.activeForeground", theme.accent_text)

    root.configure(background=theme.window_background)
    style.configure(".", background=theme.panel_background, foreground=theme.text)
    style.configure("TFrame", background=theme.panel_background)
    style.configure("TLabelframe", background=theme.panel_background, bordercolor=theme.border)
    style.configure(
        "TLabelframe.Label",
        background=theme.panel_background,
        foreground=theme.text,
    )
    style.configure("TLabel", background=theme.panel_background, foreground=theme.text)
    style.configure("Muted.TLabel", background=theme.panel_background, foreground=theme.muted_text)
    style.configure("Accent.TLabel", background=theme.panel_background, foreground=theme.accent)
    style.configure("Running.TLabel", background=theme.panel_background, foreground=theme.success)
    style.configure("Warning.TLabel", background=theme.panel_background, foreground=theme.warning)
    style.configure(
        "Status.TLabel",
        background=theme.raised_background,
        foreground=theme.text,
        relief="sunken",
    )
    style.configure(
        "TButton",
        background=theme.raised_background,
        foreground=theme.text,
        bordercolor=theme.border,
        focusthickness=1,
        focuscolor=theme.accent,
        padding=(8, 4),
    )
    style.map(
        "TButton",
        background=[
            ("active", theme.handle_active_background),
            ("disabled", theme.panel_background),
        ],
        foreground=[("disabled", theme.muted_text)],
    )
    # Toggle-like command buttons use the whole control as the state signal.
    # This is intentionally stronger than a dot or subtle icon: active modes
    # must remain obvious in a crowded production toolbar.
    style.configure(
        "Active.TButton",
        background=theme.accent,
        foreground=theme.accent_text,
        bordercolor=theme.accent,
        focusthickness=2,
        focuscolor=theme.accent_text,
        padding=(9, 4),
        font=get_ui_font(root, size=9, weight="bold"),
    )
    style.map(
        "Active.TButton",
        background=[
            ("active", theme.handle_active_background),
            ("disabled", theme.panel_background),
        ],
        foreground=[
            ("active", theme.text),
            ("disabled", theme.muted_text),
        ],
        bordercolor=[("active", theme.accent), ("disabled", theme.border)],
    )
    style.configure(
        "TCheckbutton",
        background=theme.panel_background,
        foreground=theme.text,
    )
    style.map("TCheckbutton", foreground=[("disabled", theme.muted_text)])
    style.configure(
        "TEntry",
        fieldbackground=theme.field_background,
        foreground=theme.text,
        bordercolor=theme.border,
        insertcolor=theme.text,
    )
    style.configure(
        "TCombobox",
        fieldbackground=theme.field_background,
        foreground=theme.text,
        bordercolor=theme.border,
        arrowcolor=theme.text,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", theme.field_background)],
        foreground=[("readonly", theme.text), ("disabled", theme.muted_text)],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=theme.field_background,
        foreground=theme.text,
        bordercolor=theme.border,
        arrowcolor=theme.text,
    )
    style.configure("TNotebook", background=theme.window_background, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=theme.panel_background,
        foreground=theme.text,
        padding=(10, 5),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme.raised_background), ("active", theme.handle_active_background)],
        foreground=[("selected", theme.text)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=theme.accent,
        troughcolor=theme.panel_background,
        bordercolor=theme.border,
        lightcolor=theme.accent,
        darkcolor=theme.accent,
    )
    style.configure(
        "Vertical.TScrollbar",
        background=theme.raised_background,
        troughcolor=theme.panel_background,
        bordercolor=theme.border,
        arrowcolor=theme.text,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=theme.raised_background,
        troughcolor=theme.panel_background,
        bordercolor=theme.border,
        arrowcolor=theme.text,
    )
