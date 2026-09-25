"""Read-only classification of the one currently selected LIC installation."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .active_venv import managed_venv
from .bootstrap import STEPS as BOOTSTRAP_STEPS, profile as delivery_profile, verify_extracted
from .bootstrap_layout import layout
from .compatibility_profiles import dependency_profile_for_components, recommended_profile
from .component_state import load_inventory
from .core_repair import inspect_recovery as inspect_repair_recovery
from .journal import OperationJournal
from .recovery import inspect_bootstrap_recovery
from .storage import default_model_root
from .validation import validate_environment


@dataclass(frozen=True, slots=True)
class SelectedRootState:
    root: Path
    action: str                  # install, ready, repair, resume-bootstrap, resume-repair
    detail: str
    record: dict | None = None
    diagnostic: str = ""


def inspect_selected_root(delivery: Path, root: Path) -> SelectedRootState:
    root = root.expanduser().resolve()
    record_path = root / "State/installations/lic-lite.json"
    if record_path.exists():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            generation = managed_venv(root, record)
        except (OSError, ValueError, TypeError, KeyError) as error:
            return SelectedRootState(root, "repair", "Core activation information needs review before LIC can launch.",
                                     diagnostic=str(error))
        repair = inspect_repair_recovery(delivery, root)
        if repair and repair["resumable"]:
            return SelectedRootState(root, "resume-repair", repair["summary"], record)
        try:
            places = layout(root)
            _, _, core_lock, _, channel = delivery_profile(delivery)
            if record.get("channel", {}).get("release_id") != channel["release_id"]:
                raise ValueError("Selected LIC uses another release; use its matching manager package")
            inventory = load_inventory(root)
            if inventory is None:
                raise ValueError("Managed component inventory is missing")
            approved = recommended_profile(delivery / "recipes/compatibility/profiles")
            if inventory.selected_profile != {"profile_id": approved.profile_id,
                                              "digest": approved.digest}:
                raise ValueError("Installed component profile differs from this manager")
            core_id = next(c.component_id for c in approved.components.values() if c.tier == "core")
            selected = set(inventory.installed_component_ids) | {core_id}
            lock = dependency_profile_for_components(approved, selected)
            application = places["application"] / "extracted"
            verify_extracted(delivery / "artifacts/lic-lite.zip", application)
            report = validate_environment(generation / "Scripts/python.exe", generation,
                                          places["runtime"], lock,
                                          require_cuda=any(w.name == "torch" for w in lock.wheels),
                                          lic_source_root=application)
            if not report.get("passed"):
                raise RuntimeError("The installed Python environment did not pass package or path checks")
            message = ("Core is installed and verified. Old environment files will be removed when they are no longer in use."
                       if repair and repair.get("cleanup_pending") else "Core is installed and verified.")
            return SelectedRootState(root, "ready", message, record)
        except Exception as error:
            detail = "Core files need repair. The existing environment will be kept until a replacement is verified."
            if repair and repair["status"] == "stale":
                detail += " An older repair record was preserved for review."
            return SelectedRootState(root, "repair", detail, record, str(error))
    bootstrap_path = root / "State/operations/bootstrap.json"
    if bootstrap_path.is_file():
        try:
            journal = OperationJournal.load(bootstrap_path)
            _, _, _, _, channel = delivery_profile(delivery)
            recovery = inspect_bootstrap_recovery(
                bootstrap_path, current_install_root=root,
                current_model_root=default_model_root(root))
            valid = (recovery is not None and not recovery.blocked and
                     recovery.recipe_generation == "core-v2" and
                     tuple(s.get("name") for s in journal.data["steps"]) == BOOTSTRAP_STEPS and
                     journal.data["artifacts"] == [channel] and
                     journal.data.get("status") in {"planned", "running", "cancelled", "succeeded"})
            if valid and journal.data.get("status") == "succeeded":
                final = journal.data.get("final_validation") or {}
                valid = final.get("activation_preflight_passed") is True and not final.get("activated")
            if valid:
                return SelectedRootState(root, "resume-bootstrap", recovery.summary)
            return SelectedRootState(root, "install", "Old setup state cannot be resumed here. Choose a new empty installation location; the diagnostic record remains in this folder.",
                                     diagnostic=str(journal.data.get("failure") or "Setup plan is stale or complete"))
        except (OSError, ValueError, KeyError, TypeError) as error:
            return SelectedRootState(root, "install", "Setup state is unreadable. Choose a new empty installation location; this folder was preserved.", diagnostic=str(error))
    return SelectedRootState(root, "install", "Core is not installed here.")
