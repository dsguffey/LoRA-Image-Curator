"""Bounded M1.2 CLI for planning or executing a disposable base environment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .acquisition import AcquisitionPolicy
from .artifacts import load_artifact_descriptor
from .dependency_lock import load_dependency_lock
from .environment import build_environment_plan
from .executor import execute_environment_plan
from .reporting import write_report


TRUSTED_HOSTS = ("www.python.org", "files.pythonhosted.org", "download-r2.pytorch.org")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or build a disposable LIC base venv; activation is unavailable.")
    parser.add_argument("command", choices=("plan", "execute"))
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--operation-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--lic-source-root", type=Path,
                        help="Optional already-staged LIC source used only for base-module imports")
    parser.add_argument("--output", type=Path,
                        help="Optional JSON report path under the operation root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime = load_artifact_descriptor(args.runtime)
    lock = load_dependency_lock(args.dependency_lock)
    plan = build_environment_plan(args.operation_id, lock.profile, runtime, lock.digest(),
                                  args.operation_root, cache_root=args.cache_root)
    if args.command == "plan":
        report = {"plan": json.loads(plan.canonical_json()), "plan_sha256": plan.digest(),
                  "execution_performed": False, "activation_performed": False}
    else:
        report = execute_environment_plan(
            plan, lock, AcquisitionPolicy(TRUSTED_HOSTS),
            lic_source_root=args.lic_source_root, require_cuda=True,
        )
    write_report(report, args.output, allowed_root=args.operation_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
