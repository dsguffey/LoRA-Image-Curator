"""
Shared user-interface helpers for LoRA Image Curator.

These helpers deliberately stay independent of catalog and provider code.  A
tooltip or pane handle must never become another path that can mutate analysis
results, images, or the SQLite catalog.
"""

from __future__ import annotations

import tkinter as tk

from collections.abc import Callable

from ui_fonts import get_ui_font
from ui_theme import AppTheme, get_theme


def _active_theme(widget: tk.Misc) -> AppTheme:
    """Return the palette retained on the owning Tk root.

    Dialogs and reusable components deliberately do not depend on
    ``DatasetToolsApp``.  ``apply_ttk_theme`` stores the active palette on the
    Tk root, while the fallback preserves a usable Clean Gray tooltip in small
    standalone tests or dialogs.
    """
    return getattr(widget._root(), "_dataset_tools_theme", get_theme(None))

class Tooltip:
    """Show concise consequence-oriented help for a Tk widget.

    The delay avoids flashing windows while the pointer merely crosses a
    toolbar.  Keyboard focus receives the same help as pointer hover so the
    feature remains useful without a mouse.
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        delay_ms: int = 550,
        wraplength: int = 360,
        show_on_focus: bool = True,
        dismiss_on_click: bool = True,
    ) -> None:
        self.widget = widget
        self.text = " ".join(text.split())
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._dismiss, add="+")
        if show_on_focus:
            widget.bind("<FocusIn>", self._schedule, add="+")
            widget.bind("<FocusOut>", self._dismiss, add="+")
        if dismiss_on_click:
            widget.bind("<ButtonPress>", self._dismiss, add="+")
        widget.bind("<Destroy>", self._dismiss, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel_pending()
        if self.text:
            self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self) -> None:
        self._after_id = None
        if self._window is not None or not self.widget.winfo_exists():
            return

        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._window = window

        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            wraplength=self.wraplength,
            background=_active_theme(self.widget).tooltip_background,
            foreground=_active_theme(self.widget).tooltip_foreground,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=get_ui_font(window, size=9),
        )
        label.pack()

        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        window.wm_geometry(f"+{x}+{y}")

    @property
    def is_visible(self) -> bool:
        """Return whether the contextual-help popup currently exists."""
        return self._window is not None

    def _cancel_pending(self) -> None:
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def _dismiss(self, _event: tk.Event | None = None) -> None:
        """Close pending or visible help when the pointer/focus leaves."""
        self._cancel_pending()
        if self._window is None:
            return
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._window = None


class HelpIcon(tk.Canvas):
    """A compact circled-question-mark affordance for optional field help.

    Hovering exposes concise help without adding a second click interaction or
    another keyboard-tab stop. Longer explanations remain in the Help menu;
    this component intentionally accepts only brief contextual text.
    """

    SIZE = 18

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        *,
        delay_ms: int = 450,
    ) -> None:
        theme = _active_theme(parent)
        super().__init__(
            parent,
            width=self.SIZE,
            height=self.SIZE,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=theme.panel_background,
            highlightcolor=theme.accent,
            background=theme.panel_background,
            takefocus=False,
        )
        self.help_text = " ".join(text.split())
        self.tooltip = Tooltip(
            self,
            self.help_text,
            delay_ms=delay_ms,
            show_on_focus=False,
            dismiss_on_click=False,
        )

        # These are deliberately additive. Tooltip already owns hover bindings
        # on the same canvas; replacing them would leave an icon that highlights
        # correctly but never displays its help text.
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Map>", self._redraw, add="+")
        self.bind("<Enter>", self._redraw, add="+")
        self.bind("<Leave>", self._redraw, add="+")
        self.bind("<<DatasetThemeChanged>>", self._apply_theme, add="+")
        self._redraw()

    def _apply_theme(self, _event: tk.Event | None = None) -> None:
        theme = _active_theme(self)
        try:
            self.configure(
                background=theme.panel_background,
                highlightbackground=theme.panel_background,
                highlightcolor=theme.accent,
            )
        except tk.TclError:
            return
        self._redraw()

    def _redraw(self, _event: tk.Event | None = None) -> None:
        """Draw a restrained icon that remains legible in every theme."""
        if not self.winfo_exists():
            return
        theme = _active_theme(self)
        active = self.focus_get() == self
        try:
            pointer_x = self.winfo_pointerx()
            pointer_y = self.winfo_pointery()
            active = active or (
                self.winfo_rootx() <= pointer_x < self.winfo_rootx() + self.SIZE
                and self.winfo_rooty() <= pointer_y < self.winfo_rooty() + self.SIZE
            )
        except tk.TclError:
            pass

        foreground = theme.accent if active else theme.muted_text
        self.delete("all")
        self.create_oval(
            2,
            2,
            self.SIZE - 3,
            self.SIZE - 3,
            outline=foreground,
            width=1,
        )
        self.create_text(
            (self.SIZE - 1) / 2,
            (self.SIZE - 1) / 2,
            text="?",
            fill=foreground,
            font=get_ui_font(self, size=8, weight="bold"),
        )


class AttachedPaneHandle(tk.Canvas):
    """A thin, keyboard-focusable tab attached to a sliding side pane.

    This is intentionally styled as a pane edge rather than a conventional
    command button.  Its visible arrow communicates the current open/closed
    direction, while the vertical label remains recognizable when the pane is
    collapsed.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        label: str,
        command: Callable[[], None],
        width: int = 24,
        background: str = "#E7EDF5",
        active_background: str = "#D6E4F4",
        foreground: str = "#23384D",
    ) -> None:
        super().__init__(
            parent,
            width=width,
            highlightthickness=1,
            highlightbackground="#AAB6C4",
            background=background,
            cursor="hand2",
            takefocus=True,
        )
        self.label = label
        self.command = command
        self.normal_background = background
        self.active_background = active_background
        self.foreground = foreground
        self.is_open = False

        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._activate)
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<FocusIn>", self._enter)
        self.bind("<FocusOut>", self._leave)

    def configure_theme(
        self,
        *,
        background: str,
        active_background: str,
        foreground: str,
    ) -> None:
        """Repaint the handle when the application theme changes."""
        self.normal_background = background
        self.active_background = active_background
        self.foreground = foreground
        self.configure(
            background=self.normal_background,
            highlightbackground=self.foreground,
        )
        self._redraw()

    def set_open(self, is_open: bool) -> None:
        """Update the direction marker without invoking the pane command."""
        self.is_open = bool(is_open)
        self._redraw()

    def _activate(self, _event: tk.Event | None = None) -> str:
        self.command()
        return "break"

    def _enter(self, _event: tk.Event | None = None) -> None:
        self.configure(background=self.active_background)
        self._redraw()

    def _leave(self, _event: tk.Event | None = None) -> None:
        self.configure(background=self.normal_background)
        self._redraw()

    def _redraw(self, _event: tk.Event | None = None) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        direction = "‹" if self.is_open else "›"
        self.create_text(
            width / 2,
            16,
            text=direction,
            fill=self.foreground,
            font=get_ui_font(self, size=13, weight="bold"),
        )
        try:
            self.create_text(
                width / 2,
                height / 2,
                text=self.label,
                angle=90,
                fill=self.foreground,
                font=get_ui_font(self, size=8, weight="bold"),
            )
        except tk.TclError:
            # Very old Tk builds may not support rotated canvas text.  The
            # stacked fallback keeps the handle usable without widening it.
            self.create_text(
                width / 2,
                height / 2,
                text="\n".join(self.label),
                fill=self.foreground,
                font=get_ui_font(self, size=7, weight="bold"),
            )
