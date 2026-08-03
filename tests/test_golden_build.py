"""Run the complete LoRA Image Curator golden-build release gate.

This is the authoritative handoff command. It creates only temporary synthetic
images/catalogs, runs the repository audit and complete maintained non-GUI
regression history, verifies deterministic flat release packaging, and then
runs the current cumulative GUI chain on a live desktop. It never opens or
modifies a user's catalog or dataset.

Use ``--no-gui`` only in a headless development environment. A release cannot
be called golden until the default command passes on the supported Windows
desktop:

    python -X dev -m tests.test_golden_build
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile

from pathlib import Path

import app_identity as app_identity_module

from app_identity import APP_NAME, APP_VERSION
from release_preflight import assert_clean_release_directory
from tools.golden_fixture import create_golden_fixture


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
GUI_ENTRYPOINT = "tests/test_v0280_gui.py"


def _verify_and_report_runtime_paths() -> None:
    """Prove source imports come from this checkout and identify the runtime.

    A source checkout can safely use a virtual environment stored elsewhere.
    Reporting both locations prevents that supported arrangement from looking
    like accidental source leakage, while the identity-module assertion makes
    a genuinely stale or injected project import fail immediately.
    """
    identity_path = Path(app_identity_module.__file__).resolve()
    if identity_path.parent != PROJECT_ROOT:
        raise AssertionError(
            "Project import escaped the tested source folder: "
            f"{identity_path}"
        )
    runtime_path = Path(sys.executable).resolve()
    if not runtime_path.is_file():
        raise AssertionError(f"Python runtime is missing: {runtime_path}")
    print(f"Project source: {PROJECT_ROOT}", flush=True)
    print(f"Python runtime: {runtime_path}", flush=True)


def _run(command: list[str], *, environment: dict[str, str]) -> None:
    """Run one isolated gate with visible output and fail immediately."""
    print(f"\n>>> {' '.join(command)}", flush=True)
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


def _run_gui_gate(command: list[str], *, environment: dict[str, str]) -> None:
    """Run the GUI chain and reject background diagnostics on stderr.

    Tcl reports a missing command from an orphaned ``after`` script as a
    background error rather than a Python exception. The child process can
    therefore exit successfully even though the console printed an
    ``invalid command name`` traceback. Capturing the GUI process's stderr in
    the parent makes the golden verdict depend on a clean process boundary,
    including diagnostics that Python's ``sys.unraisablehook`` cannot see.
    Standard output remains live so the user can follow each GUI checkpoint.
    """
    print(f"\n>>> {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stderr=subprocess.PIPE,
        text=True,
    )
    diagnostics = completed.stderr or ""
    if diagnostics:
        print(diagnostics, file=sys.stderr, end="", flush=True)
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command)
    if diagnostics.strip():
        raise RuntimeError(
            "The live GUI gate emitted stderr diagnostics. The build is not "
            "golden even though the GUI subprocess returned exit status 0."
        )


def _verify_extracted_archive(archive_path: Path, extraction_root: Path) -> None:
    """Verify CRC, flat layout, manifest bytes, and clean extracted inventory."""
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise AssertionError(f"Release archive CRC failed: {bad_member}")
        names = archive.namelist()
        assert names
        assert all("/" not in name or not name.split("/", 1)[0].startswith(
            f"{APP_NAME.replace(' ', '_')}_v"
        ) for name in names), "Release unexpectedly gained a versioned parent folder"
        archive.extractall(extraction_root)

    manifest_path = extraction_root / "RELEASE_MANIFEST.sha256"
    assert manifest_path.is_file()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative_name = line.partition("  ")
        assert separator and len(digest) == 64
        member = extraction_root / relative_name
        assert member.is_file(), f"Manifest member is missing: {relative_name}"
        assert hashlib.sha256(member.read_bytes()).hexdigest() == digest
    assert_clean_release_directory(extraction_root)


def _verify_synthetic_overlay(archive_path: Path, overlay_root: Path) -> None:
    """Exercise in-place extraction without copying the installed workspace.

    The user's installation legitimately contains a virtual environment,
    catalogs, reports, and archived files. An earlier golden runner copied that
    complete directory merely to simulate an overlay, making the test slow and
    crossing the source boundary it was meant to enforce. This fixture creates
    representative synthetic neighbors instead, confirms that one stale
    release member is overwritten, and proves every user-managed marker is
    preserved byte-for-byte.
    """
    overlay_root.mkdir(parents=True, exist_ok=True)
    stale_member = overlay_root / "app_identity.py"
    stale_member.write_bytes(b"synthetic stale release member\n")
    user_files = {
        overlay_root / "venv" / "Lib" / "site-packages" / "invalid_vendor.py": (
            b"this is deliberately not valid Python\n"
        ),
        overlay_root / "output" / "dataset_tools.db": (
            b"synthetic catalog marker; not a real SQLite database\n"
        ),
        overlay_root
        / "Old Files to be trashed"
        / "test_milestone_7a.py": (
            b"with sqlite3.connect('archived.db') as connection: pass\n"
        ),
    }
    for path, content in user_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    _verify_extracted_archive(archive_path, overlay_root)
    with zipfile.ZipFile(archive_path) as archive:
        assert stale_member.read_bytes() == archive.read("app_identity.py")
    for path, expected in user_files.items():
        assert path.read_bytes() == expected, (
            f"Overlay changed synthetic user-managed data: {path}"
        )


def run(*, include_gui: bool) -> None:
    """Execute every automated, package, and requested live-GUI gate."""
    _verify_and_report_runtime_paths()
    assert_clean_release_directory(PROJECT_ROOT)
    with tempfile.TemporaryDirectory(
        prefix="lora_image_curator_golden_"
    ) as temporary:
        temporary_root = Path(temporary)
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = str(temporary_root / "pycache")
        # The first-launch disclosure has its own focused settings contracts.
        # Automated GUI replay must never block on a modal user decision.
        environment["LORA_IMAGE_CURATOR_TEST_MODE"] = "1"
        # Historical GUI modules use sibling imports. Keep both the project and
        # dedicated test directory explicit for child interpreters on Windows.
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(PROJECT_ROOT), str(TEST_ROOT), environment.get("PYTHONPATH", ""))
        ).rstrip(os.pathsep)
        fixture = create_golden_fixture(temporary_root / "fixture")

        print(
            f"{APP_NAME} v{APP_VERSION} golden-build verification\n"
            "Synthetic test data only; user catalogs and images are untouched.",
            flush=True,
        )
        # Compile from the signed release inventory. DatasetTools intentionally
        # contains a virtual environment and user-managed folders, none of
        # which are project source or part of this release gate.
        _run(
            [sys.executable, "-m", "tools.compile_project"],
            environment=environment,
        )
        _run(
            [sys.executable, "tools/audit_project.py"],
            environment=environment,
        )
        _run(
            [
                sys.executable,
                "-X",
                "dev",
                "tools/run_regressions.py",
                "--fixture",
                str(fixture),
            ],
            environment=environment,
        )

        first_archive = temporary_root / "release-first.zip"
        second_archive = temporary_root / "release-second.zip"
        _run(
            [
                sys.executable,
                "tools/build_release.py",
                "--output",
                str(first_archive),
            ],
            environment=environment,
        )
        first_bytes = first_archive.read_bytes()
        _run(
            [
                sys.executable,
                "tools/build_release.py",
                "--output",
                str(second_archive),
            ],
            environment=environment,
        )
        assert first_bytes == second_archive.read_bytes(), (
            "Two release builds were not byte-for-byte deterministic."
        )
        print(
            "\nVerifying clean extraction and signed member hashes…",
            flush=True,
        )
        extracted = temporary_root / "clean_extraction"
        _verify_extracted_archive(second_archive, extracted)
        print(
            "Verifying a synthetic overwrite installation while preserving "
            "venv, output, and archived local files…",
            flush=True,
        )
        overlay = temporary_root / "overlay"
        _verify_synthetic_overlay(second_archive, overlay)

        if include_gui:
            _run_gui_gate(
                [sys.executable, "-X", "dev", str(PROJECT_ROOT / GUI_ENTRYPOINT)],
                environment=environment,
            )
        else:
            print(
                "\nGUI gates skipped by explicit --no-gui request. "
                "This result is not a final Windows golden-build pass.",
                flush=True,
            )

    if include_gui:
        print(
            f"\nGOLDEN BUILD PASSED — {APP_NAME} v{APP_VERSION}\n"
            "All maintained automated regressions, static checks, deterministic "
            "packaging checks, clean-extraction/overlay checks, and live GUI "
            "checkpoints passed.",
            flush=True,
        )
    else:
        print(
            f"\nHEADLESS GOLDEN CHECKS PASSED — {APP_NAME} v{APP_VERSION}\n"
            "Run again without --no-gui on Windows before recording a golden "
            "handoff.",
            flush=True,
        )


def main() -> int:
    """Parse the headless escape hatch and execute the release gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Skip live Tk tests; useful only for headless development checks.",
    )
    arguments = parser.parse_args()
    run(include_gui=not arguments.no_gui)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
