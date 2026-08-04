"""Read-only model/setup facts shared by provider launch preflights.

The application treats package installation and model acquisition as different
security boundaries.  Package/runtime changes belong to the existing setup
assistant, while a provider Run command may offer a model-only download after
the user sees its publisher, approximate size, destination, and license terms.

Every inspection in this module is offline.  In particular, checking the
Florence cache must never contact Hugging Face merely because the Analyze tab
was opened or its Run button was pressed.
"""

from __future__ import annotations

import json
import os

from dataclasses import dataclass
from pathlib import Path

from provider_registry import get_component


FLORENCE_COMPONENT = get_component("florence_model")
FLORENCE_MODEL_NAME = str(FLORENCE_COMPONENT["artifact"])
FLORENCE_MODEL_REVISION = str(FLORENCE_COMPONENT["revision"])


@dataclass(frozen=True, slots=True)
class FlorenceCacheStatus:
    """Describe whether the exact reviewed Florence snapshot is locally usable."""

    cache_root: Path
    snapshot_path: Path
    model_ready: bool
    missing_requirements: tuple[str, ...]


def format_download_size(byte_count: int | None) -> str:
    """Return a user-facing approximate download size from registry metadata."""
    if byte_count is None:
        return "size varies"
    gibibytes = byte_count / (1024**3)
    if gibibytes >= 1:
        return f"approximately {gibibytes:.2f} GiB"
    return f"approximately {byte_count / (1024**2):.0f} MiB"


def _huggingface_cache_root() -> Path:
    """Resolve Hugging Face's cache without importing or contacting its client.

    The environment-variable precedence mirrors ``huggingface_hub``.  Avoiding
    the client import keeps this check useful for setup diagnostics and makes
    the no-network guarantee obvious at the boundary.
    """
    explicit_hub = os.environ.get("HF_HUB_CACHE", "").strip()
    if explicit_hub:
        return Path(explicit_hub).expanduser().resolve()
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return (Path(hf_home).expanduser() / "hub").resolve()
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return (base / "huggingface" / "hub").resolve()


def _weight_files_are_complete(snapshot: Path) -> tuple[bool, str]:
    """Accept one safetensors file or a complete safetensors shard index."""
    single = snapshot / "model.safetensors"
    if single.is_file() and single.stat().st_size > 0:
        return True, ""

    index_path = snapshot / "model.safetensors.index.json"
    if not index_path.is_file():
        return False, "model.safetensors"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        weight_map = index.get("weight_map", {})
        shard_names = {str(value) for value in weight_map.values()}
    except (OSError, json.JSONDecodeError, AttributeError):
        return False, "readable model.safetensors.index.json"
    if not shard_names:
        return False, "safetensors weight shards"
    missing = [
        name
        for name in sorted(shard_names)
        if not (snapshot / name).is_file() or (snapshot / name).stat().st_size <= 0
    ]
    if missing:
        return False, "complete safetensors weight shards"
    return True, ""


def inspect_florence_cache() -> FlorenceCacheStatus:
    """Inspect the pinned Florence snapshot by filesystem reads only.

    Hugging Face stores immutable revisions beneath a deterministic
    ``models--owner--repository/snapshots/revision`` directory.  The preflight
    checks the files required by the reviewed native Transformers loader,
    including a complete safetensors payload, but never creates cache entries
    and never asks a provider whether newer files exist.
    """
    cache_root = _huggingface_cache_root()
    repository_directory = "models--" + FLORENCE_MODEL_NAME.replace("/", "--")
    snapshot = cache_root / repository_directory / "snapshots" / FLORENCE_MODEL_REVISION

    missing: list[str] = []
    for filename in ("config.json", "preprocessor_config.json", "tokenizer_config.json"):
        path = snapshot / filename
        if not path.is_file() or path.stat().st_size <= 0:
            missing.append(filename)

    tokenizer_json = snapshot / "tokenizer.json"
    split_tokenizer = (snapshot / "vocab.json", snapshot / "merges.txt")
    if not (
        tokenizer_json.is_file()
        or all(path.is_file() and path.stat().st_size > 0 for path in split_tokenizer)
    ):
        missing.append("tokenizer.json or vocab.json + merges.txt")

    weights_ready, weight_requirement = _weight_files_are_complete(snapshot)
    if not weights_ready:
        missing.append(weight_requirement)

    return FlorenceCacheStatus(
        cache_root=cache_root,
        snapshot_path=snapshot,
        model_ready=not missing,
        missing_requirements=tuple(missing),
    )
