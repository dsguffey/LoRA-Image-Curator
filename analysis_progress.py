"""Phase-aware progress and ETA calculations for long provider workflows.

The GUI receives provider callbacks from worker threads, but timing and display
policy do not belong in those providers.  This module keeps the calculation
dependency-free and deterministic enough to regression test:

* one workflow percentage advances monotonically across named phases;
* each phase has its own elapsed time and rate estimate;
* an ETA is withheld until enough real samples exist to avoid flashing a wildly
  misleading number after the first image.

The tracker intentionally estimates only the current phase.  Predicting a later
provider before it has processed any images would imply precision the app does
not possess.
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import Callable, Sequence


MINIMUM_ETA_COMPLETIONS = 5
MINIMUM_ETA_SECONDS = 2.0


@dataclass(slots=True, frozen=True)
class ProgressSnapshot:
    """One presentation-ready view of workflow and phase progress."""

    phase: str
    completed: int
    total: int
    overall_percent: float
    phase_elapsed_seconds: float
    estimated_remaining_seconds: float | None


class WorkflowProgressTracker:
    """Translate per-phase callbacks into stable overall progress and ETAs."""

    def __init__(
        self,
        phases: Sequence[str],
        *,
        weights: Sequence[float] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        normalized = tuple(str(phase).strip() for phase in phases if str(phase).strip())
        if not normalized:
            raise ValueError("At least one workflow phase is required.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Workflow phase names must be unique.")
        raw_weights = (
            tuple(float(value) for value in weights)
            if weights is not None
            else tuple(1.0 for _phase in normalized)
        )
        if len(raw_weights) != len(normalized):
            raise ValueError("Workflow weights must match the phase count.")
        if any(value <= 0 for value in raw_weights):
            raise ValueError("Workflow weights must be positive.")
        weight_total = sum(raw_weights)

        self.phases = normalized
        self.weights = tuple(value / weight_total for value in raw_weights)
        self._clock = clock
        self._phase_started_at: dict[str, float] = {}
        self._phase_first_completed: dict[str, int] = {}
        self._last_overall_percent = 0.0

    def update(self, phase: str, completed: int, total: int) -> ProgressSnapshot:
        """Record one callback and return a monotonic display snapshot."""
        if phase not in self.phases:
            raise ValueError(f"Unknown workflow phase: {phase}")
        safe_total = max(0, int(total))
        safe_completed = max(0, int(completed))
        if safe_total:
            safe_completed = min(safe_completed, safe_total)

        now = self._clock()
        if phase not in self._phase_started_at:
            self._phase_started_at[phase] = now
            self._phase_first_completed[phase] = safe_completed

        elapsed = max(0.0, now - self._phase_started_at[phase])
        phase_fraction = (
            safe_completed / safe_total
            if safe_total
            else 0.0
        )
        phase_index = self.phases.index(phase)
        completed_phase_weight = sum(self.weights[:phase_index])
        calculated_percent = (
            completed_phase_weight
            + (self.weights[phase_index] * phase_fraction)
        ) * 100.0
        # Providers are expected to arrive in phase order.  The guard makes the
        # UI robust if a delayed queue message arrives after a later phase began.
        overall_percent = max(self._last_overall_percent, calculated_percent)
        self._last_overall_percent = min(100.0, overall_percent)

        first_completed = self._phase_first_completed[phase]
        measured_items = max(0, safe_completed - first_completed)
        eta: float | None = None
        if (
            safe_total > safe_completed
            and measured_items >= MINIMUM_ETA_COMPLETIONS
            and elapsed >= MINIMUM_ETA_SECONDS
        ):
            seconds_per_item = elapsed / measured_items
            eta = seconds_per_item * (safe_total - safe_completed)

        return ProgressSnapshot(
            phase=phase,
            completed=safe_completed,
            total=safe_total,
            overall_percent=self._last_overall_percent,
            phase_elapsed_seconds=elapsed,
            estimated_remaining_seconds=eta,
        )


def format_duration(seconds: float | None) -> str:
    """Format a rough duration without suggesting second-level precision."""
    if seconds is None:
        return "calculating…"
    safe_seconds = max(0, int(round(seconds)))
    if safe_seconds < 60:
        return f"about {safe_seconds} sec"
    minutes, remaining_seconds = divmod(safe_seconds, 60)
    if minutes < 60:
        if minutes < 10 and remaining_seconds >= 30:
            minutes += 1
        return f"about {minutes} min"
    hours, remaining_minutes = divmod(minutes, 60)
    if remaining_minutes >= 30:
        hours += 1
    return f"about {hours} hr"
