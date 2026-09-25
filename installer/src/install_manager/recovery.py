"""Read-only recovery state derived from an interrupted bootstrap journal."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .storage import default_model_root


RESUMABLE_STATUSES = frozenset({"planned", "cancelled", "failed", "running", "succeeded"})


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def bootstrap_plan_digest(channel: dict, install_root: Path, model_root: Path) -> str:
    """Return the established M1.6 bootstrap identity digest."""
    install_root = _path(install_root)
    model_root = _path(model_root)
    identity = {"channel": channel, "root": str(install_root)}
    if model_root != default_model_root(install_root).resolve():
        identity["model_root"] = str(model_root)
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def core_bootstrap_plan_digest(channel: dict, install_root: Path) -> str:
    """Return the M1.9 Core-only identity; optional storage is intentionally absent."""
    identity = {"channel": channel, "root": str(_path(install_root)),
                "identity_contract": "lic-core-v2"}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class BootstrapRecovery:
    journal_path: Path
    status: str
    recorded_install_root: Path
    recorded_model_root: Path
    current_install_root: Path
    current_model_root: Path
    completed_steps: int
    total_steps: int
    model_identity_bound: bool = True
    recipe_generation: str = "legacy-core-with-florence"

    @property
    def install_matches(self) -> bool:
        return self.current_install_root == self.recorded_install_root

    @property
    def model_matches(self) -> bool:
        return not self.model_identity_bound or self.current_model_root == self.recorded_model_root

    @property
    def mismatch_fields(self) -> tuple[str, ...]:
        fields = []
        if not self.install_matches:
            fields.append("application location")
        if not self.model_matches:
            fields.append("model location")
        return tuple(fields)

    @property
    def blocked(self) -> bool:
        return bool(self.mismatch_fields)

    @property
    def resumable(self) -> bool:
        return self.status in RESUMABLE_STATUSES and not self.blocked

    @property
    def summary(self) -> str:
        if self.blocked:
            changed = " and ".join(self.mismatch_fields)
            return (f"This setup was started with a different {changed}. Restore the recorded "
                    "location to resume this setup, or begin a new installation in a different "
                    "empty location.")
        if self.status == "cancelled":
            return "Setup was paused. Verified completed work can be reused by Resume."
        if self.status == "succeeded":
            return "Setup is verified and ready to finish activation."
        return "Setup was interrupted. Verified completed work can be reused."


def inspect_bootstrap_recovery(journal_path: Path, *, current_install_root: Path,
                               current_model_root: Path) -> BootstrapRecovery | None:
    """Read a bootstrap journal without changing it or any surrounding state."""
    journal_path = journal_path.expanduser().resolve()
    if not journal_path.is_file():
        return None
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("operation_id") != "bootstrap":
        raise ValueError("Setup records have an unsupported identity.")
    status = data.get("status")
    if status not in RESUMABLE_STATUSES:
        return None
    recorded_install = _path(data["target_path"])
    inputs = data.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError("Setup records do not contain valid path inputs.")
    identity_contract = inputs.get("identity_contract")
    core_v2 = identity_contract == "lic-core-v2"
    recorded_model_value = inputs.get("model_root")
    recorded_model = (_path(recorded_model_value) if recorded_model_value
                      else default_model_root(recorded_install).resolve())
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
        raise ValueError("Setup records do not contain a valid release identity.")
    expected_digest = (core_bootstrap_plan_digest(artifacts[0], recorded_install) if core_v2 else
                       bootstrap_plan_digest(artifacts[0], recorded_install, recorded_model))
    if data.get("plan_digest") != expected_digest:
        raise ValueError("Setup path records do not match their recorded identity.")
    steps = data.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        raise ValueError("Setup records do not contain a valid step list.")
    return BootstrapRecovery(
        journal_path=journal_path,
        status=status,
        recorded_install_root=recorded_install,
        recorded_model_root=recorded_model,
        current_install_root=_path(current_install_root),
        current_model_root=_path(current_model_root),
        completed_steps=sum(1 for step in steps if step.get("status") == "completed"),
        total_steps=len(steps),
        model_identity_bound=not core_v2,
        recipe_generation=("core-v2" if core_v2 else "legacy-core-with-florence"),
    )


def restored_recovery_locations(recovery: BootstrapRecovery) -> tuple[Path, Path]:
    """Return the journal-recorded selections; this function performs no writes."""
    return recovery.recorded_install_root, recovery.recorded_model_root


def validate_new_recovery_target(candidate: Path, recovery: BootstrapRecovery) -> Path:
    """Require a distinct empty target while leaving the old partial root untouched."""
    candidate = candidate.expanduser().resolve()
    if candidate == recovery.recorded_install_root:
        raise ValueError("Choose a different empty location. The interrupted setup remains recoverable.")
    if candidate.exists() and (not candidate.is_dir() or any(candidate.iterdir())):
        raise FileExistsError("The new installation location must be empty.")
    return candidate
