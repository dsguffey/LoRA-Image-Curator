"""Dependency-light regressions for the v0.21.0 Phase 1 follow-up."""

from __future__ import annotations

from analysis_progress import WorkflowProgressTracker, format_duration
from catalog_browser import CARD_BATCH_SIZE, CARD_PAGE_SIZE


class FakeClock:
    """Small deterministic clock used to test rate estimates."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_overall_progress_does_not_restart_between_phases() -> None:
    clock = FakeClock()
    tracker = WorkflowProgressTracker(
        ("Cataloging", "Florence analysis", "Face analysis"),
        weights=(0.05, 0.65, 0.30),
        clock=clock,
    )
    catalog = tracker.update("Cataloging", 10, 10)
    assert abs(catalog.overall_percent - 5.0) < 0.0001

    florence_start = tracker.update("Florence analysis", 1, 10)
    assert florence_start.overall_percent > catalog.overall_percent
    assert florence_start.estimated_remaining_seconds is None

    clock.advance(5.0)
    florence_sampled = tracker.update("Florence analysis", 6, 10)
    assert abs(florence_sampled.estimated_remaining_seconds - 4.0) < 0.0001
    assert florence_sampled.overall_percent > florence_start.overall_percent

    # A delayed queue item from an earlier phase must never move the bar back.
    delayed = tracker.update("Cataloging", 9, 10)
    assert delayed.overall_percent == florence_sampled.overall_percent


def test_duration_labels_are_deliberately_rough() -> None:
    assert format_duration(None) == "calculating…"
    assert format_duration(12) == "about 12 sec"
    assert format_duration(95) == "about 2 min"
    assert format_duration(5_400) == "about 2 hr"


def test_canvas_page_remains_bounded() -> None:
    assert CARD_BATCH_SIZE == 100
    assert CARD_PAGE_SIZE == CARD_BATCH_SIZE
    assert CARD_PAGE_SIZE <= 100


def run() -> None:
    test_overall_progress_does_not_restart_between_phases()
    test_duration_labels_are_deliberately_rough()
    test_canvas_page_remains_bounded()
    print(
        "Milestone 10 Phase 1B tests passed: overall provider progress is "
        "monotonic, Florence ETA waits for measured samples, and thumbnail "
        "canvas pages remain bounded."
    )


if __name__ == "__main__":
    run()
