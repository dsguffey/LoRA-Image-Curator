"""Narrow adapter for LIC's canonical shared Face Analysis preference."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import uuid


SETTINGS_LOCK_FILENAME = "settings.lock"


def settings_path(appdata: Path) -> Path:
    return appdata / "LoRAImageCurator" / "settings.json"


@contextmanager
def _write_lock(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / SETTINGS_LOCK_FILENAME).open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0); lock_file.write(b"0"); lock_file.flush()
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            # Closing the coordination handle releases the Windows byte lock.
            pass


def read_face_model_root(appdata: Path) -> str:
    """Read only the canonical LIC preference; never acquire or write."""
    target = settings_path(appdata)
    if not target.is_file():
        return ""
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(value.get("face_model_root", "")) if isinstance(value, dict) else ""


def read_provider_location(appdata: Path, component_id: str) -> str:
    """Read a confirmed optional-provider preference without treating it as install state."""
    if component_id == "face-analysis":
        return read_face_model_root(appdata)
    target = settings_path(appdata)
    if not target.is_file():
        return ""
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        paths = value.get("install_manager_provider_paths", {}) if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return ""
    return str(paths.get(component_id, "")) if isinstance(paths, dict) else ""


def write_face_model_root(appdata: Path, model_root: Path | str) -> Path:
    """Atomically update only LIC's canonical path, preserving all other keys."""
    target = settings_path(appdata)
    with _write_lock(target):
        value: dict[str, object] = {}
        if target.is_file():
            candidate = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise ValueError("LIC settings document is not an object")
            value = candidate
        value["face_model_root"] = str(model_root)
        temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    return target


def write_provider_location(appdata: Path, component_id: str, provider_root: Path | str) -> Path:
    """Atomically retain only a successfully validated provider-facing location."""
    if component_id == "face-analysis":
        return write_face_model_root(appdata, provider_root)
    target = settings_path(appdata)
    with _write_lock(target):
        value: dict[str, object] = {}
        if target.is_file():
            candidate = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise ValueError("LIC settings document is not an object")
            value = candidate
        paths = value.get("install_manager_provider_paths")
        if not isinstance(paths, dict):
            paths = {}
            value["install_manager_provider_paths"] = paths
        paths[component_id] = str(provider_root)
        temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    return target
