"""Current cumulative Windows GUI smoke entry point for v0.27.23."""

from __future__ import annotations

from test_v02722_gui import run as run_v02722


def run() -> None:
    """Replay the unchanged GUI after the setup-only NVIDIA runtime repair."""
    run_v02722()
    print(
        "v0.27.23 cumulative GUI smoke test passed: the NVIDIA dependency "
        "repair leaves the established application and catalog UI unchanged."
    )


if __name__ == "__main__":
    run()
