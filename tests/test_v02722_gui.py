"""Current cumulative Windows GUI smoke entry point for v0.27.22."""

from __future__ import annotations

from test_v02721_gui import run as run_v02721


def run() -> None:
    """Replay the Windows GUI chain after the Florence recovery update."""
    run_v02721()
    print(
        "v0.27.22 cumulative GUI smoke test passed: large-catalog Florence "
        "recovery retained all maintained application checkpoints."
    )


if __name__ == "__main__":
    run()
