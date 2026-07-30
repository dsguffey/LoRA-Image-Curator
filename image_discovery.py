"""Shared, defensive image discovery for every LoRA Image Curator workflow.

All providers and metadata-only imports must agree about what constitutes a
source image. Keeping that policy in one dependency-light module prevents an
internal preview or future generated artifact from being accepted by one path
and rejected by another.

The legacy ``thumbnail_cache`` signature is intentionally narrow. Dataset
Tools v0.19.0 wrote previews named ``<24 hex chars>_<size>.webp`` beneath that
folder. Recognizing both the directory name and the generated filename lets
v0.20.0 ignore and repair its own previews without treating an unrelated user
folder or ordinary WebP image as application-owned data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


SUPPORTED_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)
LEGACY_THUMBNAIL_CACHE_FOLDER = "thumbnail_cache"


def is_legacy_thumbnail_cache_path(path: str | Path) -> bool:
    """Return whether *path* has LoRA Image Curator's exact legacy preview signature."""
    normalized = str(path).replace("\\", "/")
    pieces = tuple(piece for piece in normalized.split("/") if piece)
    if len(pieces) < 2:
        return False
    if LEGACY_THUMBNAIL_CACHE_FOLDER not in {
        piece.casefold() for piece in pieces[:-1]
    }:
        return False

    candidate = Path(pieces[-1])
    if candidate.suffix.casefold() != ".webp":
        return False
    try:
        digest_prefix, size_text = candidate.stem.rsplit("_", 1)
    except ValueError:
        return False
    return (
        len(digest_prefix) == 24
        and all(character in "0123456789abcdefABCDEF" for character in digest_prefix)
        and size_text.isdigit()
        and int(size_text) > 0
    )


def discover_supported_images(
    folder: Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    """Return supported user images in deterministic order.

    Internal generated previews are excluded even when a user selects a broad
    source folder that contains a legacy LoRA Image Curator cache.
    """
    source = folder.expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise NotADirectoryError(f"Image folder not found: {source}")

    candidates: Iterable[Path] = source.rglob("*") if recursive else source.iterdir()
    return sorted(
        (
            path.resolve()
            for path in candidates
            if path.is_file()
            and path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS
            and not is_legacy_thumbnail_cache_path(path.relative_to(source))
        ),
        key=lambda path: str(path).casefold(),
    )
