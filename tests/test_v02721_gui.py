"""Current cumulative Windows GUI smoke entry point for v0.27.21."""

from __future__ import annotations

from test_v02720_gui import run as run_v02720


def run() -> None:
    """Replay the Windows GUI chain after the provider-loader-only update."""
    run_v02720()
    print(
        "v0.27.21 cumulative GUI smoke test passed: Florence dependency and "
        "security changes retained all maintained application checkpoints."
    )


if __name__ == "__main__":
    run()
