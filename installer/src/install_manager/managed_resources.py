"""Canonical managed resource library and bounded copy-only Import operations."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import zipfile
from typing import Callable

from .bootstrap_layout import layout
from .compatibility_profiles import recommended_profile
from .component_adapters import artifact_descriptor


LIBRARY_SCHEMA_VERSION = 1
MAX_SCAN_FILES = 2048
MAX_SCAN_DEPTH = 5


@dataclass(frozen=True, slots=True)
class ManagedResourceSpec:
    identity: str
    display_name: str
    kind: str
    component_ids: tuple[str, ...]
    destination: Path
    filename: str
    size: int | None
    hashes: tuple[tuple[str, str], ...]
    artifact: dict
    archive_members: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    spec: ManagedResourceSpec
    source: Path
    state: str


@dataclass(frozen=True, slots=True)
class ImportIssue:
    source: Path
    reason: str


@dataclass(frozen=True, slots=True)
class ImportPlan:
    root: Path
    candidates: tuple[ImportCandidate, ...]
    invalid: tuple[ImportIssue, ...]
    unrecognized_count: int

    @property
    def copy_bytes(self) -> int:
        return sum(item.spec.size or 0 for item in self.candidates if item.state in {"new", "replace"})


def managed_data_layout(root: Path) -> dict[str, Path]:
    """Return deterministic optional-resource areas under one LIC root."""
    paths = layout(root.resolve())
    return {name: paths[f"data_{name}"] for name in
            ("downloads", "models", "tasks", "packages", "tools", "state")}


def resource_library_path(root: Path) -> Path:
    return managed_data_layout(root)["state"] / "resources.json"


def _digest(path: Path, kind: str = "sha256") -> str:
    if kind == "sha256":
        digest = hashlib.sha256()
    elif kind == "git-blob-sha1":
        digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    else:
        raise ValueError(f"unsupported digest kind: {kind}")
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_destination(root: Path, component_id: str, resource: dict) -> Path:
    areas = managed_data_layout(root)
    descriptor = artifact_descriptor(resource)
    kind = str(resource["kind"])
    if kind == "tool-directory":
        return areas["tools"] / "ffmpeg" / descriptor.version
    if component_id == "body-analysis":
        return areas["tasks"] / "mediapipe" / descriptor.version / descriptor.filename
    return areas["models"] / descriptor.publisher.casefold().replace(" ", "-") / descriptor.version / descriptor.filename


def canonical_resource_destination(root: Path, resource: dict, *, component_id: str = "") -> Path:
    """Project an immutable manifest resource into the canonical Data library."""
    descriptor = artifact_descriptor(resource)
    if not component_id:
        artifact_id = descriptor.artifact_id
        component_id = ("body-analysis" if artifact_id.startswith("mediapipe-") else
                        "video-extraction" if artifact_id.startswith("ffmpeg-") else
                        "face-analysis")
    return _resource_destination(root.resolve(), component_id, resource)


def canonical_package_destination(root: Path, package) -> Path:
    return (managed_data_layout(root)["packages"] / package.name / package.version /
            package.filename)


def _merge_spec(specs: dict[str, ManagedResourceSpec], spec: ManagedResourceSpec) -> None:
    prior = specs.get(spec.identity)
    if prior is None:
        specs[spec.identity] = spec
        return
    if (prior.destination, prior.hashes, prior.filename) != (spec.destination, spec.hashes, spec.filename):
        raise ValueError(f"managed resource identity conflict: {spec.identity}")
    specs[spec.identity] = ManagedResourceSpec(
        prior.identity, prior.display_name, prior.kind,
        tuple(dict.fromkeys((*prior.component_ids, *spec.component_ids))), prior.destination,
        prior.filename, prior.size, prior.hashes, prior.artifact, prior.archive_members)


def approved_resource_specs(delivery: Path, root: Path) -> tuple[ManagedResourceSpec, ...]:
    """Resolve exact profile resources into one de-duplicated physical library."""
    profile = recommended_profile(delivery / "recipes/compatibility/profiles")
    specs: dict[str, ManagedResourceSpec] = {}
    for manifest in profile.components.values():
        for package in manifest.packages:
            identity = f"wheel:{package.name}:{package.version}:{package.sha256}"
            spec = ManagedResourceSpec(
                identity, f"{package.name} {package.version} package", "package",
                (manifest.component_id,), canonical_package_destination(root, package),
                package.filename, package.size, (("sha256", package.sha256),),
                {"artifact_id": package.artifact_id, "version": package.version,
                 "filename": package.filename, "sha256": package.sha256,
                 "source": package.source_name})
            _merge_spec(specs, spec)
        if manifest.raw.get("schema_version") == 2:
            for resource in manifest.raw.get("resources", ()):
                descriptor = artifact_descriptor(resource)
                validation = resource["validation"]
                hashes = [str(validation.get("accepted_sha256") or descriptor.expected_sha256),
                          *map(str, validation.get("accepted_equivalent_sha256s", ()))]
                identity = f"artifact:{descriptor.artifact_id}:{descriptor.version}"
                _merge_spec(specs, ManagedResourceSpec(
                    identity, str(resource["resource_id"]).replace("-", " ").title(),
                    str(resource["kind"]), (manifest.component_id,),
                    _resource_destination(root, manifest.component_id, resource), descriptor.filename,
                    descriptor.expected_size, tuple(("sha256", value) for value in dict.fromkeys(hashes) if value),
                    descriptor.as_dict(), tuple(resource["installation"].get("members", ()))))

    model = json.loads((delivery / "recipes/florence2-large-ft.json").read_text(encoding="utf-8"))
    repository = "models--" + str(model["repository"]).replace("/", "--")
    snapshot = managed_data_layout(root)["models"] / "huggingface" / "hub" / repository / "snapshots" / model["revision"]
    for item in model["files"]:
        rel = PurePosixPath(item["path"])
        identity = f"florence:{model['revision']}:{rel.as_posix()}"
        _merge_spec(specs, ManagedResourceSpec(
            identity, f"Florence {rel.name}", "model-file", ("florence-captioning",),
            snapshot / Path(*rel.parts), rel.name, int(item["size"]),
            ((str(item["digest_kind"]), str(item["digest"])),),
            {"repository": model["repository"], "revision": model["revision"],
             "path": rel.as_posix(), "digest": item["digest"],
             "digest_kind": item["digest_kind"]}))
    return tuple(sorted(specs.values(), key=lambda item: item.identity))


def _bounded_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root.resolve(),)
    base = root.resolve()
    found: list[Path] = []
    for current, directories, files in os.walk(base, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(base).parts)
        directories[:] = sorted(d for d in directories
                                 if depth < MAX_SCAN_DEPTH and not (current_path / d).is_symlink())[:64]
        for name in sorted(files):
            path = current_path / name
            if not path.is_symlink():
                found.append(path)
                if len(found) >= MAX_SCAN_FILES:
                    return tuple(found)
    return tuple(found)


def _destination_state(spec: ManagedResourceSpec) -> str:
    if spec.archive_members:
        if not spec.destination.exists():
            return "new"
        if not spec.destination.is_dir():
            return "replace"
        for item in spec.archive_members:
            member = spec.destination / str(item["destination"])
            if not member.is_file() or member.stat().st_size != int(item["size"]) or _digest(member) != item["sha256"]:
                return "replace"
        return "already-present"
    if not spec.destination.exists():
        return "new"
    if spec.destination.is_file() and any(_digest(spec.destination, kind) == value for kind, value in spec.hashes):
        return "already-present"
    return "replace"


def _valid_file(path: Path, spec: ManagedResourceSpec) -> bool:
    if spec.size is not None and path.stat().st_size != spec.size:
        return False
    return any(_digest(path, kind) == value for kind, value in spec.hashes)


def discover_import(delivery: Path, root: Path, selected: Path) -> ImportPlan:
    """Scan a bounded tree and classify exact approved resources without writing."""
    selected = selected.expanduser().resolve()
    if not selected.exists():
        raise FileNotFoundError(selected)
    specs = approved_resource_specs(delivery, root)
    by_name: dict[str, list[ManagedResourceSpec]] = {}
    for spec in specs:
        by_name.setdefault(spec.filename.casefold(), []).append(spec)
    candidates: dict[str, ImportCandidate] = {}
    invalid: list[ImportIssue] = []
    files = _bounded_files(selected)
    recognized_paths: set[Path] = set()
    for path in files:
        matches = by_name.get(path.name.casefold(), ())
        if not matches:
            continue
        recognized_paths.add(path)
        valid = False
        for spec in matches:
            if spec.archive_members and path.suffix.casefold() != ".zip":
                continue
            if _valid_file(path, spec):
                candidates.setdefault(spec.identity, ImportCandidate(spec, path, _destination_state(spec)))
                valid = True
        if not valid:
            invalid.append(ImportIssue(path, "Recognized filename, but size or approved identity does not match."))

    # An extracted FFmpeg build is accepted only when every approved member has
    # the immutable member identity from the pinned archive manifest.
    for spec in (item for item in specs if item.archive_members and item.identity not in candidates):
        matched: dict[str, Path] = {}
        for item in spec.archive_members:
            possible = [path for path in files if path.name.casefold() == Path(item["destination"]).name.casefold()
                        and path.stat().st_size == int(item["size"]) and _digest(path) == item["sha256"]]
            if possible:
                matched[str(item["destination"])] = possible[0]
        if len(matched) == len(spec.archive_members):
            common = Path(os.path.commonpath([str(path.parent) for path in matched.values()]))
            candidates[spec.identity] = ImportCandidate(spec, common, _destination_state(spec))
            recognized_paths.update(matched.values())
    return ImportPlan(root.resolve(), tuple(candidates.values()), tuple(invalid),
                      sum(path not in recognized_paths for path in files))


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_resource_library(root: Path) -> dict:
    path = resource_library_path(root)
    if not path.is_file():
        return {"schema_version": LIBRARY_SCHEMA_VERSION, "installation_root": root.resolve().as_posix(),
                "resources": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != LIBRARY_SCHEMA_VERSION or Path(value["installation_root"]).resolve() != root.resolve():
        raise ValueError("managed resource library belongs to another installation")
    return value


def synchronize_resource_library(delivery: Path, root: Path) -> Path:
    """Record every canonical resource that currently passes immutable validation."""
    library = load_resource_library(root)
    records = {item["identity"]: item for item in library["resources"]}
    for spec in approved_resource_specs(delivery, root):
        if _destination_state(spec) != "already-present":
            continue
        records[spec.identity] = {
            "identity": spec.identity, "name": spec.display_name, "kind": spec.kind,
            "component_ids": list(spec.component_ids),
            "path": spec.destination.resolve().as_posix(), "artifact": spec.artifact,
            "validation": {"passed": True, "source": "managed-storage"},
        }
    library["resources"] = sorted(records.values(), key=lambda item: item["identity"])
    target = resource_library_path(root)
    _atomic_json(target, library)
    return target


def promote_package(delivery: Path, root: Path, source: Path, artifact_id: str,
                    version: str, sha256: str) -> Path:
    """Atomically promote one verified package artifact into Data/Packages."""
    matches = [spec for spec in approved_resource_specs(delivery, root)
               if spec.kind == "package" and spec.artifact.get("artifact_id") == artifact_id
               and spec.artifact.get("version") == version
               and ("sha256", sha256) in spec.hashes]
    if len(matches) != 1:
        raise ValueError("verified package is not in the approved managed-resource profile")
    candidate = ImportCandidate(matches[0], source.resolve(), _destination_state(matches[0]))
    if candidate.state == "already-present":
        return candidate.spec.destination
    if candidate.state == "replace":
        raise ValueError("managed package destination conflicts with the approved identity")
    _publish_file(candidate, False)
    synchronize_resource_library(delivery, root)
    return candidate.spec.destination


def _publish_file(candidate: ImportCandidate, replace: bool) -> None:
    destination = candidate.spec.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".import-partial")
    temporary.unlink(missing_ok=True)
    shutil.copyfile(candidate.source, temporary)
    try:
        if not _valid_file(temporary, candidate.spec):
            raise ValueError("imported copy failed identity verification")
        if destination.exists() and not replace:
            raise FileExistsError(destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_archive_members(source: Path, spec: ManagedResourceSpec, partial: Path) -> None:
    if source.is_file():
        with zipfile.ZipFile(source) as archive:
            names = {item.filename.replace("\\", "/"): item for item in archive.infolist()}
            for item in spec.archive_members:
                info = names.get(str(item["source"]).replace("\\", "/"))
                if info is None or info.file_size != int(item["size"]):
                    raise ValueError("approved FFmpeg archive member is missing")
                target = partial / str(item["destination"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                if _digest(target) != item["sha256"]:
                    raise ValueError("approved FFmpeg archive member changed")
            return
    files = _bounded_files(source)
    for item in spec.archive_members:
        match = next((path for path in files if path.name.casefold() == Path(item["destination"]).name.casefold()
                      and path.stat().st_size == int(item["size"]) and _digest(path) == item["sha256"]), None)
        if match is None:
            raise ValueError("extracted FFmpeg build is incomplete")
        target = partial / str(item["destination"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(match, target)


def _publish_directory(candidate: ImportCandidate, replace: bool) -> None:
    destination = candidate.spec.destination
    partial = destination.with_name(destination.name + ".import-partial")
    if partial.exists():
        raise FileExistsError(f"unfinished import requires review: {partial}")
    partial.mkdir(parents=True)
    backup = destination.with_name(destination.name + ".replaced")
    try:
        _copy_archive_members(candidate.source, candidate.spec, partial)
        if _destination_state(ManagedResourceSpec(
                candidate.spec.identity, candidate.spec.display_name, candidate.spec.kind,
                candidate.spec.component_ids, partial, candidate.spec.filename, candidate.spec.size,
                candidate.spec.hashes, candidate.spec.artifact, candidate.spec.archive_members)) != "already-present":
            raise ValueError("imported FFmpeg copy failed verification")
        if destination.exists():
            if not replace:
                raise FileExistsError(destination)
            if backup.exists():
                raise FileExistsError(f"replacement backup requires review: {backup}")
            os.replace(destination, backup)
        os.replace(partial, destination)
        if backup.is_dir():
            shutil.rmtree(backup)
        elif backup.exists():
            backup.unlink()
    except BaseException:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        if partial.is_dir():
            shutil.rmtree(partial)


def import_resources(delivery: Path, root: Path, selected: Path, *,
                     confirm_import: Callable[[ImportPlan], bool] = lambda plan: True,
                     confirm_replace: Callable[[ImportCandidate], bool] = lambda item: False,
                     progress: Callable[[dict], None] = lambda event: None,
                     pause_requested: Callable[[], bool] = lambda: False) -> dict:
    """Copy approved resources into managed storage after explicit confirmation."""
    progress({"phase": "scanning", "message": "Scanning for compatible resources…"})
    plan = discover_import(delivery, root, selected)
    actionable = tuple(item for item in plan.candidates if item.state != "already-present")
    if actionable and not confirm_import(plan):
        return {"state": "declined", "imported": [], "already_present": [], "invalid": len(plan.invalid)}
    library = load_resource_library(root)
    records = {item["identity"]: item for item in library["resources"]}
    imported, already, declined = [], [], []
    for index, candidate in enumerate(plan.candidates, 1):
        if pause_requested():
            from .acquisition import AcquisitionCancelled
            raise AcquisitionCancelled("Import paused at a safe file boundary")
        progress({"phase": "validating", "message": f"Validating {candidate.spec.display_name}…",
                  "current": index, "total": len(plan.candidates)})
        if candidate.state == "already-present":
            already.append(candidate.spec.identity)
        else:
            replace = candidate.state == "replace"
            if replace and not confirm_replace(candidate):
                declined.append(candidate.spec.identity)
                continue
            progress({"phase": "copying", "message": f"Copying {candidate.spec.display_name}…",
                      "current": index, "total": len(plan.candidates)})
            if candidate.spec.archive_members:
                _publish_directory(candidate, replace)
            else:
                _publish_file(candidate, replace)
            imported.append(candidate.spec.identity)
        records[candidate.spec.identity] = {
            "identity": candidate.spec.identity, "name": candidate.spec.display_name,
            "kind": candidate.spec.kind, "component_ids": list(candidate.spec.component_ids),
            "path": candidate.spec.destination.resolve().as_posix(),
            "artifact": candidate.spec.artifact,
            "validation": {"passed": True, "source": "managed-copy"}}
        # A safe-boundary Pause must leave recognized durable evidence for
        # every copy it preserves, including Imports performed before Core.
        library["resources"] = sorted(records.values(), key=lambda item: item["identity"])
        _atomic_json(resource_library_path(root), library)
    progress({"phase": "verifying", "message": "Verifying imported files…"})
    if pause_requested():
        from .acquisition import AcquisitionCancelled
        raise AcquisitionCancelled("Import paused after verified copies were preserved")
    progress({"phase": "complete", "message": "Import complete."})
    return {"state": "complete", "imported": imported, "already_present": already,
            "replacement_declined": declined, "invalid": len(plan.invalid),
            "unrecognized": plan.unrecognized_count, "copy_bytes": plan.copy_bytes}


def component_resource_status(delivery: Path, root: Path, component_id: str) -> dict:
    specs = tuple(item for item in approved_resource_specs(delivery, root)
                  if component_id in item.component_ids)
    library = load_resource_library(root)
    indexed = {item["identity"]: item for item in library["resources"]}
    available, missing = [], []
    for spec in specs:
        record = indexed.get(spec.identity)
        # The JSON is durable evidence, but exact files remain authoritative and
        # allow recovery if state publication was interrupted.
        valid = _destination_state(spec) == "already-present"
        (available if valid else missing).append(spec)
    return {"component_id": component_id, "available": tuple(available), "missing": tuple(missing),
            "resource_complete": bool(specs) and not missing}


def managed_artifact_path(delivery: Path, root: Path, artifact_id: str,
                          version: str, sha256: str) -> Path | None:
    for spec in approved_resource_specs(delivery, root):
        if (spec.artifact.get("artifact_id") == artifact_id and spec.artifact.get("version") == version
                and any(kind == "sha256" and value == sha256 for kind, value in spec.hashes)
                and _destination_state(spec) == "already-present"):
            return spec.destination
    return None
