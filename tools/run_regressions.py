"""Run the complete non-GUI historical and current regression chain safely.

The four oldest milestone tests require an existing catalog. Each receives its
own temporary copy so the caller's fixture is never migrated or edited in place.
All later tests create isolated data and run without arguments.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TESTS = (
    "tests/test_milestone_6b.py",
    "tests/test_milestone_7a.py",
    "tests/test_milestone_7b.py",
    "tests/test_milestone_7c.py",
)
SELF_CONTAINED_TESTS = (
    "tests/test_milestone_7d.py",
    "tests/test_milestone_8a.py",
    "tests/test_milestone_8b.py",
    "tests/test_milestone_8c.py",
    "tests/test_milestone_8d.py",
    "tests/test_milestone_8e.py",
    "tests/test_milestone_8f.py",
    "tests/test_milestone_8g.py",
    "tests/test_milestone_8h.py",
    "tests/test_milestone_9a.py",
    "tests/test_milestone_9b.py",
    "tests/test_milestone_10_phase1.py",
    "tests/test_milestone_10_phase1b.py",
    "tests/test_milestone_10_phase1c.py",
    "tests/test_v0240_regression.py",
    "tests/test_v0250_regression.py",
    "tests/test_v0252_regression.py",
    "tests/test_v0260_regression.py",
    "tests/test_v0270_regression.py",
    "tests/test_v0271_regression.py",
    "tests/test_v0272_regression.py",
    "tests/test_v0273_regression.py",
    "tests/test_v0274_regression.py",
    "tests/test_v0275_regression.py",
    "tests/test_v0276_regression.py",
    "tests/test_v0277_regression.py",
    "tests/test_v0278_regression.py",
    "tests/test_v0279_regression.py",
    "tests/test_v02710_regression.py",
    "tests/test_v02711_regression.py",
    "tests/test_v02712_regression.py",
    "tests/test_v02713_regression.py",
    "tests/test_v02714_regression.py",
    "tests/test_v02715_regression.py",
    "tests/test_v02716_regression.py",
    "tests/test_v02717_regression.py",
    "tests/test_v02718_regression.py",
    "tests/test_v02719_regression.py",
    "tests/test_v02720_regression.py",
    "tests/test_v02721_regression.py",
    "tests/test_v02722_regression.py",
    "tests/test_v02723_regression.py",
    "tests/test_clean_install.py",
)


def run_test(test_name: str, *arguments: str) -> None:
    """Run one test under Python development mode and stop on failure."""
    module_name = Path(test_name).with_suffix("").as_posix().replace("/", ".")
    command = [
        sys.executable,
        "-X",
        "dev",
        "-m",
        module_name,
        *arguments,
    ]
    print(f"\n=== {test_name} ===", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def run(fixture: Path) -> None:
    """Run every regression while protecting the supplied fixture database."""
    fixture = fixture.expanduser().resolve()
    if not fixture.exists() or not fixture.is_file():
        raise FileNotFoundError(f"Fixture catalog not found: {fixture}")

    with tempfile.TemporaryDirectory(
        prefix="lora_image_curator_regressions_"
    ) as temp:
        temporary_root = Path(temp)
        for test_name in FIXTURE_TESTS:
            fixture_copy = temporary_root / f"{Path(test_name).stem}.db"
            shutil.copy2(fixture, fixture_copy)
            run_test(test_name, str(fixture_copy))

    for test_name in SELF_CONTAINED_TESTS:
        run_test(test_name)


def main() -> int:
    """Parse the fixture path and execute the complete regression chain."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        required=True,
        type=Path,
        help="Schema-compatible SQLite catalog used by the four oldest tests.",
    )
    arguments = parser.parse_args()
    run(arguments.fixture)
    print("\nAll non-GUI regressions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
