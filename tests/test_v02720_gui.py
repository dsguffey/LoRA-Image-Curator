"""Current cumulative Windows GUI smoke entry point for v0.27.20."""

from __future__ import annotations

from test_v02719_gui import run as run_v02719


def run() -> None:
    """Replay the Windows runtime chain after repository/setup-only changes."""
    run_v02719()
    print(
        "v0.27.20 cumulative GUI smoke test passed: dependency and repository "
        "organization changes retained all maintained application checkpoints."
    )


if __name__ == "__main__":
    run()
