"""Current cumulative Windows GUI smoke entry point for v0.28.2."""

from __future__ import annotations

from test_v0281_gui import run as run_v0281


def run() -> None:
    """Replay the established GUI for the packaging-only v0.28.2 change."""
    run_v0281()
    print(
        "v0.28.2 cumulative GUI smoke test passed: the slim Portable Source "
        "distribution preserves the established application workflow."
    )


if __name__ == "__main__":
    run()
