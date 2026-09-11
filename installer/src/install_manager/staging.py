"""Safely copy and extract one verified LIC package into a disposable stage."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from .models import StagedPackage
from .recipe import LicRecipe
from .verification import verify_package
from .process_lock import process_lock


def _confined(root: Path, relative: str) -> Path:
    """Resolve an archive member and prove it stays below the stage root."""
    candidate = (root / PurePosixPath(relative)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"archive member escapes staging directory: {relative}")
    return candidate


def stage_package(package_path: Path, staging_directory: Path, recipe: LicRecipe) -> StagedPackage:
    with process_lock(staging_directory.resolve()):
        return _stage_package(package_path, staging_directory, recipe)


def _stage_package(package_path: Path, staging_directory: Path, recipe: LicRecipe) -> StagedPackage:
    """Verify first, then copy/extract only within the caller-approved stage."""
    inspection = verify_package(package_path, recipe)
    if not inspection.verified:
        raise ValueError(f"Refusing to stage an unverified package: {list(inspection.errors)}")
    root = staging_directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    extracted = root / "extracted"
    incoming = root / "incoming"
    extracted.mkdir(exist_ok=False)
    incoming.mkdir(exist_ok=False)
    source = Path(inspection.package_path)
    copied = incoming / source.name
    if source != copied:
        shutil.copyfile(source, copied)
    else:
        copied = source
    with zipfile.ZipFile(copied) as archive:
        for info in archive.infolist():
            if info.filename == "RELEASE_MANIFEST.sha256":
                destination = _confined(extracted, info.filename)
                destination.write_bytes(archive.read(info.filename))
                continue
            destination = _confined(extracted, info.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"refusing symlink during extraction: {info.filename}")
            destination.write_bytes(archive.read(info.filename))
    return StagedPackage(package=inspection, stage_directory=root.as_posix(),
                         copied_package=copied.as_posix(), extracted_directory=extracted.as_posix())
