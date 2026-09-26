"""Small, per-user pointer to the last successfully validated LIC installation.

This is a hint for the next launch, never proof that an installation is ready.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def preference_path(local_appdata: Path | None = None) -> Path:
    base = local_appdata or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    return base / "LICInstallManager" / "last-valid-root.json"


def read_last_valid_root(path: Path | None = None) -> Path | None:
    target = path or preference_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("root"), str):
            return None
        root = Path(data["root"])
        return root if root.is_absolute() else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def write_last_valid_root(root: Path, path: Path | None = None) -> None:
    target = path or preference_path()
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Only an existing validated installation can be remembered")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema_version": 1, "root": str(root)}, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".last-valid-root-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
