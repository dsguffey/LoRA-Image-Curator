"""Current cumulative Windows GUI smoke entry point for v0.27.17."""

from __future__ import annotations

from test_v02716_gui import run as run_v02716


def run() -> None:
    """Replay the isolated historical chain and current GUI checkpoints."""
    run_v02716()
    print(
        "v0.27.17 cumulative GUI smoke test passed: historical Tk interpreter "
        "lifetimes were isolated, stderr remained clean, and all maintained "
        "GUI checkpoints completed."
    )


if __name__ == "__main__":
    run()
