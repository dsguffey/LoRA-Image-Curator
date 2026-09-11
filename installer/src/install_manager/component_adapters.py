"""Small trusted adapters for component resources; manifests cannot name commands."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import replace

from .artifacts import AcquiredArtifact, ArtifactDescriptor, load_artifact_descriptor
from .child_process import run as child_run
from .model_resources import ensure_model, verify_snapshot
from .hf_source import hf_snapshot_target


INSTALL_ADAPTERS = {"verified-file-copy-v1", "verified-zip-members-v1"}
VALIDATION_ADAPTERS = {"sha256-file-v1", "yunet-sface-pair-v1", "mediapipe-pose-task-v1",
                       "ffmpeg-lic-video-v1", "huggingface-snapshot-v1",
                       "managed-tree-v1", "approved-profile-venv-v1"}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def artifact_descriptor(resource: dict) -> ArtifactDescriptor:
    artifact = resource.get("artifact")
    if not isinstance(artifact, dict):
        raise ValueError("managed resource requires one exact artifact")
    expected = {"artifact_id", "version", "filename", "url", "expected_sha256",
                "publisher", "source_name", "license_id", "expected_size"}
    if set(artifact) != expected:
        raise ValueError("invalid managed resource artifact descriptor")
    return ArtifactDescriptor.from_dict(artifact)


def acquisition_hosts(descriptor: ArtifactDescriptor) -> tuple[str, ...]:
    host = (descriptor.url.split("/", 3)[2]).casefold()
    policies = {
        "storage.googleapis.com": ("storage.googleapis.com",),
        "github.com": ("github.com", "release-assets.githubusercontent.com"),
        "files.pythonhosted.org": ("files.pythonhosted.org",),
        "download-r2.pytorch.org": ("download-r2.pytorch.org",),
        "raw.githubusercontent.com": ("raw.githubusercontent.com",),
    }
    if host not in policies:
        raise ValueError("resource source has no approved acquisition policy")
    return policies[host]


def resource_destination(root: Path, resource: dict) -> Path:
    installation = resource.get("installation")
    if not isinstance(installation, dict) or installation.get("adapter") not in INSTALL_ADAPTERS:
        raise ValueError("resource installation adapter is not approved")
    relative = PurePosixPath(str(installation.get("relative_path", "")))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("resource destination must be a safe relative path")
    root = root.resolve()
    destination = (root / Path(*relative.parts)).resolve()
    if root not in destination.parents:
        raise ValueError("resource destination escapes the installation root")
    return destination


def _copy_verified_file(source: Path, destination: Path, expected: str) -> dict:
    if destination.is_file():
        if _sha256(destination) != expected:
            raise ValueError("existing managed resource conflicts with the approved artifact")
        return {"path": str(destination), "sha256": expected, "reused": True}
    if destination.exists():
        raise ValueError("managed resource destination exists but is not a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{uuid.uuid4().hex}.partial")
    try:
        shutil.copyfile(source, temporary)
        if _sha256(temporary) != expected:
            raise ValueError("copied resource failed verification")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(destination), "sha256": expected, "reused": False}


def _safe_archive_names(archive: zipfile.ZipFile) -> None:
    for item in archive.infolist():
        relative = PurePosixPath(item.filename.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("archive contains an unsafe member path")
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError("archive symbolic links are not allowed")


def _install_zip_members(source: Path, destination: Path, installation: dict) -> dict:
    members = installation.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("archive adapter requires an explicit member allowlist")
    expected_keys = {"source", "destination", "size", "sha256"}
    if any(not isinstance(item, dict) or set(item) != expected_keys for item in members):
        raise ValueError("invalid archive member descriptor")
    expected_tree = {str(item["destination"]): item for item in members}
    if destination.is_dir():
        for relative, item in expected_tree.items():
            target = destination / relative
            if (not target.is_file() or target.stat().st_size != int(item["size"])
                    or _sha256(target) != str(item["sha256"])):
                raise ValueError("existing managed tool directory conflicts with its manifest")
        return {"path": str(destination), "files": len(members), "reused": True}
    if destination.exists():
        raise ValueError("managed tool destination exists but is not a directory")
    partial = destination.with_name(destination.name + f".partial-{uuid.uuid4().hex}")
    partial.mkdir(parents=True)
    try:
        with zipfile.ZipFile(source) as archive:
            _safe_archive_names(archive)
            archive_names = {item.filename.replace("\\", "/"): item
                             for item in archive.infolist()}
            for item in members:
                source_name = str(item["source"]).replace("\\", "/")
                info = archive_names.get(source_name)
                if info is None or info.is_dir() or info.file_size != int(item["size"]):
                    raise ValueError("approved archive member is missing or has the wrong size")
                relative = PurePosixPath(str(item["destination"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("archive member destination is unsafe")
                target = partial / Path(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                if _sha256(target) != str(item["sha256"]):
                    raise ValueError("installed archive member failed SHA-256 validation")
        os.replace(partial, destination)
    except BaseException:
        if partial.is_dir():
            shutil.rmtree(partial)
        raise
    return {"path": str(destination), "files": len(members), "reused": False}


def install_resource(root: Path, resource: dict,
                     acquired: AcquiredArtifact) -> dict:
    """Install one verified resource through a fixed, non-scriptable adapter."""
    if not acquired.verified:
        raise ValueError("unverified resource cannot be installed")
    descriptor = artifact_descriptor(resource)
    if acquired.actual_sha256 != descriptor.expected_sha256:
        raise ValueError("acquired resource identity changed")
    destination = resource_destination(root, resource)
    installation = resource["installation"]
    if installation["adapter"] == "verified-file-copy-v1":
        return _copy_verified_file(Path(acquired.cache_path), destination,
                                   descriptor.expected_sha256)
    if installation["adapter"] == "verified-zip-members-v1":
        return _install_zip_members(Path(acquired.cache_path), destination, installation)
    raise ValueError("resource installation adapter is not approved")


def _run(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return child_run(command, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=timeout, check=False)


def validate_resource(resource: dict, path: Path, *, python: Path | None = None,
                      application: Path | None = None) -> dict:
    validation = resource.get("validation")
    if not isinstance(validation, dict) or validation.get("adapter") not in VALIDATION_ADAPTERS:
        raise ValueError("resource validation adapter is not approved")
    adapter = validation["adapter"]
    path = path.resolve()
    accepted = {str(validation.get("accepted_sha256", "")),
                *map(str, validation.get("accepted_equivalent_sha256s", []))}
    accepted.discard("")
    if adapter in {"sha256-file-v1", "mediapipe-pose-task-v1"}:
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = _sha256(path)
        if accepted and digest not in accepted:
            raise ValueError("resource file does not have an accepted SHA-256")
        result = {"version": str(validation["version"]), "passed": True,
                  "sha256": digest, "size": path.stat().st_size}
        if adapter == "mediapipe-pose-task-v1":
            if python is None or application is None:
                raise ValueError("MediaPipe validation requires the managed runtime and LIC application")
            code = (
                "import json,sys;sys.path.insert(0,sys.argv[1]);"
                "from body_analysis import inspect_body_setup;from pathlib import Path;"
                "s=inspect_body_setup(Path(sys.argv[2]),perform_runtime_check=True);"
                "print(json.dumps({'ready':s.ready,'version':s.package_version,'sha256':s.model_sha256}))"
            )
            probe = _run([str(python), "-I", "-B", "-c", code,
                          str(application), str(path)], timeout=30)
            if probe.returncode:
                raise RuntimeError(probe.stderr.strip() or "MediaPipe task runtime probe failed")
            evidence = json.loads(probe.stdout)
            if not evidence.get("ready"):
                raise RuntimeError("MediaPipe rejected the installed task")
            result["runtime"] = evidence
        return result
    if adapter == "yunet-sface-pair-v1":
        # Each resource is installed as an immutable file.  Pair completeness
        # is checked by component_operations before publication.
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = _sha256(path)
        if digest != str(validation.get("accepted_sha256", "")):
            raise ValueError("YuNet/SFace resource does not have its approved SHA-256")
        return {"version": str(validation["version"]), "passed": True,
                "sha256": digest, "size": path.stat().st_size}
    if adapter == "ffmpeg-lic-video-v1":
        if path.is_dir():
            installation = resource.get("installation", {})
            for member in installation.get("members", ()):
                member_path = path / str(member["destination"])
                if (not member_path.is_file() or member_path.stat().st_size != int(member["size"])
                        or _sha256(member_path) != str(member["sha256"])):
                    raise ValueError("installed FFmpeg member identity changed")
        executable = path / "ffmpeg.exe" if path.is_dir() else path
        probe = _run([str(executable), "-version"])
        license_probe = _run([str(executable), "-L"])
        build = _run([str(executable), "-buildconf"])
        combined = "\n".join((probe.stdout, probe.stderr))
        license_text = "\n".join((license_probe.stdout, license_probe.stderr))
        build_text = "\n".join((build.stdout, build.stderr))
        forbidden = [flag for flag in validation.get("forbidden_configuration", ())
                     if flag in build_text]
        if (probe.returncode or "ffmpeg version" not in combined.casefold()
                or license_probe.returncode or "lesser general public license" not in license_text.casefold()
                or build.returncode or forbidden):
            raise RuntimeError("FFmpeg identity, LGPL, or build-configuration probe failed")
        with tempfile.TemporaryDirectory(prefix="install-manager-ffmpeg-probe-") as temporary:
            output = Path(temporary) / "frame.png"
            smoke = _run([str(executable), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                          "-i", "color=c=black:s=32x32:d=0.1", "-frames:v", "1", "-y", str(output)])
            if smoke.returncode or not output.is_file():
                raise RuntimeError(smoke.stderr.strip() or "FFmpeg frame output probe failed")
        return {"version": str(validation["version"]), "passed": True,
                "version_line": next(line for line in combined.splitlines() if line.strip()),
                "license": str(validation.get("license", "")),
                "forbidden_configuration_found": forbidden}
    if adapter in {"managed-tree-v1", "approved-profile-venv-v1"}:
        return {"version": str(validation["version"]), "passed": path.exists()}
    raise ValueError("resource validation requires its provider adapter")


def launch_environment(resources) -> dict[str, str]:
    """Project only fixed adapter-owned process bindings from generic resources."""
    environment: dict[str, str] = {}
    for resource in resources:
        path = Path(resource.local_path).resolve()
        if resource.adapter_id == "huggingface-snapshot-v1":
            parts = [part.casefold() for part in path.parts]
            if "hub" in parts:
                index = parts.index("hub")
                hub = Path(*path.parts[:index + 1])
            else:
                hub = path
            environment.update(HF_HUB_CACHE=str(hub), HF_HOME=str(hub.parent))
        elif resource.adapter_id in {"ffmpeg-lic-video-v1", "ffmpeg-executable"}:
            # The managed adapter records its installed tool directory, while the
            # external compatibility adapter records the selected executable.
            # Use that persisted contract instead of probing existence so launch
            # projection remains deterministic during plan and move validation.
            directory = path if resource.adapter_id == "ffmpeg-lic-video-v1" else path.parent
            environment["INSTALL_MANAGER_PATH_PREPEND"] = str(directory)
    return environment


def legacy_launch_environment(root: Path) -> dict[str, str]:
    """Isolate the M1.9 Florence record compatibility seam from generic launch."""
    path = root.resolve() / "State/components/florence.json"
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        hub = Path(record["hub_root"]).resolve()
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    if record.get("state") != "installed":
        return {}
    return {"HF_HUB_CACHE": str(hub), "HF_HOME": str(hub.parent)}


def _inside(root: Path, candidate: Path) -> bool:
    root, candidate = root.resolve(), candidate.resolve()
    return candidate == root or root in candidate.parents


def _copy_tree_verified(source: Path, destination: Path) -> None:
    if destination.is_dir():
        return
    if destination.exists():
        raise ValueError("managed resource destination conflicts with the move plan")
    partial = destination.with_name(destination.name + f".partial-{uuid.uuid4().hex}")
    partial.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, partial)
        os.replace(partial, destination)
    finally:
        if partial.is_dir():
            shutil.rmtree(partial)


def move_resource(delivery: Path, manifest, resource_state, source_root: Path,
                  destination_root: Path, selected_model_root: Path, *,
                  copy_shared_models: bool):
    """Apply ownership/disposition through fixed adapters and return relocated state."""
    source_path = Path(resource_state.local_path).resolve()
    if resource_state.ownership == "unknown/legacy":
        return resource_state
    if resource_state.ownership == "user-supplied/external":
        return resource_state
    if resource_state.ownership == "shared":
        if not copy_shared_models or resource_state.kind != "model":
            return resource_state
        if resource_state.adapter_id != "huggingface-snapshot-v1":
            raise ValueError("shared model has no approved copy adapter")
        values = manifest.raw.get("resources", ())
        if len(values) != 1 or not values[0].get("manifest"):
            raise ValueError("shared Hugging Face resource descriptor is incomplete")
        descriptor = load_artifact_descriptor(delivery / "recipes" / values[0]["manifest"])
        target = hf_snapshot_target(selected_model_root, descriptor)
        ensure_model(descriptor, target, approved_root=selected_model_root,
                     candidate=source_path, candidate_revision=descriptor.revision)
        return replace(resource_state, local_path=str(target))
    if resource_state.ownership != "manager-owned":
        raise ValueError("unsupported resource ownership")
    if not _inside(source_root, source_path):
        raise ValueError("manager-owned resource is outside its recorded installation")
    destination = destination_root.resolve() / source_path.relative_to(source_root.resolve())
    if resource_state.kind in {"application", "python-environment"}:
        return replace(resource_state, local_path=str(destination))
    if source_path.is_file():
        expected = str(resource_state.artifact.get("sha256") or _sha256(source_path))
        _copy_verified_file(source_path, destination, expected)
    elif source_path.is_dir():
        _copy_tree_verified(source_path, destination)
    else:
        raise FileNotFoundError("manager-owned move resource is missing: " + str(source_path))
    return replace(resource_state, local_path=str(destination))


def validate_moved_resource(delivery: Path, manifest, resource_state, *,
                            python: Path, application: Path) -> dict:
    """Validate moved/preserved state through fixed adapters, never manifest commands."""
    path = Path(resource_state.local_path).resolve()
    if resource_state.ownership in {"user-supplied/external", "unknown/legacy"}:
        if not path.exists():
            raise FileNotFoundError("preserved component resource is missing: " + str(path))
        expected = resource_state.artifact.get("sha256")
        if expected and path.is_file() and _sha256(path) != expected:
            raise ValueError("preserved external resource hash changed")
        if resource_state.adapter_id == "ffmpeg-executable":
            probe = _run([str(path), "-version"])
            output = "\n".join((probe.stdout, probe.stderr))
            if probe.returncode or "ffmpeg version" not in output.casefold():
                raise RuntimeError("preserved FFmpeg executable failed its identity probe")
        return {"version": "preserved-resource-v1", "passed": True,
                "ownership": resource_state.ownership}
    if resource_state.adapter_id == "huggingface-snapshot-v1":
        values = manifest.raw.get("resources", ())
        if len(values) != 1 or not values[0].get("manifest"):
            raise ValueError("Hugging Face resource descriptor is incomplete")
        descriptor = load_artifact_descriptor(delivery / "recipes" / values[0]["manifest"])
        record = path.with_name(path.name + ".resource.json")
        return verify_snapshot(descriptor, path, revision=descriptor.revision,
                               strict=record.is_file())
    if manifest.raw.get("schema_version") == 2:
        values = manifest.raw.get("resources", ())
        match = next((item for item in values
                      if item.get("resource_id") == resource_state.resource_id), None)
        if match is None:
            raise ValueError("component resource is absent from its selected manifest")
        return validate_resource(match, path, python=python, application=application)
    if not path.exists():
        raise FileNotFoundError("preserved component resource is missing: " + str(path))
    return {"version": "preserved-resource-v1", "passed": True,
            "ownership": resource_state.ownership}
