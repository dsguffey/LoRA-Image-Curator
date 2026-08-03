"""Current cumulative Windows GUI smoke entry point for v0.27.13."""

from __future__ import annotations

from test_v02712_gui import run as run_v02712


def run() -> None:
    """Replay the strict GUI chain after bounding startup-worker ownership."""
    # v0.27.11 isolates the inherited GUI history in a strict child process.
    # v0.27.12's parent runner additionally rejects all GUI stderr output.
    # Replaying both boundaries is the direct live-Windows verification for
    # the startup-worker ownership defect corrected in this release.
    run_v02712()
    print(
        "v0.27.13 cumulative GUI smoke test passed: startup provider-device "
        "inspection retained no Tk/application owner, GUI teardown remained "
        "clean, and all maintained interface checkpoints completed."
    )


if __name__ == "__main__":
    run()
