"""Journaled managed relocation. The source remains intact; deletion is a separate decision."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable

from .acquisition import sha256_file
from .artifacts import AcquiredArtifact
from .bootstrap import profile, verify_extracted
from .bootstrap_layout import layout, reject_reparse_entries, validate_root
from .compatibility_profiles import dependency_profile_for_components, load_approved_profiles
from .component_adapters import move_resource, validate_moved_resource
from .component_state import (ComponentInventory, InstalledComponent, load_inventory,
                              write_inventory)
from .dependencies import install_locked_wheels
from .environment import create_final_path_venv
from .hf_source import hf_snapshot_target
from .journal import OperationJournal
from .managed_install import _atomic_json, _install_manager, _shortcut, _tree_digest, shortcut_locations
from .florence_component import profile as florence_profile
from .model_resources import ensure_model, verify_snapshot
from .process_lock import process_lock
from .storage import inspect_model_storage, validate_model_root
from .validation import validate_environment


MOVE_STEPS = ("copy_runtime", "copy_cache", "copy_application", "copy_user_state",
              "rebuild_environment", "install_manager", "prepare_models",
              "validate_destination", "publish_destination", "update_shortcuts")


def _inside(root: Path, candidate: Path) -> bool:
    root, candidate = root.resolve(), candidate.resolve()
    return candidate == root or root in candidate.parents


def _copy_tree(source: Path, destination: Path) -> dict:
    if not source.is_dir():
        raise FileNotFoundError("Required managed source is missing: " + str(source))
    expected = _tree_digest(source)
    if destination.exists():
        if not destination.is_dir() or _tree_digest(destination) != expected:
            raise ValueError("Existing destination content does not match the managed source: " + str(destination))
        return {"source": str(source), "destination": str(destination), "sha256": expected, "reused": True}
    partial = destination.with_name(destination.name + f".partial-{os.getpid()}")
    if partial.exists():
        raise FileExistsError("Unknown partial move content requires review: " + str(partial))
    partial.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, partial)
        if _tree_digest(partial) != expected:
            raise ValueError("Copied managed content failed verification")
        os.replace(partial, destination)
    except BaseException:
        if partial.is_dir():
            shutil.rmtree(partial)
        raise
    return {"source": str(source), "destination": str(destination), "sha256": expected, "reused": False}


def _locked_artifacts(lock, cache: Path) -> tuple[AcquiredArtifact, ...]:
    artifacts = []
    for wheel in lock.wheels:
        path = cache / "verified" / wheel.artifact.artifact_id / wheel.artifact.version / wheel.artifact.filename
        if not path.is_file() or sha256_file(path) != wheel.artifact.expected_sha256:
            raise ValueError("Verified dependency cache is incomplete for move: " + wheel.name)
        artifacts.append(AcquiredArtifact(wheel.artifact, str(path), wheel.artifact.expected_sha256,
                                          path.stat().st_size, "verified", True, True, 0))
    return tuple(artifacts)


def _source_florence_state(record: dict, component_record_path: Path) -> tuple[bool, bool]:
    """Return (preserve Florence, separately managed) without changing source state."""
    source_channel = record.get("channel")
    legacy = bool(isinstance(source_channel, dict) and source_channel.get("schema_version") == 1)
    managed = False
    if component_record_path.is_file():
        try:
            managed = json.loads(component_record_path.read_text(encoding="utf-8")).get("state") == "installed"
        except (OSError, ValueError):
            managed = False
    return legacy or managed, managed


def move_plan(source_root: Path, destination_root: Path, model_root: Path,
              *, copy_models: bool, new_model_root: Path | None = None) -> dict:
    source, destination = source_root.resolve(), destination_root.expanduser().absolute().resolve()
    selected_models = validate_model_root(new_model_root if copy_models else model_root)
    if source == destination or _inside(source, destination) or _inside(destination, source):
        raise ValueError("Choose a separate destination outside the current installation")
    return {
        "source": str(source), "destination": str(destination),
        "application": {"action": "copy-and-verify"},
        "runtime": {"action": "copy-and-verify"},
        "environment": {"action": "rebuild-at-final-path"},
        "user_state": {"action": "copy-and-preserve"},
        "models": {"action": "copy-and-verify" if copy_models else "remain",
                   "source": str(model_root), "destination": str(selected_models)},
        "old_installation": {"action": "retain", "automatic_deletion": False},
    }


def _validate_destination(delivery: Path, destination: Path, model_root: Path,
                          *, lock=None, model=None) -> dict:
    if lock is None:
        _recipe, _, lock, model, _channel = profile(delivery)
    places = layout(destination)
    application = places["application"] / "extracted"
    package = delivery / "artifacts/lic-lite.zip"
    files = verify_extracted(package, application)
    environment = validate_environment(places["venv"] / "Scripts/python.exe", places["venv"],
                                       places["runtime"], lock,
                                       require_cuda=any(item.name == "torch" for item in lock.wheels),
                                       lic_source_root=application)
    models = {"verified": True, "required_for_core": False, "model_root": str(model_root)}
    if model is not None:
        snapshot = hf_snapshot_target(model_root, model)
        model_record = snapshot.with_name(snapshot.name + ".resource.json")
        models = verify_snapshot(model, snapshot, revision=model.revision, strict=model_record.is_file())
    if not files or not environment.get("passed") or not models.get("verified"):
        raise RuntimeError("The new installation did not pass managed validation")
    return {"application_files": files, "environment": environment, "model": models,
            "launch_files": (places["venv"] / "Scripts/python.exe").is_file() and
                            (application / "app.py").is_file()}


def move_installation(delivery: Path, source_root: Path, destination_root: Path, *,
                      copy_models: bool = False, new_model_root: Path | None = None,
                      status: Callable[[dict], None] = lambda event: None,
                      boundary: Callable[[str], None] = lambda step: None) -> dict:
    """Build and validate a new managed installation; never delete the old one."""
    source, destination = source_root.resolve(), destination_root.expanduser().absolute().resolve()
    source_record_path = source / "State/installations/lic-lite.json"
    record = json.loads(source_record_path.read_text(encoding="utf-8"))
    if record.get("state") != "active" or Path(record.get("root", "")).resolve() != source:
        raise ValueError("Move requires an active manager-owned LIC installation")
    current_models = validate_model_root(Path(record.get("model_root") or layout(source)["models"]))
    generic_inventory = load_inventory(source)
    florence_record_path = source / "State/components/florence.json"
    preserve_florence, managed_florence = ((False, False) if generic_inventory else
                                           _source_florence_state(record, florence_record_path))
    plan = move_plan(source, destination, current_models, copy_models=copy_models,
                     new_model_root=new_model_root)
    if generic_inventory:
        plan["selected_profile"] = dict(generic_inventory.selected_profile)
        plan["components"] = sorted(generic_inventory.installed_component_ids)
        plan["installed_component_state"] = generic_inventory.as_dict()["components"]
        plan["resources"] = [
            {"component_id": component.component_id, "resource_id": resource.resource_id,
             "ownership": resource.ownership, "disposition": resource.disposition,
             "path": resource.local_path, "artifact": resource.artifact,
             "validation": resource.validation}
            for component in generic_inventory.components for resource in component.resources]
        plan["dependency_profile"] = "selected-dated-component-inventory"
    else:
        plan["dependency_profile"] = ("preserve-core-with-florence" if preserve_florence
                                      else "provider-neutral-core-v2")
    selected_models = Path(plan["models"]["destination"])
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    journal_path = source / "State/operations" / f"move-{digest[:12]}.json"
    resume = journal_path.exists()
    validate_root(destination, delivery=delivery, resume=resume)
    if resume:
        journal = OperationJournal.load(journal_path)
        if journal.data["plan_digest"] != digest or tuple(s["name"] for s in journal.data["steps"]) != MOVE_STEPS:
            raise ValueError("Move journal does not match this exact plan")
        if journal.data["status"] == "succeeded":
            return json.loads((destination / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
        reject_reparse_entries(destination)
    else:
        journal = OperationJournal.create(journal_path.parent, f"move-{digest[:12]}", target_path=destination,
                                          plan_digest=digest, artifacts=[plan], steps=MOVE_STEPS)
    initial_steps = {step["name"]: dict(step) for step in journal.data["steps"]}
    lock_roots = sorted((source, destination), key=lambda item: str(item).casefold())
    current = ""
    with ExitStack() as stack:
        for locked in lock_roots:
            stack.enter_context(process_lock(locked))
        journal.set_status("running")
        destination.mkdir(parents=True, exist_ok=True)
        destination_places = layout(destination)
        source_places = layout(source)
        recipe, _, lock, model, channel = profile(delivery)
        source_channel = record.get("channel")
        selected_profile = None
        if generic_inventory:
            expected = generic_inventory.selected_profile
            selected_profile = next((item for item in load_approved_profiles(
                delivery / "recipes/compatibility/profiles")
                if item.profile_id == expected["profile_id"] and item.digest == expected["digest"]), None)
            if selected_profile is None:
                raise ValueError("Move cannot reproduce the installation's exact approved compatibility profile")
            lock = dependency_profile_for_components(
                selected_profile, generic_inventory.installed_component_ids)
            model = None
        elif preserve_florence:
            _core_lock, _delta_lock, lock, model = florence_profile(delivery)

        def complete(name: str, operation: Callable[[], dict]) -> dict:
            nonlocal current
            current = name
            status({"message": name.replace("_", " ").title(), "terminal": None})
            journal.set_step(name, "running")
            evidence = operation()
            journal.set_step(name, "completed", evidence=evidence)
            boundary(name)
            return evidence

        try:
            complete("copy_runtime", lambda: _copy_tree(source_places["runtime"], destination_places["runtime"]))
            complete("copy_cache", lambda: _copy_tree(source_places["cache"], destination_places["cache"]))
            complete("copy_application", lambda: _copy_tree(source_places["application"], destination_places["application"]))
            user_source = source_places["state"] / "User"
            def copy_user_state():
                if user_source.is_dir():
                    return _copy_tree(user_source, destination_places["state"] / "User")
                (destination_places["state"] / "User").mkdir(parents=True, exist_ok=True)
                return {"source": str(user_source), "destination": str(destination_places["state"] / "User"),
                        "empty": True, "reused": False}
            complete("copy_user_state", copy_user_state)

            def rebuild():
                python = destination_places["venv"] / "Scripts/python.exe"
                prior = initial_steps["rebuild_environment"]
                quarantined = None
                if destination_places["venv"].exists():
                    if prior.get("status") == "completed" and python.is_file():
                        return {"python": str(python), "rebuilt": True, "reused": True,
                                "profile": lock.profile}
                    quarantined = destination_places["venv"].with_name(
                        destination_places["venv"].name + f".interrupted-{os.getpid()}")
                    if quarantined.exists():
                        raise FileExistsError("Interrupted environment quarantine already exists")
                    os.replace(destination_places["venv"], quarantined)
                if not destination_places["venv"].exists():
                    python = create_final_path_venv(destination_places["runtime"] / "python.exe",
                                                    destination_places["venv"], approved_root=destination,
                                                    log_path=destination_places["logs"] / "move-venv.log")
                    install_locked_wheels(python, lock, _locked_artifacts(lock, destination_places["cache"]),
                                          destination_places["logs"] / "move-dependencies.log")
                return {"python": str(python), "rebuilt": True, "reused": False,
                        "quarantined": str(quarantined) if quarantined else None,
                        "profile": lock.profile}
            complete("rebuild_environment", rebuild)
            complete("install_manager", lambda: _install_manager(Path(record["manager"]).parent,
                                                                   destination / "Manager/current"))

            def models():
                if generic_inventory:
                    moved_components = []
                    for component in generic_inventory.components:
                        manifest = selected_profile.component_by_id(component.component_id)
                        resources = tuple(move_resource(
                            delivery, manifest, resource, source, destination, selected_models,
                            copy_shared_models=copy_models) for resource in component.resources)
                        moved_components.append(InstalledComponent(
                            component.capability_id, component.provider_id, component.component_id,
                            component.implementation_id, component.version, component.state,
                            component.enabled, dict(component.readiness), resources))
                    models.moved_inventory = ComponentInventory(
                        generic_inventory.schema_version, dict(generic_inventory.selected_profile),
                        destination.as_posix(), tuple(moved_components),
                        {**generic_inventory.migration, "moved_from": source.as_posix()})
                    return {"verified": True, "action": "inventory-driven",
                            "components": len(moved_components),
                            "resources": sum(len(item.resources) for item in moved_components),
                            "model_root": str(selected_models)}
                if model is None:
                    if not copy_models:
                        return {"verified": True, "required_for_core": False,
                                "action": "remain", "model_root": str(current_models)}
                    if not current_models.is_dir():
                        selected_models.mkdir(parents=True, exist_ok=True)
                        return {"verified": True, "required_for_core": False,
                                "action": "copy-and-verify", "model_root": str(selected_models),
                                "empty": True}
                    return {**_copy_tree(current_models, selected_models), "verified": True,
                            "required_for_core": False, "action": "copy-and-verify",
                            "model_root": str(selected_models)}
                old_snapshot = hf_snapshot_target(current_models, model)
                if not copy_models:
                    record_path = old_snapshot.with_name(old_snapshot.name + ".resource.json")
                    return {**verify_snapshot(model, old_snapshot, revision=model.revision,
                                              strict=record_path.is_file()),
                            "action": "remain", "model_root": str(current_models)}
                target = hf_snapshot_target(selected_models, model)
                result = ensure_model(model, target, approved_root=selected_models, candidate=old_snapshot,
                                      candidate_revision=model.revision)
                return {**result, "action": "copy-and-verify", "model_root": str(selected_models)}
            complete("prepare_models", models)
            validation = complete("validate_destination",
                                  lambda: _validate_destination(delivery, destination, selected_models,
                                                                lock=lock, model=model))
            if generic_inventory:
                python = destination_places["venv"] / "Scripts/python.exe"
                application = destination_places["application"] / "extracted"
                checks = []
                for component in models.moved_inventory.components:
                    manifest = selected_profile.component_by_id(component.component_id)
                    for resource in component.resources:
                        if resource.kind in {"application", "python-environment"}:
                            continue
                        checks.append({
                            "component_id": component.component_id,
                            "resource_id": resource.resource_id,
                            "evidence": validate_moved_resource(
                                delivery, manifest, resource, python=python, application=application)})
                validation["component_resources"] = checks

            def publish():
                moved = dict(record)
                moved.update(root=destination.as_posix(),
                             application=str(destination_places["application"] / "extracted"),
                             python=str(destination_places["venv"] / "Scripts/python.exe"),
                             manager=str(destination / "Manager/current/LIC Install Manager.exe"),
                             model_root=str(selected_models),
                             channel=(source_channel if generic_inventory or preserve_florence else channel), shortcuts={},
                             move={"from": source.as_posix(), "source_retained": True,
                                   "plan_digest": digest,
                                   "preserved_florence": preserve_florence,
                                   "component_inventory_driven": bool(generic_inventory)})
                if generic_inventory:
                    write_inventory(destination, models.moved_inventory)
                _atomic_json(destination / "State/installations/lic-lite.json", moved)
                if managed_florence:
                    component = json.loads(florence_record_path.read_text(encoding="utf-8"))
                    component["model_root"] = str(selected_models)
                    if copy_models:
                        evidence = inspect_model_storage(delivery, selected_models)
                        if evidence.get("status") != "compatible":
                            raise RuntimeError("Copied Florence model storage did not validate")
                        component["snapshot"] = evidence["snapshot"]
                        component["hub_root"] = evidence["hub_root"]
                    _atomic_json(destination / "State/components/florence.json", component)
                return moved
            moved_record = complete("publish_destination", publish)

            def shortcuts():
                target = Path(moved_record["manager"])
                choices = moved_record.get("choices", {"start_menu": True, "desktop": False})
                locations, created = shortcut_locations(), {}
                arguments = f'--launch --root "{destination}"'
                if choices.get("start_menu"):
                    created["start_menu_app"] = _shortcut(locations["start_menu_app"], target, arguments)
                    created["start_menu_manager"] = _shortcut(
                        locations["start_menu_manager"], target, f'--manage --root "{destination}"')
                if choices.get("desktop"):
                    created["desktop"] = _shortcut(locations["desktop"], target, arguments)
                moved_record["shortcuts"] = created
                _atomic_json(destination / "State/installations/lic-lite.json", moved_record)
                return {"shortcuts": created}
            complete("update_shortcuts", shortcuts)
            journal.set_validation({"passed": True, "destination": str(destination),
                                    "source_retained": True, "validation": validation})
            journal.add_cleanup_action("Offer separate removal of old manager-owned installation", performed=False)
            journal.set_status("succeeded")
            status({"message": "Move completed. The previous installation was retained.", "terminal": "success"})
            return moved_record
        except BaseException as error:
            if current:
                journal.set_step(current, "failed", error=f"{type(error).__name__}: {error}")
            journal.set_status("failed", failure=f"{type(error).__name__}: {error}")
            status({"message": "The new installation did not pass validation. Your existing installation is still available.",
                    "terminal": "error"})
            raise
