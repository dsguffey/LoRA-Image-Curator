"""User-controlled caption word selection for the browser's manual-tag workflow.

The model holds only temporary ordered word choices. The dialog never writes to a catalog;
the browser passes its result through the existing manual-tag edit service.
"""

from __future__ import annotations

import re
import tkinter as tk

from dataclasses import dataclass
from typing import Callable, Iterable
from tkinter import messagebox, ttk

from ui_fonts import get_ui_font


WORD_PATTERN = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CaptionWord:
    """One complete word and its character range in an unchanged caption."""

    start: int
    end: int
    text: str


class CaptionTagSelection:
    """Keep ordered word choices per tag and one active Shift anchor."""

    def __init__(self, caption: str) -> None:
        self.caption = caption
        self.words = tuple(
            CaptionWord(match.start(), match.end(), match.group())
            for match in WORD_PATTERN.finditer(caption)
        )
        self.candidates: list[list[int]] = []
        self.active_index: int | None = None
        self.anchor_word: int | None = None
        self._active_has_control = False

    def word_at(self, offset: int) -> int | None:
        """Snap an inside-word offset, or adjacent punctuation, to a word."""
        for index, word in enumerate(self.words):
            if word.start <= offset < word.end:
                return index
            if offset == word.end and offset < len(self.caption):
                if self.caption[offset] in ",.;:!?)]}":
                    return index
        return None

    def candidate_text(self, word_indexes: list[int]) -> str:
        """Assemble complete words in the order the user selected them."""
        return " ".join(self.words[index].text for index in word_indexes)

    def pending_tags(self) -> list[str]:
        """Return visible candidates in gesture order without duplicate names."""
        tags: list[str] = []
        seen: set[str] = set()
        for word_indexes in self.candidates:
            name = self.candidate_text(word_indexes)
            key = name.casefold()
            if name and key not in seen:
                tags.append(name)
                seen.add(key)
        return tags

    def _remove(self, index: int) -> None:
        del self.candidates[index]
        self.active_index = None
        self.anchor_word = None
        self._active_has_control = False

    def _add(self, word_indexes: list[int], anchor: int) -> None:
        if word_indexes in self.candidates:
            self.active_index = self.candidates.index(word_indexes)
        else:
            self.candidates.append(word_indexes)
            self.active_index = len(self.candidates) - 1
        self.anchor_word = anchor
        self._active_has_control = False

    def _control_add(self, word_indexes: list[int]) -> None:
        """Append new words to the active tag without changing earlier order."""
        if self.active_index is None:
            self._add([], word_indexes[0])
        assert self.active_index is not None
        active = self.candidates[self.active_index]
        active.extend(index for index in word_indexes if index not in active)
        self._active_has_control = True

    def click(self, word_index: int, *, shift: bool = False, control: bool = False) -> None:
        """Click toggles a candidate; Shift extends; Ctrl edits the active tag."""
        if not 0 <= word_index < len(self.words):
            return
        if shift and self.active_index is not None and self.anchor_word is not None:
            first, last = sorted((self.anchor_word, word_index))
            extension = list(range(first, last + 1))
            if self._active_has_control:
                self._control_add(extension)
            else:
                self.candidates[self.active_index] = extension
            return
        if control:
            if self.active_index is not None:
                active = self.candidates[self.active_index]
                if word_index in active:
                    active.remove(word_index)
                    if not active:
                        self._remove(self.active_index)
                    else:
                        self._active_has_control = True
                    return
            self._control_add([word_index])
            return
        for index in range(len(self.candidates) - 1, -1, -1):
            if word_index in self.candidates[index]:
                # A phrase is one candidate. Clicking any word removes the
                # complete phrase instead of silently splitting it.
                self._remove(index)
                return
        self._add([word_index], word_index)

    def drag(self, first: int, last: int, *, control: bool = False) -> None:
        """A drag selects a phrase; Ctrl appends it to the active tag."""
        if not (0 <= first < len(self.words) and 0 <= last < len(self.words)):
            return
        start, end = sorted((first, last))
        word_indexes = list(range(start, end + 1))
        if control:
            self._control_add(word_indexes)
            return
        self._add(word_indexes, first)


class CaptionTaggingDialog(tk.Toplevel):
    """Modal, reviewable caption-to-manual-tag staging surface."""

    def __init__(
        self,
        parent: tk.Misc,
        target_ids: tuple[int, ...],
        captions: Iterable[tuple[int, str, str]],
        parse_manual_tags: Callable[[str], list[str]],
    ) -> None:
        super().__init__(parent)
        self.target_ids = target_ids
        self.result: list[str] | None = None
        self._parse_manual_tags = parse_manual_tags
        self._captions = tuple(captions)
        # Build word spans only for captions the user actually views. A batch
        # can contain thousands of selected images while only a few are read.
        self._models: dict[int, CaptionTagSelection] = {}
        self._caption_index = 0
        self._press_word: int | None = None
        self._press_control = False
        self._press_shift = False

        self.title("Caption Tagging Mode")
        self.transient(parent.winfo_toplevel())
        self.minsize(520, 430)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(
            body,
            text=(
                f"Tagging Mode — {len(target_ids):,} image"
                f"{'s' if len(target_ids) != 1 else ''} selected"
            ),
            font=get_ui_font(self, size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text=(
                "Select whole words or phrases from a Florence caption. "
                "Finished adds pending tags to every selected image; Cancel or Esc discards them. "
                "Finish or cancel before changing the browser selection."
            ),
            wraplength=540,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(5, 10))

        navigation = ttk.Frame(body)
        navigation.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        navigation.columnconfigure(1, weight=1)
        ttk.Button(navigation, text="◀", width=3, command=lambda: self._navigate(-1)).grid(row=0, column=0)
        self.caption_source_var = tk.StringVar()
        ttk.Label(navigation, textvariable=self.caption_source_var).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(navigation, text="▶", width=3, command=lambda: self._navigate(1)).grid(row=0, column=2)

        self.caption_text = tk.Text(body, wrap="word", height=8, padx=8, pady=8, cursor="hand2")
        self.caption_text.grid(row=3, column=0, sticky="nsew")
        self.caption_text.tag_configure("candidate", background="#a9d4f0", foreground="#172331")
        self.caption_text.tag_configure("preview", background="#cde9fb", foreground="#172331")
        self.caption_text.bind("<ButtonPress-1>", self._on_press)
        self.caption_text.bind("<B1-Motion>", self._on_motion)
        self.caption_text.bind("<ButtonRelease-1>", self._on_release)

        ttk.Label(
            body,
            text=(
                "Click to select or remove a word. Shift-click extends a phrase. "
                "Ctrl-click or Ctrl-drag adds words to the active tag in selection order. "
                "Clicking a selected phrase removes that whole phrase."
            ),
            wraplength=540,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", pady=(6, 8))
        self.pending_var = tk.StringVar()
        ttk.Label(body, textvariable=self.pending_var, wraplength=540, justify="left").grid(
            row=5, column=0, sticky="ew", pady=(0, 9)
        )
        ttk.Label(body, text="Other manual tags (optional; comma separated):").grid(
            row=6, column=0, sticky="w"
        )
        self.manual_entry = ttk.Entry(body)
        self.manual_entry.grid(row=7, column=0, sticky="ew", pady=(3, 10))
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=0)
        ttk.Button(buttons, text="Finished", command=self._finished).grid(row=0, column=1, padx=(8, 0))

        # Child widget class bindings run first; this toplevel binding then
        # prevents the browser's bind_all selection/undo shortcuts from firing.
        self.bind("<KeyPress>", self._on_keypress)
        self._bind_escape_to_children(self)
        self._show_caption()
        self.grab_set()
        self.caption_text.focus_set()

    @property
    def model(self) -> CaptionTagSelection:
        image_id, _filename, caption = self._captions[self._caption_index]
        if image_id not in self._models:
            self._models[image_id] = CaptionTagSelection(caption)
        return self._models[image_id]

    def _navigate(self, step: int) -> None:
        self._caption_index = (self._caption_index + step) % len(self._captions)
        self._show_caption()

    def _show_caption(self) -> None:
        _image_id, filename, caption = self._captions[self._caption_index]
        self.caption_source_var.set(
            f"Caption {self._caption_index + 1:,} of {len(self._captions):,}: {filename}"
        )
        self.caption_text.configure(state="normal")
        self.caption_text.delete("1.0", "end")
        self.caption_text.insert("1.0", caption or "No Florence caption for this image.")
        self.caption_text.configure(state="disabled")
        _ = self.model
        self._render()

    def _word_at_event(self, event: tk.Event) -> int | None:
        index = self.caption_text.index(f"@{event.x},{event.y}")
        offset = int(self.caption_text.count("1.0", index, "chars")[0])
        return self.model.word_at(offset)

    def _on_press(self, event: tk.Event) -> str:
        self._press_word = self._word_at_event(event)
        self._press_control = bool(event.state & 0x0004)
        self._press_shift = bool(event.state & 0x0001)
        return "break"

    def _on_motion(self, event: tk.Event) -> str:
        self.caption_text.tag_remove("preview", "1.0", "end")
        last = self._word_at_event(event)
        if self._press_word is not None and last is not None and last != self._press_word:
            first, last = sorted((self._press_word, last))
            words = self.model.words
            self.caption_text.tag_add(
                "preview", f"1.0+{words[first].start}c", f"1.0+{words[last].end}c"
            )
        return "break"

    def _on_release(self, event: tk.Event) -> str:
        first = self._press_word
        self._press_word = None
        last = self._word_at_event(event)
        control = self._press_control or bool(event.state & 0x0004)
        shift = self._press_shift or bool(event.state & 0x0001)
        self._press_control = False
        self._press_shift = False
        if first is None or last is None:
            self._render()
            return "break"
        # Windows Tk uses ControlMask 0x0004. Remember press state too: the
        # modifier can be released before ButtonRelease reaches this widget.
        if first != last and not shift:
            self.model.drag(first, last, control=control)
        else:
            self.model.click(last, shift=shift, control=control)
        self._render()
        return "break"

    def _render(self) -> None:
        self.caption_text.tag_remove("candidate", "1.0", "end")
        self.caption_text.tag_remove("preview", "1.0", "end")
        words = self.model.words
        for word_indexes in self.model.candidates:
            for index in word_indexes:
                word = words[index]
                self.caption_text.tag_add(
                    "candidate", f"1.0+{word.start}c", f"1.0+{word.end}c"
                )
        tags: list[str] = []
        seen: set[str] = set()
        for model in self._models.values():
            for tag in model.pending_tags():
                if tag.casefold() not in seen:
                    tags.append(tag)
                    seen.add(tag.casefold())
        self.pending_var.set(
            f"Pending tags ({len(tags):,}) for {len(self.target_ids):,} image"
            f"{'s' if len(self.target_ids) != 1 else ''}: "
            + (" · ".join(tags) if tags else "none")
        )

    def _on_keypress(self, event: tk.Event) -> str:
        if event.keysym == "Escape":
            self._cancel()
        return "break"

    def _bind_escape_to_children(self, widget: tk.Misc) -> None:
        """Let Esc cancel even when a focused Tk class consumes the key first."""
        for child in widget.winfo_children():
            child.bind("<Escape>", self._cancel_key, add="+")
            self._bind_escape_to_children(child)

    def _cancel_key(self, _event: tk.Event) -> str:
        self._cancel()
        return "break"

    def _finished(self) -> None:
        pending = [tag for model in self._models.values() for tag in model.pending_tags()]
        typed = self.manual_entry.get().strip()
        if typed:
            pending.append(typed)
        if pending:
            try:
                # The existing manual-entry parser owns whitespace, length,
                # count, and case-insensitive deduplication rules for both
                # caption-picked and typed candidates.
                pending = self._parse_manual_tags("\n".join(pending))
            except ValueError as error:
                messagebox.showinfo("Invalid tags", str(error), parent=self)
                return
        self.result = pending
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
