"""CLI for M1.1 LIC inspection, planning and verified disposable staging."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .detection import detect_explicit
from .planner import build_plan
from .recipe import load_recipe
from .reporting import plan_report, staged_report, write_report
from .staging import stage_package
from .verification import verify_package


def _parser() -> argparse.ArgumentParser:
    """Build the explicit, non-installing command interface."""
    parser = argparse.ArgumentParser(description="Plan and stage a verified LIC package; never install or activate it.")
    parser.add_argument("command", choices=("inspect", "plan", "stage"))
    parser.add_argument("--package", type=Path, required=True, help="Explicit LIC source-package ZIP path")
    parser.add_argument("--recipe", type=Path, required=True, help="Declarative LIC recipe JSON")
    parser.add_argument("--stage-dir", type=Path, required=True, help="Explicit disposable staging directory")
    parser.add_argument("--existing", type=Path, action="append", default=[], help="Exact existing resource path to probe")
    parser.add_argument("--output", type=Path, help="Optional report path inside an approved report/staging root")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute only inspect/plan/stage operations; activation is impossible."""
    args = _parser().parse_args(argv)
    recipe = load_recipe(args.recipe)
    inspection = verify_package(args.package, recipe)
    if args.command == "inspect":
        report = {"application": recipe.display_name, "version": recipe.version,
                  "package": {"path": inspection.package_path, "sha256": inspection.package_sha256,
                               "integrity": inspection.integrity_ok, "completeness": inspection.completeness_ok,
                               "provenance": inspection.provenance_ok, "verified": inspection.verified,
                               "errors": inspection.errors},
                  "existing_resources": [candidate.__dict__ if hasattr(candidate, "__dict__") else {
                      "resource_id": candidate.resource_id, "path": candidate.path, "exists": candidate.exists,
                      "kind": candidate.kind, "state": candidate.state.value} for candidate in detect_explicit(args.existing)],
                  "installation_executed": False, "activation_allowed": False}
    elif args.command == "plan":
        plan = build_plan(recipe, inspection, args.stage_dir, detect_explicit(args.existing))
        report = plan_report(plan)
    else:
        staged = stage_package(args.package, args.stage_dir, recipe)
        report = staged_report(staged)
    write_report(report, args.output, allowed_root=args.stage_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
