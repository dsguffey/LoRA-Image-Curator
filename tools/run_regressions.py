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
    "test_milestone_6b.py",
    "test_milestone_7a.py",
    "test_milestone_7b.py",
    "test_milestone_7c.py",
)
SELF_CONTAINED_TESTS = (
    "test_milestone_7d.py",
    "test_milestone_8a.py",
    "test_milestone_8b.py",
    "test_milestone_8c.py",
    "test_milestone_8d.py",
    "test_milestone_8e.py",
    "test_milestone_8f.py",
    "test_milestone_8g.py",
    "test_milestone_8h.py",
    "test_milestone_9a.py",
    "test_milestone_9b.py",
    "test_milestone_10_phase1.py",
    "test_milestone_10_phase1b.py",
    "test_milestone_10_phase1c.py",
    "test_v0240_regression.py",
    "test_v0250_regression.py",
    "test_v0252_regression.py",
    "test_v0260_regression.py",
    "test_v0270_regression.py",
    "test_v0271_regression.py",
    "test_v0272_regression.py",
    "test_v0273_regression.py",
    "test_v0274_regression.py",
    "test_v0275_regression.py",
    "test_v0276_regression.py",
    "test_v0277_regression.py",
    "test_v0278_regression.py",
    "test_v0279_regression.py",
    "test_v02710_regression.py",
    "test_v02711_regression.py",
    "test_v02712_regression.py",
    "test_v02713_regression.py",
    "test_v02714_regression.py",
    "test_v02715_regression.py",
    "test_v02716_regression.py",
    "test_v02717_regression.py",
)


def run_test(test_name: str, *arguments: str) -> None:
    """Run one test under Python development mode and stop on failure."""
    command = [
        sys.executable,
        "-X",
        "dev",
        str(PROJECT_ROOT / test_name),
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
