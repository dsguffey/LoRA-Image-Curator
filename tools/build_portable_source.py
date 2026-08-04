"""Build and verify the slim end-user Portable Source archive.

The Portable Source package remains setup-driven: it contains every
manifest-owned top-level application module plus only the user-facing setup,
launcher, license, provenance, and instruction files named by policy. It does
not contain the future private Windows runtime, repository tooling, provider
models, or any data discovered by walking the installation directory.

Selection always starts from ``RELEASE_MANIFEST.sha256``. That signed source
inventory prevents an in-place development/user installation from leaking
catalogs, environments, caches, archives, or untracked source into the ZIP.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile

from pathlib import Path, PurePosixPath
from typing import Any

try:
    from tools.build_release import FIXED_ZIP_TIMESTAMP, zip_info
    from tools.compile_project import manifest_release_files
except ModuleNotFoundError as error:
    # Direct script execution places ``tools`` rather than the project root on
    # sys.path. Keep both supported entry points on the same implementation.
    if error.name != "tools":
        raise
    from build_release import FIXED_ZIP_TIMESTAMP, zip_info
    from compile_project import manifest_release_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "portable_source_payload_policy.json"
MANIFEST_NAME = "RELEASE_MANIFEST.sha256"


def load_application_version() -> tuple[str, str]:
    """Load application identity without importing the Tk application."""
    identity_path = PROJECT_ROOT / "app_identity.py"
    specification = importlib.util.spec_from_file_location(
        "portable_source_app_identity",
        identity_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Could not load app_identity.py.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return str(module.APP_NAME), str(module.APP_VERSION)


def load_policy() -> dict[str, Any]:
    """Read and minimally validate the release-owned portable-source policy."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported portable-source policy schema.")
    if not policy.get("include_all_manifested_top_level_python"):
        raise ValueError("Portable Source must include every root app module.")
    for required_key in (
        "artifact_name_template",
        "included_files",
        "archive_name_overrides",
        "required_archive_files",
        "excluded_directories",
        "excluded_repository_files",
        "never_collect_patterns",
    ):
        if required_key not in policy:
            raise ValueError(f"Portable-source policy is missing {required_key!r}.")
    return policy


def _owned_source_files() -> dict[str, Path]:
    """Return signed source members keyed by normalized archive-style paths."""
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): path
        for path in manifest_release_files(PROJECT_ROOT)
    }


def portable_source_members() -> list[tuple[str, Path]]:
    """Return deterministic ``(archive name, source path)`` package members.

    Runtime Python selection intentionally follows a narrow structural rule:
    every top-level Python module in the signed source release is application
    or setup code. Repository tests and build tools live in their own excluded
    directories. This rule automatically carries a new runtime module into the
    end-user package without allowing an unmanifested local file to enter it.
    """
    policy = load_policy()
    owned = _owned_source_files()
    selected_source_names = {
        name
        for name in owned
        if "/" not in name and PurePosixPath(name).suffix.casefold() == ".py"
    }
    selected_source_names.update(str(name) for name in policy["included_files"])

    missing = sorted(selected_source_names - set(owned), key=str.casefold)
    if missing:
        raise FileNotFoundError(
            "Portable-source inputs are absent from the signed source release: "
            f"{missing}"
        )

    overrides = {
        str(source): str(destination)
        for source, destination in policy["archive_name_overrides"].items()
    }
    members: list[tuple[str, Path]] = []
    archive_names: set[str] = set()
    for source_name in selected_source_names:
        archive_name = overrides.get(source_name, source_name)
        validate_member_name(archive_name, policy=policy)
        if archive_name in archive_names:
            raise ValueError(f"Duplicate portable archive name: {archive_name}")
        archive_names.add(archive_name)
        members.append((archive_name, owned[source_name]))
    return sorted(members, key=lambda item: (item[0].casefold(), item[0]))


def validate_member_name(member_name: str, *, policy: dict[str, Any]) -> None:
    """Reject unsafe, repository-only, generated, or private package paths."""
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts or not member_name:
        raise ValueError(f"Unsafe portable archive member: {member_name}")
    if any(part in set(policy["excluded_directories"]) for part in path.parts):
        raise ValueError(f"Excluded directory in portable archive: {member_name}")
    if member_name in set(policy["excluded_repository_files"]):
        raise ValueError(f"Repository-only file in portable archive: {member_name}")
    for pattern in policy["never_collect_patterns"]:
        if fnmatch.fnmatch(path.name.casefold(), str(pattern).casefold()):
            raise ValueError(f"Forbidden portable artifact: {member_name}")


def _manifest_bytes(members: list[tuple[str, Path]]) -> bytes:
    """Build the package-specific SHA-256 inventory in archive order."""
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {archive_name}"
        for archive_name, path in members
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_archive(archive_path: Path) -> tuple[int, str]:
    """Verify exact inventory, CRC, policy exclusions, and member hashes."""
    policy = load_policy()
    expected_members = [name for name, _path in portable_source_members()]
    expected_names = [*expected_members, MANIFEST_NAME]
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"Portable archive CRC failed: {bad_member}")
        names = archive.namelist()
        if names != expected_names:
            raise ValueError("Portable archive inventory or order is incorrect.")
        if len(names) != len(set(names)):
            raise ValueError("Portable archive contains duplicate member names.")
        for name in names:
            validate_member_name(name, policy=policy)

        missing_required = sorted(
            set(policy["required_archive_files"]) - set(names),
            key=str.casefold,
        )
        if missing_required:
            raise ValueError(
                f"Portable archive is missing required files: {missing_required}"
            )

        expected_manifest = "\n".join(
            f"{hashlib.sha256(archive.read(name)).hexdigest()}  {name}"
            for name in expected_members
        ) + "\n"
        actual_manifest = archive.read(MANIFEST_NAME).decode("utf-8")
        if actual_manifest != expected_manifest:
            raise ValueError("Portable manifest does not match archive bytes.")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return len(expected_names), digest


def build_archive(output_path: Path) -> tuple[int, str]:
    """Audit the source boundary, build deterministically, and verify output."""
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "audit_project.py"), "--quiet"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    members = portable_source_members()
    manifest = _manifest_bytes(members)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_name, source_path in members:
            archive.writestr(zip_info(archive_name), source_path.read_bytes())
        manifest_info = zip_info(MANIFEST_NAME)
        # Assert the shared deterministic metadata has not drifted away from the
        # portable builder's documented timestamp contract.
        if manifest_info.date_time != FIXED_ZIP_TIMESTAMP:
            raise AssertionError("Shared deterministic ZIP timestamp drifted.")
        archive.writestr(manifest_info, manifest)
    return verify_archive(output_path)


def main() -> int:
    """Build the default/versioned archive or a caller-selected destination."""
    app_name, version = load_application_version()
    policy = load_policy()
    default_name = str(policy["artifact_name_template"]).format(version=version)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT.parent / default_name,
        help="Archive destination (default: beside the source directory).",
    )
    arguments = parser.parse_args()
    output_path = arguments.output.expanduser().resolve()
    member_count, digest = build_archive(output_path)
    print(f"{app_name} v{version} Portable Source built: {output_path}")
    print(f"Members: {member_count}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
