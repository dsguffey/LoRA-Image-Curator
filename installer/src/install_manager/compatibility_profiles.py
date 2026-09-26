"""Immutable, dated LIC compatibility profiles composed from component manifests.

Loading and selecting profile metadata is deliberately passive.  Artifact acquisition
and environment mutation remain explicit operations owned by the installers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from .dependency_lock import load_dependency_lock, normalize_distribution


PROFILE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.[1-9]\d*)?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMPONENT_KEYS = ("core", "florence", "insightface", "mediapipe", "ffmpeg")
ALLOWED_WHEEL_HOSTS = {"files.pythonhosted.org", "download-r2.pytorch.org"}
PACKAGE_SCOPES = {"component", "shared"}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def profile_sort_key(profile_id: str) -> tuple[int, int, int, int]:
    """Order approved dates and same-day revisions without consulting upstream."""
    if not PROFILE_ID.fullmatch(profile_id):
        raise ValueError("invalid dated compatibility profile ID")
    date, _, revision = profile_id.partition(".")
    year, month, day = (int(part) for part in date.split("-"))
    try:
        calendar_date(year, month, day)
    except ValueError as error:
        raise ValueError("invalid dated compatibility profile ID") from error
    return year, month, day, int(revision or 0)


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _exact_digest(value: object, label: str) -> str:
    digest = str(value).lower()
    if not SHA256.fullmatch(digest):
        raise ValueError(f"{label} requires an exact SHA-256")
    return digest


def _relative_json(root: Path, value: object) -> Path:
    text = str(value)
    if not text or "\\" in text or text.startswith("/") or ".." in Path(text).parts:
        raise ValueError("manifest reference must be a safe relative path")
    path = (root / text).resolve()
    if path.parent != root.resolve() or path.suffix.casefold() != ".json":
        raise ValueError("component manifests must be direct JSON children")
    return path


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    name: str
    version: str
    filename: str
    sha256: str
    scope: str
    source_kind: str
    artifact_id: str
    size: int | None
    url: str | None
    publisher: str
    source_name: str
    license_id: str
    import_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentManifest:
    manifest_id: str
    component_id: str
    capability: str
    tier: str
    provider_id: str
    native_version: str
    packages: tuple[PackageIdentity, ...]
    raw: dict[str, object]
    digest: str


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    profile_id: str
    approval_basis: str
    python_version: str
    platform: str
    components: dict[str, ComponentManifest]
    package_inventory: dict[str, str]
    distributions: dict[str, tuple[str, ...]]
    raw: dict[str, object]
    digest: str

    @property
    def full_stack(self) -> tuple[ComponentManifest, ...]:
        return tuple(self.components.values())

    @property
    def component_keys(self) -> tuple[str, ...]:
        return tuple(self.components)

    def component_by_id(self, component_id: str) -> ComponentManifest:
        matches = [item for item in self.components.values()
                   if item.component_id == component_id]
        if len(matches) != 1:
            raise ValueError(f"profile does not contain one component {component_id!r}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class CachedArtifactIdentity:
    """Exact cache identity; a missing URL means reviewed local bytes only."""

    artifact_id: str
    version: str
    filename: str
    expected_sha256: str
    expected_size: int | None
    url: str | None
    publisher: str
    source_name: str
    license_id: str

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id, "version": self.version,
            "filename": self.filename, "url": self.url,
            "expected_sha256": self.expected_sha256,
            "publisher": self.publisher, "source_name": self.source_name,
            "license_id": self.license_id, "expected_size": self.expected_size,
        }


@dataclass(frozen=True, slots=True)
class ResolvedWheel:
    name: str
    version: str
    artifact: CachedArtifactIdentity
    import_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedDependencyProfile:
    """DependencyLock-compatible exact subset selected by component identity."""

    profile: str
    python_version: str
    platform: str
    wheels: tuple[ResolvedWheel, ...]

    @property
    def expected_inventory(self) -> dict[str, str]:
        return {normalize_distribution(item.name): item.version for item in self.wheels}

    def digest(self) -> str:
        return canonical_digest({
            "profile": self.profile, "python_version": self.python_version,
            "platform": self.platform,
            "wheels": [{"name": item.name, "version": item.version,
                        "artifact": item.artifact.as_dict(),
                        "import_names": list(item.import_names)} for item in self.wheels],
        })


def _package_from_record(record: dict[str, object]) -> PackageIdentity:
    required = {"name", "version", "scope", "artifact"}
    if set(record) != required or record["scope"] not in PACKAGE_SCOPES:
        raise ValueError("invalid component package record")
    artifact = record["artifact"]
    if not isinstance(artifact, dict):
        raise ValueError("package artifact must be an object")
    required_artifact = {"filename", "sha256", "size", "source", "license_id"}
    if set(artifact) != required_artifact:
        raise ValueError("invalid component package artifact")
    filename = str(artifact["filename"])
    size = artifact["size"]
    if (not filename.casefold().endswith(".whl")
            or (size is not None and int(size) <= 0)):
        raise ValueError("component package requires an exact wheel identity")
    source = artifact["source"]
    if not isinstance(source, dict) or source.get("kind") not in {"https", "downstream-build"}:
        raise ValueError("unknown package artifact source")
    if source["kind"] == "https":
        if set(source) != {"kind", "url"}:
            raise ValueError("invalid HTTPS wheel source")
        parsed = urlparse(str(source["url"]))
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_WHEEL_HOSTS:
            raise ValueError("wheel source host is not approved")
        if unquote(Path(parsed.path).name) != filename:
            raise ValueError("wheel source filename mismatch")
    else:
        expected = {"kind", "upstream_url", "upstream_sha256", "build_adapter", "availability"}
        if set(source) != expected or source["availability"] != "preserved-reviewed-bytes":
            raise ValueError("invalid downstream-build provenance")
        parsed = urlparse(str(source["upstream_url"]))
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_WHEEL_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("downstream upstream source is not approved")
        _exact_digest(source["upstream_sha256"], "downstream upstream")
    name = normalize_distribution(str(record["name"]))
    return PackageIdentity(
        name, str(record["version"]), filename,
        _exact_digest(artifact["sha256"], "wheel"), str(record["scope"]),
        str(source["kind"]), name, int(size) if size is not None else None,
        str(source["url"]) if source["kind"] == "https" else None,
        name, "PyPI" if source["kind"] == "https" else "reviewed downstream build",
        str(artifact["license_id"]), (),
    )


def load_component_manifest(path: Path, *, recipes_root: Path) -> ComponentManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    common = {"schema_version", "manifest_id", "component_id", "capability", "tier",
              "provider", "native_version", "python_version", "platform", "approved_sources",
              "dependency_lock", "packages", "resources", "validation", "provenance"}
    schema = data.get("schema_version")
    required = common if schema == 1 else common | {"provider_id", "policy"}
    if schema not in {1, 2} or set(data) != required:
        raise ValueError("unsupported component manifest schema")
    if data["tier"] not in {"core", "optional"}:
        raise ValueError("component tier must be core or optional")
    if data["python_version"] != "3.14.6" or data["platform"] != "win_amd64":
        raise ValueError("component is outside the approved Windows/Python baseline")
    packages: list[PackageIdentity] = []
    lock_ref = data["dependency_lock"]
    if lock_ref is not None:
        if not isinstance(lock_ref, dict) or set(lock_ref) != {"path", "file_sha256", "semantic_sha256"}:
            raise ValueError("invalid dependency-lock reference")
        lock_path = (recipes_root / str(lock_ref["path"])).resolve()
        if lock_path.parent != recipes_root.resolve() or file_digest(lock_path) != _exact_digest(
                lock_ref["file_sha256"], "dependency lock file"):
            raise ValueError("dependency lock bytes changed")
        lock = load_dependency_lock(lock_path)
        if lock.digest() != _exact_digest(lock_ref["semantic_sha256"], "dependency lock"):
            raise ValueError("dependency lock semantics changed")
        for wheel in lock.wheels:
            wheel.artifact.validate(tuple(ALLOWED_WHEEL_HOSTS))
        packages.extend(PackageIdentity(
            normalize_distribution(wheel.name), wheel.version, wheel.artifact.filename,
            wheel.artifact.expected_sha256, "component", "https",
            wheel.artifact.artifact_id, wheel.artifact.expected_size, wheel.artifact.url,
            wheel.artifact.publisher, wheel.artifact.source_name, wheel.artifact.license_id,
            wheel.import_names) for wheel in lock.wheels)
    package_records = data["packages"]
    if not isinstance(package_records, list):
        raise ValueError("component packages must be a list")
    packages.extend(_package_from_record(item) for item in package_records)
    names = [item.name for item in packages]
    if len(names) != len(set(names)):
        raise ValueError("component manifest contains duplicate package names")
    validation = data["validation"]
    if not isinstance(validation, dict) or not validation.get("probe") or not validation.get("evidence"):
        raise ValueError("component validation evidence is incomplete")
    provider_id = str(data.get("provider_id") or re.sub(
        r"[^a-z0-9]+", "-", str(data["provider"]).casefold()).strip("-"))
    if not SAFE_ID.fullmatch(provider_id):
        raise ValueError("component provider requires a stable safe identity")
    if schema == 2:
        policy = data["policy"]
        if not isinstance(policy, dict) or policy.get("payment") not in {
                "required", "optional", "none-identified"}:
            raise ValueError("invalid provider policy metadata")
    return ComponentManifest(
        str(data["manifest_id"]), str(data["component_id"]), str(data["capability"]),
        str(data["tier"]), provider_id, str(data["native_version"]), tuple(packages),
        data, canonical_digest(data),
    )


def load_compatibility_profile(path: Path) -> CompatibilityProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "profile_id", "status", "approval_basis", "python_version",
                "platform", "hardware_profile", "components", "reference_environment",
                "distributions", "policy", "validation"}
    if set(data) != required or data["schema_version"] != 1:
        raise ValueError("unsupported compatibility profile schema")
    profile_id = str(data["profile_id"])
    profile_sort_key(profile_id)
    if data["status"] != "approved":
        raise ValueError("compatibility profile is not an approved dated profile")
    if data["python_version"] != "3.14.6" or data["platform"] != "win_amd64":
        raise ValueError("profile is outside the approved Windows/Python baseline")
    component_refs = data["components"]
    if not isinstance(component_refs, dict) or not component_refs:
        raise ValueError("profile must bind at least one component manifest")
    if any(not SAFE_ID.fullmatch(str(key)) for key in component_refs):
        raise ValueError("profile component key is unsafe")
    component_root = path.parent.parent / "components"
    recipes_root = path.parent.parent.parent
    components: dict[str, ComponentManifest] = {}
    for key, ref in component_refs.items():
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise ValueError("invalid component-manifest reference")
        component_path = _relative_json(component_root, ref["path"])
        if file_digest(component_path) != _exact_digest(ref["sha256"], "component manifest"):
            raise ValueError("component manifest bytes changed")
        component = load_component_manifest(component_path, recipes_root=recipes_root)
        components[key] = component
    component_ids = [item.component_id for item in components.values()]
    if len(component_ids) != len(set(component_ids)):
        raise ValueError("profile contains duplicate component identities")
    core_keys = tuple(key for key, item in components.items() if item.tier == "core")
    if len(core_keys) != 1 or any(item.tier != "optional" for item in components.values()
                                  if item.tier != "core"):
        raise ValueError("profile must contain exactly one Core and optional remainder")
    resolved: dict[str, PackageIdentity] = {}
    for component in components.values():
        for package in component.packages:
            prior = resolved.get(package.name)
            if prior and (prior.version, prior.filename, prior.sha256) != (
                    package.version, package.filename, package.sha256):
                raise ValueError(f"component package conflict: {package.name}")
            resolved.setdefault(package.name, package)
    reference = data["reference_environment"]
    expected_inventory = {normalize_distribution(str(name)): str(version)
                          for name, version in reference["package_inventory"].items()}
    actual_inventory = {name: package.version for name, package in resolved.items()}
    if actual_inventory != expected_inventory:
        raise ValueError("resolved component packages differ from the accepted environment")
    forbidden = {normalize_distribution(str(name)) for name in reference["forbidden_distributions"]}
    if forbidden & actual_inventory.keys():
        raise ValueError("reference environment contains a forbidden overlapping distribution")
    distributions = {name: tuple(value) for name, value in data["distributions"].items()}
    if distributions != {"LIC Lite": core_keys, "LIC Full": tuple(component_refs)}:
        raise ValueError("Lite and Full must reference the same approved profile components")
    policy = data["policy"]
    if policy.get("profile_selection") != "approved-only" or policy.get("unchecked_installed") != "preserve":
        raise ValueError("unsafe profile operation policy")
    return CompatibilityProfile(
        profile_id, str(data["approval_basis"]), str(data["python_version"]),
        str(data["platform"]), components, actual_inventory, distributions, data,
        canonical_digest(data),
    )


def load_approved_profiles(directory: Path) -> tuple[CompatibilityProfile, ...]:
    """Load the locally supplied approved profiles and choose recency from their IDs."""
    profiles = tuple(load_compatibility_profile(path) for path in directory.glob("*.json"))
    if not profiles:
        raise ValueError("no approved compatibility profiles found")
    if len({profile.profile_id for profile in profiles}) != len(profiles):
        raise ValueError("duplicate compatibility profile ID")
    return tuple(sorted(profiles, key=lambda profile: profile_sort_key(profile.profile_id)))


def recommended_profile(directory: Path) -> CompatibilityProfile:
    """Return the newest locally approved profile, never arbitrary upstream latest."""
    return load_approved_profiles(directory)[-1]


def accepted_installed_profile(directory: Path, selected_profile: dict[str, str],
                               installed_ids: set[str]) -> CompatibilityProfile:
    """Keep exact prior 0.7.1 installations usable across source-metadata corrections.

    The 2026-09-11 profile remains immutable. Its FFmpeg archive's LICENSE.txt
    member had a one-character manifest typo, but its ZIP and executable hashes
    were correct. The 2026-09-25 profile used Git LFS pointer URLs for Face;
    the corrected source returns the same pinned model bytes. This is
    deliberately not a general profile migration rule.
    """
    profiles = load_approved_profiles(directory)
    current = profiles[-1]
    identity = {"profile_id": current.profile_id, "digest": current.digest}
    if selected_profile == identity:
        return current
    historical_digests = {
        "2026-09-11": "ddd947d8dd52c28d4cb2ccae9ae998683807dd24dd5d1f42ebc95331886db6dd",
        "2026-09-25": "bb5bf432f745da4f2dc613137817044915329c2c03f0b39c3e1bc4f0f2a94671",
        "2026-09-26": "235b50d01905018046cda02dd93df37efb0adba7d20a81be74f4332bfb6e492e",
    }
    previous_id = selected_profile.get("profile_id")
    expected = historical_digests.get(previous_id)
    historical = next((item for item in profiles if item.profile_id == previous_id), None)
    if (historical is None or expected is None
            or selected_profile != {"profile_id": previous_id, "digest": expected}
            or historical.digest != expected
            or previous_id == "2026-09-11" and "video-extraction" in installed_ids):
        raise ValueError("Installed component profile differs from this manager")
    old_lock = dependency_profile_for_components(historical, installed_ids)
    new_lock = dependency_profile_for_components(current, installed_ids)
    old_wheels = {(wheel.name, wheel.version, wheel.artifact.expected_sha256)
                  for wheel in old_lock.wheels}
    new_wheels = {(wheel.name, wheel.version, wheel.artifact.expected_sha256)
                  for wheel in new_lock.wheels}
    if old_wheels != new_wheels:
        raise ValueError("Historical installed package closure differs from this manager")
    return historical


def plan_dependency_operation(profile: CompatibilityProfile, *, checked: set[str],
                              installed: set[str]) -> dict[str, tuple[str, ...]]:
    """Describe user decisions only; this function never acquires or removes content."""
    core = tuple(key for key, item in profile.components.items() if item.tier == "core")
    optional = {key for key, item in profile.components.items() if item.tier == "optional"}
    unknown = (checked | installed) - optional
    if unknown:
        raise ValueError("unknown optional component selection")
    return {
        "apply": tuple(key for key in profile.components if key in core or key in checked & installed),
        "confirm_install": tuple(key for key in profile.components if key in checked - installed),
        "preserve_unchecked": tuple(key for key in profile.components if key in installed - checked),
    }


def dependency_profile_for_components(
        profile: CompatibilityProfile, component_ids: set[str]) -> ResolvedDependencyProfile:
    """Resolve one exact approved subset; this is composition, never dependency solving."""
    known = {item.component_id for item in profile.components.values()}
    if not component_ids or component_ids - known:
        raise ValueError("component selection is empty or outside the approved profile")
    core = {item.component_id for item in profile.components.values() if item.tier == "core"}
    if not core <= component_ids:
        raise ValueError("component selection must include Core")
    resolved: dict[str, ResolvedWheel] = {}
    for component in profile.components.values():
        if component.component_id not in component_ids:
            continue
        for package in component.packages:
            artifact = CachedArtifactIdentity(
                package.artifact_id, package.version, package.filename, package.sha256,
                package.size, package.url, package.publisher, package.source_name,
                package.license_id)
            wheel = ResolvedWheel(package.name, package.version, artifact,
                                  package.import_names)
            prior = resolved.get(package.name)
            if prior and (prior.version, prior.artifact.filename,
                          prior.artifact.expected_sha256) != (
                              wheel.version, wheel.artifact.filename,
                              wheel.artifact.expected_sha256):
                raise ValueError(f"component package conflict: {package.name}")
            resolved.setdefault(package.name, wheel)
    return ResolvedDependencyProfile(
        f"lic-components-{profile.profile_id}", profile.python_version,
        profile.platform, tuple(resolved.values()))


def customer_browse_resources() -> tuple[str, ...]:
    """Meaningful external resources; internal wheels remain manager-controlled."""
    return ("dependency-profile", "lic-package", "florence-model", "insightface-model",
            "mediapipe-model", "ffmpeg-executable")
