"""Current cumulative Windows GUI smoke entry point for v0.27.10."""

from __future__ import annotations

from test_v0279_gui import run as run_v0279


def run() -> None:
    """Replay the complete maintained GUI chain through v0.27.10."""
    run_v0279()
    print(
        "v0.27.10 cumulative GUI smoke test passed: installed local archives "
        "remain outside project validation and all interface checkpoints "
        "completed."
    )


if __name__ == "__main__":
    run()
