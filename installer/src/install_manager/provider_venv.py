"""Build optional package closures away from the activated LIC environment."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .active_venv import managed_venv
from .bootstrap_layout import layout
from .component_state import ComponentInventory, write_inventory
from .journal import OperationJournal
from .managed_install import _atomic_json
from .process_lock import process_lock


RECORD = "State/installations/lic-lite.json"


def new_generation(root: Path) -> Path:
    """Choose a final-path, manager-owned venv without moving a built Windows venv."""
    root = root.resolve()
    parent = layout(root)["venv"].parent.resolve()
    candidate = parent / ("venv-provider-" + uuid4().hex[:12])
    if candidate.exists() or candidate.parent.resolve() != parent:
        raise ValueError("Provider environment target is not a new managed generation")
    return candidate


def inventory_for_generation(inventory: ComponentInventory,
                             generation: Path) -> ComponentInventory:
    """Keep the Core Python resource pointed at the same generation as activation."""
    changed = []
    for component in inventory.components:
        if component.component_id != "lic-core":
            changed.append(component)
            continue
        resources = tuple(replace(resource, local_path=str(generation / "Scripts/python.exe"))
                          if resource.resource_id == "python-environment" else resource
                          for resource in component.resources)
        changed.append(replace(component, resources=resources))
    return replace(inventory, components=tuple(changed))


def record_candidate(journal, root: Path, old_record: dict,
                     old_inventory: ComponentInventory, old_venv: Path,
                     candidate: Path) -> None:
    previous = journal.data.get("provider_environment") or {}
    orphaned = list(previous.get("orphaned_generations", ()))
    if previous.get("candidate") and previous["candidate"] != str(candidate):
        orphaned.append(previous["candidate"])
    journal.data["provider_environment"] = {
        "old_venv": str(old_venv), "candidate": str(candidate),
        "old_record": old_record, "old_inventory": old_inventory.as_dict(),
        "orphaned_generations": orphaned, "promotion_pending": False,
    }
    journal._write()


def restore_incomplete_promotion(root: Path, journal) -> bool:
    """Roll back the small record/inventory publication window after a crash."""
    snapshot = journal.data.get("provider_environment") or {}
    if not snapshot.get("promotion_pending"):
        return False
    old_record = snapshot["old_record"]
    old_venv = Path(snapshot["old_venv"])
    if (not old_venv.is_dir() or Path(old_record["root"]).resolve() != root.resolve()
            or managed_venv(root, old_record) != old_venv):
        raise ValueError("Previous healthy Core generation is unavailable for provider rollback")
    _atomic_json(root / RECORD, old_record)
    write_inventory(root, ComponentInventory.from_dict(snapshot["old_inventory"]))
    snapshot["promotion_pending"] = False
    snapshot["rolled_back_incomplete_promotion"] = True
    journal._write()
    return True


def recover_pending_provider_promotions(root: Path) -> tuple[Path, ...]:
    """On Manager relaunch, restore only a journaled incomplete pointer switch."""
    operation_dir = root.resolve() / "State/operations"
    if not operation_dir.is_dir():
        return ()
    candidates = (*operation_dir.glob("component-*.json"), operation_dir / "florence.json")
    pending = []
    for path in candidates:
        if path.is_file():
            try:
                if (OperationJournal.load(path).data.get("provider_environment") or {}).get(
                        "promotion_pending"):
                    pending.append(path)
            except (OSError, ValueError, KeyError, TypeError):
                # Existing journal inspection owns the user-facing diagnostic.
                continue
    if not pending:
        return ()
    restored = []
    with process_lock(root):
        for path in pending:
            if restore_incomplete_promotion(root, OperationJournal.load(path)):
                restored.append(path)
    return tuple(restored)


def promote_generation(root: Path, journal, candidate: Path,
                       old_record: dict, old_inventory: ComponentInventory,
                       new_inventory: ComponentInventory, validate_active) -> dict:
    """Publish only a validated provider closure; restore old pointers on failure."""
    new_record = dict(old_record, python=str(candidate / "Scripts/python.exe"))
    snapshot = journal.data["provider_environment"]
    snapshot.update(new_record=new_record, new_inventory=new_inventory.as_dict(),
                    promotion_pending=True)
    journal._write()
    try:
        _atomic_json(root / RECORD, new_record)
        write_inventory(root, new_inventory)
        validate_active()
    except BaseException:
        _atomic_json(root / RECORD, old_record)
        write_inventory(root, old_inventory)
        snapshot["promotion_pending"] = False
        snapshot["rolled_back"] = True
        journal._write()
        raise
    snapshot["promotion_pending"] = False
    snapshot["promoted"] = True
    journal._write()
    return new_record
