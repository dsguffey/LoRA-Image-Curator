"""Current cumulative Windows GUI smoke entry point for v0.27.16."""

from __future__ import annotations

from test_v02715_gui import run as run_v02715


def run() -> None:
    """Replay the full GUI chain for the repository-readiness candidate."""
    run_v02715()
    print(
        "v0.27.16 cumulative GUI smoke test passed: repository-readiness files "
        "are in the signed release boundary and all maintained GUI checkpoints "
        "completed."
    )


if __name__ == "__main__":
    run()
