"""Dependency-light regression coverage for the v0.27 browser UX release."""

from __future__ import annotations

from types import SimpleNamespace

from browser_workflow import BrowserFilterState, apply_browser_filter_state
from browser_workflow_dialogs import CurationOptions


def _record(image_id: int, **overrides: object) -> SimpleNamespace:
    """Build a complete readiness/body fixture with conservative defaults."""
    values: dict[str, object] = {
        "image_id": image_id,
        "search_blob": "portrait, person",
        "manual_tags": "portrait",
        "ai_tags_active": "person",
        "ai_tags_excluded": "",
        "manual_keyword": "test_person",
        "caption": "A portrait.",
        "review_status": "keep",
        "file_status": "present",
        "suggested_identity": "",
        "identity_review_status": "",
        "face_count": 1,
        "width": 1024,
        "height": 1536,
        "quality_status": "success",
        "sharpness_score": 200.0,
        "perceptual_hash": f"{image_id:016x}",
        "file_location_count": 1,
        "nearest_duplicate_similarity": None,
        "has_manual_metadata": True,
        "ocr_text": "",
        "body_analysis_available": True,
        "body_detected": True,
        "body_face_visible": True,
        "body_pose_count": 1,
        "full_body": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_independent_subject_filters() -> None:
    """Face, body, and catalog-state filters must compose rather than replace."""
    matching = _record(1)
    no_face = _record(2, face_count=0)
    partial = _record(3, full_body=False)
    unreviewed = _record(4, review_status="unreviewed")
    state = BrowserFilterState(
        catalog_state="Reviewed",
        face_state="Has face",
        body_state="Full body",
    )
    result = apply_browser_filter_state((matching, no_face, partial, unreviewed), state)
    assert [record.image_id for record in result.records] == [1]
    assert state.is_active()
    assert "Has face" in state.summary()
    assert "Full body" in state.summary()


def test_legacy_filter_migration_and_clear_state() -> None:
    """A v0.26 single-dropdown value migrates without remaining perpetually active."""
    migrated = BrowserFilterState(catalog_state="Full body").normalized()
    assert migrated.catalog_state == "All images"
    assert migrated.body_state == "Full body"
    assert migrated.is_active()
    assert not BrowserFilterState().is_active()


def test_curation_defaults_and_static_ui_contract() -> None:
    """Protect the separate curation workflow and conspicuous filter styling."""
    options = CurationOptions()
    assert options.blur is True
    assert options.no_person_or_face is False
    assert options.small_face_percent == 0.25

    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    browser_source = (project / "catalog_browser.py").read_text(encoding="utf-8")
    app_source = (project / "app.py").read_text(encoding="utf-8")
    theme_source = (project / "ui_theme.py").read_text(encoding="utf-8")
    setup_source = (project / "body_setup_dialog.py").read_text(encoding="utf-8")
    assert 'text="Filters"' in browser_source
    assert 'text="Filters On"' in browser_source
    assert "self._build_curation_panel(browser_area)" not in browser_source
    assert 'label="Remove Unnecessary Images…"' in app_source
    assert 'label="Privacy & Diagnostics…"' in app_source
    assert 'label="Body / Pose Scanning…"' in app_source
    assert 'menu_bar.add_cascade(label="Filters"' in app_source
    assert '"Active.TButton"' in theme_source
    assert "threading.Thread" in setup_source
    assert "after_idle(self._start)" in setup_source


def main() -> None:
    test_independent_subject_filters()
    test_legacy_filter_migration_and_clear_state()
    test_curation_defaults_and_static_ui_contract()
    print(
        "v0.27.0 regression tests passed: composable subject filters, legacy "
        "migration, separate curation, active-button styling, and responsive setup."
    )


if __name__ == "__main__":
    main()
