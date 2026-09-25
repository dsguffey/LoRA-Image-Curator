"""Journaled replacement of an activated LIC environment.

Every generation is created at its final path. Promotion changes the durable
activation pointer, never a path-bound venv directory. The former generation
remains available until the promoted one passes a second validation.
"""
from __future__ import annotations

from dataclasses import replace
import ctypes
import json
import os
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

from .acquisition import AcquisitionCancelled
from .active_venv import managed_venv
from .artifacts import ArtifactDescriptor
from .bootstrap import profile as delivery_profile, verify_extracted
from .bootstrap_layout import layout
from .compatibility_profiles import (accepted_installed_profile,
                                     dependency_profile_for_components, recommended_profile)
from .component_adapters import validate_resource
from .component_operations import _acquire, _resources
from .component_state import ComponentInventory, InstalledComponent, load_inventory, write_inventory
from .dependencies import install_locked_wheels
from .dependency_lock import normalize_distribution
from .environment import create_final_path_venv
from .journal import OperationJournal
from .lic_readiness import run_probe
from .managed_install import _atomic_json
from .managed_resources import managed_data_layout
from .process_lock import process_lock
from .storage import inspect_model_storage
from .validation import validate_environment


STEPS = ("inspect", "acquire", "construct", "validate", "promote",
         "validate_active", "cleanup")
JOURNAL = "State/operations/core-repair.json"
RECORD = "State/installations/lic-lite.json"


def _generation(root: Path, path: Path) -> Path:
    """Refuse to touch any tree outside the two manager-owned venv name forms."""
    root = root.resolve()
    path = Path(path).absolute()
    parent = layout(root)["venv"].parent.resolve()
    if (path.parent.resolve() != parent or
            not (path.name == "venv" or path.name.startswith(("venv-repair-", "venv-provider-"))) or
            path.is_symlink() or getattr(path, "is_junction", lambda: False)()):
        raise ValueError("Repair environment is outside the managed LIC layout")
    return path


def _new_generation(root: Path) -> Path:
    return _generation(root, layout(root)["venv"].with_name("venv-repair-" + uuid4().hex[:12]))


def _read_active(root: Path) -> dict:
    record = json.loads((root / RECORD).read_text(encoding="utf-8"))
    managed_venv(root, record)
    return record


def _snapshot(journal: OperationJournal) -> dict:
    return journal.data["repair"]


def _save(journal: OperationJournal, **values) -> None:
    journal.data["repair"].update(values)
    journal._write()


def _validate(delivery: Path, root: Path, generation: Path, selected: set[str]) -> dict:
    profile = recommended_profile(delivery / "recipes/compatibility/profiles")
    lock = dependency_profile_for_components(profile, selected)
    places = layout(root)
    application = places["application"] / "extracted"
    verify_extracted(delivery / "artifacts/lic-lite.zip", application)
    report = validate_environment(generation / "Scripts/python.exe", generation,
                                  places["runtime"], lock,
                                  require_cuda=any(w.name == "torch" for w in lock.wheels),
                                  lic_source_root=application)
    if report.get("passed") is not True:
        raise RuntimeError("Replacement Core environment did not pass exact package and path validation")
    return report


def _optional_check(delivery: Path, root: Path, generation: Path,
                    component: InstalledComponent) -> dict:
    if component.component_id == "florence-captioning":
        model = next((r for r in component.resources if r.kind == "model"), None)
        if model is None:
            raise ValueError("Florence model was not recorded in the managed inventory")
        evidence = inspect_model_storage(delivery, Path(model.local_path))
        if evidence.get("status") != "compatible":
            raise ValueError("Florence model no longer matches the approved revision")
        snapshot = Path(evidence["snapshot"])
        probe = run_probe("caption", generation / "Scripts/python.exe",
                          layout(root)["application"] / "extracted", snapshot,
                          root / "Validation" / ("core-repair-florence-" + uuid4().hex[:8]),
                          snapshot.parent.parent.parent)
        if probe.get("passed") is not True:
            raise RuntimeError("Florence rejected the repaired environment or approved model")
        return {"passed": probe.get("passed") is True, "model": str(snapshot)}
    manifest = recommended_profile(delivery / "recipes/compatibility/profiles").component_by_id(
        component.component_id)
    resources = _resources(manifest)
    by_id = {r.resource_id: r for r in component.resources}
    results = []
    for resource in resources:
        saved = by_id.get(str(resource["resource_id"]))
        if saved is None:
            raise ValueError("Previously installed provider resource is missing from inventory")
        results.append(validate_resource(resource, Path(saved.local_path),
                                         python=generation / "Scripts/python.exe",
                                         application=layout(root)["application"] / "extracted"))
    return {"passed": True, "resources": results}


def inspect_recovery(delivery: Path, root: Path) -> dict | None:
    path = root.resolve() / JOURNAL
    if not path.is_file():
        return None
    try:
        journal = OperationJournal.load(path)
        data = journal.data
        repair = data["repair"]
        same_root = Path(data["target_path"]).resolve() == root.resolve()
        profile = accepted_installed_profile(
            delivery / "recipes/compatibility/profiles", repair["profile"],
            {"lic-core", *repair.get("intended_optional", ())})
        valid = (same_root and repair["profile"] == {"profile_id": profile.profile_id,
                  "digest": profile.digest} and
                 tuple(s["name"] for s in data["steps"]) == STEPS and
                 _generation(root, Path(repair["old_venv"])) and
                 _generation(root, Path(repair["replacement_venv"])))
        if not valid:
            raise ValueError("Repair record belongs to another root or approved profile")
        active = _read_active(root)
        current = managed_venv(root, active)
        if current not in {Path(repair["old_venv"]), Path(repair["replacement_venv"])}:
            raise ValueError("The active environment changed after this repair began")
        status = data.get("status")
        return {"status": status, "resumable": status in {"planned", "running", "cancelled"},
                "cleanup_pending": bool(repair.get("cleanup_pending")),
                "summary": ("Repair was interrupted. Resume will verify preserved work before changing the active installation."
                            if status in {"planned", "running", "cancelled"} else
                            "The last repair failed. Retry Repair after reviewing its details."
                            if status == "failed" else
                            "Repair is complete; removal of the old environment can be retried later."
                            if repair.get("cleanup_pending") else "Repair completed."),
                "journal": str(path)}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "stale", "resumable": False, "cleanup_pending": False,
                "summary": f"An older repair record cannot be resumed safely: {error}. It was preserved for review.",
                "journal": str(path)}


def _cleanup(journal: OperationJournal, root: Path) -> bool:
    old = _generation(root, Path(_snapshot(journal)["old_venv"]))
    replacement = _generation(root, Path(_snapshot(journal)["replacement_venv"]))
    if managed_venv(root) != replacement:
        raise ValueError("Cannot clean a rollback environment before promotion")
    if old == replacement:
        raise ValueError("Rollback and replacement environments are identical")
    try:
        if old.exists():
            _check_windows_delete_sharing(old)
            shutil.rmtree(old)
        _save(journal, cleanup_pending=False)
        journal.set_step("cleanup", "completed", evidence={"old_environment_removed": True})
        return True
    except OSError as error:
        _save(journal, cleanup_pending=True, cleanup_error=str(error))
        journal.set_step("cleanup", "failed", error=str(error))
        return False


def _check_windows_delete_sharing(directory: Path) -> None:
    """Defer cleanup before touching files when another process denies delete sharing.

    This is a preflight, not an atomic guarantee against a process opening a
    file between the check and removal. Any later failure is journaled.
    """
    if os.name != "nt":
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                          wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    for path in directory.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        handle = open_file(str(path), 0x00010000, 0x00000007, None, 3, 0, None)
        if handle == invalid:
            error = ctypes.get_last_error()
            raise PermissionError(error, "Old environment file is in use; close LIC before cleanup", str(path))
        close(handle)


def retry_cleanup(root: Path) -> bool:
    """Retry only post-success cleanup; a lock never changes Core readiness."""
    root = root.resolve()
    with process_lock(root):
        path = root / JOURNAL
        if not path.is_file():
            return False
        journal = OperationJournal.load(path)
        if journal.data.get("status") != "succeeded" or not _snapshot(journal).get("cleanup_pending"):
            return False
        return _cleanup(journal, root)


def _archive_journal(path: Path) -> Path:
    """Keep completed or unusable repair evidence before a new attempt."""
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    archived = history / f"core-repair-{uuid4().hex}.json"
    path.replace(archived)
    return archived


def execute(delivery: Path, root: Path, *, resume: bool = False,
            cache_source: Path | None = None,
            status: Callable[[dict], None] = lambda event: None,
            pause_requested: Callable[[], bool] = lambda: False) -> dict:
    """Repair an activated installation with a validated, separately built venv."""
    root = root.resolve()
    with process_lock(root):
        active = _read_active(root)
        old = managed_venv(root, active)
        profile = recommended_profile(delivery / "recipes/compatibility/profiles")
        _, _, core_lock, _, channel = delivery_profile(delivery)
        if active.get("channel", {}).get("release_id") != channel["release_id"]:
            raise ValueError("This manager delivery cannot repair a different LIC release")
        inventory = load_inventory(root)
        if inventory is None:
            raise ValueError("Core repair needs a matching durable component inventory")
        profile = accepted_installed_profile(
            delivery / "recipes/compatibility/profiles", inventory.selected_profile,
            inventory.installed_component_ids)
        core_id = next(c.component_id for c in profile.components.values() if c.tier == "core")
        core_profile = dependency_profile_for_components(profile, {core_id})
        identity = lambda wheels: {(normalize_distribution(w.name), w.version,
                                    w.artifact.expected_sha256) for w in wheels}
        if identity(core_profile.wheels) != identity(core_lock.wheels):
            raise ValueError("Core compatibility profile differs from the approved delivery lock")
        journal_path = root / JOURNAL
        if journal_path.exists():
            recovery = inspect_recovery(delivery, root)
            journal = OperationJournal.load(journal_path)
            if recovery and recovery["status"] == "succeeded":
                if recovery["cleanup_pending"] and not _cleanup(journal, root):
                    raise PermissionError("Old environment files are still in use. Close LIC and retry cleanup before another repair")
                _archive_journal(journal_path)
            elif recovery and recovery["status"] == "stale" and not resume:
                _archive_journal(journal_path)
            elif not resume:
                raise ValueError("An earlier Core repair exists; inspect and resume its preserved record")
            else:
                if not recovery or not recovery["resumable"] and recovery["status"] != "failed":
                    raise ValueError("Core repair record is not safely resumable")
                previous = _snapshot(journal)
                old = _generation(root, Path(previous["old_venv"]))
                replacement = _generation(root, Path(previous["replacement_venv"]))
                if managed_venv(root) not in {old, replacement}:
                    raise ValueError("Core changed after the interrupted repair; preserve it for review")
        if not journal_path.exists():
            if resume:
                raise ValueError("No matching Core repair exists to resume")
            replacement = _new_generation(root)
            journal = OperationJournal.create(journal_path.parent, "core-repair", target_path=root,
                                               plan_digest=profile.digest,
                                               artifacts=[{"profile_id": profile.profile_id,
                                                           "core_lock": core_lock.digest()}], steps=STEPS,
                                               inputs={"install_root": str(root)})
            journal.data["repair"] = {
                "profile": dict(inventory.selected_profile), "old_venv": str(old),
                "replacement_venv": str(replacement), "previous_record": active,
                "previous_inventory": json.loads(json.dumps(inventory.as_dict())),
                "intended_optional": sorted(inventory.installed_component_ids - {core_id}),
                "selected_optional": [], "optional_outcomes": {}, "promoted": False,
                "cleanup_pending": False, "partial_generations": []}
            journal._write()
        prior = _snapshot(journal)
        old = _generation(root, Path(prior["old_venv"]))
        replacement = _generation(root, Path(prior["replacement_venv"]))
        journal.set_status("running")
        try:
            if managed_venv(root) == replacement:
                selected = {core_id, *prior["selected_optional"]}
                try:
                    _validate(delivery, root, replacement, selected)
                    if "new_inventory" not in prior or "new_record" not in prior:
                        raise ValueError("Promoted repair lacks its validated activation snapshot")
                    _atomic_json(root / RECORD, prior["new_record"])
                    write_inventory(root, ComponentInventory.from_dict(prior["new_inventory"]))
                    _save(journal, promoted=True)
                    journal.set_step("validate_active", "completed", evidence={"passed": True})
                except BaseException:
                    if old.exists():
                        _atomic_json(root / RECORD, prior["previous_record"])
                        write_inventory(root, ComponentInventory.from_dict(prior["previous_inventory"]))
                        _save(journal, promoted=False)
                    raise
            else:
                if not old.exists():
                    raise FileNotFoundError("The previous environment is missing; no recoverable copy remains")
                journal.set_step("inspect", "completed", evidence={"old_preserved": str(old)})
                if pause_requested():
                    raise AcquisitionCancelled("Repair paused before acquiring packages")
                # A partial venv cannot be trusted after process interruption.
                # Preserve it for diagnostics, and start a new final-path generation.
                if replacement.exists() and not any(s["name"] == "validate" and
                                                     s["status"] == "completed" for s in journal.data["steps"]):
                    partial = list(prior["partial_generations"])
                    partial.append(str(replacement))
                    replacement = _new_generation(root)
                    _save(journal, replacement_venv=str(replacement), partial_generations=partial)
                selected = {core_id, *prior["selected_optional"]}
                validated_before = replacement.is_dir() and any(
                    s["name"] == "validate" and s["status"] == "completed"
                    for s in journal.data["steps"])
                if not validated_before:
                    outcomes = dict(prior["optional_outcomes"])
                    intended = list(prior["intended_optional"])
                    cache = managed_data_layout(root)["downloads"]
                    source = cache_source or delivery / "offline-artifacts"
                    acquired = {}
                    def fetch(wheel):
                        key = wheel.artifact.expected_sha256
                        if key not in acquired:
                            descriptor = ArtifactDescriptor.from_dict(wheel.artifact.as_dict())
                            acquired[key] = _acquire(descriptor, delivery, root, cache, source,
                                                     lambda event: status(event), journal)
                        return acquired[key]
                    for wheel in core_profile.wheels:
                        fetch(wheel)
                    selected = {core_id}
                    for component_id in intended:
                        if pause_requested():
                            raise AcquisitionCancelled("Repair paused before restoring optional providers")
                        try:
                            candidate = dependency_profile_for_components(profile, selected | {component_id})
                            for wheel in candidate.wheels:
                                fetch(wheel)
                            selected.add(component_id)
                            outcomes[component_id] = {"packages": "available"}
                        except AcquisitionCancelled:
                            raise
                        except Exception as error:
                            outcomes[component_id] = {"packages": "unavailable", "error": str(error)}
                    _save(journal, selected_optional=sorted(selected - {core_id}),
                          optional_outcomes=outcomes)
                    journal.set_step("acquire", "completed", evidence={"selected": sorted(selected)})
                    lock = dependency_profile_for_components(profile, selected)
                    if pause_requested():
                        raise AcquisitionCancelled("Repair paused before building the replacement")
                    python = create_final_path_venv(layout(root)["runtime"] / "python.exe", replacement,
                                                    approved_root=root,
                                                    log_path=root / "Logs/core-repair-create.log")
                    install_locked_wheels(python, lock,
                                          tuple(fetch(wheel) for wheel in lock.wheels),
                                          root / "Logs/core-repair-dependencies.log")
                    journal.set_step("construct", "completed", evidence={"path": str(replacement),
                                                                          "package_count": len(lock.wheels)})
                    try:
                        report = _validate(delivery, root, replacement, selected)
                    except Exception:
                        if selected == {core_id}:
                            raise
                        # A failed optional package closure never becomes active.
                        # Build a fresh Core-only generation, preserving both old
                        # and failed candidate for review.
                        partial = list(prior["partial_generations"])
                        partial.append(str(replacement))
                        replacement = _new_generation(root)
                        for component_id in selected - {core_id}:
                            outcomes[component_id] = {"packages": "validation-failed",
                                                      "error": "Combined optional package closure failed exact validation"}
                        selected = {core_id}
                        _save(journal, replacement_venv=str(replacement),
                              partial_generations=partial, selected_optional=[],
                              optional_outcomes=outcomes)
                        python = create_final_path_venv(layout(root)["runtime"] / "python.exe", replacement,
                                                        approved_root=root,
                                                        log_path=root / "Logs/core-repair-create-core-only.log")
                        install_locked_wheels(python, core_profile,
                                              tuple(fetch(wheel) for wheel in core_profile.wheels),
                                              root / "Logs/core-repair-core-only.log")
                        report = _validate(delivery, root, replacement, selected)
                    journal.set_step("validate", "completed", evidence={"passed": True,
                                                                         "path_consistency": report["path_consistency"]})
                else:
                    selected = {core_id, *prior["selected_optional"]}
                    _validate(delivery, root, replacement, selected)
                if pause_requested():
                    raise AcquisitionCancelled("Repair paused before activation")
                outcomes = dict(_snapshot(journal)["optional_outcomes"])
                updated_components = []
                for component in ComponentInventory.from_dict(prior["previous_inventory"]).components:
                    if component.component_id == core_id:
                        resources = tuple(replace(r, local_path=str(replacement / "Scripts/python.exe"))
                                          if r.resource_id == "python-environment" else r
                                          for r in component.resources)
                        updated_components.append(replace(component, state="installed", enabled=True,
                                                          readiness={"version": "lic-core-readiness-v2", "passed": True},
                                                          resources=resources))
                    elif component.component_id in prior["intended_optional"]:
                        if component.component_id in selected:
                            try:
                                result = _optional_check(delivery, root, replacement, component)
                                outcomes[component.component_id] = {"passed": True, "validation": result}
                                updated_components.append(replace(component, state="installed", enabled=True,
                                                                  readiness={"version": "repair-v1", "passed": True}))
                            except Exception as error:
                                outcomes[component.component_id] = {"passed": False, "error": str(error)}
                                # Packages remain in the venv, so retain package-set
                                # membership while marking readiness false.
                                updated_components.append(replace(component, state="installed", enabled=True,
                                                                  readiness={"version": "repair-v1", "passed": False,
                                                                             "reason": str(error)}))
                        else:
                            updated_components.append(replace(component, state="partial", enabled=False,
                                                              readiness={"version": "repair-v1", "passed": False,
                                                                         "reason": outcomes.get(component.component_id, {}).get("error",
                                                                                "Provider packages could not be restored")}))
                    else:
                        updated_components.append(component)
                updated_inventory = replace(ComponentInventory.from_dict(prior["previous_inventory"]),
                                            components=tuple(updated_components))
                new_record = dict(prior["previous_record"], python=str(replacement / "Scripts/python.exe"))
                _save(journal, optional_outcomes=outcomes, new_record=new_record,
                      new_inventory=json.loads(json.dumps(updated_inventory.as_dict())))
                try:
                    _atomic_json(root / RECORD, new_record)
                    write_inventory(root, updated_inventory)
                    _save(journal, promoted=True)
                    journal.set_step("promote", "completed", evidence={"active_python": new_record["python"]})
                    _validate(delivery, root, replacement, selected)
                    journal.set_step("validate_active", "completed", evidence={"passed": True})
                except BaseException:
                    if old.exists():
                        _atomic_json(root / RECORD, prior["previous_record"])
                        write_inventory(root, ComponentInventory.from_dict(prior["previous_inventory"]))
                        _save(journal, promoted=False)
                    raise
            journal.set_validation({"passed": True, "core_ready": True,
                                    "optional_outcomes": _snapshot(journal)["optional_outcomes"],
                                    "activated": True})
            journal.set_status("succeeded")
            cleaned = _cleanup(journal, root)
            status({"kind": "status", "message": "Core repair passed validation." +
                    (" Old files will be removed after they are no longer in use." if not cleaned else ""),
                    "terminal": "success"})
            return {"record": _read_active(root), "optional_outcomes": _snapshot(journal)["optional_outcomes"],
                    "cleanup_pending": not cleaned}
        except AcquisitionCancelled as error:
            journal.set_status("cancelled", failure=str(error))
            raise
        except BaseException as error:
            journal.set_status("failed", failure=f"{type(error).__name__}: {error}")
            raise
