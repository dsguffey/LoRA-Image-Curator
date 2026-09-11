"""Engineering entrypoint for an explicitly isolated LIC model/readiness proof."""
import argparse
import json
from pathlib import Path

from .artifacts import load_artifact_descriptor
from .dependency_lock import load_dependency_lock
from .lic_readiness import execute_readiness, plan_readiness
from .model_resources import load_model
from .recipe import load_recipe
from .reporting import write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Plan/test LIC Lite readiness; never activate.')
    parser.add_argument('command', choices=('plan', 'execute'))
    for name in ('package', 'recipe', 'model', 'base-lock', 'runtime', 'base-journal', 'operation-root', 'shared-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--allow-download', action='store_true')
    parser.add_argument('--ca-bundle', type=Path,
                        help='Reviewed additive CA bundle required with --allow-download')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    model, recipe = load_model(args.model), load_recipe(args.recipe)
    plan = plan_readiness(model, recipe, operation_id=args.operation_id,
                          operation_root=args.operation_root, shared_root=args.shared_root,
                          base_journal=args.base_journal, candidate=args.candidate,
                          allow_download=args.allow_download)
    result = plan if args.command == 'plan' else execute_readiness(
        plan, model, recipe, args.package, load_dependency_lock(args.base_lock),
        load_artifact_descriptor(args.runtime), ca_bundle=args.ca_bundle)
    write_report(result, args.output, allowed_root=args.operation_root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
