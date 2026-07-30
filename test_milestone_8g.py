"""Dependency-free regression tests for Milestone 8G finalization and handoff."""

from __future__ import annotations

import tempfile

from pathlib import Path
from types import SimpleNamespace

from dataset_export import (
    ExportImageRecord,
    ExportOptions,
    build_export_plan,
    execute_export,
)
from readiness_frame import eligible_export_records
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    TrainingTextLayers,
    build_training_text,
)


def _record(image_id: int, source: Path) -> ExportImageRecord:
    return ExportImageRecord(
        image_id=image_id,
        source_path=source,
        filename=source.name,
        content_sha256=f"{image_id:064x}",
        review_status="keep",
        suggested_identity="",
        identity_review_status="",
        excluded_ai_tags=(),
        layers=TrainingTextLayers(
            trigger_keyword="subject_token",
            manual_tags=("portrait",),
            active_ai_tags=("person",),
            raw_caption="A portrait photograph.",
        ),
    )


def test_final_scope_excludes_only_deliberate_non_training_statuses() -> None:
    records = [
        SimpleNamespace(image_id=1, review_status="keep"),
        SimpleNamespace(image_id=2, review_status="review"),
        SimpleNamespace(image_id=3, review_status="unreviewed"),
        SimpleNamespace(image_id=4, review_status="reject"),
        SimpleNamespace(image_id=5, review_status="quarantined"),
    ]
    eligible = eligible_export_records(records)
    assert [record.image_id for record in eligible] == [1, 2, 3]


def test_all_lora_targets_have_transparent_training_profiles() -> None:
    layers = TrainingTextLayers(
        trigger_keyword="subject_token",
        manual_tags=("portrait",),
        active_ai_tags=("person",),
    )
    assert build_training_text(
        layers, BUILTIN_TRAINING_PROFILES["sd15_lora"]
    ) == "subject_token, portrait"
    assert build_training_text(
        layers, BUILTIN_TRAINING_PROFILES["general_lora"]
    ) == "subject_token, portrait, person"


def test_handoff_readme_is_planned_and_written_without_overwrite() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8g_") as temporary:
        root = Path(temporary)
        source = root / "portrait.png"
        source.write_bytes(b"fixture-image")
        destination = root / "export"
        destination.mkdir()
        (destination / "README.txt").write_text("preserve me", encoding="utf-8")

        plan = build_export_plan(
            [_record(1, source)],
            ExportOptions(
                destination=destination,
                profile=BUILTIN_TRAINING_PROFILES["flux_lora"],
                create_manifest=False,
                create_readme=True,
                handoff_scope='Image set "Final Gal Gadot"',
                handoff_notes=(
                    "Readiness: 92% — Ready to export.",
                    "Possible Duplicates: 2 images.",
                ),
            ),
        )
        assert plan.readme_path is not None
        assert plan.readme_path.name == "README_2.txt"

        result = execute_export(plan)
        assert result.status == "complete"
        assert result.readme_path == plan.readme_path
        assert (destination / "README.txt").read_text(encoding="utf-8") == "preserve me"
        text = plan.readme_path.read_text(encoding="utf-8")
        assert 'Scope: Image set "Final Gal Gadot"' in text
        assert "Training text profile: Flux LoRA" in text
        assert "Readiness: 92% — Ready to export." in text
        assert "Possible Duplicates: 2 images." in text
        assert "learning-rate" in text


def run() -> None:
    test_final_scope_excludes_only_deliberate_non_training_statuses()
    test_all_lora_targets_have_transparent_training_profiles()
    test_handoff_readme_is_planned_and_written_without_overwrite()
    print(
        "Milestone 8G tests passed: eligible-scope filtering, SD 1.5 and General "
        "handoff profiles, collision-safe README planning, and non-destructive "
        "training-handoff documentation."
    )


if __name__ == "__main__":
    run()
