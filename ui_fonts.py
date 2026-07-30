"""
Centralized, Tcl-safe fonts for LoRA Image Curator.

Tk accepts several font representations, but tuple and description-string
forms still require Tcl to parse a family such as ``Segoe UI``.  That parsing
differs across Python/Tk combinations and caused the v0.23.0 Windows startup
failure.  This module crosses the Python/Tcl boundary with real
``tkinter.font.Font`` objects instead.

Font objects must remain alive for as long as their widgets use them.  The
registry is therefore owned by the Tk root and caches one object per complete
font specification.  Dialogs, browser cards, canvas labels, and text tags can
all request fonts without creating duplicate Tcl resources or managing object
lifetimes themselves.
"""

from __future__ import annotations

import tkinter as tk

from tkinter import font as tkfont


UI_FONT_FAMILY = "Segoe UI"
MONOSPACE_FONT_FAMILY = "Consolas"
_FONT_CACHE_ATTRIBUTE = "_dataset_tools_font_cache"


def _resolve_tk_root(owner: object) -> tk.Misc:
    """Return the Tk root associated with a widget or small UI controller.

    Most callers pass a real Tk widget, which exposes ``._root()``.  A few
    presentation helpers—most notably ``ThumbnailCard``—are plain Python
    controllers that own their actual widget through ``.outer``.  Supporting
    that narrow wrapper shape here makes the font boundary defensive against a
    partially upgraded installation while keeping failures explicit for
    unrelated objects.

    The ``._root()`` method is private Tkinter API, but it is the standard
    mechanism Tkinter itself uses to locate the interpreter-owning root.  This
    helper confines that dependency to one documented location.
    """
    root_getter = getattr(owner, "_root", None)
    if callable(root_getter):
        return root_getter()

    outer_widget = getattr(owner, "outer", None)
    root_getter = getattr(outer_widget, "_root", None)
    if callable(root_getter):
        return root_getter()

    raise TypeError(
        "get_ui_font() requires a Tk widget or a UI controller whose "
        "'outer' attribute is a Tk widget"
    )


def get_ui_font(
    widget: object,
    *,
    size: int = 9,
    weight: str = "normal",
    family: str = UI_FONT_FAMILY,
) -> tkfont.Font:
    """Return a cached real Tk font for ``widget``'s Tcl interpreter.

    Supplying the family, size, and weight as keyword arguments avoids every
    multiword font-description parser that previously failed on Python 3.14.
    Attaching the cache to the root also prevents Tkinter from deleting a font
    while an existing widget or text tag still references it.
    """
    root = _resolve_tk_root(widget)
    cache = getattr(root, _FONT_CACHE_ATTRIBUTE, None)
    if cache is None:
        cache = {}
        setattr(root, _FONT_CACHE_ATTRIBUTE, cache)

    key = (str(family), int(size), str(weight))
    font = cache.get(key)
    if font is None:
        font = tkfont.Font(
            root=root,
            family=key[0],
            size=key[1],
            weight=key[2],
        )
        cache[key] = font
    return font
