"""Versioned provider-neutral installed capability and resource state."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .compatibility_profiles import CompatibilityProfile, load_approved_profiles


SCHEMA_VERSION = 1
OWNERSHIP = {"manager-owned", "user-supplied/external", "shared", "unknown/legacy"}
DISPOSITIONS = {"rebuild", "copy-and-verify", "preserve-reference", "preserve-in-place"}
COMPONENT_STATES = {"installed", "partial", "disabled", "error"}


@dataclass(frozen=True, slots=True)
class ResourceState:
    resource_id: str
    kind: str
    adapter_id: str
    local_path: str
    ownership: str
    disposition: str
    artifact: dict[str, Any]
    source: dict[str, Any]
    validation: dict[str, Any]

    def __post_init__(self) -> None:
        if not all((self.resource_id, self.kind, self.adapter_id, self.local_path)):
            raise ValueError("component resource identity and local path are required")
        if self.ownership not in OWNERSHIP:
            raise ValueError("unknown resource ownership")
        if self.disposition not in DISPOSITIONS:
            raise ValueError("unknown resource move disposition")
        if not isinstance(self.artifact, dict) or not isinstance(self.source, dict):
            raise ValueError("resource artifact and provenance must be objects")
        if not isinstance(self.validation, dict) or not self.validation.get("version"):
            raise ValueError("resource validation version is required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResourceState":
        required = {"resource_id", "kind", "adapter_id", "local_path", "ownership",
                    "disposition", "artifact", "source", "validation"}
        if set(value) != required:
            raise ValueError("invalid component resource state")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class InstalledComponent:
    capability_id: str
    provider_id: str
    component_id: str
    implementation_id: str
    version: str
    state: str
    enabled: bool
    readiness: dict[str, Any]
    resources: tuple[ResourceState, ...]

    def __post_init__(self) -> None:
        if not all((self.capability_id, self.provider_id, self.component_id,
                    self.implementation_id, self.version)):
            raise ValueError("component identity is incomplete")
        if self.state not in COMPONENT_STATES:
            raise ValueError("invalid installed component state")
        if not isinstance(self.readiness, dict) or not self.readiness.get("version"):
            raise ValueError("component readiness version is required")
        ids = [item.resource_id for item in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("component resource identities must be unique")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstalledComponent":
        required = {"capability_id", "provider_id", "component_id", "implementation_id",
                    "version", "state", "enabled", "readiness", "resources"}
        if set(value) != required or not isinstance(value["resources"], list):
            raise ValueError("invalid installed component state")
        copied = dict(value)
        copied["resources"] = tuple(ResourceState.from_dict(item)
                                    for item in value["resources"])
        return cls(**copied)


@dataclass(frozen=True, slots=True)
class ComponentInventory:
    schema_version: int
    selected_profile: dict[str, str]
    installation_root: str
    components: tuple[InstalledComponent, ...]
    migration: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported component inventory schema")
        if set(self.selected_profile) != {"profile_id", "digest"}:
            raise ValueError("component inventory requires an exact selected profile")
        ids = [item.component_id for item in self.components]
        implementations = [(item.capability_id, item.provider_id, item.implementation_id)
                           for item in self.components]
        if len(ids) != len(set(ids)) or len(implementations) != len(set(implementations)):
            raise ValueError("component inventory contains duplicate identities")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get(self, component_id: str) -> InstalledComponent | None:
        return next((item for item in self.components if item.component_id == component_id), None)

    @property
    def installed_component_ids(self) -> set[str]:
        return {item.component_id for item in self.components
                if item.state == "installed" and item.enabled}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ComponentInventory":
        required = {"schema_version", "selected_profile", "installation_root",
                    "components", "migration"}
        if set(value) != required or not isinstance(value["components"], list):
            raise ValueError("invalid component inventory")
        return cls(
            int(value["schema_version"]), dict(value["selected_profile"]),
            str(value["installation_root"]),
            tuple(InstalledComponent.from_dict(item) for item in value["components"]),
            dict(value["migration"]),
        )


def inventory_path(root: Path) -> Path:
    return root.resolve() / "State/components/inventory.json"


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with temporary.open("wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def write_inventory(root: Path, inventory: ComponentInventory) -> Path:
    root = root.resolve()
    if Path(inventory.installation_root).resolve() != root:
        raise ValueError("component inventory belongs to another installation")
    target = inventory_path(root)
    _atomic_write(target, inventory.as_dict())
    return target


def load_inventory(root: Path) -> ComponentInventory | None:
    """Read only. Legacy projection is an explicit separate operation."""
    target = inventory_path(root)
    if not target.is_file():
        return None
    inventory = ComponentInventory.from_dict(json.loads(target.read_text(encoding="utf-8")))
    if Path(inventory.installation_root).resolve() != root.resolve():
        raise ValueError("component inventory root does not match this installation")
    return inventory


def empty_inventory(root: Path, profile: CompatibilityProfile, *, migration=None) -> ComponentInventory:
    return ComponentInventory(
        SCHEMA_VERSION,
        {"profile_id": profile.profile_id, "digest": profile.digest},
        root.resolve().as_posix(), (), dict(migration or {"source": "native-v1"}),
    )


def replace_component(inventory: ComponentInventory,
                      component: InstalledComponent) -> ComponentInventory:
    values = [item for item in inventory.components if item.component_id != component.component_id]
    values.append(component)
    return ComponentInventory(
        inventory.schema_version, dict(inventory.selected_profile),
        inventory.installation_root, tuple(values), dict(inventory.migration))


def with_profile(inventory: ComponentInventory,
                 profile: CompatibilityProfile) -> ComponentInventory:
    known = {item.component_id for item in profile.components.values()}
    if any(item.component_id not in known for item in inventory.components):
        raise ValueError("new profile cannot represent an installed component")
    return ComponentInventory(
        inventory.schema_version,
        {"profile_id": profile.profile_id, "digest": profile.digest},
        inventory.installation_root, inventory.components, dict(inventory.migration))


def component_from_manifest(manifest, *, state: str, enabled: bool,
                            readiness: dict[str, Any],
                            resources: Iterable[ResourceState]) -> InstalledComponent:
    return InstalledComponent(
        capability_id=str(manifest.raw.get("capability_id") or re.sub(
            r"[^a-z0-9]+", "-", manifest.capability.casefold()).strip("-")),
        provider_id=manifest.provider_id,
        component_id=manifest.component_id,
        implementation_id=str(manifest.raw.get("implementation_id") or manifest.manifest_id),
        version=manifest.native_version,
        state=state, enabled=enabled, readiness=dict(readiness),
        resources=tuple(resources),
    )


def project_legacy_inventory(delivery: Path, root: Path,
                             active_record: dict[str, Any]) -> ComponentInventory:
    """Project M1.9/M1.10 state without writing or guessing resource ownership."""
    profiles = load_approved_profiles(delivery / "recipes/compatibility/profiles")
    profile = next((item for item in profiles if item.profile_id == "2026-09-06"), profiles[0])
    inventory = empty_inventory(root, profile, migration={
        "source": "legacy-m1.9-m1.10-projection", "persisted": False,
    })
    core = next(item for item in profile.components.values() if item.tier == "core")
    core_resources = (
        ResourceState("application", "application", "managed-tree-v1",
                      str(active_record.get("application") or root / "Application/extracted"),
                      "manager-owned", "copy-and-verify", {},
                      {"kind": "legacy-active-record"},
                      {"version": "legacy-active-record-v1", "passed": True}),
        ResourceState("python-environment", "python-environment", "approved-profile-venv-v1",
                      str(active_record.get("python") or root / "venv/Scripts/python.exe"),
                      "manager-owned", "rebuild", {}, {"kind": "approved-profile"},
                      {"version": "legacy-active-record-v1", "passed": True}),
    )
    inventory = replace_component(inventory, component_from_manifest(
        core, state="installed", enabled=True,
        readiness={"version": "lic-core-readiness-v2", "passed": True},
        resources=core_resources))

    legacy_file = root / "State/components/florence.json"
    legacy_data: dict[str, Any] = {}
    if legacy_file.is_file():
        try:
            legacy_data = json.loads(legacy_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            legacy_data = {}
    legacy_channel = active_record.get("channel")
    has_florence = (legacy_data.get("state") == "installed" or
                    isinstance(legacy_channel, dict) and legacy_channel.get("schema_version") == 1)
    if has_florence:
        manifest = profile.component_by_id("florence-captioning")
        model_path = str(legacy_data.get("snapshot") or legacy_data.get("model_root") or
                         active_record.get("model_root") or root / "Models")
        resource = ResourceState(
            "florence-model", "model", "huggingface-snapshot-v1", model_path,
            "unknown/legacy", "preserve-in-place",
            {"digest": str(legacy_data.get("model_digest") or "unknown")},
            {"kind": "legacy-component-record"},
            {"version": "legacy-florence-v1", "passed": True},
        )
        inventory = replace_component(inventory, component_from_manifest(
            manifest, state="installed", enabled=True,
            readiness={"version": "legacy-florence-v1", "passed": True},
            resources=(resource,)))
    return inventory


def load_or_project_inventory(delivery: Path, root: Path,
                              active_record: dict[str, Any]) -> ComponentInventory:
    return load_inventory(root) or project_legacy_inventory(delivery, root, active_record)
