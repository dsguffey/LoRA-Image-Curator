"""Durable, inspectable operation journal for M1.2 mutations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any


_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OperationJournal:
    """Atomic JSON snapshots; every step remains understandable after interruption."""

    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self.data = data

    @classmethod
    def create(cls, root: Path, operation_id: str, *, target_path: Path,
               plan_digest: str, artifacts: list[dict[str, Any]], steps: tuple[str, ...],
               inputs: dict[str, str] | None = None) -> "OperationJournal":
        if not _OPERATION_ID.fullmatch(operation_id):
            raise ValueError("unsafe operation ID")
        root = root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{operation_id}.json"
        if path.exists():
            raise FileExistsError(f"operation journal already exists: {path}")
        now = _now()
        data = {
            "schema_version": 1, "operation_id": operation_id,
            "target_path": target_path.expanduser().resolve().as_posix(),
            "plan_digest": plan_digest, "artifacts": artifacts,
            "inputs": deepcopy(inputs or {}),
            "status": "planned", "started_at": now, "updated_at": now,
            "steps": [{"name": name, "status": "planned"} for name in steps],
            "failure": None, "cleanup_actions": [], "unvalidated_acquisitions": [],
            "final_validation": None,
        }
        journal = cls(path, data)
        journal._write()
        return journal

    def _write(self) -> None:
        self.data["updated_at"] = _now()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = (json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with temporary.open("wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        # Windows readers may briefly deny replacement when they open without
        # delete sharing (Explorer, diagnostics, or the acceptance observer).
        # Keep the complete old snapshot visible and retry for a bounded period.
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.replace(temporary, self.path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def set_status(self, status: str, *, failure: str | None = None) -> None:
        if status not in {"planned", "running", "failed", "cancelled", "succeeded"}:
            raise ValueError("invalid operation status")
        self.data["status"] = status
        self.data["failure"] = failure
        self._write()

    def set_step(self, name: str, status: str, **evidence: Any) -> None:
        if status not in {"planned", "running", "failed", "cancelled", "completed"}:
            raise ValueError("invalid step status")
        for step in self.data["steps"]:
            if step["name"] == name:
                step["status"] = status
                step.update(evidence)
                self._write()
                return
        raise KeyError(f"unknown journal step: {name}")

    def set_validation(self, report: dict[str, Any]) -> None:
        self.data["final_validation"] = deepcopy(report)
        self._write()

    def add_cleanup_action(self, action: str, *, performed: bool) -> None:
        self.data["cleanup_actions"].append({"action": action, "performed": performed})
        self._write()

    def record_unvalidated_acquisition(self, path: Path | str) -> None:
        """Record only an attempt-owned temporary download, never a trusted artifact."""
        value = str(Path(path).expanduser().resolve())
        values = self.data.setdefault("unvalidated_acquisitions", [])
        if value not in values:
            values.append(value)
            self._write()

    def clear_unvalidated_acquisition(self, path: Path | str) -> None:
        value = str(Path(path).expanduser().resolve())
        values = self.data.setdefault("unvalidated_acquisitions", [])
        if value in values:
            values.remove(value)
            self._write()

    @classmethod
    def load(cls, path: Path) -> "OperationJournal":
        path = path.expanduser().resolve()
        return cls(path, json.loads(path.read_text(encoding="utf-8")))
