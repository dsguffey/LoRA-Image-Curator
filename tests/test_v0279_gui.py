"""Current cumulative Windows GUI smoke entry point for v0.27.9."""

from __future__ import annotations

from test_v0278_gui import run as run_v0278


def run() -> None:
    """Run every cumulative GUI checkpoint through v0.27.8."""
    run_v0278()
    print(
        "v0.27.9 cumulative GUI smoke test passed: Python 3.14 SQLite handles "
        "closed cleanly and all maintained interface checkpoints completed."
    )


if __name__ == "__main__":
    run()
