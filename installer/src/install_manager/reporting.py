"""Stable text/JSON reports for the M1.1 command line."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import InstallPlan, StagedPackage


def plan_report(plan: InstallPlan) -> dict[str, Any]:
    """Return a user-readable report with explicit non-execution state."""
    return {"plan": plan.as_dict(), "plan_sha256": plan.digest(),
            "message": "LIC package is verified and staged/plannable; installation has not occurred."}


def staged_report(staged: StagedPackage) -> dict[str, Any]:
    """Return a stable staging report without timestamps or machine noise."""
    package = asdict(staged.package)
    package["verified"] = staged.package.verified
    return {"staged": True, "state": staged.state.value, "package": package,
            "stage_directory": staged.stage_directory, "copied_package": staged.copied_package,
            "extracted_directory": staged.extracted_directory, "activation_allowed": False,
            "installation_executed": False,
            "message": "LIC was copied and extracted into staging only; activation was refused by M1.1."}


def write_report(report: dict[str, Any], output: Path | None, *, allowed_root: Path | None = None) -> None:
    """Write only when the caller explicitly requests an approved report path."""
    if output is not None:
        output = output.expanduser().resolve()
        if allowed_root is not None:
            root = allowed_root.expanduser().resolve()
            if output != root and root not in output.parents:
                raise ValueError("report output must remain inside the approved staging directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
