"""Current cumulative Windows GUI smoke entry point for v0.28.0."""

from __future__ import annotations

from test_v02723_gui import run as run_v02723


def run() -> None:
    """Replay the established GUI under the portable-foundation release."""
    run_v02723()
    print(
        "v0.28.0 cumulative GUI smoke test passed: provider provenance, "
        "first-launch disclosure, and smart-launch changes preserve the "
        "established catalog workspace."
    )


if __name__ == "__main__":
    run()
