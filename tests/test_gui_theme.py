# SPDX-License-Identifier: MIT
from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from winpodx.gui import theme  # noqa: E402


def _focus_rule_body(qss: str, selector: str) -> str | None:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>[^{{}}]*)\}}", qss)
    return None if match is None else match.group("body")


def _assert_focus_rule(qss: str, selector: str, ring: str) -> None:
    body = _focus_rule_body(qss, selector)

    assert body is not None
    assert f"border: {ring};" in body
    assert "background" not in body
    assert "color" not in body


@pytest.mark.parametrize(
    ("constant_name", "selector"),
    [
        ("CHECKBOX", "QCheckBox:focus"),
        ("NAV_ITEM", "QPushButton#navItem:focus"),
        ("APP_TILE", "QFrame#appTile:focus"),
    ],
)
def test_shared_focus_ring_is_present_for_named_controls(constant_name: str, selector: str) -> None:
    qss = getattr(theme, constant_name)

    _assert_focus_rule(qss, selector, theme.FOCUS_RING)


def test_app_tile_focus_ring_matches_both_shared_tile_object_names() -> None:
    for selector in ("QFrame#appTile:focus", "QFrame#appTileBtn:focus"):
        _assert_focus_rule(theme.APP_TILE, selector, theme.FOCUS_RING)


def test_combo_uses_bundled_chevron_icon() -> None:
    assert "QComboBox::down-arrow" in theme.COMBO
    assert "chevron-down" in theme.COMBO
    assert "image: none" not in theme.COMBO


def test_combo_chevron_follows_color_scheme() -> None:
    from pathlib import Path

    from winpodx.gui import _theme_qss

    icons = Path(_theme_qss.__file__).with_name("icons")
    light = icons / "chevron-down-light.svg"
    dark = icons / "chevron-down-dark.svg"
    theme.rebuild("dark")
    assert "chevron-down-dark.svg" in theme.COMBO
    assert dark.is_file()
    theme.rebuild("light")
    assert "chevron-down-light.svg" in theme.COMBO
    assert light.is_file()
    assert light.read_text(encoding="utf-8") != dark.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("constant_name", "ring_name"),
    [
        ("BTN_PRIMARY", "FOCUS_RING_ON_ACCENT"),
        ("BTN_SECONDARY", "FOCUS_RING"),
        ("BTN_ACCENT", "FOCUS_RING_ON_ACCENT"),
        ("BTN_DANGER", "FOCUS_RING"),
        ("BTN_GHOST", "FOCUS_RING"),
    ],
)
def test_all_button_styles_use_the_contextual_focus_ring(
    constant_name: str, ring_name: str
) -> None:
    qss = getattr(theme, constant_name)

    _assert_focus_rule(qss, "QPushButton:focus", getattr(theme, ring_name))


@pytest.mark.parametrize(
    ("constant_name", "selector"),
    [
        ("NAV_ITEM", "QPushButton#navItem:checked:focus"),
        ("BTN_GHOST", "QPushButton:checked:focus"),
    ],
)
def test_selected_controls_use_a_distinct_focus_ring(constant_name: str, selector: str) -> None:
    qss = getattr(theme, constant_name)

    _assert_focus_rule(qss, selector, theme.FOCUS_RING_ON_SELECTION)


def test_contextual_focus_rings_use_existing_palette_tokens() -> None:
    assert theme.FOCUS_RING == f"2px solid {theme.C.TEXT}"
    assert theme.FOCUS_RING_ON_ACCENT == f"2px solid {theme.C.CRUST}"
    assert theme.FOCUS_RING_ON_SELECTION == f"2px solid {theme.C.TEXT}"


LEGACY_EXPORTS = (
    "C",
    "rgba",
    "SPACE_XS",
    "SPACE_S",
    "SPACE_M",
    "SPACE_L",
    "SPACE_XL",
    "SPACE_XXL",
    "SPACE_XXXL",
    "RADIUS_XS",
    "RADIUS_S",
    "RADIUS_M",
    "RADIUS_L",
    "RADIUS_XL",
    "RADIUS_XXL",
    "FONT_CAPTION",
    "FONT_BODY",
    "FONT_SUBHEAD",
    "FONT_HEADER",
    "FONT_TITLE",
    "FONT_HERO",
    "FONT_DISPLAY",
    "CONTROL_HEIGHT",
    "CONTROL_HEIGHT_L",
    "CARD_BORDER",
    "CARD_BORDER_HOVER",
    "FOCUS_RING",
    "FOCUS_RING_ON_ACCENT",
    "FOCUS_RING_ON_SELECTION",
    "ACCENT_GREEN",
    "ACCENT_GREEN_HOVER",
    "ACCENT_GREEN_PRESSED",
    "TOOL_ACCENT",
    "TOOL_ICON_BG",
    "TOOL_ICON_BORDER",
    "TOOL_ICON_FG",
    "avatar_color",
    "accent_color",
    "GLOBAL_STYLE",
    "POD_CHIP",
    "POD_CTRL",
    "STATUS_BANNER_WARN",
    "INPUT",
    "COMBO",
    "SEARCH_BAR",
    "BTN_PRIMARY",
    "BTN_SECONDARY",
    "BTN_ACCENT",
    "BTN_DANGER",
    "BTN_GHOST",
    "FILTER_CHIP",
    "VIEW_TOGGLE",
    "APP_TILE",
    "ACTION_ROW",
    "SETTINGS_SECTION",
    "SECTION_CARD",
    "EMPTY_STATE",
    "SECTION_LABEL",
    "PAGE_TITLE",
    "PAGE_SUBTITLE",
    "BADGE",
    "CHECKBOX",
    "RADIO",
    "LIST_WIDGET",
    "SCROLL_AREA",
    "TERMINAL",
    "PLAIN_TEXT",
    "DIALOG",
    "INFO_BAR",
    "SIDEBAR",
    "NAV_ITEM",
    "TOP_STRIP",
)

NEW_EXPORTS = (
    "SCHEME_LIGHT",
    "SCHEME_DARK",
    "current_scheme",
    "rebuild",
    "palette_for",
    "apply_to_app",
    "NAV_PANE_WIDTH",
    "NAV_PANE_COMPACT",
    "NAV_ITEM_HEIGHT",
    "NAV_INDICATOR",
    "SETTINGS_ROW_MIN",
    "CONTROL_HEIGHT_W11",
    "TOGGLE_W",
    "TOGGLE_H",
    "FONT_FAMILY",
    "NAV_PANE",
    "NAV_SEARCH",
    "SETTINGS_CARD",
    "SETTINGS_CARD_HOVER",
    "TOGGLE_SWITCH",
    "HYPERLINK_BTN",
    "START_TILE",
    "PAGE_HEADER",
    "BREADCRUMB",
    "TAB_BAR",
    "SLIDER",
    "SPIN_BOX",
    "PROGRESS",
)


@pytest.fixture(autouse=True)
def _restore_theme_scheme() -> None:
    previous = theme.current_scheme() if hasattr(theme, "current_scheme") else None
    yield
    if previous is not None and hasattr(theme, "rebuild"):
        theme.rebuild(previous)


def test_legacy_export_names_still_exist() -> None:
    missing = [name for name in LEGACY_EXPORTS if not hasattr(theme, name)]

    assert missing == []


def test_new_fluent_api_names_exist() -> None:
    missing = [name for name in NEW_EXPORTS if not hasattr(theme, name)]

    assert missing == []


def test_scheme_constants_are_light_and_dark() -> None:
    assert theme.SCHEME_LIGHT == "light"
    assert theme.SCHEME_DARK == "dark"


def test_rebuild_light_sets_fluent_tokens() -> None:
    theme.rebuild(theme.SCHEME_LIGHT)

    assert theme.current_scheme() == "light"
    assert theme.C.BASE == "#F3F3F3"
    assert theme.C.TEXT == "#1B1B1B"
    assert theme.C.SURFACE0 == "#FFFFFF"
    assert theme.C.MANTLE == "#F9F9F9"
    assert theme.C.BLUE == "#0067C0"
    assert theme.C.CRUST == "#FFFFFF"
    assert theme.C.GREEN == "#0F7B0F"
    assert theme.C.YELLOW == "#9D5D00"
    assert theme.C.RED == "#C42B1C"
    assert "#0067C0" in theme.BTN_PRIMARY
    assert "* { background: transparent; }" in " ".join(theme.GLOBAL_STYLE.split())


def test_rebuild_dark_sets_fluent_tokens() -> None:
    theme.rebuild(theme.SCHEME_DARK)

    assert theme.current_scheme() == "dark"
    assert theme.C.BASE == "#1F1F1F"
    assert theme.C.TEXT == "#FFFFFF"
    assert theme.C.SURFACE0 == "#2B2B2B"
    assert theme.C.MANTLE == "#191919"
    assert theme.C.BLUE == "#60CDFF"
    assert theme.C.CRUST == "#000000"
    assert theme.C.GREEN == "#6CCB5F"
    assert "#60CDFF" in theme.BTN_PRIMARY


def test_current_scheme_round_trips_light_and_dark() -> None:
    theme.rebuild("light")
    assert theme.current_scheme() == "light"
    theme.rebuild("dark")
    assert theme.current_scheme() == "dark"


def test_rebuild_unknown_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError):
        theme.rebuild("solarized")


def test_rebuild_updates_module_attribute_but_not_previously_bound_string() -> None:
    theme.rebuild(theme.SCHEME_DARK)
    bound = theme.BTN_PRIMARY

    theme.rebuild(theme.SCHEME_LIGHT)

    assert theme.BTN_PRIMARY != bound
    assert "#0067C0" in theme.BTN_PRIMARY
    assert "#60CDFF" in bound


def test_fluent_geometry_tokens() -> None:
    assert theme.RADIUS_XS == 2
    assert theme.RADIUS_S == 4
    assert theme.RADIUS_M == 4
    assert theme.RADIUS_L == 8
    assert theme.RADIUS_XL == 8
    assert theme.RADIUS_XXL == 8
    assert theme.FONT_CAPTION == 12
    assert theme.FONT_BODY == 14
    assert theme.FONT_SUBHEAD == 14
    assert theme.FONT_HEADER == 16
    assert theme.FONT_TITLE == 20
    assert theme.FONT_HERO == 28
    assert theme.FONT_DISPLAY == 28
    assert theme.CONTROL_HEIGHT == 32
    assert theme.CONTROL_HEIGHT_L == 36
    assert theme.SPACE_XS == 4
    assert theme.SPACE_S == 8
    assert theme.SPACE_M == 12
    assert theme.SPACE_L == 16
    assert theme.SPACE_XL == 24
    assert theme.SPACE_XXL == 32
    assert theme.SPACE_XXXL == 48
    assert theme.NAV_PANE_WIDTH == 320
    assert theme.NAV_PANE_COMPACT == 48
    assert theme.NAV_ITEM_HEIGHT == 36
    assert theme.NAV_INDICATOR == 3
    assert theme.SETTINGS_ROW_MIN == 68
    assert theme.CONTROL_HEIGHT_W11 == 32
    assert theme.TOGGLE_W == 40
    assert theme.TOGGLE_H == 20


def test_global_style_keeps_transparent_wildcard_and_inherits_the_desktop_font() -> None:
    assert "* { background: transparent; }" in " ".join(theme.GLOBAL_STYLE.split())
    assert "font-family:" not in theme.GLOBAL_STYLE


def test_nav_item_is_transparent_row_with_selected_primary_text() -> None:
    theme.rebuild(theme.SCHEME_LIGHT)
    qss = theme.NAV_ITEM

    assert "min-height: 36px" in qss
    assert "QPushButton#navItem:checked" in qss
    assert theme.C.TEXT in qss


def test_scroll_area_uses_thin_thumb() -> None:
    assert "width: 10px" in theme.SCROLL_AREA
    assert "QScrollBar::handle:vertical:hover" in theme.SCROLL_AREA


def test_terminal_uses_layer_alt_and_primary_text() -> None:
    theme.rebuild(theme.SCHEME_DARK)

    assert theme.C.MANTLE in theme.TERMINAL
    assert theme.C.TEXT in theme.TERMINAL
    assert "monospace" in theme.TERMINAL
    assert theme.C.MANTLE in theme.PLAIN_TEXT
    assert theme.C.TEXT in theme.PLAIN_TEXT


def test_palette_for_light_window_is_app_background() -> None:
    from PySide6.QtGui import QPalette

    palette = theme.palette_for("light")

    assert palette.color(QPalette.ColorRole.Window).name().upper() == "#F3F3F3"


def test_palette_for_dark_window_is_app_background() -> None:
    from PySide6.QtGui import QPalette

    palette = theme.palette_for("dark")

    assert palette.color(QPalette.ColorRole.Window).name().upper() == "#1F1F1F"


def test_palette_for_unknown_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError):
        theme.palette_for("high-contrast")


@pytest.mark.parametrize("scheme", [theme.SCHEME_LIGHT, theme.SCHEME_DARK])
def test_inner_control_tokens_exist_for_both_schemes(scheme: str) -> None:
    theme.rebuild(scheme)

    for name in ("CHECKBOX", "RADIO", "LIST_WIDGET", "TAB_BAR", "SLIDER", "SPIN_BOX", "PROGRESS"):
        qss = getattr(theme, name)
        assert isinstance(qss, str)
        assert qss.strip()


@pytest.mark.parametrize("scheme", [theme.SCHEME_LIGHT, theme.SCHEME_DARK])
def test_global_style_menu_selected_uses_subtle_hover_not_accent(scheme: str) -> None:
    theme.rebuild(scheme)
    body = _focus_rule_body(theme.GLOBAL_STYLE, "QMenu::item:selected")

    assert body is not None
    assert f"background: {theme._PALETTE.subtle_hover}" in body
    assert theme.C.BLUE not in body


def test_nav_item_checked_is_not_semibold() -> None:
    assert "font-weight: 600" not in theme.NAV_ITEM


def test_checkbox_references_checkmark_on_accent_svg() -> None:
    assert "checkmark-on-accent.svg" in theme.CHECKBOX


def test_inner_control_svg_assets_exist() -> None:
    from pathlib import Path

    icons = Path(theme.__file__).resolve().parent / "icons"
    for name in (
        "checkmark-on-accent.svg",
        "radio-dot.svg",
        "chevron-up-light.svg",
        "chevron-up-dark.svg",
    ):
        assert (icons / name).is_file(), name
