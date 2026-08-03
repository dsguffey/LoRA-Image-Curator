"""Dependency-light regressions for the v0.23.0 visual/workflow polish pass."""

from __future__ import annotations

import ast

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from catalog_browser import (
    BROWSER_HISTORY_LIMIT,
    CARD_BATCH_SIZE,
    CARD_PAGE_SIZE,
    SEARCH_DEBOUNCE_MS,
    BrowserHistoryEntry,
    CatalogBrowserFrame,
)
from settings_manager import AppSettings
import ui_fonts
from ui_fonts import MONOSPACE_FONT_FAMILY, UI_FONT_FAMILY, get_ui_font
from ui_theme import DEFAULT_THEME_KEY, THEMES, get_theme, normalize_theme_key


def test_safe_page_defaults() -> None:
    settings = AppSettings()
    assert settings.browser_images_per_page == 100
    assert settings.appearance_theme == DEFAULT_THEME_KEY
    assert CARD_BATCH_SIZE == 100
    assert CARD_PAGE_SIZE == 100
    assert SEARCH_DEBOUNCE_MS == 500


def test_curated_theme_registry_has_safe_fallback() -> None:
    assert normalize_theme_key("missing-theme") == DEFAULT_THEME_KEY
    assert get_theme("missing-theme").key == DEFAULT_THEME_KEY
    assert {"clean_gray", "soft_light", "dark_workstation", "high_contrast"}.issubset(
        THEMES
    )
    for theme in THEMES.values():
        assert theme.label
        assert theme.window_background.startswith("#")
        assert theme.card_background.startswith("#")


def test_shared_history_entry_describes_both_action_kinds() -> None:
    selection = BrowserHistoryEntry(
        kind="selection",
        description="Select current page",
        before=frozenset(),
        after=frozenset({1, 2}),
    )
    catalog = BrowserHistoryEntry(
        kind="catalog",
        description="Add manual tag",
        operation_id=7,
    )

    assert selection.after == frozenset({1, 2})
    assert selection.operation_id is None
    assert catalog.operation_id == 7
    assert BROWSER_HISTORY_LIMIT >= 30


def test_font_registry_uses_structured_font_arguments_and_caches_objects() -> None:
    """Protect the Python 3.14/Tk multiword-family startup fix."""

    class FakeRoot:
        pass

    class FakeWidget:
        def __init__(self, root: FakeRoot) -> None:
            self.root = root

        def _root(self) -> FakeRoot:
            return self.root

    root = FakeRoot()
    widget = FakeWidget(root)
    created_fonts: list[object] = []

    def create_font(**_kwargs):
        font = object()
        created_fonts.append(font)
        return font

    with patch.object(ui_fonts.tkfont, "Font", side_effect=create_font) as constructor:
        regular = get_ui_font(widget, size=9)
        assert get_ui_font(widget, size=9) is regular
        monospace = get_ui_font(widget, size=10, family=MONOSPACE_FONT_FAMILY)

    assert regular is created_fonts[0]
    assert monospace is created_fonts[1]
    assert constructor.call_count == 2
    regular_arguments = constructor.call_args_list[0].kwargs
    assert regular_arguments["root"] is root
    assert regular_arguments["family"] == UI_FONT_FAMILY
    assert regular_arguments["size"] == 9
    assert regular_arguments["weight"] == "normal"


def test_font_registry_accepts_controller_owned_outer_widget() -> None:
    """Keep mixed v0.23.0 files from crashing on ThumbnailCard fonts.

    The corrected browser passes ``ThumbnailCard.outer`` directly, but the
    font boundary also accepts the controller itself.  This protects users who
    accidentally merge an older ``catalog_browser.py`` into the repaired
    package instead of replacing the application folder atomically.
    """

    class FakeRoot:
        pass

    class FakeWidget:
        def __init__(self, root: FakeRoot) -> None:
            self.root = root

        def _root(self) -> FakeRoot:
            return self.root

    class FakeThumbnailCard:
        def __init__(self, outer: FakeWidget) -> None:
            self.outer = outer

    root = FakeRoot()
    controller = FakeThumbnailCard(FakeWidget(root))

    with patch.object(ui_fonts.tkfont, "Font", return_value=object()) as constructor:
        get_ui_font(controller, size=7, weight="bold")

    arguments = constructor.call_args.kwargs
    assert arguments["root"] is root
    assert arguments["family"] == UI_FONT_FAMILY
    assert arguments["size"] == 7
    assert arguments["weight"] == "bold"


def test_gui_code_has_no_literal_font_descriptions() -> None:
    """Forbid the tuple/string forms that failed on the user's Tk build."""
    source_directory = Path(__file__).resolve().parents[1]
    unsafe_arguments: list[str] = []

    for path in source_directory.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "font" and isinstance(
                    keyword.value,
                    (ast.Constant, ast.List, ast.Tuple),
                ):
                    unsafe_arguments.append(f"{path.name}:{node.lineno}")

    assert not unsafe_arguments, (
        "GUI font options must use get_ui_font(), not Tcl-parsed literals: "
        + ", ".join(unsafe_arguments)
    )

    theme_source = (source_directory / "ui_theme.py").read_text(encoding="utf-8")
    assert 'option_add("*Font", get_ui_font(' in theme_source


def test_thumbnail_card_resolves_fonts_from_a_real_tk_widget() -> None:
    """Prevent plain controller objects from being passed to get_ui_font()."""
    source_path = Path(__file__).resolve().parents[1] / "catalog_browser.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    thumbnail_card = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ThumbnailCard"
    )
    invalid_calls: list[int] = []

    for node in ast.walk(thumbnail_card):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_ui_font"
            and node.args
        ):
            continue
        first_argument = node.args[0]
        if isinstance(first_argument, ast.Name) and first_argument.id == "self":
            invalid_calls.append(node.lineno)

    assert not invalid_calls, (
        "ThumbnailCard is a controller rather than a Tk widget; resolve fonts "
        "through self.outer or another owned widget at lines "
        + ", ".join(str(line) for line in invalid_calls)
    )


def test_theme_repaint_tolerates_destroyed_empty_state_label() -> None:
    """Keep catalog loading followed by a theme change from touching dead Tk."""

    class DestroyedLabel:
        def winfo_exists(self) -> bool:
            return False

        def configure(self, **_kwargs) -> None:
            raise AssertionError("A destroyed empty-state label must not be configured")

    browser = SimpleNamespace(
        empty_label=DestroyedLabel(),
        colors={
            "browser_background": "#101010",
            "muted_text": "#CCCCCC",
        },
    )

    CatalogBrowserFrame._style_empty_label(browser)
    assert browser.empty_label is None


def run() -> None:
    test_safe_page_defaults()
    test_curated_theme_registry_has_safe_fallback()
    test_shared_history_entry_describes_both_action_kinds()
    test_font_registry_uses_structured_font_arguments_and_caches_objects()
    test_font_registry_accepts_controller_owned_outer_widget()
    test_gui_code_has_no_literal_font_descriptions()
    test_thumbnail_card_resolves_fonts_from_a_real_tk_widget()
    test_theme_repaint_tolerates_destroyed_empty_state_label()
    print(
        "Milestone 10 Phase 1C tests passed: browser pages default to the "
        "safe 100-image maximum, search debounces at 500 ms, curated themes "
        "have a safe fallback, selection/catalog actions share one "
        "chronological history representation, and GUI fonts use cached "
        "tkinter.font.Font objects resolved through real Tk widgets; theme "
        "repaints ignore destroyed empty-state labels."
    )


if __name__ == "__main__":
    run()
