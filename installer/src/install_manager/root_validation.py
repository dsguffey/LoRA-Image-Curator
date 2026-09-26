"""Generation guard for asynchronous selected-installation inspection."""
from __future__ import annotations

from pathlib import Path


class RootValidation:
    def __init__(self):
        self.generation = 0
        self.selected: Path | None = None
        self.in_progress = False

    def begin(self, root: Path) -> int:
        self.generation += 1
        self.selected = root.expanduser().resolve()
        self.in_progress = True
        return self.generation

    def accept(self, generation: int, root: Path) -> bool:
        if not (self.in_progress and generation == self.generation and
                self.selected == root.expanduser().resolve()):
            return False
        self.in_progress = False
        return True
