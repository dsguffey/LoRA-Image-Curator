"""Current cumulative Windows GUI smoke entry point for v0.27.6."""

from __future__ import annotations

from test_v0275_gui import run as run_v0275


def run() -> None:
    """Run every cumulative GUI checkpoint through v0.27.5."""
    run_v0275()
    print(
        "v0.27.6 cumulative GUI smoke test passed: all maintained current "
        "interface checkpoints completed on a live Tk desktop."
    )


if __name__ == "__main__":
    run()
