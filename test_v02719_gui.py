"""Current cumulative Windows GUI smoke entry point for v0.27.19."""

from __future__ import annotations

from test_v02718_gui import run as run_v02718


def run() -> None:
    """Replay the Windows runtime chain after the setup-only update."""
    run_v02718()
    print(
        "v0.27.19 cumulative GUI smoke test passed: portable setup changes "
        "retained all maintained application checkpoints."
    )


if __name__ == "__main__":
    run()
