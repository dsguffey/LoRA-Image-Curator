"""Exact, journaled optional-component operations over bounded trusted adapters."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Callable

from .acquisition import (AcquisitionCancelled, AcquisitionPolicy, acquire_artifact,
                          admit_local_artifact, sha256_file)
from .artifacts import ArtifactDescriptor
from .bootstrap_layout import layout, reject_reparse_entries
from .compatibility_profiles import (ResolvedDependencyProfile, canonical_digest,
                                     dependency_profile_for_components,
                                     recommended_profile)
from .component_adapters import (acquisition_hosts, artifact_descriptor, install_resource,
                                 resource_destination, validate_resource)
from .component_catalog import ComponentFacts, load_component_catalog
from .component_state import (ComponentInventory, ResourceState, component_from_manifest,
                              load_or_project_inventory, replace_component, with_profile,
                              write_inventory)
from .dependencies import install_locked_wheels
from .journal import OperationJournal
from .lic_face_settings import read_face_model_root
from .process_lock import process_lock
from .validation import validate_environment


STEPS = ("verify_core", "acquire_dependencies", "install_dependencies",
         "acquire_resource", "install_resource", "validate", "publish")


@dataclass(frozen=True, slots=True)
class ComponentRecovery:
    journal_path: Path
    component_id: str
    status: str
    completed_steps: int
    total_steps: int
    resumable: bool
    blocked: bool
    summary: str
    selected_path: str | None


def _profile(delivery: Path):
    return recommended_profile(delivery / "recipes/compatibility/profiles")


def canonical_face_model_root() -> Path:
    """Return LIC's one shared Face Analysis location, never a manager cache."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    value = read_face_model_root(appdata)
    if not value:
        raise ValueError("Choose a Face Analysis model location before installing models.")
    return Path(value).expanduser().resolve()


def _definition(delivery: Path, component_id: str):
    catalog = load_component_catalog(delivery / "recipes/lic-components.json")
    matches = [item for item in catalog if item.component_id == component_id]
    if len(matches) != 1:
        raise ValueError("component is not present exactly once in the local catalog")
    return matches[0]


def _resources(manifest) -> tuple[dict, ...]:
    values = manifest.raw.get("resources")
    if manifest.raw.get("schema_version") != 2 or not isinstance(values, list) or not values:
        raise ValueError("this managed component requires schema-v2 resources")
    required = {"resource_id", "kind", "artifact", "installation", "ownership",
                "disposition", "validation"}
    if any(not isinstance(value, dict) or set(value) != required for value in values):
        raise ValueError("invalid managed component resource")
    ids = [str(value["resource_id"]) for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("managed component resource IDs must be unique")
    return tuple(values)


def _artifact_from_package(package) -> ArtifactDescriptor:
    if not package.url:
        raise ValueError(f"{package.name} has reviewed bytes but no approved acquisition source")
    return ArtifactDescriptor(
        package.artifact_id, package.version, package.filename, package.url,
        package.sha256, package.publisher, package.source_name, package.license_id,
        package.size)


def _lock_from_wheels(profile_id: str, python_version: str, platform: str,
                      wheels) -> ResolvedDependencyProfile:
    return ResolvedDependencyProfile(profile_id, python_version, platform, tuple(wheels))


def _plan(delivery: Path, root: Path, component_id: str,
          selected_path: Path | None = None) -> dict:
    profile = _profile(delivery)
    manifest = profile.component_by_id(component_id)
    definition = _definition(delivery, component_id)
    if not definition.managed_install:
        raise ValueError("component has no approved managed acquisition")
    root = root.resolve()
    active_path = root / "State/installations/lic-lite.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("state") != "active" or Path(active.get("root", "")).resolve() != root:
        raise ValueError("Install and activate Core before adding an optional capability")
    inventory = load_or_project_inventory(delivery, root, active)
    inventory = with_profile(inventory, profile)
    selected_ids = set(inventory.installed_component_ids) | {component_id}
    dependency_profile = dependency_profile_for_components(profile, selected_ids)
    current_profile = dependency_profile_for_components(
        profile, inventory.installed_component_ids or {
            next(item.component_id for item in profile.components.values() if item.tier == "core")})
    new_names = set(dependency_profile.expected_inventory) - set(current_profile.expected_inventory)
    dependency_artifacts = [item.artifact.as_dict() for item in dependency_profile.wheels
                            if item.name in new_names]
    resources = _resources(manifest)
    external = None
    if selected_path is not None and str(selected_path):
        candidate = selected_path.expanduser().resolve()
        if not candidate.is_file() and not candidate.is_dir():
            raise FileNotFoundError(candidate)
        if candidate.is_file():
            external = {"path": str(candidate), "sha256": sha256_file(candidate),
                        "size": candidate.stat().st_size}
        else:
            external = {"path": str(candidate), "sha256": None, "size": None}
    if selected_path is not None and len(resources) != 1:
        raise ValueError("This component's existing-resource selection must be recorded separately.")
    if component_id == "face-analysis" and not external:
        face_root = selected_path.expanduser().resolve() if selected_path is not None else canonical_face_model_root()
        destinations = [face_root / str(artifact_descriptor(resource).filename) for resource in resources]
    else:
        destinations = ([Path(external["path"])] if external else
                        [resource_destination(root, resource) for resource in resources])
    return {
        "schema_version": 1,
        "operation": "install-component",
        "target_installation": root.as_posix(),
        "selected_profile": {"profile_id": profile.profile_id, "digest": profile.digest},
        "requested_capabilities": [str(manifest.raw.get("capability_id") or manifest.capability)],
        "component": {"component_id": manifest.component_id,
                      "provider_id": manifest.provider_id,
                      "implementation_id": str(manifest.raw.get("implementation_id") or manifest.manifest_id),
                      "manifest_digest": manifest.digest, "version": manifest.native_version},
        "dependency_profile_digest": dependency_profile.digest(),
        "dependency_artifacts": dependency_artifacts,
        "resource_artifacts": [artifact_descriptor(resource).as_dict() for resource in resources],
        "resource_destinations": [str(destination) for destination in destinations],
        "external_reuse": external,
    }


def operation_plan(delivery: Path, root: Path, component_id: str,
                   selected_path: Path | None = None) -> dict:
    """Build the fully disclosed immutable plan without acquiring or writing."""
    return _plan(delivery, root, component_id, selected_path)


def operation_digest(delivery: Path, root: Path, component_id: str,
                     selected_path: Path | None = None) -> str:
    return canonical_digest(_plan(delivery, root, component_id, selected_path))


def operation_download_summary(delivery: Path, root: Path, component_id: str,
                               selected_path: Path | None = None) -> dict[str, int]:
    """Report only network bytes for an exact approved optional-operation plan.

    A verified cache hit still requires offline installation into a new venv;
    it contributes zero download bytes.  Arbitrary similarly named files do not.
    """
    plan = _plan(delivery, root, component_id, selected_path)
    cache = layout(root.resolve())["cache"]
    resources = () if plan["external_reuse"] else tuple(plan["resource_artifacts"])
    descriptors = tuple(ArtifactDescriptor.from_dict(item) for item in
                        (*plan["dependency_artifacts"], *resources))
    cached = 0
    download = 0
    for descriptor in descriptors:
        candidate = cache / "verified" / descriptor.artifact_id / descriptor.version / descriptor.filename
        hit = (candidate.is_file() and sha256_file(candidate) == descriptor.expected_sha256 and
               (descriptor.expected_size is None or candidate.stat().st_size == descriptor.expected_size))
        if hit:
            cached += 1
        else:
            download += int(descriptor.expected_size or 0)
    return {"artifact_count": len(descriptors), "cached_artifact_count": cached,
            "download_bytes": download}


def inspect_recovery(delivery: Path, root: Path, component_id: str,
                     selected_path: Path | None = None) -> ComponentRecovery | None:
    """Read an optional-operation journal; this never downloads or writes."""
    journal_path = root.resolve() / f"State/operations/component-{component_id}.json"
    if not journal_path.is_file():
        return None
    journal = OperationJournal.load(journal_path)
    if journal.data.get("status") == "succeeded":
        return None
    identity_matches = False
    try:
        if selected_path is None:
            plans = journal.data.get("artifacts", ())
            if len(plans) == 1 and plans[0].get("external_reuse"):
                selected_path = Path(plans[0]["external_reuse"]["path"])
        identity_matches = (journal.data.get("plan_digest") == operation_digest(
            delivery, root, component_id, selected_path))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    steps_match = tuple(item.get("name") for item in journal.data.get("steps", ())) == STEPS
    blocked = not identity_matches or not steps_match
    completed = sum(item.get("status") == "completed" for item in journal.data.get("steps", ()))
    if not identity_matches:
        summary = ("The previous setup used a different provider location or approved plan and cannot be resumed. "
                   "Its diagnostic record was preserved.")
    elif not steps_match:
        summary = "The interrupted operation uses an unsupported journal step contract."
    else:
        summary = "Setup was interrupted. Resume continues only the same authorized artifact plan."
    status = str(journal.data.get("status", ""))
    return ComponentRecovery(journal_path, component_id, status, completed, len(STEPS),
                             status in {"planned", "running", "cancelled", "failed"} and not blocked,
                             blocked, summary, str(selected_path) if selected_path else None)


def discard_cancelled_attempt(root: Path, component_id: str) -> Path:
    """Archive a cancelled journal without deleting artifacts, resources, or evidence."""
    journal_path = root.resolve() / f"State/operations/component-{component_id}.json"
    journal = OperationJournal.load(journal_path)
    if journal.data.get("status") != "cancelled":
        raise ValueError("Only a cancelled setup attempt can be discarded safely.")
    history = journal_path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    stem = f"component-{component_id}-cancelled-{str(journal.data.get('plan_digest', 'unknown'))[:12]}"
    target = history / f"{stem}.json"
    suffix = 1
    while target.exists():
        target = history / f"{stem}-{suffix}.json"; suffix += 1
    journal_path.replace(target)
    return target


def _acquire(descriptor: ArtifactDescriptor, delivery: Path, cache: Path,
             cache_source: Path | None, progress, journal: OperationJournal) -> object:
    policy = AcquisitionPolicy(acquisition_hosts(descriptor))
    if cache_source:
        candidate = cache_source / "verified" / descriptor.artifact_id / descriptor.version / descriptor.filename
        if candidate.is_file():
            return admit_local_artifact(descriptor, candidate, cache, policy)
    return acquire_artifact(
        descriptor, cache, policy, ca_bundle=delivery / "trust/cacert.pem", progress=progress,
        partial_observer=lambda path, state: (
            journal.record_unvalidated_acquisition(path) if state in {"created", "unvalidated"}
            else journal.clear_unvalidated_acquisition(path)),
        keep_partial_on_cancel=True)


def _resource_state(manifest, resource: dict, local_path: Path, validation: dict,
                    *, external: bool, external_adapter: str | None = None) -> ResourceState:
    descriptor = artifact_descriptor(resource)
    actual = sha256_file(local_path) if local_path.is_file() else descriptor.expected_sha256
    artifact = ({"sha256": actual,
                 "size": local_path.stat().st_size if local_path.is_file() else None}
                if external else
                {"artifact_id": descriptor.artifact_id, "version": descriptor.version,
                 "sha256": actual, "size": local_path.stat().st_size
                 if local_path.is_file() else None})
    return ResourceState(
        str(resource["resource_id"]),
        "external-resource" if external else str(resource["kind"]),
        (str(external_adapter) if external and external_adapter
         else str(resource["validation"]["adapter"])), str(local_path.resolve()),
        "user-supplied/external" if external else str(resource["ownership"]),
        "preserve-reference" if external else str(resource["disposition"]),
        artifact,
        ({"kind": "user-selected", "path": str(local_path.resolve())} if external else
         {"kind": "https", "url": descriptor.url, "publisher": descriptor.publisher,
          "source_name": descriptor.source_name}),
        dict(validation),
    )


def record_existing_selection(delivery: Path, root: Path, component_id: str,
                              selected_path: Path, facts: ComponentFacts) -> Path | None:
    """Persist an explicit validated external selection only for an active install."""
    active_path = root.resolve() / "State/installations/lic-lite.json"
    if not active_path.is_file():
        return None
    active = json.loads(active_path.read_text(encoding="utf-8"))
    profile = _profile(delivery)
    manifest = profile.component_by_id(component_id)
    inventory = with_profile(load_or_project_inventory(delivery, root, active), profile)
    path = selected_path.resolve()
    resource = ResourceState(
        f"external-{component_id}", "external-resource", definition_adapter(delivery, component_id),
        str(path), "user-supplied/external", "preserve-reference",
        {"sha256": sha256_file(path) if path.is_file() else None,
         "size": path.stat().st_size if path.is_file() else None},
        {"kind": "user-selected"},
        {"version": "existing-selection-v1", "passed": bool(facts.verified),
         "detail": facts.detail},
    )
    component = component_from_manifest(
        manifest, state="installed" if facts.verified else "partial",
        enabled=bool(facts.verified),
        readiness={"version": "existing-selection-v1", "passed": bool(facts.verified)},
        resources=(resource,))
    return write_inventory(root, replace_component(inventory, component))


def definition_adapter(delivery: Path, component_id: str) -> str:
    return _definition(delivery, component_id).validation_adapter


def execute(delivery: Path, root: Path, component_id: str, selected_path: Path | None = None, *,
            cache_source: Path | None = None, resume: bool = False,
            status: Callable[[dict], None] = lambda event: None,
            cancel_requested: Callable[[], bool] = lambda: False) -> dict:
    """Execute only an explicit component Install/Resume action."""
    if component_id == "florence-captioning":
        from .florence_component import execute as execute_florence
        if selected_path is None:
            raise ValueError("Florence requires its disclosed model storage location")
        legacy = execute_florence(delivery, root, selected_path, cache_source=cache_source,
                                  resume=resume, status=status,
                                  cancel_requested=cancel_requested)
        profile = _profile(delivery)
        manifest = profile.component_by_id(component_id)
        active = json.loads((root / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
        inventory = with_profile(load_or_project_inventory(delivery, root, active), profile)
        resource = ResourceState(
            "florence-model", "model", "huggingface-snapshot-v1", str(legacy["snapshot"]),
            "shared", "preserve-reference", {"digest": legacy["model_digest"]},
            {"kind": "huggingface", "repository": "florence-community/Florence-2-large-ft"},
            {"version": "florence-caption-v1", "passed": True})
        component = component_from_manifest(
            manifest, state="installed", enabled=True,
            readiness={"version": "florence-caption-v1", "passed": True},
            resources=(resource,))
        write_inventory(root, replace_component(inventory, component))
        return {**legacy, "component_inventory": str(root / "State/components/inventory.json")}

    root = root.resolve()
    plan = _plan(delivery, root, component_id, selected_path)
    digest = canonical_digest(plan)
    profile = _profile(delivery)
    manifest = profile.component_by_id(component_id)
    resources = _resources(manifest)
    paths = layout(root)
    if plan["external_reuse"]:
        # Explicit reuse must be proven before any package mutation occurs.
        validate_resource(resources[0], Path(plan["resource_destinations"][0]),
                          python=paths["venv"] / "Scripts/python.exe",
                          application=paths["application"] / "extracted")
    journal_path = root / f"State/operations/component-{component_id}.json"
    with process_lock(root):
        if resume:
            journal = OperationJournal.load(journal_path)
            if (journal.data.get("plan_digest") != digest or
                    tuple(item.get("name") for item in journal.data.get("steps", ())) != STEPS):
                raise ValueError("component journal does not match this exact authorized plan")
            reject_reparse_entries(root)
        else:
            if journal_path.exists():
                previous = OperationJournal.load(journal_path)
                if previous.data.get("status") != "succeeded":
                    raise ValueError("an interrupted component operation exists; use Resume")
                history = journal_path.parent / "history"
                history.mkdir(parents=True, exist_ok=True)
                stem = f"component-{component_id}-{str(previous.data.get('plan_digest', 'unknown'))[:12]}"
                archived = history / f"{stem}.json"
                sequence = 1
                while archived.exists():
                    archived = history / f"{stem}-{sequence}.json"
                    sequence += 1
                journal_path.replace(archived)
            journal = OperationJournal.create(
                journal_path.parent, f"component-{component_id}", target_path=root,
                plan_digest=digest, artifacts=[plan], steps=STEPS,
                inputs={"install_root": str(root), "component_id": component_id,
                        "resource_destinations": plan["resource_destinations"]})
        initial = {item["name"]: dict(item) for item in journal.data["steps"]}
        acquired_dependencies = []
        acquired_resources: list[object | None] = [None] * len(resources)
        installed_paths = [Path(value) for value in plan["resource_destinations"]]
        active = json.loads((root / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
        inventory = with_profile(load_or_project_inventory(delivery, root, active), profile)
        selected_ids = inventory.installed_component_ids | {component_id}
        final_lock = dependency_profile_for_components(profile, selected_ids)
        current_ids = inventory.installed_component_ids or {
            next(item.component_id for item in profile.components.values() if item.tier == "core")}
        prior_lock = dependency_profile_for_components(profile, current_ids)
        missing_names = set(final_lock.expected_inventory) - set(prior_lock.expected_inventory)
        delta = _lock_from_wheels(final_lock.profile + "-delta", final_lock.python_version,
                                  final_lock.platform,
                                  (item for item in final_lock.wheels if item.name in missing_names))
        current = ""
        current_number = 0

        def progress(event):
            if cancel_requested():
                raise AcquisitionCancelled("Cancellation requested")
            status({**event, "step": current_number, "total_steps": len(STEPS), "terminal": None})

        def emit(message, terminal=None):
            status({"kind": "status", "message": message, "step": current_number,
                    "total_steps": len(STEPS), "terminal": terminal})

        journal.set_status("running")
        try:
            results = {}
            for current_number, current in enumerate(STEPS, 1):
                if cancel_requested():
                    raise AcquisitionCancelled("Cancellation requested")
                prior = initial[current]
                journal.set_step(current, "running")
                emit(f"[{current_number}/{len(STEPS)}] {current.replace('_', ' ').title()}")
                if current == "verify_core":
                    python = paths["venv"] / "Scripts/python.exe"
                    application = paths["application"] / "extracted"
                    if not python.is_file() or not (application / "app.py").is_file():
                        raise RuntimeError("Core must be active before an optional component is installed")
                    result = {"passed": True, "active_record": str(root / "State/installations/lic-lite.json")}
                elif current == "acquire_dependencies":
                    acquired_dependencies = [
                        _acquire(ArtifactDescriptor.from_dict(item.artifact.as_dict()), delivery,
                                 paths["cache"], cache_source, progress, journal)
                        for item in delta.wheels]
                    result = {"verified": True, "count": len(acquired_dependencies),
                              "reused": sum(item.reused for item in acquired_dependencies)}
                elif current == "install_dependencies":
                    result = (prior.get("evidence", {}) if prior["status"] == "completed" else
                              install_locked_wheels(paths["venv"] / "Scripts/python.exe", delta,
                                                    tuple(acquired_dependencies),
                                                    paths["logs"] / f"component-{component_id}-dependencies.log")
                              if delta.wheels else {"wheel_count": 0, "offline": True})
                elif current == "acquire_resource":
                    if plan["external_reuse"]:
                        result = [{**plan["external_reuse"], "verified": True,
                                   "reused": True, "source": "user-selected"}]
                    else:
                        acquired_resources = [_acquire(artifact_descriptor(resource), delivery,
                                                       paths["cache"], cache_source, progress, journal)
                                              for resource in resources]
                        result = [item.as_dict() for item in acquired_resources]
                elif current == "install_resource":
                    if plan["external_reuse"]:
                        result = [{"path": str(installed_paths[0]), "external": True, "reused": True}]
                    else:
                        installed = []
                        for index, resource in enumerate(resources):
                            acquired = acquired_resources[index]
                            if acquired is None:
                                acquired = _acquire(artifact_descriptor(resource), delivery, paths["cache"],
                                                    cache_source, progress, journal)
                                acquired_resources[index] = acquired
                            installed.append(install_resource(root, resource, acquired))
                        result = installed
                        installed_paths = [Path(item["path"]) for item in installed]
                elif current == "validate":
                    environment = validate_environment(
                        paths["venv"] / "Scripts/python.exe", paths["venv"], paths["runtime"],
                        final_lock, require_cuda=any(item.name == "torch"
                                                     for item in final_lock.wheels),
                        lic_source_root=paths["application"] / "extracted")
                    if not environment.get("passed"):
                        raise RuntimeError("selected component dependency profile failed validation")
                    runtimes = [validate_resource(resource, installed_path,
                                                  python=paths["venv"] / "Scripts/python.exe",
                                                  application=paths["application"] / "extracted")
                                for resource, installed_path in zip(resources, installed_paths)]
                    result = {"passed": True, "environment": environment, "resources": runtimes}
                elif current == "publish":
                    resource_states = tuple(
                        _resource_state(manifest, resource, installed_path, runtime,
                                        external=bool(plan["external_reuse"]),
                                        external_adapter=(definition_adapter(delivery, component_id)
                                                          if plan["external_reuse"] else None))
                        for resource, installed_path, runtime in zip(
                            resources, installed_paths, results["validate"]["resources"]))
                    component = component_from_manifest(
                        manifest, state="installed", enabled=True,
                        readiness={"version": "component-resource-set-v1", "passed": True},
                        resources=resource_states)
                    inventory = replace_component(inventory, component)
                    target = write_inventory(root, inventory)
                    result = {"inventory": str(target), "component": component_id,
                              "resource": str(installed_paths[0]),
                              "resources": [str(item) for item in installed_paths]}
                results[current] = result
                journal.set_step(current, "completed", evidence=result)
                current = ""
            if cancel_requested():
                raise AcquisitionCancelled("Cancellation requested at the final safe boundary")
            journal.set_validation({"passed": True, "optional_capability_ready": True,
                                    "profile": inventory.selected_profile})
            journal.set_status("succeeded")
            emit("Optional capability is installed and verified.", terminal="success")
            return {"component": component_id, "state": "installed",
                    "resource": str(installed_paths[0]), "resources": [str(item) for item in installed_paths],
                    "profile": inventory.selected_profile}
        except AcquisitionCancelled as error:
            if current:
                journal.set_step(current, "cancelled", error=f"{type(error).__name__}: {error}")
            journal.set_status("cancelled", failure=f"{type(error).__name__}: {error}")
            emit("Installation canceled safely. Verified work was preserved.", terminal="cancelled")
            raise
        except BaseException as error:
            if current:
                journal.set_step(current, "failed", error=f"{type(error).__name__}: {error}")
            journal.set_status("failed", failure=f"{type(error).__name__}: {error}")
            emit(f"Installation stopped safely: {error}", terminal="error")
            raise
