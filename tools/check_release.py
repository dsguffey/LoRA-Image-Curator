"""Check release hashes, reviewed Git coverage and Portable Source imports.

Run ``python -B -m tools.check_release`` before a release. ``--regenerate``
refreshes hashes only after coverage passes; new files must first be reviewed
and added to the inventory. No directory scanning or package installation occurs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

try:
    from tools.build_release import INCLUDED_NAMES, INCLUDED_SUFFIXES, REQUIRED_MEMBERS, validate_member_name
    from tools.compile_project import MANIFEST_FILENAME, manifest_release_files
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    from build_release import INCLUDED_NAMES, INCLUDED_SUFFIXES, REQUIRED_MEMBERS, validate_member_name
    from compile_project import MANIFEST_FILENAME, manifest_release_files

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def tracked_source_files(root: Path) -> set[str] | None:
    """Read the exact checkout's Git index; extracted source needs no Git."""
    if not (root / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                            check=True, capture_output=True)
    names = set(result.stdout.decode("utf-8").split("\0")) - {"", MANIFEST_FILENAME}
    for name in sorted(names):
        validate_member_name(name)
        path = PurePosixPath(name)
        if path.name not in INCLUDED_NAMES and path.suffix.casefold() not in INCLUDED_SUFFIXES:
            raise ValueError(f"Tracked file needs an explicit release-policy decision: {name}")
    return names


def portable_inputs(root: Path, owned: set[str]) -> set[str]:
    """Apply the existing explicit Portable Source policy and required list."""
    policy = json.loads((root / "portable_source_payload_policy.json").read_text(encoding="utf-8"))
    selected = {n for n in owned if "/" not in n and n.endswith(".py")}
    selected.update(policy["included_files"])
    missing = selected - owned
    if missing:
        raise ValueError(f"Portable inputs missing from source manifest: {sorted(missing)}")
    overrides = policy["archive_name_overrides"]
    archive_names = {overrides.get(n, n) for n in selected} | {MANIFEST_FILENAME}
    required = set(policy["required_archive_files"]) - archive_names
    if required:
        raise ValueError(f"Required Portable Source files omitted: {sorted(required)}")
    return selected


def check_local_imports(root: Path, selected: set[str], owned: set[str]) -> None:
    """Reject missing local modules, including lazy and relative imports.

    Third-party imports are validated by the extracted-package smoke test;
    this dependency-free gate checks project-owned module families only.
    """
    modules = {n[:-3].replace("/", ".").removesuffix(".__init__"): n
               for n in owned if n.endswith(".py")}
    families = {m.split(".")[0] for m in modules}
    for name in sorted(selected):
        if not name.endswith(".py"):
            continue
        package = name.split("/")[:-1]
        tree = ast.parse((root / name).read_bytes(), filename=name)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                base = ".".join([*prefix, *([node.module] if node.module else [])])
                imports.append(base)
                imports.extend(f"{base}.{alias.name}" for alias in node.names
                               if f"{base}.{alias.name}" in modules)
        for module in imports:
            if module.split(".")[0] not in families:
                continue
            if module not in modules or modules[module] not in selected:
                raise ValueError(f"Missing packaged local import: {name} -> {module}")
            parts = module.split(".")
            for length in range(1, len(parts)):
                initializer = "/".join(parts[:length]) + "/__init__.py"
                if initializer not in selected:
                    raise ValueError(f"Missing package initializer: {initializer}")


def check_release(root: Path = PROJECT_ROOT, *, check_hashes: bool = True) -> dict[str, int]:
    """Validate both payload boundaries without modifying the source tree."""
    root = root.resolve()
    files = manifest_release_files(root)
    owned = {p.relative_to(root).as_posix() for p in files}
    for name in owned:
        validate_member_name(name)
    missing = REQUIRED_MEMBERS - owned
    if missing:
        raise ValueError(f"Required source release members omitted: {sorted(missing)}")
    tracked = tracked_source_files(root)
    if tracked is not None and tracked - owned:
        raise ValueError(f"Tracked source omitted from manifest: {sorted(tracked - owned)}")
    selected = portable_inputs(root, owned)
    check_local_imports(root, selected, owned)
    if check_hashes:
        recorded = dict(line.split("  ", 1)[::-1] for line in
                        (root / MANIFEST_FILENAME).read_text(encoding="utf-8").splitlines())
        stale = [name for name in sorted(owned)
                 if hashlib.sha256((root / name).read_bytes()).hexdigest() != recorded[name].lower()]
        if stale:
            raise ValueError(f"Stale release hashes: {stale}")
    return {"source_files": len(files), "portable_files": len(selected)}


def main() -> int:
    """Expose one non-mutating gate and an explicit hash-only regeneration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()
    if args.regenerate:
        check_release(check_hashes=False)
        try:
            from tools.build_release import manifest_bytes
        except ModuleNotFoundError as error:
            if error.name != "tools":
                raise
            from build_release import manifest_bytes
        (PROJECT_ROOT / MANIFEST_FILENAME).write_bytes(manifest_bytes(list(manifest_release_files(PROJECT_ROOT))))
    result = check_release()
    print(f"Release gate passed: {result['source_files']} source files; "
          f"{result['portable_files']} Portable Source inputs; hashes and local imports verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
