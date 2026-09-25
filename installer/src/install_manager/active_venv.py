"""Resolve the activated, path-bound LIC environment without following arbitrary records."""
from __future__ import annotations

import json
from pathlib import Path

from .bootstrap_layout import layout


def managed_venv(root: Path, record: dict | None = None) -> Path:
    """Return the active generation, or the initial path before Core activation.

    A repaired venv is created at its final path. Moving a Windows venv after
    creation would leave embedded absolute paths pointing at the old location.
    """
    root = root.resolve()
    if record is None:
        path = root / "State/installations/lic-lite.json"
        if not path.is_file():
            return layout(root)["venv"]
        record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("state") != "active" or Path(record.get("root", "")).resolve() != root:
        raise ValueError("Core activation record belongs to another installation")
    python = Path(record["python"])
    generation = python.parent.parent
    parent = layout(root)["venv"].parent
    if (python.name.casefold() != "python.exe" or python.parent.name.casefold() != "scripts"
            or generation.parent.resolve() != parent.resolve()
            or not (generation.name == "venv" or generation.name.startswith(("venv-repair-", "venv-provider-")))
            or generation.is_symlink() or getattr(generation, "is_junction", lambda: False)()
            or python.resolve() != generation.resolve() / "Scripts/python.exe"):
        raise ValueError("Core activation record names an unmanaged Python environment")
    return generation
