"""Verify LIC source-package identity, archive safety and manifest completeness."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .models import PackageInspection
from .recipe import LicRecipe


def _safe_member(name: str) -> None:
    """Reject traversal, absolute/drive paths, links and non-normal separators."""
    path = PurePosixPath(name)
    if (not name or name.endswith("/") or name in {"."} or path.is_absolute()
            or ".." in path.parts or path.as_posix() != name):
        raise ValueError(f"Unsafe archive member: {name!r}")
    if "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        raise ValueError(f"Unsafe archive member: {name!r}")


def _digest(path: Path) -> str:
    """Hash a candidate package without loading it all into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _parse_manifest(data: bytes) -> dict[str, str]:
    """Parse the exact two-space SHA-256 manifest format used by LIC."""
    entries: dict[str, str] = {}
    for raw_line in data.decode("utf-8").splitlines():
        digest, separator, raw_name = raw_line.partition("  ")
        name = raw_name.strip()
        if not separator or len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise ValueError(f"Malformed release manifest line: {raw_line!r}")
        _safe_member(name)
        if name == "RELEASE_MANIFEST.sha256" or name in entries:
            raise ValueError(f"Duplicate or recursive manifest member: {name!r}")
        entries[name] = digest.lower()
    if not entries:
        raise ValueError("Release manifest is empty")
    return entries


def verify_package(package_path: Path, recipe: LicRecipe) -> PackageInspection:
    """Verify a LIC source package before any staging write occurs."""
    package_path = package_path.expanduser().resolve()
    errors: list[str] = []
    package_digest = _digest(package_path) if package_path.is_file() else ""
    provenance_ok = package_path.is_file() and package_digest == recipe.expected_sha256
    if not provenance_ok:
        errors.append("package source identity or expected SHA-256 does not match the recipe")
    archive_names: list[str] = []
    manifest_members: dict[str, str] = {}
    integrity_ok = False
    completeness_ok = False
    try:
        with zipfile.ZipFile(package_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("archive CRC check failed")
            infos = archive.infolist()
            if len(infos) != len({info.filename for info in infos}):
                raise ValueError("archive contains duplicate member names")
            for info in infos:
                _safe_member(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"archive contains a symbolic link: {info.filename}")
                archive_names.append(info.filename)
            if "RELEASE_MANIFEST.sha256" not in archive_names:
                raise ValueError("LIC source-package manifest is missing")
            manifest_members = _parse_manifest(archive.read("RELEASE_MANIFEST.sha256"))
            actual_payload = set(archive_names) - {"RELEASE_MANIFEST.sha256"}
            if actual_payload != set(manifest_members):
                raise ValueError("manifest coverage differs from archive payload")
            for name, expected in manifest_members.items():
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != expected:
                    raise ValueError(f"manifest hash mismatch: {name}")
            integrity_ok = True
            missing = sorted(set(recipe.required_archive_files) - actual_payload)
            missing_runtime = sorted(set(recipe.required_runtime_inputs) - actual_payload)
            if missing or missing_runtime:
                details = []
                if missing:
                    details.append(f"required archive files missing: {missing}")
                if missing_runtime:
                    details.append(f"required runtime inputs missing: {missing_runtime}")
                raise ValueError("; ".join(details))
            completeness_ok = True
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeError) as error:
        errors.append(str(error))
    return PackageInspection(
        artifact_id=recipe.artifact_id, version=recipe.version, source=recipe.source,
        package_path=package_path.as_posix(), package_sha256=package_digest,
        archive_members=tuple(archive_names), manifest_members=tuple(sorted(manifest_members)),
        required_members=tuple(recipe.required_archive_files), integrity_ok=integrity_ok,
        completeness_ok=completeness_ok, provenance_ok=provenance_ok,
        errors=tuple(dict.fromkeys(errors)),
    )
