"""Current cumulative Windows GUI smoke entry point for v0.27.8."""

from __future__ import annotations

from test_v0277_gui import run as run_v0277


def run() -> None:
    """Run every cumulative GUI checkpoint through v0.27.7."""
    run_v0277()
    print(
        "v0.27.8 cumulative GUI smoke test passed: installed runtime output "
        "remained outside the golden audit and all maintained interface "
        "checkpoints completed."
    )


if __name__ == "__main__":
    run()
