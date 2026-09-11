"""Independent application/model storage policy and verified model reuse status."""
from __future__ import annotations

from pathlib import Path
import os
import shutil

from .hf_source import hf_snapshot_target
from .model_resources import ModelSnapshot, load_model, verify_snapshot


def default_model_root(application_root: Path) -> Path:
    return application_root.expanduser().absolute() / "Shared/Models"


def validate_model_root(path: Path, *, required_bytes: int = 0,
                        max_length: int = 150) -> Path:
    """Validate a user-selected local model root without adopting or modifying it."""
    absolute = path.expanduser().absolute()
    resolved = absolute.resolve()
    if absolute != resolved or len(str(resolved)) > max_length or len(resolved.parts) < 3:
        raise ValueError("Choose a short, direct AI model folder without links")
    if str(resolved).startswith("\\\\"):
        raise ValueError("Network AI model folders are not qualified")
    for parent in (absolute, *absolute.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("AI model storage through a link or junction is unsupported")
    for name in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value:
            protected = Path(value).resolve()
            if resolved == protected or protected in resolved.parents:
                raise ValueError("Choose a standard-user AI model location")
    parent = resolved
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError("The AI model location has no usable parent folder")
    if required_bytes and shutil.disk_usage(parent).free < required_bytes:
        raise OSError("The selected AI model location does not have enough free space")
    return resolved


def model_from_delivery(delivery: Path) -> ModelSnapshot:
    return load_model(delivery / "recipes/florence2-large-ft.json")


def inspect_model_storage(delivery: Path, model_root: Path) -> dict:
    """Return concise reuse evidence. Unknown files are read only and never removed."""
    model = model_from_delivery(delivery)
    # An existing exact HF snapshot can be longer than a safe root under which
    # more repository/revision components would be appended.
    root = validate_model_root(model_root, max_length=240)
    snapshot = hf_snapshot_target(root, model)
    expected_bytes = sum(item.size for item in model.files)
    candidates = _bounded_hf_candidates(root, model)
    if len(candidates) > 1:
        return {"status": "ambiguous", "compatible": False, "reusable": False,
                "message": "More than one compatible Florence model was found. Choose the exact model folder.",
                "model_root": str(root), "candidates": [str(item) for item in candidates],
                "expected_bytes": expected_bytes, "download_bytes": expected_bytes}
    if candidates:
        snapshot = candidates[0]
    hub_root = _hub_root_for_snapshot(snapshot, model)
    if not snapshot.exists():
        return {"status": "missing", "compatible": False, "reusable": False,
                "message": "Required model not found — it will be downloaded and verified.",
                "model_root": str(root), "snapshot": str(snapshot),
                "hub_root": str(hub_root),
                "expected_bytes": expected_bytes, "download_bytes": expected_bytes}
    try:
        evidence = verify_snapshot(model, snapshot, revision=model.revision, strict=False)
    except (OSError, ValueError) as error:
        return {"status": "incompatible", "compatible": False, "reusable": False,
                "message": "Files were found here, but they do not match the verified model version required by LIC.",
                "reason": str(error), "model_root": str(root), "snapshot": str(snapshot),
                "expected_bytes": expected_bytes, "download_bytes": expected_bytes}
    if hub_root is None:
        return {"status": "compatible", "compatible": True, "reusable": True,
                "message": "Existing compatible Florence image-analysis model found.",
                "model_root": str(root), "snapshot": str(snapshot), "hub_root": str(snapshot.parent),
                "flat_snapshot": True, "expected_bytes": expected_bytes, "download_bytes": 0,
                "revision": evidence["revision"], "manifest_sha256": evidence["manifest_sha256"]}
    return {"status": "compatible", "compatible": True, "reusable": True,
            "message": "Existing compatible Florence image-analysis model found.",
            "model_root": str(root), "snapshot": str(snapshot),
            "hub_root": str(hub_root),
            "expected_bytes": expected_bytes, "download_bytes": 0,
            "revision": evidence["revision"], "manifest_sha256": evidence["manifest_sha256"]}


def _hub_root_for_snapshot(snapshot: Path, model: ModelSnapshot) -> Path | None:
    """Return the exact HF_HUB_CACHE directory represented by a verified snapshot path."""
    repository = "models--" + model.repository.replace("/", "--")
    for parent in (snapshot, *snapshot.parents):
        if parent.name == repository:
            return parent.parent.resolve()
    return None


def _bounded_hf_candidates(root: Path, model: ModelSnapshot) -> list[Path]:
    """Find only this pinned snapshot in known Hugging Face layouts.

    A selection can be the exact snapshot, the manager's shared-model root, its
    ``huggingface`` directory, a Hub cache directory, or the repository cache
    directory.  This intentionally never recursively scans arbitrary folders.
    """
    canonical = hf_snapshot_target(root, model)
    repo = "models--" + model.repository.replace("/", "--")
    revision = model.revision
    direct = (
        root,
        canonical,
        root / "snapshots" / revision,
        root / repo / "snapshots" / revision,
        root / "hub" / repo / "snapshots" / revision,
        root / "huggingface" / "hub" / repo / "snapshots" / revision,
    )
    candidates = []
    for candidate in direct:
        try:
            if candidate.is_dir() and verify_snapshot(model, candidate, revision=revision, strict=False):
                candidates.append(candidate.resolve())
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(candidates))


def storage_review(delivery: Path, application_root: Path, model_root: Path,
                   *, base_download_bytes: int = 100_000_000) -> dict:
    application = application_root.expanduser().absolute()
    model = inspect_model_storage(delivery, model_root)
    return {"application_root": str(application), "model_root": model["model_root"],
            "model": model, "download_bytes": base_download_bytes,
            "disk_requirement_gib": 2, "locations_independent": True}
