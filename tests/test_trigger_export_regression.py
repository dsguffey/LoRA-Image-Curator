"""Synthetic trigger-keyword projection and preview/sidecar parity contracts."""

from __future__ import annotations

import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path

from catalog_edits import CatalogEditService
from dataset_export import (
    DatasetExportRepository,
    ExportOptions,
    build_export_plan,
    execute_export,
)
from export_dialog import DatasetExportDialog
from tests.test_milestone_7d import _seed_catalog
from training_text import (
    BUILTIN_TRAINING_PROFILES,
    TrainingTextLayers,
    build_training_text,
    custom_training_profile,
)


class _Value:
    """Minimal Tk variable substitute for exercising the real preview method."""

    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class _PreviewHarness:
    """Run export-dialog profile resolution and preview without a Tk window."""

    _current_profile = DatasetExportDialog._current_profile
    _refresh_sample = DatasetExportDialog._refresh_sample

    def __init__(self, records: list, profile_label: str) -> None:
        self.records = records
        self.profile_var = _Value(profile_label)
        self.sample_var = _Value()
        self.preflight_var = _Value()
        self.custom_trigger_var = _Value(True)
        self.custom_manual_var = _Value(True)
        self.custom_ai_var = _Value(True)
        self.custom_caption_var = _Value(False)

    def _preflight_lines(self) -> list[str]:
        return []


def _current_catalog(root: Path) -> tuple[Path, list[Path]]:
    """Seed provider layers, then assign real current-category trigger tags."""
    database, _ids, sources = _seed_catalog(root)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM image_tags WHERE tag_id = 1")
        for tag_id, name in ((2, "manualtag"), (3, "aitag1"), (4, "aitag2")):
            connection.execute(
                "UPDATE tags SET name = ?, normalized_name = ? WHERE id = ?",
                (name, name, tag_id),
            )
        connection.execute("DELETE FROM analysis_tag_suggestions WHERE tag_id = 5")
        connection.execute(
            "UPDATE analysis_results SET caption = ? WHERE image_id IN (1, 2)",
            ("Known full generated caption.",),
        )
    editor = CatalogEditService(database)
    assert editor.set_manual_keyword([1], "subjecttoken") == 1
    assert editor.set_manual_keyword([2], "othertoken") == 1
    return database, sources


def test_flux_preview_builder_and_written_sidecars_agree() -> None:
    """An editor-assigned trigger must survive export projection and writing."""
    with tempfile.TemporaryDirectory(prefix="lic_trigger_export_") as temporary:
        root = Path(temporary)
        database, sources = _current_catalog(root)
        original_sources = [path.read_bytes() for path in sources[:2]]
        records = DatasetExportRepository(database).fetch_records([1, 2])
        preview = _PreviewHarness(records, "Flux LoRA")
        profile = preview._current_profile()
        preview._refresh_sample()
        preview_text = str(preview.sample_var.get()).split("\n", 1)[1]
        canonical_text = build_training_text(records[0].layers, profile)
        plan = build_export_plan(
            records,
            ExportOptions(
                destination=root / "export",
                profile=profile,
                copy_images=False,
                create_sidecars=True,
                create_manifest=False,
                create_readme=False,
            ),
        )
        result = execute_export(plan)
        assert result.status == "complete"
        first_sidecar = plan.items[0].sidecar_path
        second_sidecar = plan.items[1].sidecar_path
        assert first_sidecar is not None and second_sidecar is not None
        written_text = first_sidecar.read_text(encoding="utf-8").removesuffix("\n")
        second_text = second_sidecar.read_text(encoding="utf-8").removesuffix("\n")
        print(
            f"resolved={profile.key} include_trigger={profile.include_trigger}; "
            f"preview={preview_text!r}; builder={canonical_text!r}; "
            f"sidecar={written_text!r}",
            flush=True,
        )
        assert preview_text == canonical_text == written_text == (
            "subjecttoken, manualtag, aitag1, aitag2"
        )
        assert second_text == "othertoken, manualtag, aitag1, aitag2"
        assert first_sidecar.read_bytes() == (written_text + "\n").encode("utf-8")
        assert [path.read_bytes() for path in sources[:2]] == original_sources


def test_training_text_profile_matrix() -> None:
    """Keep profile inclusion, ordering, cleanup, and deduplication exact."""
    flux = BUILTIN_TRAINING_PROFILES["flux_lora"]
    custom_caption = custom_training_profile(
        include_trigger=True,
        include_manual_tags=True,
        include_ai_tags=True,
        include_raw_caption=True,
    )
    custom_without_trigger = custom_training_profile(
        include_trigger=False,
        include_manual_tags=True,
        include_ai_tags=True,
        include_raw_caption=False,
    )
    all_layers = TrainingTextLayers(
        "subjecttoken", ("manualtag",), ("aitag1", "aitag2"),
        "Known full generated caption.",
    )
    cases = (
        ("trigger only", TrainingTextLayers("subjecttoken"), flux, "subjecttoken"),
        ("trigger and manual", TrainingTextLayers("subjecttoken", ("manualtag",)), flux,
         "subjecttoken, manualtag"),
        ("trigger and AI", TrainingTextLayers("subjecttoken", (), ("aitag1", "aitag2")), flux,
         "subjecttoken, aitag1, aitag2"),
        ("Flux all tags", all_layers, flux, "subjecttoken, manualtag, aitag1, aitag2"),
        ("custom caption", all_layers, custom_caption,
         "subjecttoken, manualtag, aitag1, aitag2; Known full generated caption."),
        ("trigger disabled", all_layers, custom_without_trigger,
         "manualtag, aitag1, aitag2"),
        ("trigger absent", TrainingTextLayers("", ("manualtag",), ("aitag1",)), flux,
         "manualtag, aitag1"),
        ("trigger whitespace", TrainingTextLayers("  subjecttoken  ", ("manualtag",)), flux,
         "subjecttoken, manualtag"),
        ("duplicate selected tag", TrainingTextLayers(
            "subjecttoken", ("SUBJECTTOKEN", "manualtag"), ("subjecttoken", "aitag1")
         ), flux, "subjecttoken, manualtag, aitag1"),
        ("SDXL", all_layers, BUILTIN_TRAINING_PROFILES["sdxl_lora"],
         "subjecttoken, manualtag"),
        ("SD 1.5", all_layers, BUILTIN_TRAINING_PROFILES["sd15_lora"],
         "subjecttoken, manualtag"),
        ("General", all_layers, BUILTIN_TRAINING_PROFILES["general_lora"],
         "subjecttoken, manualtag, aitag1, aitag2"),
        ("Caption Dataset", all_layers, BUILTIN_TRAINING_PROFILES["caption_dataset"],
         "Known full generated caption."),
    )
    for label, layers, profile, expected in cases:
        assert build_training_text(layers, profile) == expected, label


def test_current_category_precedes_legacy_keyword() -> None:
    """A current UI edit wins if an old catalog still has a legacy keyword."""
    with tempfile.TemporaryDirectory(prefix="lic_keyword_priority_") as temporary:
        root = Path(temporary)
        database, _ids, _sources = _seed_catalog(root)
        assert CatalogEditService(database).set_manual_keyword([1], "subjecttoken") == 1
        records = DatasetExportRepository(database).fetch_records([1, 3])
        assert records[0].layers.trigger_keyword == "subjecttoken"
        assert records[1].layers.trigger_keyword == "subject_token"


def test_disabled_trigger_preview_and_sidecar_agree() -> None:
    """Custom exclusion omits the assigned keyword in both output surfaces."""
    with tempfile.TemporaryDirectory(prefix="lic_trigger_disabled_") as temporary:
        root = Path(temporary)
        database, _sources = _current_catalog(root)
        record = DatasetExportRepository(database).fetch_records([1])[0]
        preview = _PreviewHarness([record], "Custom")
        preview.custom_trigger_var.set(False)
        profile = preview._current_profile()
        assert profile.include_trigger is False
        preview._refresh_sample()
        preview_text = str(preview.sample_var.get()).split("\n", 1)[1]
        canonical_text = build_training_text(record.layers, profile)
        plan = build_export_plan(
            [record],
            ExportOptions(
                destination=root / "export",
                profile=profile,
                copy_images=False,
                create_sidecars=True,
                create_manifest=False,
                create_readme=False,
            ),
        )
        assert execute_export(plan).status == "complete"
        sidecar = plan.items[0].sidecar_path
        assert sidecar is not None
        assert preview_text == canonical_text == "manualtag, aitag1, aitag2"
        assert sidecar.read_bytes() == (canonical_text + "\n").encode("utf-8")


if __name__ == "__main__":
    test_flux_preview_builder_and_written_sidecars_agree()
    test_training_text_profile_matrix()
    test_current_category_precedes_legacy_keyword()
    test_disabled_trigger_preview_and_sidecar_agree()
    print("Trigger export regression passed.")
