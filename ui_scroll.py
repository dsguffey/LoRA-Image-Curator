"""Route mouse-wheel input to the scrollable region beneath the pointer.

Tkinter does not automatically forward wheel events from widgets embedded in a
Canvas to that Canvas.  Binding every descendant is brittle because many
scrollable surfaces create children later.  This module installs one
toplevel-wide dispatcher and registers scroll owners instead:

* the closest registered ancestor beneath the pointer wins;
* nested regions such as a Text log inside a scrolling page remain independent;
* wheel input outside a registered region is left untouched;
* Windows/macOS ``MouseWheel`` and Linux button events share one path.

The registry is intentionally UI-only.  It owns no application state and does
not call Tk from worker threads.
"""

from __future__ import annotations

import tkinter as tk
import weakref

from dataclasses import dataclass
from typing import Callable


PostScrollCallback = Callable[[], None]


@dataclass(slots=True)
class _ScrollRegion:
    target: tk.Misc
    post_scroll: PostScrollCallback | None = None


class _MousewheelDispatcher:
    """One scoped wheel router attached to one Tk toplevel."""

    def __init__(self, toplevel: tk.Misc) -> None:
        self.toplevel = toplevel
        self.regions: weakref.WeakKeyDictionary[tk.Misc, _ScrollRegion] = (
            weakref.WeakKeyDictionary()
        )
        toplevel.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        toplevel.bind_all("<Button-4>", self._on_linux_mousewheel, add="+")
        toplevel.bind_all("<Button-5>", self._on_linux_mousewheel, add="+")

    def register(
        self,
        owner: tk.Misc,
        target: tk.Misc,
        *,
        post_scroll: PostScrollCallback | None = None,
    ) -> None:
        self.regions[owner] = _ScrollRegion(target, post_scroll)

    def _region_for_event(self, event: tk.Event) -> _ScrollRegion | None:
        """Return the closest registered ancestor beneath the mouse pointer."""
        try:
            widget = self.toplevel.winfo_containing(event.x_root, event.y_root)
        except (AttributeError, tk.TclError):
            widget = getattr(event, "widget", None)

        # Native scrollable/choice controls already own wheel semantics through
        # their Tk class bindings. Do not also move an enclosing Canvas.
        if widget is not None and widget not in self.regions:
            try:
                widget_class = str(widget.winfo_class())
            except tk.TclError:
                widget_class = ""
            if widget_class in {
                "Text",
                "Listbox",
                "Treeview",
                "TCombobox",
                "Combobox",
                "TScale",
                "Scale",
                "TSpinbox",
                "Spinbox",
            }:
                return None

        while widget is not None:
            region = self.regions.get(widget)
            if region is not None:
                return region
            parent_name = getattr(widget, "winfo_parent", lambda: "")()
            if not parent_name:
                break
            try:
                widget = widget._nametowidget(parent_name)  # type: ignore[attr-defined]
            except (KeyError, tk.TclError):
                break
        return None

    @staticmethod
    def _windows_units(delta: int) -> int:
        """Normalize both classic 120-step wheels and high-resolution devices."""
        if delta == 0:
            return 0
        steps = int(-delta / 120)
        if steps == 0:
            steps = -1 if delta > 0 else 1
        return steps

    def _scroll(self, event: tk.Event, units: int) -> str | None:
        region = self._region_for_event(event)
        if region is None or units == 0:
            return None
        try:
            region.target.yview_scroll(units, "units")  # type: ignore[attr-defined]
            if region.post_scroll is not None:
                region.post_scroll()
        except tk.TclError:
            return None
        return "break"

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        return self._scroll(event, self._windows_units(int(event.delta)))

    def _on_linux_mousewheel(self, event: tk.Event) -> str | None:
        return self._scroll(event, -1 if event.num == 4 else 1)


_DISPATCHERS: weakref.WeakKeyDictionary[tk.Misc, _MousewheelDispatcher] = (
    weakref.WeakKeyDictionary()
)


def register_mousewheel_region(
    owner: tk.Misc,
    target: tk.Misc | None = None,
    *,
    post_scroll: PostScrollCallback | None = None,
) -> None:
    """Make the wheel scroll ``target`` while the pointer is inside ``owner``.

    Registration is safe before descendants are constructed because routing is
    based on the live widget ancestry at event time.
    """
    toplevel = owner.winfo_toplevel()
    dispatcher = _DISPATCHERS.get(toplevel)
    if dispatcher is None:
        dispatcher = _MousewheelDispatcher(toplevel)
        _DISPATCHERS[toplevel] = dispatcher
    dispatcher.register(
        owner,
        target if target is not None else owner,
        post_scroll=post_scroll,
    )
