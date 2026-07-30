"""Cooperative run-control primitives shared by local analysis providers.

Provider libraries frequently execute one indivisible model call at a time.
Cancellation and pause therefore take effect only between images.  This keeps
catalog commits atomic and, for pause/resume, leaves the already-loaded model
resident in memory so continuing does not pay model-startup cost again.
"""

from __future__ import annotations

import time

from threading import Event


class AnalysisCancelled(RuntimeError):
    """Signal a deliberate user cancellation between provider work items."""


def raise_if_cancelled(cancel_event: Event | None) -> None:
    """Stop at a safe item boundary when cancellation has been requested."""
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelled(
            "The provider run was cancelled by the user. Results committed "
            "before cancellation remain in the catalog."
        )


def wait_if_paused(
    pause_event: Event | None,
    cancel_event: Event | None = None,
    *,
    poll_seconds: float = 0.10,
) -> None:
    """Wait at a safe image boundary until a paused run is resumed.

    The loop continues checking cancellation so a user can cancel a paused run
    without first resuming it. A short bounded sleep avoids busy-spinning while
    still making Pause, Resume, and Cancel feel responsive.
    """
    if pause_event is None:
        raise_if_cancelled(cancel_event)
        return
    while pause_event.is_set():
        raise_if_cancelled(cancel_event)
        time.sleep(max(0.02, float(poll_seconds)))
    raise_if_cancelled(cancel_event)
