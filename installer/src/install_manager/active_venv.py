"""Resolve the activated, path-bound LIC environment without following arbitrary records."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re

from .bootstrap_layout import layout


def provider_venv_parent(root: Path) -> Path:
    """Short per-user, per-installation final path for optional-provider venvs.

    PyTorch includes license paths too deep for a normal Desktop installation
    root even when the generation name itself is short.  The root digest keeps
    independent LIC installations separate without lengthening the venv path.
    """
    identity = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:10]
    return Path.home().resolve() / "LICV" / identity


def managed_generation(root: Path, generation: Path) -> bool:
    """Accept both new short generations and existing activated LIC generations."""
    root = root.resolve()
    generation = Path(generation).absolute()
    legacy_parent = layout(root)["venv"].parent
    short_parent = provider_venv_parent(root)
    if generation.is_symlink() or getattr(generation, "is_junction", lambda: False)():
        return False
    if generation.parent.resolve() == legacy_parent.resolve():
        return generation.name == "venv" or generation.name.startswith(("venv-repair-", "venv-provider-"))
    return (not short_parent.parent.is_symlink()
            and not getattr(short_parent.parent, "is_junction", lambda: False)()
            and not short_parent.is_symlink()
            and not getattr(short_parent, "is_junction", lambda: False)()
            and generation.parent.resolve() == short_parent
            and re.fullmatch(r"p[0-9a-f]{10}", generation.name) is not None)


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
    if (python.name.casefold() != "python.exe" or python.parent.name.casefold() != "scripts"
            or not managed_generation(root, generation)
            or python.resolve() != generation.resolve() / "Scripts/python.exe"):
        raise ValueError("Core activation record names an unmanaged Python environment")
    return generation
