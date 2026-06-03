# SPDX-License-Identifier: MIT
"""winpodx design system: GitHub Dark palette."""

from __future__ import annotations


class C:
    """GitHub Dark palette."""

    ROSEWATER = "#ffa198"
    FLAMINGO = "#ffa198"
    PINK = "#f778ba"
    MAUVE = "#bc8cff"
    RED = "#f85149"
    MAROON = "#da3633"
    PEACH = "#ffa657"
    YELLOW = "#d29922"
    GREEN = "#3fb950"
    TEAL = "#56d364"
    SKY = "#79c0ff"
    SAPPHIRE = "#388bfd"
    BLUE = "#58a6ff"
    LAVENDER = "#a5d6ff"

    TEXT = "#e6edf3"
    SUBTEXT1 = "#c9d1d9"
    SUBTEXT0 = "#8b949e"
    OVERLAY2 = "#8b949e"
    OVERLAY1 = "#6e7681"
    OVERLAY0 = "#656d76"
    SURFACE2 = "#484f58"
    SURFACE1 = "#30363d"
    SURFACE0 = "#21262d"
    BASE = "#161b22"
    MANTLE = "#0d1117"
    CRUST = "#010409"


def rgba(hex_color: str, alpha: float) -> str:
    """Return a Qt stylesheet rgba() color from ``#rrggbb`` and 0..1 alpha."""
    value = hex_color.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


# --------------------------------------------------------------------------- #
# Design tokens.
#
# These are *named* magic numbers. Use them in new widget code so the design
# system stays consistent and a future "tighten / loosen everything by N px"
# pass is a single edit here rather than a grep across every page module.
#
# Spacing -- 4 px base unit, 1.5x scale.
# Used for QVBoxLayout / QHBoxLayout setSpacing, addSpacing(), and layout
# setContentsMargins.
# --------------------------------------------------------------------------- #
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24
SPACE_XXL = 32
SPACE_XXXL = 40

# Border radius scale. Match button / card / input visual weight.
RADIUS_XS = 4  # inline chips, badges
RADIUS_S = 6  # secondary controls, terminal panel
RADIUS_M = 8  # inputs, buttons, primary controls
RADIUS_L = 10  # search bar, terminal dock
RADIUS_XL = 12  # cards, app tiles
RADIUS_XXL = 14  # settings sections, app cards (grid view)

# Type scale. Keep narrow -- five sizes cover everything from caption to title.
FONT_CAPTION = 11  # secondary detail, summaries, helper text
FONT_BODY = 13  # default text, inputs, buttons
FONT_SUBHEAD = 14  # section labels, search bar
FONT_HEADER = 15  # card headers
FONT_TITLE = 18  # page titles
FONT_HERO = 22  # main window title, top-of-page heroes
FONT_DISPLAY = 24  # sparse page hero title

CONTROL_HEIGHT = 36
CONTROL_HEIGHT_L = 40
CARD_BORDER = f"1px solid {C.SURFACE1}"
CARD_BORDER_HOVER = f"1px solid {C.SURFACE2}"
FOCUS_RING = f"1px solid {C.BLUE}"


_AVATAR_PALETTE = [
    C.BLUE,
    C.MAUVE,
    C.PEACH,
    C.GREEN,
    C.PINK,
    C.SKY,
    C.YELLOW,
    C.TEAL,
]

_ACCENT_PALETTE = [
    C.BLUE,
    C.MAUVE,
    C.PEACH,
    C.GREEN,
    C.PINK,
    C.TEAL,
    C.SAPPHIRE,
    C.LAVENDER,
]


def avatar_color(name: str) -> str:
    """Deterministic accent color for an app name."""
    return _AVATAR_PALETTE[sum(ord(ch) for ch in name) % len(_AVATAR_PALETTE)]


def accent_color(index: int) -> str:
    """Rotating accent for tool icons."""
    return _ACCENT_PALETTE[index % len(_ACCENT_PALETTE)]


# Global: applied to central widget, cascades to children.
GLOBAL_STYLE = f"""
    * {{ background: transparent; }}
    QLabel {{ background: transparent; }}
    QToolTip {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE2};
        border-radius: {RADIUS_S}px;
        padding: 6px 8px;
        font-size: {FONT_CAPTION}px;
    }}
    QMenu {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE2};
        border-radius: {RADIUS_M}px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 18px;
        border-radius: {RADIUS_S}px;
    }}
    QMenu::item:selected {{
        background: {rgba(C.BLUE, 0.16)};
        color: {C.BLUE};
    }}
    QProgressBar {{
        background: {C.SURFACE0};
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
    }}
    QProgressBar::chunk {{
        background: {C.BLUE};
        border-radius: 3px;
    }}
"""

# Top Navigation Bar
TOP_BAR = f"""
    QWidget#topBar {{
        background: {C.BASE};
        border-top: 1px solid rgba(255, 255, 255, 0.04);
        border-bottom: 1px solid {C.SURFACE1};
        min-height: 56px;
        max-height: 56px;
    }}
"""

TAB_BTN = f"""
    QPushButton {{
        color: {C.OVERLAY1};
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        padding: 15px 16px 14px 16px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        color: {C.SUBTEXT1};
        background: {rgba(C.SURFACE1, 0.42)};
        border-bottom: 2px solid {C.SURFACE2};
    }}
    QPushButton:focus {{
        color: {C.TEXT};
        background: {rgba(C.SURFACE1, 0.34)};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        border-bottom: 2px solid {C.BLUE};
        background: {rgba(C.BLUE, 0.10)};
        font-weight: bold;
    }}
"""

POD_CHIP = f"""
    QFrame#podChip {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        min-height: 30px;
        max-height: 30px;
    }}
"""

POD_CTRL = f"""
    QPushButton {{
        background: transparent;
        color: {C.SUBTEXT0};
        border: none;
        border-radius: {RADIUS_S}px;
        padding: 4px 8px;
        font-size: 16px;
        min-width: 26px;
        max-height: 24px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {C.SURFACE1};
    }}
    QPushButton:disabled {{
        color: {C.SURFACE2};
    }}
"""

# Status Banner: shown below top bar when pod is stopped/paused.
STATUS_BANNER_WARN = f"""
    QFrame#statusBanner {{
        background: {C.SURFACE0};
        border-bottom: 1px solid {C.SURFACE1};
        min-height: 36px;
        max-height: 36px;
    }}
"""

# Form Inputs
INPUT = f"""
    QLineEdit {{
        background: {C.MANTLE};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE1};
        border-radius: {RADIUS_M}px;
        padding: 9px 13px;
        font-size: 13px;
        min-height: 18px;
        selection-background-color: {C.BLUE};
        selection-color: {C.CRUST};
    }}
    QLineEdit:hover {{
        border-color: {C.SURFACE2};
        background: {C.BASE};
    }}
    QLineEdit:focus {{
        border-color: {C.BLUE};
        background: {C.BASE};
    }}
    QLineEdit:read-only {{
        background: {C.SURFACE0};
        color: {C.OVERLAY0};
        border-color: transparent;
    }}
"""

COMBO = f"""
    QComboBox {{
        background: {C.MANTLE};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE1};
        border-radius: {RADIUS_M}px;
        padding: 8px 30px 8px 13px;
        font-size: 13px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border-color: {C.SURFACE2};
        background: {C.BASE};
    }}
    QComboBox:focus {{
        border-color: {C.BLUE};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 26px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {C.SUBTEXT0};
        width: 0;
        height: 0;
        margin-right: 10px;
    }}
    QComboBox QAbstractItemView {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE1};
        border-radius: 6px;
        selection-background-color: {C.SURFACE1};
        selection-color: {C.BLUE};
        outline: none;
        padding: 4px;
    }}
"""

SEARCH_BAR = f"""
    QLineEdit {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 2px solid transparent;
        border-radius: {RADIUS_L}px;
        padding: 10px 16px 10px 14px;
        font-size: 14px;
        min-height: 20px;
    }}
    QLineEdit:hover {{
        border-color: {C.SURFACE1};
    }}
    QLineEdit:focus {{
        border-color: {C.BLUE};
        background: {C.BASE};
    }}
"""

# Buttons
BTN_PRIMARY = f"""
    QPushButton {{
        background: {C.BLUE};
        color: {C.CRUST};
        font-size: 13px;
        font-weight: bold;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-bottom: 1px solid rgba(0, 0, 0, 0.2);
        border-radius: {RADIUS_M}px;
        padding: 9px 20px;
        min-height: 18px;
    }}
    QPushButton:hover {{ background: {C.SAPPHIRE}; }}
    QPushButton:pressed {{
        background: {C.SAPPHIRE};
        border: 1px solid rgba(0, 0, 0, 0.2);
    }}
    QPushButton:disabled {{
        background: {C.SURFACE1};
        color: {C.OVERLAY0};
        border: 1px solid transparent;
    }}
"""

BTN_SECONDARY = f"""
    QPushButton {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        font-size: 13px;
        border: 1px solid {C.SURFACE1};
        border-radius: {RADIUS_M}px;
        padding: 8px 16px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        background: {C.SURFACE1};
    }}
    QPushButton:pressed {{ background: {C.SURFACE2}; }}
"""

BTN_ACCENT = f"""
    QPushButton {{
        background: {C.GREEN};
        color: {C.CRUST};
        font-size: 13px;
        font-weight: bold;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-bottom: 1px solid rgba(0, 0, 0, 0.2);
        border-radius: {RADIUS_M}px;
        padding: 8px 18px;
        min-height: 18px;
    }}
    QPushButton:hover {{ background: {C.TEAL}; }}
    QPushButton:pressed {{ background: {C.GREEN}; }}
    QPushButton:disabled {{
        background: {C.SURFACE1};
        color: {C.OVERLAY0};
        border: 1px solid transparent;
    }}
"""

BTN_DANGER = f"""
    QPushButton {{
        background: transparent;
        color: {C.PEACH};
        font-size: 13px;
        border: 1px solid {rgba(C.RED, 0.24)};
        border-radius: {RADIUS_M}px;
        padding: 7px 12px;
        min-height: 16px;
    }}
    QPushButton:hover {{
        background: {rgba(C.RED, 0.18)};
        color: {C.RED};
        border-color: {C.RED};
    }}
    QPushButton:pressed {{
        background: {C.RED};
        color: {C.CRUST};
    }}
    QPushButton:disabled {{
        color: {C.OVERLAY0};
        border-color: {C.SURFACE1};
        background: transparent;
    }}
"""

BTN_GHOST = f"""
    QPushButton {{
        background: transparent;
        color: {C.SUBTEXT0};
        font-size: 12px;
        border: none;
        border-radius: {RADIUS_S}px;
        padding: 7px 12px;
        min-height: 16px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {C.SURFACE0};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        background: {rgba(C.BLUE, 0.12)};
    }}
    QPushButton:disabled {{
        color: {C.SURFACE2};
    }}
"""

# Filter chip.
FILTER_CHIP = f"""
    QPushButton {{
        background: {C.SURFACE0};
        color: {C.SUBTEXT0};
        font-size: 12px;
        border: 1px solid {C.SURFACE1};
        border-radius: 13px;
        padding: 5px 16px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        border-color: {C.OVERLAY0};
        background: {C.SURFACE1};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        border-color: {C.BLUE};
        background: {rgba(C.BLUE, 0.12)};
        font-weight: bold;
    }}
"""

# View toggle (grid/list).
VIEW_TOGGLE = f"""
    QPushButton {{
        background: {C.SURFACE0};
        color: {C.OVERLAY0};
        font-size: 14px;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 6px 10px;
        min-width: 32px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {rgba(C.BLUE, 0.14)};
        border-color: {rgba(C.BLUE, 0.26)};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        background: {C.SURFACE1};
    }}
"""

# App Card (grid view).
APP_CARD = f"""
    QFrame#appCard {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: {RADIUS_XXL}px;
    }}
    QFrame#appCard:hover {{
        background: {rgba(C.SURFACE1, 0.82)};
        border-color: {C.BLUE};
        border-top: 1px solid rgba(255, 255, 255, 0.1);
    }}
"""

# App Tile (list view).
APP_TILE = f"""
    QFrame#appTile {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: {RADIUS_L}px;
    }}
    QFrame#appTile:hover {{
        background: {rgba(C.SURFACE1, 0.82)};
        border-color: {C.BLUE};
    }}
"""

# Tool Action Row (maintenance page).
ACTION_ROW = f"""
    QFrame#actionRow {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: {RADIUS_XL}px;
    }}
    QFrame#actionRow:hover {{
        background: {rgba(C.SURFACE1, 0.82)};
        border-color: {C.SURFACE2};
    }}
"""

# Settings Section
SETTINGS_SECTION = f"""
    QFrame#settingsSection {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: {RADIUS_XXL}px;
    }}
"""

SECTION_CARD = SETTINGS_SECTION

EMPTY_STATE = f"""
    QFrame#emptyState {{
        background: {rgba(C.SURFACE0, 0.72)};
        border: 1px dashed {C.SURFACE2};
        border-radius: {RADIUS_XL}px;
    }}
"""

SECTION_LABEL = f"""
    QLabel {{
        background: transparent;
        color: {C.SUBTEXT0};
        font-size: {FONT_CAPTION}px;
        font-weight: bold;
        text-transform: uppercase;
    }}
"""

PAGE_TITLE = f"""
    QLabel {{
        background: transparent;
        color: {C.TEXT};
        font-size: {FONT_HERO}px;
        font-weight: bold;
    }}
"""

PAGE_SUBTITLE = f"""
    QLabel {{
        background: transparent;
        color: {C.OVERLAY0};
        font-size: {FONT_BODY}px;
    }}
"""

BADGE = f"""
    QLabel {{
        border-radius: {RADIUS_S}px;
        padding: 2px 7px;
        font-size: {FONT_CAPTION}px;
        font-weight: bold;
    }}
"""

CHECKBOX = f"""
    QCheckBox {{
        color: {C.SUBTEXT1};
        font-size: {FONT_BODY}px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {C.SURFACE2};
        background: {C.MANTLE};
    }}
    QCheckBox::indicator:hover {{
        border-color: {C.BLUE};
    }}
    QCheckBox::indicator:checked {{
        background: {C.BLUE};
        border-color: {C.BLUE};
    }}
    QCheckBox:disabled {{
        color: {C.OVERLAY0};
    }}
"""

RADIO = f"""
    QRadioButton {{
        color: {C.SUBTEXT1};
        font-size: {FONT_BODY}px;
        spacing: 8px;
    }}
    QRadioButton::indicator {{
        width: 15px;
        height: 15px;
        border-radius: 8px;
        border: 1px solid {C.SURFACE2};
        background: {C.MANTLE};
    }}
    QRadioButton::indicator:checked {{
        border: 4px solid {C.BLUE};
        background: {C.CRUST};
    }}
"""

LIST_WIDGET = f"""
    QListWidget {{
        background: {C.MANTLE};
        color: {C.TEXT};
        border: 1px solid {C.SURFACE1};
        border-radius: {RADIUS_M}px;
        padding: 6px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 6px 8px;
        border-radius: {RADIUS_S}px;
    }}
    QListWidget::item:selected {{
        background: {rgba(C.BLUE, 0.18)};
        color: {C.BLUE};
    }}
"""

# Scroll Area
SCROLL_AREA = f"""
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {C.SURFACE1};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {C.SURFACE2};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: none;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {C.SURFACE1};
        min-width: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {C.SURFACE2};
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
"""

# Terminal Dock
TERMINAL = f"""
    QTextEdit {{
        background: {C.CRUST};
        color: {C.SUBTEXT1};
        font-family: 'JetBrains Mono', 'Fira Code',
                     'Cascadia Code', monospace;
        font-size: 12px;
        border: 1px solid {C.SURFACE0};
        border-radius: {RADIUS_L}px;
        padding: 14px;
        selection-background-color: {C.SURFACE1};
    }}
"""

PLAIN_TEXT = f"""
    QPlainTextEdit {{
        background: {C.CRUST};
        color: {C.SUBTEXT1};
        font-family: 'JetBrains Mono', 'Fira Code',
                     'Cascadia Code', monospace;
        font-size: 12px;
        border: 1px solid {C.SURFACE0};
        border-radius: {RADIUS_L}px;
        padding: 12px;
        selection-background-color: {C.SURFACE1};
    }}
"""

DIALOG = f"""
    QDialog {{
        background: {C.MANTLE};
        color: {C.TEXT};
    }}
    QLabel {{
        background: transparent;
        color: {C.TEXT};
    }}
"""

# Bottom Info Bar
INFO_BAR = f"""
    QWidget#infoBar {{
        background: {C.BASE};
        border-top: 1px solid rgba(255, 255, 255, 0.04);
        min-height: 32px;
        max-height: 32px;
    }}
"""
