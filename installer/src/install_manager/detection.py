"""Bounded exact-path discovery for existing resources."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import ResourceCandidate, ResourceState, normalized_path


def _hash(path: Path) -> str:
    """Hash one explicitly supplied file; no directory walk is performed."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def detect_explicit(paths: tuple[Path, ...] | list[Path], *, kind: str = "resource") -> tuple[ResourceCandidate, ...]:
    """Probe only caller-supplied paths and return deterministic candidate facts."""
    candidates: list[ResourceCandidate] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        exists = resolved.exists()
        is_file = resolved.is_file()
        candidates.append(ResourceCandidate(
            resource_id=f"{kind}:{normalized_path(resolved)}", kind=kind,
            path=normalized_path(resolved), state=ResourceState.DETECTED,
            exists=exists, is_file=is_file,
            sha256=_hash(resolved) if is_file else "",
            size=resolved.stat().st_size if is_file else None,
            discovery="explicit", ownership="external" if exists else "unknown",
            compatible=None, reason="present at explicitly supplied path" if exists else "not found",
        ))
    return tuple(sorted(candidates, key=lambda item: (item.kind, item.path.casefold(), item.path)))
