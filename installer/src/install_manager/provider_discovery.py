"""Bounded, read-only discovery of approved provider resources."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .component_catalog import ComponentDefinition
from .model_resources import load_model, verify_snapshot


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider_root: Path
    resource_path: Path


def _children(root: Path) -> tuple[Path, ...]:
    """Return a deliberately small, deterministic set of local folders."""
    conventional = (root / name for name in ("MediaPipe", "models", "body", "face", "Face Analysis",
                                               "Face Analysis - OpenCV YuNet + SFace", "huggingface", "hub"))
    immediate = tuple(sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.casefold())[:32])
    return tuple(dict.fromkeys((root, *conventional, *immediate)))


def _accepted_task(definition: ComponentDefinition, path: Path) -> bool:
    import hashlib
    expected = {str(definition.identity.get("sha256", "")),
                *map(str, definition.identity.get("accepted_equivalent_sha256s", ())) }
    expected.discard("")
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() in expected


def _accepted_face(definition: ComponentDefinition, path: Path) -> bool:
    import hashlib
    if not path.is_dir():
        return False
    for artifact in definition.artifacts:
        candidate = path / str(artifact["filename"])
        if (not candidate.is_file() or candidate.stat().st_size != int(artifact["size"]) or
                hashlib.sha256(candidate.read_bytes()).hexdigest() != str(artifact["sha256"])):
            return False
    return True


def _florence_candidates(delivery: Path, root: Path) -> tuple[ProviderCandidate, ...]:
    model = load_model(delivery / "recipes/florence2-large-ft.json")
    repository = "models--" + model.repository.replace("/", "--")
    revision = model.revision
    # Fixed cache shapes plus a shallow conventional-root probe.  No arbitrary recursion.
    probes = [root, root / "snapshots" / revision, root / repository / "snapshots" / revision,
              root / "hub" / repository / "snapshots" / revision,
              root / "huggingface" / "hub" / repository / "snapshots" / revision]
    for child in _children(root):
        probes.extend((child / "snapshots" / revision, child / repository / "snapshots" / revision,
                       child / "hub" / repository / "snapshots" / revision))
    result = []
    for candidate in dict.fromkeys(probes):
        try:
            if candidate.is_dir():
                verify_snapshot(model, candidate, revision=model.revision, strict=False)
                result.append(ProviderCandidate(candidate.resolve(), candidate.resolve()))
        except (OSError, ValueError):
            continue
    return tuple(dict.fromkeys(result))


def discover_provider_candidates(delivery: Path, definition: ComponentDefinition,
                                 selected_root: Path) -> tuple[ProviderCandidate, ...]:
    """Find exact approved resources below a user-selected folder; never writes or fetches."""
    root = selected_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing provider folder.")
    if definition.component_id == "florence-captioning":
        return _florence_candidates(delivery, root)
    if definition.component_id == "video-extraction":
        # Direct folder plus its conventional bin child; never arbitrary recursion.
        folders = tuple(dict.fromkeys((root, root / "bin")))
        return tuple(ProviderCandidate(folder.resolve(), (folder / "ffmpeg.exe").resolve())
                     for folder in folders if (folder / "ffmpeg.exe").is_file())
    candidates: list[ProviderCandidate] = []
    for folder in _children(root):
        if definition.component_id == "body-analysis":
            task = folder / "pose_landmarker_full.task"
            if _accepted_task(definition, task):
                candidates.append(ProviderCandidate(folder.resolve(), task.resolve()))
        elif definition.component_id == "face-analysis" and _accepted_face(definition, folder):
            candidates.append(ProviderCandidate(folder.resolve(), folder.resolve()))
    return tuple(dict.fromkeys(candidates))


def discovery_message(definition: ComponentDefinition, root: Path) -> str:
    required = ("the approved Florence snapshot" if definition.component_id == "florence-captioning" else
                "pose_landmarker_full.task" if definition.component_id == "body-analysis" else
                "ffmpeg.exe in this folder or its bin folder" if definition.component_id == "video-extraction" else
                "the approved YuNet and SFace model files")
    return (f"No approved {required} was found in {root}. The Manager checked this folder and a bounded set "
            "of immediate provider folders. No files were changed and no network activity occurred.")
