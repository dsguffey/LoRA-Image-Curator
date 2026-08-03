"""Current cumulative Windows GUI smoke entry point for v0.27.7."""

from __future__ import annotations

from test_v0276_gui import run as run_v0276


def run() -> None:
    """Run every cumulative GUI checkpoint through v0.27.6."""
    run_v0276()
    print(
        "v0.27.7 cumulative GUI smoke test passed: project-owned compilation "
        "scope and all maintained interface checkpoints completed."
    )


if __name__ == "__main__":
    run()
