"""Dependency-light regression for the v0.25.x pre-1.0 readiness releases.

The test protects the rename's compatibility contract, InsightFace Browse
translation, first-priority Windows Alt-navigation chord, and public packaging
identity without creating a Tk window or loading an inference model.
"""

from __future__ import annotations

import inspect
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from app_identity import (
    APP_DATA_DIRECTORY_NAME,
    APP_NAME,
    APP_VERSION,
    CATALOG_APPLICATION_ID,
    LEGACY_CATALOG_APPLICATION_ID,
)
from catalog import Catalog
from catalog_browser import ALT_NAVIGATION_BINDTAG, CatalogBrowserFrame
from catalog_lifecycle import validate_catalog_database
from face_analyzer import (
    get_model_path,
    model_selection_from_pack_folder,
    normalize_model_name,
)


class _AltChordHarness:
    """Controller-shaped harness for the user's exact Windows key sequence."""

    def __init__(self) -> None:
        self.card_page_index = 0
        self.images_per_page = 25
        self.visible_records = [object()] * 125
        self.duplicate_review_clusters: tuple[tuple[int, ...], ...] = ()
        self._last_page_shortcut_at = 0.0
        self._capture_alt_modifier_for_page_navigation = True
        self._alt_modifier_held = False
        self._alt_page_navigation_active = False
        self._alt_navigation_keys_down: set[str] = set()

    @staticmethod
    def winfo_ismapped() -> bool:
        return True

    def _append_next_card_batch(self) -> None:
        self.card_page_index += 1

    def _show_previous_card_page(self) -> None:
        self.card_page_index -= 1

    _page_shortcut_is_repeat = CatalogBrowserFrame._page_shortcut_is_repeat


class _BindtagHarness:
    """Minimal widget contract used to verify first-priority tag insertion."""

    def __init__(self) -> None:
        self.tags = ("widget", "class", ".", "all")

    def bindtags(self, new_tags: tuple[str, ...] | None = None) -> tuple[str, ...]:
        if new_tags is not None:
            self.tags = tuple(new_tags)
        return self.tags


def test_public_identity() -> None:
    """Visible metadata is branded while compatibility names stay explicit."""
    assert APP_NAME == "LoRA Image Curator"
    version_text = Path(__file__).with_name("VERSION.txt").read_text(
        encoding="utf-8"
    )
    project_metadata = Path(__file__).with_name("pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert f"Version {APP_VERSION}" in version_text
    assert f'version = "{APP_VERSION}"' in project_metadata
    assert APP_DATA_DIRECTORY_NAME == "LoRAImageCurator"
    assert CATALOG_APPLICATION_ID == APP_NAME
    assert LEGACY_CATALOG_APPLICATION_ID == "Dataset Tools"

    source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert 'APPLICATION_TITLE = f"{APP_NAME} — LoRA Dataset Workspace"' in source
    assert "Created by {AUTHOR_NAME}" in source
    assert "command=self._choose_face_model_pack" in source
    assert 'help_attribute="face_model_pack_help"' in source

    readme = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
    assert "https://www.linkedin.com/in/davidsguffey/" in readme
    assert "David Scott Guffey" in readme


def test_current_and_legacy_catalog_identity() -> None:
    """New catalogs use the new marker and old catalogs remain openable."""
    with tempfile.TemporaryDirectory(
        prefix="lora_image_curator_catalog_identity_"
    ) as temp:
        database = Path(temp) / "catalog.db"
        with Catalog(database):
            pass

        with closing(sqlite3.connect(database)) as connection, connection:
            marker = connection.execute(
                "SELECT value FROM catalog_metadata WHERE key = 'application'"
            ).fetchone()
            assert marker == (CATALOG_APPLICATION_ID,)
        assert validate_catalog_database(database) == database.resolve()

        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE catalog_metadata SET value = ? WHERE key = 'application'",
                (LEGACY_CATALOG_APPLICATION_ID,),
            )
        assert validate_catalog_database(database) == database.resolve()


def test_insightface_model_browse_contract() -> None:
    """A chosen pack becomes the exact safe root/name pair InsightFace loads."""
    with tempfile.TemporaryDirectory(
        prefix="lora_image_curator_model_"
    ) as temp:
        root = Path(temp) / "insightface-home"
        pack = root / "models" / "compatible_pack"
        pack.mkdir(parents=True)
        (pack / "recognition.onnx").write_bytes(b"test fixture")

        model_name, model_root = model_selection_from_pack_folder(pack)
        assert model_name == "compatible_pack"
        assert model_root == root.resolve()
        assert get_model_path(model_name, model_root) == pack.resolve()

        invalid_layout = Path(temp) / "not-models" / "pack"
        invalid_layout.mkdir(parents=True)
        (invalid_layout / "recognition.onnx").write_bytes(b"test fixture")
        try:
            model_selection_from_pack_folder(invalid_layout)
        except ValueError as error:
            assert "inside an InsightFace 'models' folder" in str(error)
        else:
            raise AssertionError("Expected invalid pack-layout rejection.")

    for invalid_name in ("../escape", "models/pack", r"models\\pack", "."):
        try:
            normalize_model_name(invalid_name)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe model name was accepted: {invalid_name!r}")


def test_complete_alt_navigation_chord() -> None:
    """The browser owns held Alt before Windows menu traversal can claim it."""
    binding_source = inspect.getsource(CatalogBrowserFrame._bind_shortcuts)
    assert "bind_class(" in binding_source
    assert "ALT_NAVIGATION_BINDTAG" in binding_source
    assert '"<KeyPress-Alt_L>"' in binding_source
    assert '"<KeyPress-Alt_R>"' in binding_source
    assert '"<Alt-KeyRelease-Left>"' in binding_source
    assert '"<Alt-KeyRelease-Right>"' in binding_source
    assert '"<KeyRelease-Alt_L>"' in binding_source
    assert '"<KeyRelease-Alt_R>"' in binding_source

    widget = _BindtagHarness()
    CatalogBrowserFrame._prepend_alt_navigation_bindtag(widget)
    CatalogBrowserFrame._prepend_alt_navigation_bindtag(widget)
    assert widget.tags[0] == ALT_NAVIGATION_BINDTAG
    assert widget.tags.count(ALT_NAVIGATION_BINDTAG) == 1

    harness = _AltChordHarness()
    assert CatalogBrowserFrame._alt_page_modifier_pressed(harness, None) == "break"
    assert harness._alt_modifier_held is True

    for expected_page in (1, 2, 3):
        right_press = SimpleNamespace(keysym="Right")
        result = CatalogBrowserFrame._next_page_shortcut(harness, right_press)
        assert result == "break"
        assert harness.card_page_index == expected_page

        # Windows auto-repeat produces additional KeyPress events without an
        # intervening release. They remain consumed but cannot move again.
        assert (
            CatalogBrowserFrame._next_page_shortcut(harness, right_press)
            == "break"
        )
        assert harness.card_page_index == expected_page
        assert (
            CatalogBrowserFrame._alt_navigation_arrow_released(
                harness,
                right_press,
            )
            == "break"
        )

    assert harness._alt_page_navigation_active is True
    assert (
        CatalogBrowserFrame._alt_page_modifier_released(harness, None)
        == "break"
    )
    assert harness.card_page_index == 3
    assert harness._alt_modifier_held is False
    assert harness._alt_page_navigation_active is False
    assert harness._alt_navigation_keys_down == set()
    assert CatalogBrowserFrame._alt_page_modifier_released(harness, None) is None


def run() -> None:
    test_public_identity()
    test_current_and_legacy_catalog_identity()
    test_insightface_model_browse_contract()
    test_complete_alt_navigation_chord()
    print(
        "v0.25.x regression passed: public identity, catalog compatibility, "
        "validated InsightFace Browse, and first-priority Alt navigation chord."
    )


if __name__ == "__main__":
    run()
