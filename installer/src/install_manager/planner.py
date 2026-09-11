"""Produce a deterministic, non-installing plan from verified facts."""
from __future__ import annotations

from pathlib import Path

from .detection import detect_explicit
from .models import ActionKind, InstallPlan, PackageInspection, PlanAction, ResourceCandidate, ResourceState
from .recipe import LicRecipe


def build_plan(recipe: LicRecipe, package: PackageInspection, staging_directory: Path,
               candidates: tuple[ResourceCandidate, ...] = ()) -> InstallPlan:
    """Build an explicit M1.1 plan; refuse to plan activation from an invalid package."""
    if not package.verified:
        raise ValueError(f"Cannot plan an unverified LIC package: {list(package.errors)}")
    stage = staging_directory.expanduser().resolve()
    actions = (
        PlanAction(ActionKind.DETECT, ResourceState.DETECTED, "inspect explicitly supplied existing resources"),
        PlanAction(ActionKind.ACQUIRE, ResourceState.PLANNED_ACQUISITION, "use the selected verified package; no download executed"),
        PlanAction(ActionKind.VERIFY, ResourceState.VERIFIED, "verify source identity, archive integrity and manifest completeness"),
        PlanAction(ActionKind.STAGE, ResourceState.STAGED, "copy the verified package into the approved staging directory"),
        PlanAction(ActionKind.EXTRACT, ResourceState.STAGED, "extract only into the approved staging directory"),
        PlanAction(ActionKind.VALIDATE, ResourceState.VERIFIED, "run package/setup validation after staging"),
        PlanAction(ActionKind.ACTIVATE, ResourceState.NOT_EXECUTED, "activation is outside M1.1 and was not executed"),
    )
    notes = (
        "No LIC installation, environment creation, dependency installation or model download occurred.",
        "Existing resources are observations only; no ownership or deletion authority is inferred.",
        "The plan is valid only for this exact package digest and recipe version.",
    )
    return InstallPlan(
        schema_version=1, application_id=recipe.application_id, display_name=recipe.display_name,
        application_version=recipe.version, package=package, staging_directory=stage.as_posix(),
        existing_resources=tuple(sorted(candidates, key=lambda item: (item.kind, item.path.casefold(), item.path))),
        actions=actions, activation_allowed=False, installation_executed=False, notes=notes,
    )
