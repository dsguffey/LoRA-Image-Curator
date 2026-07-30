"""Current cumulative Windows GUI smoke entry point for v0.27.18."""

from __future__ import annotations

from test_v02717_gui import run as run_v02717


def run() -> None:
    """Replay the Windows-passing runtime chain for this documentation release."""
    run_v02717()
    print(
        "v0.27.18 cumulative GUI smoke test passed: the professional "
        "repository review retained all maintained GUI checkpoints."
    )


if __name__ == "__main__":
    run()
