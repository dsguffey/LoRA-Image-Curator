"""Bounded cleanup for unvalidated, attempt-owned acquisition files."""
from __future__ import annotations

from pathlib import Path
import shutil

from .journal import OperationJournal


def unfinished_acquisitions(journal: OperationJournal) -> tuple[Path, ...]:
    root = Path(journal.data["target_path"]).resolve()
    values: list[Path] = []
    for raw in journal.data.get("unvalidated_acquisitions", ()):
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError, TypeError):
            continue
        # Journals may name only temporary files within their owned target.
        if root in path.parents and ".partial" in path.name and path.exists():
            values.append(path)
    return tuple(values)


def delete_unfinished_acquisitions(journal: OperationJournal) -> tuple[Path, ...]:
    deleted: list[Path] = []
    for path in unfinished_acquisitions(journal):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        journal.clear_unvalidated_acquisition(path)
        deleted.append(path)
    journal.add_cleanup_action("delete-unvalidated-acquisition-files", performed=bool(deleted))
    return tuple(deleted)
