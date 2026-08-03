"""Verify clean-source, post-setup, and upgrade QA boundaries without mutation.

This check is designed for the eventual real new-computer test. It reads the
signed release inventory, the adjacent virtual-environment state, and the
per-user settings location, but it never creates, renames, or deletes them.
That makes its verdict useful without risking catalogs or an established setup.
"""

from __future__ import annotations

import argparse
import hashlib
import os

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "RELEASE_MANIFEST.sha256"
APP_DATA_DIRECTORY_NAME = "LoRAImageCurator"


@dataclass(frozen=True, slots=True)
class CleanInstallReport:
    """Describe release, environment, and per-user state without changing it."""

    release_members: int
    release_hashes_valid: bool
    local_venv_exists: bool
    app_data_path: Path
    app_data_exists: bool
    settings_exists: bool


def app_data_path(environment: dict[str, str] | None = None) -> Path:
    """Resolve the same per-user settings location used by the Windows app."""
    values = os.environ if environment is None else environment
    base = values.get("APPDATA", "").strip()
    if not base:
        raise RuntimeError(
            "APPDATA is not defined. Run this QA check from a normal Windows "
            "Command Prompt or PowerShell session."
        )
    return Path(base).expanduser().resolve() / APP_DATA_DIRECTORY_NAME


def verify_manifest(release_root: Path) -> tuple[int, bool]:
    """Verify every signed member and reject unsafe or missing manifest paths."""
    root = release_root.expanduser().resolve()
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise FileNotFoundError(f"Release manifest not found: {manifest}")
    count = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, separator, raw_name = line.partition("  ")
        relative = Path(raw_name.strip().replace("\\", "/"))
        if (
            not separator
            or len(digest) != 64
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ValueError(f"Malformed or unsafe manifest line: {line!r}")
        member = root / relative
        if not member.is_file():
            raise FileNotFoundError(f"Signed release member is missing: {relative}")
        if hashlib.sha256(member.read_bytes()).hexdigest() != digest.casefold():
            return count + 1, False
        count += 1
    if not count:
        raise RuntimeError("Release manifest contains no members.")
    return count, True


def inspect_clean_install(
    release_root: Path = PROJECT_ROOT,
    *,
    environment: dict[str, str] | None = None,
) -> CleanInstallReport:
    """Return clean-install facts while leaving source and user state untouched."""
    root = release_root.expanduser().resolve()
    members, hashes_valid = verify_manifest(root)
    user_data = app_data_path(environment)
    return CleanInstallReport(
        release_members=members,
        release_hashes_valid=hashes_valid,
        local_venv_exists=(root / "venv" / "Scripts" / "python.exe").is_file(),
        app_data_path=user_data,
        app_data_exists=user_data.exists(),
        settings_exists=(user_data / "settings.json").is_file(),
    )


def phase_errors(phase: str, report: CleanInstallReport) -> tuple[str, ...]:
    """Return actionable failures for one documented Windows QA phase."""
    errors: list[str] = []
    if not report.release_hashes_valid:
        errors.append("one or more signed release files do not match the manifest")
    if phase == "before-setup":
        if report.local_venv_exists:
            errors.append("the test folder already contains a local venv")
        if report.app_data_exists:
            errors.append(
                f"existing per-user state would mask first-run behavior: "
                f"{report.app_data_path}"
            )
    elif phase == "after-setup" and not report.local_venv_exists:
        errors.append("guided setup has not created the local venv")
    elif phase == "upgrade" and not report.settings_exists:
        errors.append("upgrade QA requires an existing settings.json")
    return tuple(errors)


def main() -> int:
    """Inspect one Windows QA phase and print a concise pass/fail report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("before-setup", "after-setup", "upgrade"),
        required=True,
    )
    parser.add_argument("--release-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args()
    report = inspect_clean_install(arguments.release_root)
    print(f"Release members verified: {report.release_members}")
    print(f"Local venv present: {'yes' if report.local_venv_exists else 'no'}")
    print(f"Per-user app data: {report.app_data_path}")
    print(f"Existing settings: {'yes' if report.settings_exists else 'no'}")
    errors = phase_errors(arguments.phase, report)
    if errors:
        print(f"\n{arguments.phase} QA is not clean:")
        for error in errors:
            print(f"  - {error}")
        print("No files or settings were changed.")
        return 1
    print(f"\n{arguments.phase} QA boundary passed. No files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
