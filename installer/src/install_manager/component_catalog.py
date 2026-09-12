"""Declarative component catalog and lifecycle contracts for managed products."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import os
from pathlib import Path
import hashlib
import subprocess
import threading
from typing import Callable
from .child_process import run as child_run


class ComponentPhase(StrEnum):
    CHECKING = "checking"
    NOT_INSTALLED = "not-installed"
    PARTIAL = "partial"
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    CANCELING = "canceling"
    INSTALLED = "installed"
    UPDATE_AVAILABLE = "update-available"
    REPAIR_REQUIRED = "repair-required"
    INCOMPATIBLE = "incompatible"
    ERROR = "error"


class ComponentAction(StrEnum):
    INSTALL = "Install"
    RESUME = "Resume setup"
    CHECK_UPDATES = "Check for updates"
    UPDATE = "Update"
    REPAIR = "Repair"
    CANCEL = "Cancel"
    CANCEL_QUEUE = "Cancel queued"
    USE_EXISTING = "Use existing files"
    NONE = ""


@dataclass(frozen=True, slots=True)
class ComponentDefinition:
    component_id: str
    capability: str
    description: str
    tier: str
    required_for_readiness: bool
    provider: str
    publisher: str
    source_name: str
    source_url: str
    third_party: bool
    estimated_bytes: int | None
    storage_policy: str
    selector_type: str
    identity: dict[str, object]
    license: str
    restrictions: str
    compatibility: str
    dependencies: tuple[str, ...]
    acquisition_adapter: str
    validation_adapter: str
    update_policy: str
    help_anchor: str
    managed_install: bool
    unavailable_reason: str = ""
    artifacts: tuple[dict[str, object], ...] = ()
    provider_policy: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tier not in {"core", "optional"}:
            raise ValueError("component tier must be core or optional")
        if self.required_for_readiness != (self.tier == "core"):
            raise ValueError("only core components participate in product readiness")
        if self.selector_type not in {"none", "file", "directory"}:
            raise ValueError("invalid existing-artifact selector type")
        if not all((self.component_id, self.capability, self.description, self.provider,
                    self.publisher, self.source_name, self.source_url, self.validation_adapter,
                    self.help_anchor)):
            raise ValueError("component disclosure is incomplete")
        if self.estimated_bytes is not None and self.estimated_bytes <= 0:
            raise ValueError("download estimate must be positive when known")
        if self.managed_install and self.acquisition_adapter in {"user-supplied", "deferred"}:
            raise ValueError("managed Install needs a real acquisition adapter")
        if not self.managed_install and not self.unavailable_reason:
            raise ValueError("unavailable managed Install requires an explicit reason")
        if self.provider_policy and self.provider_policy.get("payment") not in {
                "required", "optional", "none-identified"}:
            raise ValueError("invalid provider payment policy")


@dataclass(slots=True)
class ComponentFacts:
    phase: ComponentPhase = ComponentPhase.NOT_INSTALLED
    verified: bool = False
    resumable: bool = False
    completed_bytes: int = 0
    total_bytes: int | None = None
    selected_path: str = ""
    # The customer-facing provider folder can differ from the exact resource
    # member used by an operation (notably MediaPipe's .task file).
    resource_path: str = ""
    detail: str = ""
    approved_update_available: bool = False
    recovery_blocked: bool = False
    # A short, technical explanation shown only in Details.  The card uses
    # ``detail`` for a clear next action instead of exposing raw exceptions.
    diagnostic: str = ""


def load_component_catalog(path: Path) -> tuple[ComponentDefinition, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") not in {1, 2} or not isinstance(value.get("components"), list):
        raise ValueError("unsupported component catalog")
    result = []
    seen = set()
    for item in value["components"]:
        component_id = item.get("component_id")
        if component_id in seen:
            raise ValueError("duplicate component ID")
        seen.add(component_id)
        data = dict(item)
        data["dependencies"] = tuple(data.get("dependencies", ()))
        data["artifacts"] = tuple(data.get("artifacts", ()))
        result.append(ComponentDefinition(**data))
    known = {item.component_id for item in result}
    if any(dependency not in known for item in result for dependency in item.dependencies):
        raise ValueError("component dependency refers to an unknown component")
    return tuple(result)


def component_action(definition: ComponentDefinition, facts: ComponentFacts) -> ComponentAction:
    if facts.phase == ComponentPhase.QUEUED:
        return ComponentAction.CANCEL_QUEUE
    if facts.phase in {ComponentPhase.PREPARING, ComponentPhase.DOWNLOADING,
                       ComponentPhase.VERIFYING, ComponentPhase.INSTALLING,
                       ComponentPhase.CANCELING}:
        return ComponentAction.CANCEL
    if facts.phase == ComponentPhase.PARTIAL:
        if facts.recovery_blocked:
            return ComponentAction.NONE
        if facts.resumable:
            return ComponentAction.RESUME
        return ComponentAction.INSTALL if definition.managed_install else ComponentAction.NONE
    if facts.phase == ComponentPhase.UPDATE_AVAILABLE and facts.approved_update_available:
        return ComponentAction.UPDATE
    if facts.phase == ComponentPhase.UPDATE_AVAILABLE:
        return ComponentAction.NONE
    if facts.phase == ComponentPhase.REPAIR_REQUIRED:
        return ComponentAction.REPAIR
    if facts.phase == ComponentPhase.INSTALLED and facts.verified:
        return (ComponentAction.CHECK_UPDATES
                if definition.update_policy != "no-approved-channel" else ComponentAction.NONE)
    if facts.phase in {ComponentPhase.INCOMPATIBLE, ComponentPhase.ERROR}:
        return ComponentAction.USE_EXISTING if definition.selector_type != "none" else ComponentAction.REPAIR
    if not definition.managed_install:
        return ComponentAction.USE_EXISTING if definition.selector_type != "none" else ComponentAction.NONE
    return ComponentAction.INSTALL


def progress_presentation(facts: ComponentFacts) -> dict[str, object]:
    active = facts.phase in {ComponentPhase.CHECKING, ComponentPhase.PREPARING,
                             ComponentPhase.DOWNLOADING, ComponentPhase.VERIFYING,
                             ComponentPhase.INSTALLING, ComponentPhase.CANCELING}
    if facts.verified and facts.phase in {ComponentPhase.INSTALLED, ComponentPhase.UPDATE_AVAILABLE}:
        return {"mode": "determinate", "value": 100.0, "active": False}
    if facts.total_bytes and facts.total_bytes > 0:
        value = min(100.0, max(0.0, 100.0 * facts.completed_bytes / facts.total_bytes))
        return {"mode": "determinate", "value": value, "active": active}
    if active:
        return {"mode": "indeterminate", "value": 0.0, "active": True}
    return {"mode": "determinate", "value": 0.0, "active": False}


def product_ready(definitions: tuple[ComponentDefinition, ...],
                  facts: dict[str, ComponentFacts]) -> bool:
    required = [item for item in definitions if item.required_for_readiness]
    return bool(required) and all(facts[item.component_id].verified and
                                  facts[item.component_id].phase in
                                  {ComponentPhase.INSTALLED, ComponentPhase.UPDATE_AVAILABLE}
                                  for item in required)


class CancellationToken:
    def __init__(self) -> None:
        self._requested = threading.Event()

    def request(self) -> None:
        self._requested.set()

    def requested(self) -> bool:
        return self._requested.is_set()


@dataclass(slots=True)
class OperationRequest:
    component_id: str
    operation: str
    token: CancellationToken = field(default_factory=CancellationToken)


class ComponentOperationQueue:
    """One active component operation plus an explicit, cancelable FIFO queue."""

    def __init__(self) -> None:
        self.active: OperationRequest | None = None
        self.pending: list[OperationRequest] = []

    def submit(self, component_id: str, operation: str) -> tuple[OperationRequest, bool]:
        if self.active and self.active.component_id == component_id:
            return self.active, False
        existing = next((item for item in self.pending if item.component_id == component_id), None)
        if existing:
            return existing, False
        request = OperationRequest(component_id, operation)
        if self.active is None:
            self.active = request
            return request, True
        self.pending.append(request)
        return request, False

    def cancel(self, component_id: str) -> str:
        if self.active and self.active.component_id == component_id:
            self.active.token.request()
            return "canceling"
        for index, request in enumerate(self.pending):
            if request.component_id == component_id:
                self.pending.pop(index)
                return "queue-canceled"
        return "not-found"

    def state_for(self, component_id: str) -> str:
        """Return queue truth for UI labels; rendered component facts may lag worker events."""
        if self.active and self.active.component_id == component_id:
            return "active"
        if any(item.component_id == component_id for item in self.pending):
            return "queued"
        return "idle"

    def complete(self, component_id: str) -> OperationRequest | None:
        if self.active is None or self.active.component_id != component_id:
            raise ValueError("only the active component can complete")
        self.active = self.pending.pop(0) if self.pending else None
        return self.active


def default_install_root(local_appdata: str | None = None) -> Path:
    base = local_appdata or os.environ.get("LOCALAPPDATA", "")
    if not base:
        raise RuntimeError("LOCALAPPDATA is required for the default Windows installation location")
    return Path(base) / "LoRA Image Curator"


def validate_existing_selection(definition: ComponentDefinition, selected: Path,
                                *, runner=subprocess.run) -> ComponentFacts:
    """Validate a user selection without adopting, rewriting, or deleting it."""
    path = selected.expanduser().resolve()
    if definition.selector_type == "file" and not path.is_file():
        return ComponentFacts(ComponentPhase.INCOMPATIBLE, detail="The selected file does not exist.",
                              selected_path=str(path))
    if (definition.selector_type == "directory" and not path.is_dir() and
            not (definition.validation_adapter in {"mediapipe-task-file", "ffmpeg-executable"} and path.is_file())):
        return ComponentFacts(ComponentPhase.INCOMPATIBLE, detail="The selected folder does not exist.",
                              selected_path=str(path))
    if definition.validation_adapter == "mediapipe-task-file":
        provider_root, task = (path, path / "pose_landmarker_full.task") if path.is_dir() else (path.parent, path)
        if task.suffix.casefold() != ".task" or not task.is_file():
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="Choose a provider folder containing pose_landmarker_full.task.",
                                  selected_path=str(provider_root))
        expected = str(definition.identity.get("sha256", ""))
        actual = hashlib.sha256(task.read_bytes()).hexdigest()
        accepted = {expected, *map(str, definition.identity.get("accepted_equivalent_sha256s", ())) }
        if actual not in accepted:
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="The selected task file does not match the approved model hash.",
                                  selected_path=str(provider_root), resource_path=str(task))
        return ComponentFacts(ComponentPhase.PARTIAL, resumable=False, completed_bytes=task.stat().st_size,
                              total_bytes=task.stat().st_size, selected_path=str(provider_root), resource_path=str(task),
                              detail="Existing model verified. The MediaPipe package pack is still required.")
    if definition.validation_adapter == "yunet-sface-pair-v1":
        if not path.is_dir():
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="Choose the folder containing both approved Face Analysis ONNX files.",
                                  selected_path=str(path))
        artifacts = definition.artifacts
        expected = {str(item.get("filename")): item for item in artifacts
                    if isinstance(item, dict) and item.get("filename")}
        if len(expected) != 2:
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="The Face Analysis component metadata is incomplete.", selected_path=str(path))
        total = 0
        for filename, item in expected.items():
            candidate = path / filename
            if not candidate.is_file():
                return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                      detail=f"Face Analysis is incomplete: {filename} is missing.", selected_path=str(path))
            if candidate.stat().st_size != int(item.get("size", -1)):
                return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                      detail=f"Face Analysis is invalid: {filename} has the wrong size.", selected_path=str(path))
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != str(item.get("sha256", "")):
                return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                      detail=f"Face Analysis is invalid: {filename} does not match the approved hash.", selected_path=str(path))
            total += candidate.stat().st_size
        return ComponentFacts(ComponentPhase.INSTALLED, verified=True, completed_bytes=total,
                              total_bytes=total, selected_path=str(path),
                              detail="Existing OpenCV YuNet + SFace models verified. No download is required.")
    if definition.validation_adapter == "insightface-pack-directory":
        if path.parent.name.casefold() != "models" or not any(path.rglob("*.onnx")):
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="Choose an InsightFace pack folder directly inside a models folder.",
                                  selected_path=str(path))
        return ComponentFacts(
            ComponentPhase.PARTIAL, selected_path=str(path),
            detail=("Not configured. InsightFace model files were found and verified. Automatic "
                    "setup of the remaining InsightFace components is not currently available."))
    if definition.validation_adapter == "ffmpeg-executable":
        if path.is_dir():
            path = path / "ffmpeg.exe"
        if path.name.casefold() != "ffmpeg.exe" or not path.is_file():
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="Choose an FFmpeg folder containing ffmpeg.exe.", selected_path=str(path.parent))
        try:
            runner = child_run if runner is subprocess.run else runner
            result = runner([str(path), "-version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30, check=False,
                            shell=False)
        except subprocess.TimeoutExpired:
            return ComponentFacts(ComponentPhase.PARTIAL,
                                  detail="FFmpeg did not respond during verification. You can retry or choose another FFmpeg folder.",
                                  selected_path=str(path.parent), resource_path=str(path))
        except (OSError, subprocess.SubprocessError) as error:
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail=f"FFmpeg probe failed: {type(error).__name__}: {error}",
                                  selected_path=str(path.parent))
        output = "\n".join((result.stdout or "", result.stderr or ""))
        if result.returncode != 0 or "ffmpeg version" not in output.casefold():
            return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                                  detail="The selected program did not identify itself as FFmpeg.",
                                  selected_path=str(path.parent), resource_path=str(path))
        return ComponentFacts(
            ComponentPhase.INSTALLED, verified=True, selected_path=str(path.parent), resource_path=str(path),
            detail=("Existing compatible FFmpeg executable verified. Select it in LoRA Image "
                    "Curator's video dialog when you want to extract frames."))
    return ComponentFacts(ComponentPhase.INCOMPATIBLE,
                          detail="This component has no approved existing-file validator.",
                          selected_path=str(path))
