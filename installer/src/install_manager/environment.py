"""Deterministic M1.2 planning and final-path runtime/venv construction."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import uuid
import zipfile

from .artifacts import AcquiredArtifact, ArtifactDescriptor
from .child_process import run as child_run
from .process_lock import process_lock


@dataclass(frozen=True, slots=True)
class EnvironmentPlan:
    schema_version: int
    operation_id: str
    profile: str
    runtime: ArtifactDescriptor
    dependency_lock_sha256: str
    operation_root: str
    runtime_path: str
    final_venv_path: str
    cache_path: str
    journal_path: str
    steps: tuple[str, ...]
    blockers: tuple[str, ...]
    activation_allowed: bool = False

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _inside(root: Path, candidate: Path) -> bool:
    root = root.expanduser().resolve()
    candidate = candidate.expanduser().resolve()
    return candidate == root or root in candidate.parents


def build_environment_plan(operation_id: str, profile: str, runtime: ArtifactDescriptor,
                           dependency_lock_sha256: str, operation_root: Path,
                           *, cache_root: Path | None = None) -> EnvironmentPlan:
    """Choose stable final paths before creating any environment files."""
    root = operation_root.expanduser().resolve()
    runtime_path = root / "runtimes" / f"python-{runtime.version}-win-x64"
    final_venv = root / "apps" / "lic-m12-proof" / "venv"
    cache = ((cache_root or (root / "cache" / "artifacts")).expanduser().resolve())
    journal = root / "state" / "operations" / f"{operation_id}.json"
    blockers = []
    for label, path in (("runtime", runtime_path), ("final venv", final_venv)):
        if path.exists():
            blockers.append(f"{label} target already exists: {path.as_posix()}")
    return EnvironmentPlan(
        1, operation_id, profile, runtime, dependency_lock_sha256, root.as_posix(),
        runtime_path.as_posix(), final_venv.as_posix(), cache.as_posix(), journal.as_posix(),
        ("acquire_runtime", "extract_runtime", "create_final_path_venv",
         "acquire_dependencies", "install_dependencies", "validate_environment"),
        tuple(blockers), False,
    )


def _safe_zip_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    plain = name[:-1] if name.endswith("/") else name
    plain_path = PurePosixPath(plain)
    if (not plain or plain_path.is_absolute() or ".." in plain_path.parts or "\\" in name
            or ":" in name or any(ord(character) < 32 for character in name)
            or plain_path.as_posix() != plain):
        raise ValueError(f"unsafe runtime archive member: {name!r}")
    return path


def extract_runtime(artifact: AcquiredArtifact, target: Path,
                    *, required_members: tuple[str, ...] = ("python.exe", "Lib/venv/__init__.py")) -> Path:
    with process_lock(target.resolve()):
        return _extract_runtime(artifact, target, required_members=required_members)


def _extract_runtime(artifact: AcquiredArtifact, target: Path,
                     *, required_members: tuple[str, ...]) -> Path:
    """Verify again, safely extract beside the final target, then atomically publish."""
    if not artifact.verified:
        raise ValueError("runtime artifact is not verified")
    source = Path(artifact.cache_path).resolve()
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != artifact.descriptor.expected_sha256:
        raise ValueError("runtime cache changed after acquisition")
    target = target.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"runtime target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / f".{target.name}.partial-{uuid.uuid4().hex}"
    partial.mkdir()
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            folded = [name.rstrip("/").casefold() for name in names]
            if len(folded) != len(set(folded)):
                raise ValueError("runtime archive contains duplicate or case-colliding members")
            for info in infos:
                relative = _safe_zip_name(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"runtime archive contains a symbolic link: {info.filename}")
                destination = (partial / relative).resolve()
                if partial not in destination.parents and destination != partial:
                    raise ValueError("runtime member escapes extraction root")
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source_file, destination.open("wb") as output:
                        shutil.copyfileobj(source_file, output)
            missing = [name for name in required_members if not (partial / PurePosixPath(name)).is_file()]
            if missing:
                raise ValueError(f"runtime archive missing required members: {missing}")
        os.replace(partial, target)
        return target
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def create_final_path_venv(runtime_python: Path, final_venv: Path, *, approved_root: Path,
                           log_path: Path) -> Path:
    """Create the venv at its declared final path and refuse populated targets."""
    runtime_python = runtime_python.expanduser().resolve()
    final_venv = final_venv.expanduser().resolve()
    if not _inside(approved_root, final_venv):
        raise ValueError("venv target escapes the approved operation root")
    if final_venv.exists():
        raise FileExistsError(f"final venv target already exists: {final_venv}")
    final_venv.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    command = [str(runtime_python), "-I", "-B", "-m", "venv", str(final_venv)]
    with log_path.open("w", encoding="utf-8") as log:
        result = child_run(command, env=environment, stdout=log,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"venv creation failed with exit code {result.returncode}")
    interpreter = final_venv / "Scripts" / "python.exe"
    if not interpreter.is_file():
        raise RuntimeError("venv creation did not produce the final interpreter")
    return interpreter
