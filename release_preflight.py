"""Detect obsolete top-level Python files after an in-place release update.

Generated catalogs, caches, models, and user folders are intentionally ignored.
The check compares only importable top-level ``.py`` files against the signed
release inventory, which is the contamination pattern observed when an older
DatasetTools file remained after newer files were overwritten.
"""

from __future__ import annotations

from pathlib import Path


MANIFEST_FILENAME = "RELEASE_MANIFEST.sha256"


def expected_top_level_python_files(release_root: Path) -> frozenset[str]:
    """Read top-level Python inventory from the release manifest."""
    manifest = release_root / MANIFEST_FILENAME
    if not manifest.is_file():
        return frozenset()
    expected: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        _digest, separator, raw_path = line.partition("  ")
        if not separator:
            continue
        relative = Path(raw_path.strip().replace("\\", "/"))
        if len(relative.parts) == 1 and relative.suffix.casefold() == ".py":
            expected.add(relative.name)
    return frozenset(expected)


def unexpected_top_level_python_files(release_root: Path) -> tuple[str, ...]:
    """Return importable root files not present in the delivered inventory."""
    root = release_root.expanduser().resolve()
    expected = expected_top_level_python_files(root)
    if not expected:
        return ()
    actual = {path.name for path in root.glob("*.py") if path.is_file()}
    return tuple(sorted(actual - expected, key=str.casefold))


def assert_clean_release_directory(release_root: Path) -> None:
    """Raise one actionable error before historical GUI modules are imported."""
    unexpected = unexpected_top_level_python_files(release_root)
    if not unexpected:
        return
    listed = "\n".join(f"  - {name}" for name in unexpected)
    raise RuntimeError(
        "This release folder contains unexpected top-level Python files, "
        "usually because an older release file is now obsolete.\n\n"
        f"Unexpected files:\n{listed}\n\n"
        "Move those named files to a backup folder (or delete them if already "
        "backed up), then rerun the smoke test. Do not remove catalogs, images, "
        "models, caches, the virtual environment, or other user-data folders."
    )
