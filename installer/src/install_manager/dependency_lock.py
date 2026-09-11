"""Exact wheel lock consumed by the M1.2 offline installer."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactDescriptor


def normalize_distribution(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


@dataclass(frozen=True, slots=True)
class LockedWheel:
    name: str
    version: str
    artifact: ArtifactDescriptor
    import_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DependencyLock:
    schema_version: int
    profile: str
    python_version: str
    platform: str
    wheels: tuple[LockedWheel, ...]

    @property
    def expected_inventory(self) -> dict[str, str]:
        return {normalize_distribution(wheel.name): wheel.version for wheel in self.wheels}

    def canonical_json(self) -> str:
        data = {
            "schema_version": self.schema_version, "profile": self.profile,
            "python_version": self.python_version, "platform": self.platform,
            "wheels": [{"name": wheel.name, "version": wheel.version,
                        "artifact": wheel.artifact.as_dict(),
                        "import_names": list(wheel.import_names)} for wheel in self.wheels],
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyLock":
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError("unsupported dependency lock schema")
        wheels = tuple(
            LockedWheel(str(item["name"]), str(item["version"]),
                        ArtifactDescriptor.from_dict(item["artifact"]),
                        tuple(str(name) for name in item.get("import_names", [])))
            for item in data.get("wheels", [])
        )
        if not wheels:
            raise ValueError("dependency lock is empty")
        names = [normalize_distribution(wheel.name) for wheel in wheels]
        filenames = [wheel.artifact.filename.casefold() for wheel in wheels]
        if len(names) != len(set(names)) or len(filenames) != len(set(filenames)):
            raise ValueError("dependency lock contains duplicate distributions or files")
        for wheel in wheels:
            if normalize_distribution(wheel.name) not in normalize_distribution(wheel.artifact.filename):
                raise ValueError(f"wheel filename does not identify {wheel.name}")
            if wheel.version != wheel.artifact.version:
                raise ValueError(f"wheel version disagrees with artifact: {wheel.name}")
        return cls(1, str(data["profile"]), str(data["python_version"]),
                   str(data["platform"]), wheels)


def load_dependency_lock(path: Path) -> DependencyLock:
    return DependencyLock.from_dict(json.loads(path.read_text(encoding="utf-8")))
