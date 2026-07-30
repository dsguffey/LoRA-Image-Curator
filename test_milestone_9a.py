"""Dependency-free regressions for Milestone 9A training-text validation."""

from __future__ import annotations

from types import SimpleNamespace

from advanced_search import record_matches_query
from dataset_readiness import build_readiness_report
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    TrainingTextLayers,
    find_repeated_training_text_groups,
)


def _record(**overrides):
    values = {
        "image_id": 1,
        "filename": "portrait.jpg",
        "manual_tags": "red dress, studio",
        "ai_tags_active": "smiling, woman",
        "ai_tags_excluded": "",
        "manual_keyword": "gal_gadot",
        "caption": "A smiling woman in a studio.",
        "review_status": "keep",
        "file_status": "present",
        "suggested_identity": "",
        "identity_review_status": "",
        "face_count": 1,
        "width": 1024,
        "height": 1536,
        "quality_status": "success",
        "sharpness_score": 200.0,
        "file_location_count": 1,
        "nearest_duplicate_similarity": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def run() -> None:
    repeated_a = _record(image_id=1)
    repeated_b = _record(
        image_id=2,
        filename="same_text_action_pose.jpg",
        caption="Different raw caption, but the effective LoRA tags are unchanged.",
        ai_tags_active="woman, smiling, red dress",
    )
    distinct = _record(
        image_id=3,
        filename="distinct.jpg",
        manual_tags="armor, sword, action pose",
    )
    empty = _record(
        image_id=4,
        filename="empty.jpg",
        manual_keyword="",
        manual_tags="",
        ai_tags_active="",
    )
    rejected_repeat = _record(
        image_id=5,
        filename="rejected_repeat.jpg",
        review_status="reject",
    )

    report = build_readiness_report(
        (repeated_a, repeated_b, distinct, empty, rejected_repeat)
    )

    repeated_issue = next(
        issue for issue in report.issues if issue.label == "Repeated Training Text"
    )
    assert repeated_issue.count == 2
    assert repeated_issue.severity == "review"
    assert repeated_issue.maximum_deduction == 5
    assert repeated_issue.query == "id:1 OR id:2"
    assert record_matches_query(repeated_a, repeated_issue.query)
    assert record_matches_query(repeated_b, repeated_issue.query)
    assert not record_matches_query(distinct, repeated_issue.query)
    assert not record_matches_query(rejected_repeat, repeated_issue.query)

    no_text_issue = next(issue for issue in report.issues if issue.label == "No Training Text")
    assert no_text_issue.count == 1
    assert no_text_issue.query == "id:4"

    # The active readiness profile controls the exact sidecar validation. SDXL
    # omits AI tags, so records that differ only in AI suggestions repeat there
    # even when their Flux sidecars are distinct.
    flux_distinct = _record(
        image_id=6,
        filename="flux_distinct_a.jpg",
        manual_tags="portrait",
        ai_tags_active="smiling",
    )
    flux_distinct_b = _record(
        image_id=7,
        filename="flux_distinct_b.jpg",
        manual_tags="portrait",
        ai_tags_active="serious",
    )
    flux_report = build_readiness_report(
        (flux_distinct, flux_distinct_b),
        profile_key="flux_character_lora",
    )
    sdxl_report = build_readiness_report(
        (flux_distinct, flux_distinct_b),
        profile_key="sdxl_character_lora",
    )
    assert next(
        issue for issue in flux_report.issues
        if issue.label == "Repeated Training Text"
    ).count == 0
    assert next(
        issue for issue in sdxl_report.issues
        if issue.label == "Repeated Training Text"
    ).count == 2

    ai_only = _record(
        image_id=8,
        filename="ai_only.jpg",
        manual_keyword="",
        manual_tags="",
        ai_tags_active="woman",
    )
    assert next(
        issue for issue in build_readiness_report(
            (ai_only,),
            profile_key="flux_character_lora",
        ).issues
        if issue.label == "No Training Text"
    ).count == 0
    assert next(
        issue for issue in build_readiness_report(
            (ai_only,),
            profile_key="sdxl_character_lora",
        ).issues
        if issue.label == "No Training Text"
    ).count == 1

    # Cross-layer duplicates are removed by the canonical export builder. The
    # validator must therefore group these despite their different raw layers.
    canonical_groups = find_repeated_training_text_groups(
        (
            (
                10,
                TrainingTextLayers(
                    trigger_keyword="person",
                    manual_tags=("portrait",),
                    active_ai_tags=("smiling",),
                ),
            ),
            (
                11,
                TrainingTextLayers(
                    trigger_keyword="person",
                    manual_tags=("portrait", "smiling"),
                    active_ai_tags=("smiling",),
                ),
            ),
        ),
        BUILTIN_TRAINING_PROFILES["flux_lora"],
    )
    assert canonical_groups == ((10, 11),)

    print(
        "Milestone 9A tests passed: profile-specific exported sidecar text is "
        "validated with the canonical builder, findings open exact browser "
        "result sets, rejected records are ignored, and empty text remains a "
        "separate blocking issue."
    )


if __name__ == "__main__":
    run()
