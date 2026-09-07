# SPDX-License-Identifier: MIT
"""Windows 11 Fluent design tokens and QSS.

``rebuild(scheme)`` mutates ``C`` class attributes and every module-level QSS /
border string in place via ``globals().update(...)``. Names observed through
``theme.X`` (attribute access) reflect the new scheme. Callers that bound a
value at import (``from winpodx.gui.theme import BTN_PRIMARY``) keep the old
string until they re-read ``theme.BTN_PRIMARY``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from PySide6.QtGui import QFont, QPalette
    from PySide6.QtWidgets import QApplication

Scheme = Literal["light", "dark"]

SCHEME_LIGHT: Final = "light"
SCHEME_DARK: Final = "dark"

FONT_FAMILY: Final = (
    "'Segoe UI Variable', 'Segoe UI', 'Noto Sans', 'Inter', 'DejaVu Sans', sans-serif"
)

NAV_PANE_WIDTH: Final = 320
NAV_PANE_COMPACT: Final = 48
TITLE_BAR_H: Final = 32
CAPTION_BTN_W: Final = 46
RESIZE_MARGIN: Final = 6
MIN_SHRINK_RATIO: Final = 0.6
WRAP_RATIO: Final = 0.92
EMPTY_PANEL_RATIO: Final = 0.4
NAV_ITEM_HEIGHT: Final = 36
PAGE_MARGIN_TOP: Final = 28
PAGE_MARGIN_X: Final = 36
CONTENT_MAX_WIDTH: Final = 1000
SCROLL_GUTTER: Final = 10
NAV_INDICATOR: Final = 3
SETTINGS_ROW_MIN: Final = 68
CONTROL_HEIGHT_W11: Final = 32
TOGGLE_W: Final = 40
TOGGLE_H: Final = 20

SPACE_XS: Final = 4
SPACE_S: Final = 8
SPACE_M: Final = 12
SPACE_L: Final = 16
SPACE_XL: Final = 24
SPACE_XXL: Final = 32
SPACE_XXXL: Final = 48

RADIUS_XS: Final = 2
RADIUS_S: Final = 4
RADIUS_M: Final = 4
RADIUS_L: Final = 8
RADIUS_XL: Final = 8
RADIUS_XXL: Final = 8

FONT_CAPTION: Final = 12
FONT_BODY: Final = 14
FONT_SUBHEAD: Final = 14
FONT_HEADER: Final = 16
FONT_TITLE: Final = 20
FONT_HERO: Final = 28
FONT_DISPLAY: Final = 28

CONTROL_HEIGHT: Final = 32
CONTROL_HEIGHT_L: Final = 36
# Minimum pointer/touch hit target for primary interactive controls (WCAG 2.5.5).
HIT_TARGET: Final = 44


@dataclass(frozen=True)
class UnknownColorSchemeError(ValueError):
    """Raised when ``rebuild`` / ``palette_for`` receive an unknown scheme."""

    __slots__ = ("scheme",)
    scheme: str

    def __str__(self) -> str:
        return f"unknown colour scheme: {self.scheme!r}"


@dataclass(frozen=True)
class _FluentPalette:
    """One Fluent (WinUI 3) colour set, plus the legacy ``C.*`` mapping."""

    rosewater: str
    flamingo: str
    pink: str
    mauve: str
    red: str
    maroon: str
    peach: str
    yellow: str
    green: str
    teal: str
    sky: str
    sapphire: str
    blue: str
    lavender: str
    text: str
    subtext1: str
    subtext0: str
    overlay2: str
    overlay1: str
    overlay0: str
    surface2: str
    surface1: str
    surface0: str
    base: str
    mantle: str
    crust: str
    card_stroke: str
    divider: str
    subtle_hover: str
    control_fill: str
    control_hover: str
    control_pressed: str
    control_input_active: str
    control_disabled: str
    text_disabled: str
    nav_pane: str
    nav_hover: str
    nav_selected: str
    success_hover: str
    success_pressed: str
    tool_accent: str
    tool_icon_fg: str


_LIGHT = _FluentPalette(
    rosewater="#E3008C",
    flamingo="#E3008C",
    pink="#C239B3",
    mauve="#744DA9",
    red="#C42B1C",
    maroon="#C42B1C",
    peach="#9D5D00",
    yellow="#9D5D00",
    green="#0F7B0F",
    teal="#0F7B0F",
    sky="#1975C5",
    sapphire="#3183CA",
    blue="#0067C0",
    lavender="#1975C5",
    text="#1B1B1B",
    subtext1="#5D5D5D",
    subtext0="#8A8A8A",
    overlay2="#8A8A8A",
    overlay1="#A0A0A0",
    overlay0="#767676",
    surface2="#D9D9D9",
    surface1="#F9F9F9",
    surface0="#FFFFFF",
    base="#F3F3F3",
    mantle="#F9F9F9",
    crust="#FFFFFF",
    card_stroke="rgba(0, 0, 0, 0.0578)",
    divider="rgba(0, 0, 0, 0.0803)",
    subtle_hover="rgba(0, 0, 0, 0.0373)",
    control_fill="#FDFDFD",
    control_hover="#F9F9F9",
    control_pressed="#F3F3F3",
    control_input_active="#FFFFFF",
    control_disabled="rgba(249, 249, 249, 0.30)",
    text_disabled="rgba(0, 0, 0, 0.36)",
    nav_pane="#EBEBEB",
    nav_hover="rgba(0, 0, 0, 0.05)",
    nav_selected="rgba(0, 0, 0, 0.06)",
    success_hover="#198C19",
    success_pressed="#0C640C",
    tool_accent="#0067C0",
    tool_icon_fg="#5D5D5D",
)

_DARK = _FluentPalette(
    rosewater="#FF8C9E",
    flamingo="#FF8C9E",
    pink="#F472D0",
    mauve="#B4A0FF",
    red="#FF99A4",
    maroon="#FF99A4",
    peach="#FCE100",
    yellow="#FCE100",
    green="#6CCB5F",
    teal="#6CCB5F",
    sky="#7BD4FF",
    sapphire="#4FB8E8",
    blue="#60CDFF",
    lavender="#7BD4FF",
    text="#FFFFFF",
    subtext1="#C5C5C5",
    subtext0="#8B8B8B",
    overlay2="#8B8B8B",
    overlay1="#6D6D6D",
    overlay0="#7A7A7A",
    surface2="#3D3D3D",
    surface1="#333333",
    surface0="#2B2B2B",
    base="#1F1F1F",
    mantle="#191919",
    crust="#000000",
    card_stroke="rgba(255, 255, 255, 0.055)",
    divider="rgba(255, 255, 255, 0.08)",
    subtle_hover="rgba(255, 255, 255, 0.0605)",
    control_fill="#333333",
    control_hover="#3A3A3A",
    control_pressed="#272727",
    control_input_active="#1F1F1F",
    control_disabled="rgba(255, 255, 255, 0.04)",
    text_disabled="rgba(255, 255, 255, 0.36)",
    nav_pane="#1A1A1A",
    nav_hover="rgba(255, 255, 255, 0.055)",
    nav_selected="rgba(255, 255, 255, 0.075)",
    success_hover="#7ED66F",
    success_pressed="#5BB84F",
    tool_accent="#8AA4BE",
    tool_icon_fg="#C5C5C5",
)

_PALETTES: Final[dict[Scheme, _FluentPalette]] = {
    SCHEME_LIGHT: _LIGHT,
    SCHEME_DARK: _DARK,
}


class C:
    """Fluent palette mapped onto the legacy Catppuccin-style names.

    Attributes are mutated in place by ``rebuild`` so ``theme.C.BASE`` (and
    any ``C`` alias bound at import) always reflects the active scheme.
    """

    ROSEWATER = _DARK.rosewater
    FLAMINGO = _DARK.flamingo
    PINK = _DARK.pink
    MAUVE = _DARK.mauve
    RED = _DARK.red
    MAROON = _DARK.maroon
    PEACH = _DARK.peach
    YELLOW = _DARK.yellow
    GREEN = _DARK.green
    TEAL = _DARK.teal
    SKY = _DARK.sky
    SAPPHIRE = _DARK.sapphire
    BLUE = _DARK.blue
    LAVENDER = _DARK.lavender
    TEXT = _DARK.text
    SUBTEXT1 = _DARK.subtext1
    SUBTEXT0 = _DARK.subtext0
    OVERLAY2 = _DARK.overlay2
    OVERLAY1 = _DARK.overlay1
    OVERLAY0 = _DARK.overlay0
    SURFACE2 = _DARK.surface2
    SURFACE1 = _DARK.surface1
    SURFACE0 = _DARK.surface0
    BASE = _DARK.base
    MANTLE = _DARK.mantle
    CRUST = _DARK.crust


_SCHEME: Scheme = SCHEME_DARK
_PALETTE: _FluentPalette = _DARK


def nav_pane_color() -> str:
    return _PALETTE.nav_pane


def rgba(hex_color: str, alpha: float) -> str:
    """Return a Qt stylesheet rgba() color from ``#rrggbb`` and 0..1 alpha."""
    value = hex_color.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _parse_scheme(scheme: str) -> Scheme:
    if scheme == SCHEME_LIGHT or scheme == SCHEME_DARK:
        return scheme
    raise UnknownColorSchemeError(scheme)


def _apply_palette(palette: _FluentPalette) -> None:
    C.ROSEWATER = palette.rosewater
    C.FLAMINGO = palette.flamingo
    C.PINK = palette.pink
    C.MAUVE = palette.mauve
    C.RED = palette.red
    C.MAROON = palette.maroon
    C.PEACH = palette.peach
    C.YELLOW = palette.yellow
    C.GREEN = palette.green
    C.TEAL = palette.teal
    C.SKY = palette.sky
    C.SAPPHIRE = palette.sapphire
    C.BLUE = palette.blue
    C.LAVENDER = palette.lavender
    C.TEXT = palette.text
    C.SUBTEXT1 = palette.subtext1
    C.SUBTEXT0 = palette.subtext0
    C.OVERLAY2 = palette.overlay2
    C.OVERLAY1 = palette.overlay1
    C.OVERLAY0 = palette.overlay0
    C.SURFACE2 = palette.surface2
    C.SURFACE1 = palette.surface1
    C.SURFACE0 = palette.surface0
    C.BASE = palette.base
    C.MANTLE = palette.mantle
    C.CRUST = palette.crust


def current_scheme() -> str:
    """Return the scheme last applied by ``rebuild`` (``light`` or ``dark``)."""
    return _SCHEME


def rebuild(scheme: str) -> None:
    """Rebuild ``C`` and every module-level QSS/border string for ``scheme``.

    Already-imported *names* (``from theme import BTN_PRIMARY``) keep the
    previous string; ``theme.BTN_PRIMARY`` and ``C.*`` update in place.
    """
    global _SCHEME, _PALETTE
    parsed = _parse_scheme(scheme)
    _PALETTE = _PALETTES[parsed]
    _apply_palette(_PALETTE)
    _SCHEME = parsed
    from winpodx.gui._theme_qss import build_styles

    globals().update(build_styles())


def avatar_color(name: str) -> str:
    """Deterministic accent color for an app name."""
    palette = (
        C.BLUE,
        C.MAUVE,
        C.PEACH,
        C.GREEN,
        C.PINK,
        C.SKY,
        C.YELLOW,
        C.TEAL,
    )
    return palette[sum(ord(ch) for ch in name) % len(palette)]


def accent_color(index: int) -> str:
    """Muted accent for tool icons."""
    palette = (_PALETTE.tool_accent,)
    return palette[index % len(palette)]


def palette_for(scheme: str) -> QPalette:
    """Build a Fusion ``QPalette`` for ``scheme`` without mutating module state."""
    from PySide6.QtGui import QColor, QPalette

    colors = _PALETTES[_parse_scheme(scheme)]
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(colors.base))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(colors.text))
    qp.setColor(QPalette.ColorRole.Base, QColor(colors.mantle))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.surface0))
    qp.setColor(QPalette.ColorRole.Text, QColor(colors.text))
    qp.setColor(QPalette.ColorRole.Button, QColor(colors.surface0))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(colors.blue))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.crust))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.surface0))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.text))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.overlay0))
    return qp


_FONT_DIR = Path(__file__).with_name("fonts")
_BUNDLED_FONTS = ("selawk.ttf", "selawksb.ttf", "selawkb.ttf")
_fonts_loaded = False


def _load_bundled_fonts() -> None:
    global _fonts_loaded
    if _fonts_loaded:
        return
    from PySide6.QtGui import QFontDatabase

    for name in _BUNDLED_FONTS:
        path = _FONT_DIR / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    _fonts_loaded = True


def _ui_font_family() -> str | None:
    """Windows UI face: ``Segoe UI`` when installed, else the bundled Selawik (OFL)."""
    from PySide6.QtGui import QFontDatabase

    _load_bundled_fonts()
    families = set(QFontDatabase.families())
    for candidate in ("Segoe UI Variable", "Segoe UI", "Selawik"):
        if candidate in families:
            return candidate
    return None


def ui_font(base: QFont) -> QFont:
    """The app font: Windows UI face at the desktop's point size (falls back to ``base``)."""
    from PySide6.QtGui import QFont

    family = _ui_font_family()
    if family is None:
        return QFont(base)
    font = QFont(family)
    if base.pointSize() > 0:
        font.setPointSize(base.pointSize())
    elif base.pixelSize() > 0:
        font.setPixelSize(base.pixelSize())
    return font


def apply_to_app(app: QApplication) -> None:
    """Set Fusion, the Windows UI face at the desktop size, and the current palette."""
    app.setStyle("Fusion")
    app.setFont(ui_font(app.font()))
    app.setPalette(palette_for(current_scheme()))


# Populated by ``rebuild`` below. Declared so importers and type checkers see them.
CARD_BORDER: str
CARD_BORDER_HOVER: str
FOCUS_RING: str
FOCUS_RING_ON_ACCENT: str
FOCUS_RING_ON_SELECTION: str
ACCENT_GREEN: str
ACCENT_GREEN_HOVER: str
ACCENT_GREEN_PRESSED: str
TOOL_ACCENT: str
TOOL_ICON_BG: str
TOOL_ICON_BORDER: str
TOOL_ICON_FG: str
GLOBAL_STYLE: str
POD_CHIP: str
POD_CTRL: str
STATUS_BANNER_WARN: str
INPUT: str
COMBO: str
SEARCH_BAR: str
BTN_PRIMARY: str
BTN_SECONDARY: str
BTN_ACCENT: str
BTN_DANGER: str
BTN_GHOST: str
FILTER_CHIP: str
VIEW_TOGGLE: str
APP_TILE: str
ACTION_ROW: str
SETTINGS_SECTION: str
SECTION_CARD: str
EMPTY_STATE: str
SECTION_LABEL: str
PAGE_TITLE: str
PAGE_SUBTITLE: str
BADGE: str
CHECKBOX: str
RADIO: str
LIST_WIDGET: str
SCROLL_AREA: str
TERMINAL: str
PLAIN_TEXT: str
DIALOG: str
INFO_BAR: str
SIDEBAR: str
NAV_ITEM: str
TOP_STRIP: str
NAV_PANE: str
NAV_SEARCH: str
SETTINGS_CARD: str
SETTINGS_CARD_HOVER: str
TOGGLE_SWITCH: str
HYPERLINK_BTN: str
START_TILE: str
PAGE_HEADER: str
BREADCRUMB: str
TAB_BAR: str
SLIDER: str
SPIN_BOX: str
PROGRESS: str


def _initial_scheme() -> str:
    override = os.environ.get("WINPODX_COLOR_SCHEME", "").strip().lower()
    return override if override in (SCHEME_LIGHT, SCHEME_DARK) else SCHEME_LIGHT


rebuild(_initial_scheme())
