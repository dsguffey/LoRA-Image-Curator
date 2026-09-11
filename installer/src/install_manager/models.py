"""Small, deterministic records shared by the M1.1 planning slice."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any


class ResourceState(StrEnum):
    """State vocabulary that prevents detection from being confused with use."""

    DETECTED = "detected"
    PLANNED_ACQUISITION = "planned_acquisition"
    STAGED = "staged"
    VERIFIED = "verified"
    NOT_EXECUTED = "install_not_executed"


class ActionKind(StrEnum):
    """Closed set of M1.1 planner actions; recipes cannot supply shell text."""

    DETECT = "detect"
    ACQUIRE = "acquire"
    VERIFY = "verify"
    STAGE = "stage"
    EXTRACT = "extract"
    VALIDATE = "validate"
    ACTIVATE = "activate"


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    """One explicitly probed local path and its measured, non-authoritative facts."""

    resource_id: str
    kind: str
    path: str
    state: ResourceState
    exists: bool
    is_file: bool
    sha256: str = ""
    size: int | None = None
    discovery: str = "explicit"
    ownership: str = "unknown"
    compatible: bool | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PackageInspection:
    """Verified package facts independent of any staging or activation action."""

    artifact_id: str
    version: str
    source: str
    package_path: str
    package_sha256: str
    archive_members: tuple[str, ...]
    manifest_members: tuple[str, ...]
    required_members: tuple[str, ...]
    integrity_ok: bool
    completeness_ok: bool
    provenance_ok: bool
    errors: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """Return whether all independent package checks passed."""
        return self.integrity_ok and self.completeness_ok and self.provenance_ok


@dataclass(frozen=True, slots=True)
class PlanAction:
    """One bounded operation in a deterministic, reviewable plan."""

    kind: ActionKind
    state: ResourceState
    description: str
    executed: bool = False


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Deterministic M1.1 plan; it contains no timestamps or host-mutated facts."""

    schema_version: int
    application_id: str
    display_name: str
    application_version: str
    package: PackageInspection
    staging_directory: str
    existing_resources: tuple[ResourceCandidate, ...]
    actions: tuple[PlanAction, ...]
    activation_allowed: bool = False
    installation_executed: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-compatible data with stable enum values and ordering."""
        return asdict(self)

    def canonical_json(self) -> str:
        """Serialize the plan deterministically for review and repeatability."""
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        """Hash the exact canonical plan representation."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StagedPackage:
    """Describe a verified disposable stage, explicitly refusing activation."""

    package: PackageInspection
    stage_directory: str
    copied_package: str
    extracted_directory: str
    state: ResourceState = ResourceState.STAGED
    activation_allowed: bool = False
    installation_executed: bool = False


def normalized_path(path: Path) -> str:
    """Use a stable Windows-friendly display form without touching the path."""
    return path.expanduser().resolve().as_posix()
