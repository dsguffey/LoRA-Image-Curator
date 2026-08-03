"""Dependency-free tests for the non-mutating new-computer QA boundary."""

from __future__ import annotations

import hashlib
import tempfile

from pathlib import Path

from tools.clean_install_check import inspect_clean_install, phase_errors


def _fixture_release(root: Path) -> Path:
    """Create a minimal signed source folder for clean-install contract tests."""
    root.mkdir(parents=True)
    member = root / "app.py"
    member.write_text("# synthetic release member\n", encoding="utf-8")
    digest = hashlib.sha256(member.read_bytes()).hexdigest()
    (root / "RELEASE_MANIFEST.sha256").write_text(
        f"{digest}  app.py\n",
        encoding="utf-8",
    )
    return root


def test_before_setup_distinguishes_folder_and_user_state() -> None:
    """Catch both a reused source folder and APPDATA that masks first run."""
    with tempfile.TemporaryDirectory(prefix="lora_clean_install_") as temporary:
        root = Path(temporary)
        release = _fixture_release(root / "release")
        appdata = root / "appdata"
        environment = {"APPDATA": str(appdata)}

        report = inspect_clean_install(release, environment=environment)
        assert phase_errors("before-setup", report) == ()

        (release / "venv" / "Scripts").mkdir(parents=True)
        (release / "venv" / "Scripts" / "python.exe").write_bytes(b"synthetic")
        (appdata / "LoRAImageCurator").mkdir(parents=True)
        reused = inspect_clean_install(release, environment=environment)
        errors = phase_errors("before-setup", reused)
        assert any("venv" in error for error in errors)
        assert any("first-run" in error for error in errors)


def test_after_setup_and_upgrade_require_their_expected_state() -> None:
    """Keep post-setup and upgrade phases explicit instead of conflating them."""
    with tempfile.TemporaryDirectory(prefix="lora_upgrade_qa_") as temporary:
        root = Path(temporary)
        release = _fixture_release(root / "release")
        environment = {"APPDATA": str(root / "appdata")}
        report = inspect_clean_install(release, environment=environment)
        assert phase_errors("after-setup", report)
        assert phase_errors("upgrade", report)


def test_manifest_tampering_fails_without_mutating_the_release() -> None:
    """Reject altered archive bytes while leaving the fixture in place."""
    with tempfile.TemporaryDirectory(prefix="lora_manifest_qa_") as temporary:
        root = Path(temporary)
        release = _fixture_release(root / "release")
        source = release / "app.py"
        source.write_text("# changed\n", encoding="utf-8")
        report = inspect_clean_install(
            release,
            environment={"APPDATA": str(root / "appdata")},
        )
        assert not report.release_hashes_valid
        assert source.read_text(encoding="utf-8") == "# changed\n"


if __name__ == "__main__":
    test_before_setup_distinguishes_folder_and_user_state()
    test_after_setup_and_upgrade_require_their_expected_state()
    test_manifest_tampering_fails_without_mutating_the_release()
    print("Clean-install QA boundary tests passed.")
