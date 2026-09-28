"""Run supported non-GUI historical contracts and current regressions safely.

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
    "tests/test_v0280_regression.py",
    "tests/test_v0281_regression.py",
    "tests/test_v0283_regression.py",
    "tests/test_v0284_regression.py",
    "tests/test_trigger_export_regression.py",
    "tests/test_export_source_resolution.py",
    "tests/test_florence_caption_search.py",
    "tests/test_caption_tagging_mode.py",
    "tests/test_browser_polish.py",
    "tests/test_clean_install.py",
)

# These versioned files remain historical evidence and in the source archive.
# Only assertions tied to retired UI, packaging, setup, or documentation
# contracts are omitted from today's gate. Every other test_* function in
# each listed module still runs; a renamed/deleted omission fails loudly.
RETIRED_ASSERTIONS = {
    "tests/test_v0250_regression.py": {
        "test_public_identity": "InsightFace pack Browse was replaced by YuNet/SFace model-folder selection",
    },
    "tests/test_v02714_regression.py": {
        "test_ui_contracts_explain_progress_and_scroll_ownership": "the Face progress label names retired InsightFace",
    },
    "tests/test_v02718_regression.py": {
        "test_readme_serves_a_first_time_repository_visitor": "README now documents Install Manager installation, not source ZIP setup",
        "test_dependency_free_repository_workflow_is_bounded": "CI now installs NumPy and Pillow for current contracts",
    },
    "tests/test_v02719_regression.py": {
        "test_github_documentation_matches_dependency_tiers": "README now documents managed installation and YuNet/SFace, not manual InsightFace setup",
    },
    "tests/test_v02720_regression.py": {
        "test_recycle_bin_is_standard_and_body_analysis_remains_optional": "MediaPipe is now exactly pinned by the managed dependency profile",
        "test_tests_are_public_but_no_longer_clutter_the_repository_root": "README now directs users to Install Manager rather than developer gates",
    },
    "tests/test_v02721_regression.py": {
        "test_native_input_and_setup_readiness_boundaries_are_explicit": "setup now reads the pinned Transformers version from a profile",
    },
    "tests/test_v0281_regression.py": {
        "test_gui_preflights_name_every_download_before_network_authority": "LIC no longer downloads the retired InsightFace pack",
    },
}

SELECTED_HISTORY_SCRIPT = """
import importlib
import inspect
import sys

module = importlib.import_module(sys.argv[1])
retired = set(sys.argv[2:])
tests = [
    (name, function)
    for name, function in vars(module).items()
    if name.startswith('test_')
    and inspect.isfunction(function)
    and function.__module__ == module.__name__
]
missing = retired - {name for name, _function in tests}
if missing:
    raise AssertionError(f'Retired historical assertions disappeared: {sorted(missing)}')
for name, function in tests:
    if name not in retired:
        function()
print(f'{module.__name__}: {len(tests) - len(retired)} supported historical contracts passed')
"""


def run_test(test_name: str, *arguments: str) -> None:
    """Run one test under Python development mode and stop on failure."""
    module_name = Path(test_name).with_suffix("").as_posix().replace("/", ".")
    retired = RETIRED_ASSERTIONS.get(test_name)
    if retired:
        command = [sys.executable, "-X", "dev", "-c", SELECTED_HISTORY_SCRIPT,
                   module_name, *retired]
    else:
        command = [sys.executable, "-X", "dev", "-m", module_name, *arguments]
    print(f"\n=== {test_name} ===", flush=True)
    if retired:
        for name, reason in retired.items():
            print(f"Historical only: {name} - {reason}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def run(fixture: Path) -> None:
    """Run supported contracts while protecting the supplied fixture database."""
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
    """Parse the fixture path and execute the supported regression chain."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        required=True,
        type=Path,
        help="Schema-compatible SQLite catalog used by the four oldest tests.",
    )
    arguments = parser.parse_args()
    run(arguments.fixture)
    print("\nAll supported non-GUI regressions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
