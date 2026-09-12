"""Narrow adapters from managed resources to LIC's canonical settings."""
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
    """Compatibility reader; only LIC's canonical Face setting remains active."""
    if component_id == "face-analysis":
        return read_face_model_root(appdata)
    return ""


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


def write_body_model_path(appdata: Path, model_path: Path | str) -> Path:
    """Point LIC at its manager-owned MediaPipe task without external-path state."""
    target = settings_path(appdata)
    with _write_lock(target):
        value: dict[str, object] = {}
        if target.is_file():
            candidate = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise ValueError("LIC settings document is not an object")
            value = candidate
        value["body_model_path"] = str(model_path)
        temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    return target


def write_provider_location(appdata: Path, component_id: str, provider_root: Path | str) -> Path:
    """Compatibility writer; arbitrary provider-path preferences are retired."""
    if component_id == "face-analysis":
        return write_face_model_root(appdata, provider_root)
    raise ValueError("arbitrary provider-path preferences are no longer supported; use Import")
