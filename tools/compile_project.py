"""Compile only Python files owned by the installed project release.

LoRA Image Curator is deliberately installed beside its virtual environment,
catalogs, models, caches, and other user-managed folders. A recursive
``compileall`` of the installation directory therefore tests third-party or
user files that are outside the application's release boundary. The signed
release manifest is the authoritative inventory for this check.
"""

from __future__ import annotations

import py_compile

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FILENAME = "RELEASE_MANIFEST.sha256"


def manifest_release_files(project_root: Path) -> tuple[Path, ...]:
    """Return the safe, existing project files named by the release manifest.

    DatasetTools is an in-place installation rather than a disposable source
    checkout. Users legitimately keep virtual environments, catalogs, caches,
    old release copies, and other private folders beside the application.
    Consequently, ownership is established by the signed release inventory,
    never by recursively walking everything below the installation directory.

    Hash verification belongs to the archive/extraction gates. This reader
    validates inventory structure and file presence so it remains usable while
    a developer is preparing the next release and the recorded hashes still
    describe the previous build.
    """
    root = project_root.expanduser().resolve()
    manifest = root / MANIFEST_FILENAME
    if not manifest.is_file():
        raise FileNotFoundError(
            f"Release manifest not found: {manifest}. "
            "Install a complete release before running the golden test."
        )

    release_files: list[Path] = []
    seen: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, separator, raw_name = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in digest)
        ):
            raise ValueError(f"Malformed release-manifest line: {line!r}")
        relative = Path(raw_name.strip().replace("\\", "/"))
        normalized = relative.as_posix()
        if (
            not normalized
            or relative.is_absolute()
            or ".." in relative.parts
            or normalized == MANIFEST_FILENAME
        ):
            raise ValueError(f"Unsafe release-manifest path: {raw_name!r}")
        if normalized in seen:
            raise ValueError(f"Duplicate release-manifest path: {raw_name!r}")
        seen.add(normalized)
        owned_file = root / relative
        if not owned_file.is_file():
            raise FileNotFoundError(f"Manifest project file is missing: {relative}")
        release_files.append(owned_file)

    if not release_files:
        raise RuntimeError("Release manifest does not contain any project files.")
    return tuple(release_files)


def manifest_python_files(project_root: Path) -> tuple[Path, ...]:
    """Return project-owned Python paths from the signed release inventory."""
    python_files = tuple(
        path
        for path in manifest_release_files(project_root)
        if path.suffix.casefold() == ".py"
    )
    if not python_files:
        raise RuntimeError("Release manifest does not contain any Python files.")
    return python_files


def compile_project_python(project_root: Path = PROJECT_ROOT) -> int:
    """Compile every manifested Python file and return the verified count."""
    python_files = manifest_python_files(project_root)
    for source in python_files:
        py_compile.compile(str(source), doraise=True)
    return len(python_files)


def main() -> int:
    """Run the bounded source compilation check."""
    count = compile_project_python()
    print(
        f"Project compilation passed: {count} manifested Python files; "
        "virtual environments and user-managed folders were excluded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
