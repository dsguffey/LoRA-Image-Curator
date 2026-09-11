"""Activation, durable manager installation, shortcuts and normal LIC launch."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

from .activation import evaluate_criteria
from .bootstrap import profile, verify_extracted
from .bootstrap_layout import layout, reject_reparse_entries
from .journal import OperationJournal
from .lic_readiness import CORE_READINESS_CRITERIA
from .process_lock import process_lock
from .compatibility_profiles import recommended_profile
from .component_adapters import launch_environment, legacy_launch_environment
from .component_state import (ResourceState, component_from_manifest,
                              empty_inventory, replace_component,
                              load_inventory, write_inventory)
from .storage import default_model_root, validate_model_root
from .validation import validate_environment

ACTIVATION_STEPS = ("prepare_model_storage", "install_manager", "create_shortcuts", "publish_active_record")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def revalidate_testing_ready(delivery: Path, root: Path) -> dict:
    paths = layout(root.resolve())
    journal = OperationJournal.load(paths["state"] / "operations/bootstrap.json")
    final = journal.data.get("final_validation") or {}
    if journal.data.get("status") != "succeeded" or not final.get("activation_preflight_passed") or final.get("activated"):
        raise ValueError("A completed, unactivated testing-ready installation is required")
    reject_reparse_entries(root)
    recipe, _, lock, _model, channel = profile(delivery)
    application = paths["application"] / "extracted"
    installed_python = paths["venv"] / "Scripts/python.exe"
    package = delivery / "artifacts/lic-lite.zip"
    app_files = verify_extracted(package, application)
    environment = validate_environment(installed_python, paths["venv"], paths["runtime"], lock,
                                       require_cuda=False, lic_source_root=application)
    model_root = validate_model_root(Path(final.get("model_root") or default_model_root(root)))
    previous_checks = final.get("criteria", {}).get("checks", {})
    checks = dict(previous_checks, application_verified=app_files > 0,
                  runtime_verified=environment.get("passed") is True,
                  final_venv_valid=environment.get("path_consistency") is True,
                  dependencies_exact=environment.get("passed") is True,
                  pip_check=environment.get("pip_check", {}).get("exit_code") == 0,
                  required_imports=environment.get("passed") is True,
                  target_safe=True, recovery_available=True, journal_complete=True)
    criteria = evaluate_criteria(checks, provider_criteria=CORE_READINESS_CRITERIA)
    if not criteria["criteria_met"]:
        raise RuntimeError("Activation preflight revalidation failed: " + ", ".join(criteria["blockers"]))
    return {"criteria": criteria, "channel": channel, "application_files": app_files,
            "environment": environment, "model_root": str(model_root)}


def _install_manager(source: Path, destination: Path) -> dict:
    source = source.resolve()
    if not (source / "LIC Install Manager.exe").is_file():
        raise FileNotFoundError("The verified delivered manager directory is incomplete")
    expected = _tree_digest(source)
    if destination.exists():
        if _tree_digest(destination) != expected:
            raise ValueError("Installed manager files differ; repair is required")
        return {"path": str(destination), "sha256": expected, "reused": True}
    partial = destination.with_name(destination.name + f".partial-{os.getpid()}")
    if partial.exists():
        raise FileExistsError("An unknown partial manager installation requires review")
    partial.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, partial)
    if _tree_digest(partial) != expected:
        raise ValueError("Manager copy verification failed")
    os.replace(partial, destination)
    return {"path": str(destination), "sha256": expected, "reused": False}


def shortcut_locations() -> dict[str, Path]:
    appdata = Path(os.environ["APPDATA"])
    programs = appdata / "Microsoft/Windows/Start Menu/Programs"
    return {"start_menu_app": programs / "LoRA Image Curator.lnk",
            "start_menu_manager": programs / "LIC Install Manager.lnk",
            "desktop": Path(os.environ["USERPROFILE"]) / "Desktop" / "LoRA Image Curator.lnk"}


def _shortcut(path: Path, target: Path, arguments: str, *, runner=subprocess.run) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:IM_SHORTCUT_PATH);$s.TargetPath=$env:IM_SHORTCUT_TARGET;$s.Arguments=$env:IM_SHORTCUT_ARGUMENTS;$s.WorkingDirectory=$env:IM_SHORTCUT_WORKDIR;$s.Save()"
    command = [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
               "-NoProfile", "-NonInteractive", "-Command", script]
    environment = dict(os.environ, IM_SHORTCUT_PATH=str(path), IM_SHORTCUT_TARGET=str(target),
                       IM_SHORTCUT_ARGUMENTS=arguments, IM_SHORTCUT_WORKDIR=str(target.parent))
    runner(command, check=True, timeout=30, env=environment)
    if not path.is_file():
        raise RuntimeError("Shortcut was not created")
    return {"path": str(path), "target": str(target), "arguments": arguments, "owned": True}


def activate(delivery: Path, root: Path, manager_source: Path, *, start_menu: bool = True,
             desktop: bool = False, model_root: Path | None = None, shortcut_runner=subprocess.run,
             boundary: Callable[[str], None] = lambda step: None) -> dict:
    root = root.resolve()
    with process_lock(root):
        preflight = revalidate_testing_ready(delivery, root)
        selected_model_root = validate_model_root(model_root or Path(preflight["model_root"]))
        journal_path = root / "State/operations/activation.json"
        identity = hashlib.sha256(json.dumps({"root": root.as_posix(), "channel": preflight["channel"],
                                             "model_root": str(selected_model_root)}, sort_keys=True).encode()).hexdigest()
        if journal_path.exists():
            journal = OperationJournal.load(journal_path)
            if journal.data["plan_digest"] != identity:
                raise ValueError("Activation journal does not match this installation")
            if journal.data["status"] == "succeeded":
                return json.loads((root / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
            if tuple(step["name"] for step in journal.data["steps"]) != ACTIVATION_STEPS:
                raise ValueError("Older interrupted activation state requires review")
        else:
            journal = OperationJournal.create(journal_path.parent, "activation", target_path=root,
                                              plan_digest=identity, artifacts=[preflight["channel"]], steps=ACTIVATION_STEPS)
        journal.set_status("running")
        try:
            journal.set_step("prepare_model_storage", "completed", evidence={
                "model_root": str(selected_model_root), "optional_content_acquired": False})
            boundary("prepare_model_storage")
            manager = _install_manager(manager_source, root / "Manager/current")
            journal.set_step("install_manager", "completed", evidence=manager)
            boundary("install_manager")
            target = root / "Manager/current/LIC Install Manager.exe"
            arguments = f'--launch --root "{root}"'
            shortcuts = {}
            locations = shortcut_locations()
            if start_menu:
                shortcuts["start_menu_app"] = _shortcut(locations["start_menu_app"], target, arguments,
                                                         runner=shortcut_runner)
                shortcuts["start_menu_manager"] = _shortcut(locations["start_menu_manager"], target,
                                                             f'--manage --root "{root}"', runner=shortcut_runner)
            if desktop:
                shortcuts["desktop"] = _shortcut(locations["desktop"], target, arguments, runner=shortcut_runner)
            journal.set_step("create_shortcuts", "completed", evidence={"shortcuts": shortcuts,
                             "choices": {"start_menu": start_menu, "desktop": desktop}})
            boundary("create_shortcuts")
            record = {"schema_version": 1, "product": "LIC Lite", "state": "active",
                      "root": root.as_posix(), "application": str(layout(root)["application"] / "extracted"),
                      "python": str(layout(root)["venv"] / "Scripts/python.exe"),
                      "manager": str(target), "channel": preflight["channel"], "shortcuts": shortcuts,
                      "model_root": str(selected_model_root),
                      "choices": {"start_menu": start_menu, "desktop": desktop},
                      "launch": {"module": "app.py", "working_directory": str(layout(root)["application"] / "extracted")}}
            profile_root = delivery / "recipes/compatibility/profiles"
            if profile_root.is_dir():
                compatibility = recommended_profile(profile_root)
                core = next(item for item in compatibility.components.values() if item.tier == "core")
                resources = (
                    ResourceState("application", "application", "managed-tree-v1",
                                  record["application"], "manager-owned", "copy-and-verify", {},
                                  {"kind": "approved-release-channel", "release_id": record["channel"]["release_id"]},
                                  {"version": "lic-core-readiness-v2", "passed": True}),
                    ResourceState("python-environment", "python-environment", "approved-profile-venv-v1",
                                  record["python"], "manager-owned", "rebuild", {},
                                  {"kind": "approved-compatibility-profile",
                                   "profile_id": compatibility.profile_id},
                                  {"version": "lic-core-readiness-v2", "passed": True}),
                )
                inventory = replace_component(
                    empty_inventory(root, compatibility),
                    component_from_manifest(
                        core, state="installed", enabled=True,
                        readiness={"version": "lic-core-readiness-v2", "passed": True},
                        resources=resources))
                write_inventory(root, inventory)
            _atomic_json(root / "State/installations/lic-lite.json", record)
            journal.set_step("publish_active_record", "completed", evidence={"state": "active"})
            journal.set_validation({"activation_preflight_passed": True, "activated": True})
            journal.set_status("succeeded")
            return record
        except BaseException as error:
            journal.set_status("failed", failure=f"{type(error).__name__}: {error}")
            raise


def launch(root: Path, *, runner=subprocess.Popen):
    root = root.resolve()
    record = json.loads((root / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
    if record.get("state") != "active" or Path(record["root"]).resolve() != root:
        raise ValueError("LIC is not active at this managed location")
    python = Path(record["python"])
    cwd = Path(record["application"])
    if not python.is_file() or not (cwd / "app.py").is_file():
        raise FileNotFoundError("Managed LIC launch files are incomplete; run Repair")
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        environment.pop(name, None)
    model_root = Path(record.get("model_root") or layout(root)["models"])
    hub_root = model_root / "huggingface/hub"
    try:
        inventory = load_inventory(root)
        bindings = (launch_environment(resource for component in inventory.components
                                       for resource in component.resources)
                    if inventory else legacy_launch_environment(root))
        path_prepend = bindings.pop("INSTALL_MANAGER_PATH_PREPEND", "")
        if path_prepend:
            environment["PATH"] = path_prepend + os.pathsep + environment.get("PATH", "")
        hub_root = Path(bindings.get("HF_HUB_CACHE", hub_root)).resolve()
    except (OSError, ValueError, TypeError, KeyError):
        pass
    hf_home = hub_root.parent if hub_root.name.casefold() == "hub" else hub_root
    user_state = root / "State/User"
    appdata = user_state / "AppData/Roaming"
    localappdata = user_state / "AppData/Local"
    appdata.mkdir(parents=True, exist_ok=True)
    localappdata.mkdir(parents=True, exist_ok=True)
    environment.update(HF_HOME=str(hf_home), HF_HUB_CACHE=str(hub_root),
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                       APPDATA=str(appdata), LOCALAPPDATA=str(localappdata),
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    environment.update(bindings)
    log_path = root / "Logs/lic-launch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab")
    try:
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_path(sys.argv[2],run_name='__main__')"
        return runner([str(python), "-I", "-B", "-c", bootstrap, str(cwd), str(cwd / "app.py")],
                      cwd=cwd, env=environment,
                      stdout=stream, stderr=subprocess.STDOUT)
    finally:
        stream.close()
