"""Run dependency-light repository, security, and documentation checks.

This is a bounded static review, not a claim of formal security verification.
It protects the release rules most likely to regress during ordinary edits:
dangerous dynamic execution, shell-enabled subprocesses, committed private
artifacts, environment-specific paths, and undocumented Python modules.
"""

from __future__ import annotations

import argparse
import ast
import re

from pathlib import Path

try:
    # Package import is used by regressions and ``python -m`` execution.
    from tools.compile_project import manifest_release_files
except ModuleNotFoundError as error:
    # Direct ``python tools/audit_project.py`` execution places ``tools`` rather
    # than the project root on sys.path.
    if error.name != "tools":
        raise
    from compile_project import manifest_release_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".bat",
    ".vbs",
    ".toml",
    ".yml",
    ".yaml",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".onnx",
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
}
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?i)[a-z]:\\users\\[^\\\s]+"),
    re.compile(r"/(?:home|Users)/[^/\s]+"),
    re.compile(r"/workspace/(?:scratch/)?[A-Za-z0-9_-]+"),
)
CREDENTIAL_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def iter_public_files() -> list[Path]:
    """Return only project-owned files eligible for public review.

    The application is installed in place beside user-managed folders. The
    signed manifest is therefore the same ownership boundary used by source
    compilation and packaging; arbitrary local archives must never become
    public source merely because they sit beneath ``DatasetTools``.
    """
    return list(manifest_release_files(PROJECT_ROOT))


def literal_bool_keyword(call: ast.Call, keyword_name: str) -> bool | None:
    """Return a literal Boolean keyword value, or ``None`` when not literal."""
    for keyword in call.keywords:
        if keyword.arg != keyword_name:
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(
            keyword.value.value, bool
        ):
            return keyword.value.value
    return None


def _is_direct_sqlite_connect(call: ast.AST) -> bool:
    """Return whether a context expression is bare ``sqlite3.connect(...)``.

    A SQLite connection's context manager owns transaction completion only; it
    does not close the native database handle.  Project code must wrap direct
    connections in ``contextlib.closing`` (and may then enter the connection as
    a second context manager when commit/rollback semantics are required).
    """
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "connect"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "sqlite3"
    )


def audit_python(path: Path, errors: list[str], metrics: dict[str, int]) -> None:
    """Inspect one Python module's syntax, docs, and dangerous call patterns."""
    relative = path.relative_to(PROJECT_ROOT)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
    except (OSError, UnicodeError, SyntaxError) as error:
        errors.append(f"{relative}: cannot parse: {error}")
        return

    # Test function names already communicate their contract and commonly rely
    # on assertion bodies as executable documentation. The portfolio metric
    # therefore measures production and release-tool APIs while every Python
    # file still receives the security-call scan below.
    measure_documentation = not path.name.startswith("test_")
    if measure_documentation:
        metrics["python_modules"] += 1
        if ast.get_docstring(tree):
            metrics["documented_modules"] += 1
        else:
            errors.append(f"{relative}: missing module docstring")

    if measure_documentation:
        # Measure the public module surface, not every small property, local
        # worker callback, or test helper. That matches this project's
        # maintenance-focused documentation policy and avoids incentivizing
        # noisy comments on self-explanatory implementation detail.
        for node in tree.body:
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ) or node.name.startswith("_"):
                continue
            metrics["public_objects"] += 1
            if ast.get_docstring(node):
                metrics["documented_public_objects"] += 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if _is_direct_sqlite_connect(item.context_expr):
                    errors.append(
                        f"{relative}:{item.context_expr.lineno}: bare "
                        "sqlite3.connect context does not close the database; "
                        "wrap it in contextlib.closing"
                    )
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            errors.append(
                f"{relative}:{node.lineno}: dynamic {node.func.id}() is forbidden"
            )
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "system"
        ):
            errors.append(f"{relative}:{node.lineno}: os.system() is forbidden")
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and literal_bool_keyword(node, "shell") is True
        ):
            errors.append(
                f"{relative}:{node.lineno}: subprocess shell=True is forbidden"
            )


def run_audit(*, quiet: bool = False) -> int:
    """Run the bounded audit and return zero only when release gates pass."""
    errors: list[str] = []
    metrics = {
        "python_modules": 0,
        "documented_modules": 0,
        "public_objects": 0,
        "documented_public_objects": 0,
    }
    files = iter_public_files()

    for path in files:
        relative = path.relative_to(PROJECT_ROOT)
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            errors.append(f"{relative}: private/generated artifact is present")
        if path.suffix.casefold() == ".py":
            audit_python(path, errors, metrics)
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: cannot read text: {error}")
            continue
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(content):
                errors.append(f"{relative}: contains an environment-specific path")
                break
        # This file necessarily contains credential signatures as regex
        # literals, so it is excluded from its own signature scan.
        if relative.as_posix() != "tools/audit_project.py":
            for pattern in CREDENTIAL_PATTERNS:
                if pattern.search(content):
                    errors.append(f"{relative}: resembles a committed credential")
                    break

    module_count = metrics["python_modules"]
    documented_modules = metrics["documented_modules"]
    public_count = metrics["public_objects"]
    documented_public = metrics["documented_public_objects"]
    public_coverage = (
        (documented_public / public_count) * 100.0 if public_count else 100.0
    )
    if documented_modules != module_count:
        errors.append("Every Python module must retain module-level documentation.")
    if public_coverage < 95.0:
        errors.append(
            f"Public object documentation coverage is {public_coverage:.1f}%."
        )

    if errors:
        print("Project audit failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if not quiet:
        print(
            "Project audit passed: "
            f"{len(files)} public files; "
            f"{documented_modules}/{module_count} documented Python modules; "
            f"{documented_public}/{public_count} documented public objects "
            f"({public_coverage:.1f}%); no forbidden artifacts, private paths, "
            "credential signatures, dynamic evaluation, os.system, or "
            "subprocess shell=True; no transaction-only bare SQLite connection "
            "contexts."
        )
    return 0


def main() -> int:
    """Parse CLI options and run the project audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print output only when a release gate fails.",
    )
    arguments = parser.parse_args()
    return run_audit(quiet=arguments.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
