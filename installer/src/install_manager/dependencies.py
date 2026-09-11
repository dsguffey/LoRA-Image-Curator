"""Acquire and install an exact base wheel set without online resolution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from .acquisition import AcquisitionPolicy, acquire_artifact
from .artifacts import AcquiredArtifact
from .dependency_lock import DependencyLock
from .child_process import run as child_run


def acquire_locked_wheels(lock: DependencyLock, cache_root: Path,
                          policy: AcquisitionPolicy) -> tuple[AcquiredArtifact, ...]:
    """Acquire each exact wheel through the same verified-cache primitive."""
    return tuple(acquire_artifact(wheel.artifact, cache_root, policy) for wheel in lock.wheels)


def install_locked_wheels(venv_python: Path, lock: DependencyLock,
                          artifacts: tuple[AcquiredArtifact, ...], log_path: Path) -> dict[str, object]:
    """Install only verified locked wheels, offline and with dependency resolution disabled."""
    by_filename = {Path(artifact.cache_path).name.casefold(): artifact for artifact in artifacts}
    ordered: list[str] = []
    for wheel in lock.wheels:
        artifact = by_filename.get(wheel.artifact.filename.casefold())
        if artifact is None or not artifact.verified:
            raise ValueError(f"verified wheel unavailable: {wheel.name}=={wheel.version}")
        ordered.append(artifact.cache_path)
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INDEX": "1",
                        "PIP_CONFIG_FILE": os.devnull})
    command = [str(venv_python), "-I", "-B", "-m", "pip", "install", "--no-index",
               "--no-deps", "--only-binary=:all:", *ordered]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = child_run(command, env=environment, stdout=log,
                                stderr=subprocess.STDOUT, check=False)
    evidence = {"command": command, "exit_code": result.returncode,
                "wheel_count": len(ordered), "offline": True, "resolver_disabled": True}
    if result.returncode:
        raise RuntimeError(json.dumps(evidence, sort_keys=True))
    return evidence
