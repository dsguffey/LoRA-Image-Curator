"""Acquire and install an exact base wheel set without online resolution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from .acquisition import AcquisitionPolicy, acquire_artifact
from .artifacts import AcquiredArtifact
from .dependency_lock import DependencyLock
from .child_process import run as child_run


class DependencyInstallError(RuntimeError):
    """Persistent, bounded pip failure evidence for the provider recovery journal."""

    def __init__(self, evidence: dict[str, object]):
        self.evidence = evidence
        super().__init__(json.dumps(evidence, sort_keys=True))


def dependency_failure_details(error: BaseException | str) -> dict[str, object] | None:
    if isinstance(error, DependencyInstallError):
        return error.evidence
    value = str(error)
    try:
        parsed = json.loads(value[value.index("{"):])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("kind") == "dependency-install" else None


def _log_tail(path: Path, limit: int = 8192) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read().decode("utf-8", errors="replace")[-limit:]


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
                "wheel_count": len(ordered), "offline": True, "resolver_disabled": True,
                "log_path": str(log_path)}
    if result.returncode:
        tail = _log_tail(log_path)
        # An error path naming a distribution is evidence; pip's preceding
        # "Installing collected packages" list is not proof of the culprit.
        match = re.search(r"[\\/]([A-Za-z0-9_.-]+?)-[0-9][^\\/]*\.dist-info[\\/]", tail)
        error_line = next((line.strip() for line in reversed(tail.splitlines())
                           if "ERROR:" in line or "[WinError" in line), "")
        raise DependencyInstallError({**evidence, "kind": "dependency-install",
                                      "package": match.group(1) if match else "",
                                      "error_summary": error_line[:500],
                                      "output_tail": tail})
    return evidence
