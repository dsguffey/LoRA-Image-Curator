"""Build and verify a deterministic LoRA Image Curator source release.

The archive contains the source, tests, documentation, and tooling intended for
GitHub. Runtime data and heavyweight third-party artifacts are excluded by
policy rather than by a hand-curated ZIP command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
import zipfile

from pathlib import Path, PurePosixPath

try:
    # Package import is used by regressions and ``python -m`` execution.
    from tools.compile_project import manifest_release_files
except ModuleNotFoundError as error:
    # Direct ``python tools/build_release.py`` execution places ``tools`` rather
    # than the project root on sys.path.
    if error.name != "tools":
        raise
    from compile_project import manifest_release_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
INCLUDED_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".bat",
    ".vbs",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
}
INCLUDED_NAMES = {".gitattributes", ".gitignore", "LICENSE"}
EXCLUDED_DIRECTORIES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "venv",
    ".venv",
    "models",
    "thumbnail_cache",
    "dependency_backups",
    # The installed application writes catalogs, timestamped backups, and
    # provider/export reports here. Those files belong to the user and must
    # never become release members merely because the source archive is built
    # from an in-place DatasetTools installation.
    "output",
    "release_audit",
    "release_output",
}
FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".onnx",
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".zip",
}
REQUIRED_MEMBERS = {
    ".gitignore",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "VERSION.txt",
    "app.py",
    "app_identity.py",
    "tests/test_v0250_regression.py",
    "tests/test_v0250_gui.py",
    "tests/test_v0252_regression.py",
    "tests/test_v0252_gui.py",
    "tests/test_v0260_regression.py",
    "tests/test_v0260_gui.py",
    "tests/test_v0270_regression.py",
    "tests/test_v0270_gui.py",
    "tests/test_v0271_regression.py",
    "tests/test_v0271_gui.py",
    "tests/test_v0272_regression.py",
    "tests/test_v0272_gui.py",
    "tests/test_v0273_regression.py",
    "tests/test_v0273_gui.py",
    "tests/test_v0274_regression.py",
    "tests/test_v0274_gui.py",
    "tests/test_v0275_regression.py",
    "tests/test_v0275_gui.py",
    "tests/test_v0276_regression.py",
    "tests/test_v0276_gui.py",
    "tests/test_v0277_regression.py",
    "tests/test_v0277_gui.py",
    "tests/test_v0278_regression.py",
    "tests/test_v0278_gui.py",
    "tests/test_v0279_regression.py",
    "tests/test_v0279_gui.py",
    "tests/test_v02710_regression.py",
    "tests/test_v02710_gui.py",
    "tests/test_v02711_regression.py",
    "tests/test_v02711_gui.py",
    "tests/test_v02712_regression.py",
    "tests/test_v02712_gui.py",
    "tests/test_v02713_regression.py",
    "tests/test_v02713_gui.py",
    "tests/test_v02714_regression.py",
    "tests/test_v02714_gui.py",
    "tests/test_v02715_regression.py",
    "tests/test_v02715_gui.py",
    "tests/test_v02716_regression.py",
    "tests/test_v02716_gui.py",
    "tests/test_v02717_regression.py",
    "tests/test_v02717_gui.py",
    "tests/test_v02718_regression.py",
    "tests/test_v02718_gui.py",
    "tests/test_v02719_regression.py",
    "tests/test_v02719_gui.py",
    "tests/test_v02720_regression.py",
    "tests/test_v02720_gui.py",
    "tests/test_v02721_regression.py",
    "tests/test_v02721_gui.py",
    "tests/test_v02722_regression.py",
    "tests/test_v02722_gui.py",
    "tests/test_v02723_regression.py",
    "tests/test_v02723_gui.py",
    "tests/test_v0280_regression.py",
    "tests/test_v0280_gui.py",
    "tests/test_clean_install.py",
    "tests/__init__.py",
    "tests/paths.py",
    "setup_assistant.py",
    "Setup and Launch LoRA Image Curator.bat",
    "Install Base Dependencies.bat",
    "Install Body Analysis Dependencies.bat",
    "tests/test_golden_build.py",
    ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
    ".github/pull_request_template.md",
    ".github/workflows/repository-checks.yml",
    ".gitattributes",
    "GIT_READY_CHECKLIST.md",
    "THIRD_PARTY_NOTICE.md",
    "provider_registry.json",
    "portable_payload_policy.json",
    "SBOM.spdx.json",
    "docs/GOLDEN_TEST.md",
    "docs/CLEAN_INSTALL_QA.md",
    "tools/audit_project.py",
    "tools/build_release.py",
    "tools/compile_project.py",
    "tools/clean_install_check.py",
    "tools/golden_fixture.py",
    "tools/run_regressions.py",
    "tools/generate_sbom.py",
}


def load_application_version() -> tuple[str, str]:
    """Load name/version constants without importing the GUI."""
    identity_path = PROJECT_ROOT / "app_identity.py"
    specification = importlib.util.spec_from_file_location(
        "release_app_identity",
        identity_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Could not load app_identity.py.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return str(module.APP_NAME), str(module.APP_VERSION)


def release_files() -> list[Path]:
    """Return the stable, manifest-owned public source set.

    A release is commonly built from the user's established DatasetTools
    installation. Reading the signed inventory prevents local archives,
    datasets, environments, and other adjacent material from being swept into
    the ZIP simply because their filenames look like source or documentation.
    """
    selected = list(manifest_release_files(PROJECT_ROOT))
    for path in selected:
        if path.name not in INCLUDED_NAMES and path.suffix.casefold() not in (
            INCLUDED_SUFFIXES
        ):
            relative = path.relative_to(PROJECT_ROOT)
            raise ValueError(f"Unsupported manifested release member: {relative}")
    return selected


def zip_info(member_name: str) -> zipfile.ZipInfo:
    """Create deterministic metadata for one regular archive member."""
    info = zipfile.ZipInfo(member_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def manifest_bytes(files: list[Path]) -> bytes:
    """Return a SHA-256 manifest for every source member in archive order."""
    lines: list[str] = []
    for path in files:
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_member_name(member_name: str) -> None:
    """Reject unsafe, private, or generated archive paths."""
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive member: {member_name}")
    if any(part in EXCLUDED_DIRECTORIES for part in path.parts):
        raise ValueError(f"Excluded directory in archive: {member_name}")
    if path.suffix.casefold() in FORBIDDEN_ARCHIVE_SUFFIXES:
        raise ValueError(f"Forbidden artifact in archive: {member_name}")


def verify_archive(archive_path: Path) -> tuple[int, str]:
    """Verify one overwrite-in-place archive and its generated manifest."""
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"Archive CRC failed: {bad_member}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate member names.")
        for name in names:
            validate_member_name(name)
        relative_names = names
        missing = sorted(REQUIRED_MEMBERS - set(names))
        if missing:
            raise ValueError(f"Archive is missing required members: {missing}")

        manifest_member = "RELEASE_MANIFEST.sha256"
        manifest_text = archive.read(manifest_member).decode("utf-8")
        expected_lines: list[str] = []
        for name, relative_name in zip(names, relative_names, strict=True):
            if relative_name == "RELEASE_MANIFEST.sha256":
                continue
            digest = hashlib.sha256(archive.read(name)).hexdigest()
            expected_lines.append(f"{digest}  {relative_name}")
        if manifest_text != "\n".join(expected_lines) + "\n":
            raise ValueError("Release manifest does not match archive bytes.")

    archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return len(names), archive_digest


def build_archive(output_path: Path) -> tuple[int, str]:
    """Audit the tree, write the deterministic archive, then verify it."""
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "audit_project.py"), "--quiet"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    files = release_files()
    manifest = manifest_bytes(files)
    # Keep the extracted-folder preflight useful in the development tree too.
    (PROJECT_ROOT / "RELEASE_MANIFEST.sha256").write_bytes(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            relative_name = path.relative_to(PROJECT_ROOT).as_posix()
            validate_member_name(relative_name)
            archive.writestr(zip_info(relative_name), path.read_bytes())
        archive.writestr(
            zip_info("RELEASE_MANIFEST.sha256"),
            manifest,
        )
    return verify_archive(output_path)


def main() -> int:
    """Parse the output option, build the release, and print verification data."""
    app_name, version = load_application_version()
    default_name = f"LoRA_Image_Curator_Source_v{version}.zip"
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
    print(f"{app_name} v{version} release built: {output_path}")
    print(f"Members: {member_count}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
