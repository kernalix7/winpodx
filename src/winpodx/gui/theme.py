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
GLOBAL_STYLE = """
    * { background: transparent; }
    QLabel { background: transparent; }
"""

# Top Navigation Bar
TOP_BAR = f"""
    QWidget#topBar {{
        background: {C.BASE};
        border-top: 1px solid rgba(255, 255, 255, 0.04);
        border-bottom: 1px solid {C.SURFACE1};
        min-height: 52px;
        max-height: 52px;
    }}
"""

TAB_BTN = f"""
    QPushButton {{
        color: {C.OVERLAY1};
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        padding: 14px 18px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        color: {C.SUBTEXT1};
        background: rgba(48, 54, 61, 0.4);
        border-bottom: 2px solid {C.SURFACE2};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        border-bottom: 2px solid {C.BLUE};
        background: rgba(88, 166, 255, 0.08);
        font-weight: bold;
    }}
"""

POD_CHIP = f"""
    QFrame#podChip {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 14px;
        max-height: 28px;
    }}
"""

POD_CTRL = f"""
    QPushButton {{
        background: transparent;
        color: {C.SUBTEXT0};
        border: none;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 16px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {C.SURFACE1};
    }}
    QPushButton:disabled {{
        color: {C.SURFACE1};
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
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
        selection-background-color: {C.BLUE};
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
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
    }}
    QComboBox:focus {{
        border-color: {C.BLUE};
    }}
    QComboBox::drop-down {{
        border: none;
        padding-right: 10px;
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
        border-radius: 10px;
        padding: 10px 16px 10px 14px;
        font-size: 14px;
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
        border-radius: 8px;
        padding: 10px 24px;
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
        border-radius: 8px;
        padding: 9px 20px;
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
        border-radius: 8px;
        padding: 8px 18px;
    }}
    QPushButton:hover {{ background: {C.TEAL}; }}
"""

BTN_DANGER = f"""
    QPushButton {{
        background: transparent;
        color: {C.OVERLAY0};
        font-size: 13px;
        border: 1px solid {C.SURFACE0};
        border-radius: 8px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{
        background: {C.RED};
        color: {C.CRUST};
        border-color: {C.RED};
    }}
"""

BTN_GHOST = f"""
    QPushButton {{
        background: transparent;
        color: {C.SUBTEXT0};
        font-size: 12px;
        border: none;
        border-radius: 6px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {C.SURFACE0};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        background: {C.SURFACE0};
    }}
"""

# Filter chip.
FILTER_CHIP = f"""
    QPushButton {{
        background: {C.SURFACE0};
        color: {C.SUBTEXT0};
        font-size: 12px;
        border: 1px solid {C.SURFACE1};
        border-radius: 12px;
        padding: 5px 16px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        border-color: {C.OVERLAY0};
        background: {C.SURFACE1};
    }}
    QPushButton:checked {{
        color: {C.BLUE};
        border-color: {C.BLUE};
        background: rgba(88, 166, 255, 0.12);
        font-weight: bold;
    }}
"""

# View toggle (grid/list).
VIEW_TOGGLE = f"""
    QPushButton {{
        background: {C.SURFACE0};
        color: {C.OVERLAY0};
        font-size: 14px;
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        min-width: 32px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {C.SURFACE1};
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
        border-radius: 12px;
    }}
    QFrame#appCard:hover {{
        background: rgba(48, 54, 61, 0.85);
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
        border-radius: 10px;
    }}
    QFrame#appTile:hover {{
        background: rgba(48, 54, 61, 0.85);
        border-color: {C.BLUE};
    }}
"""

# Tool Action Row (maintenance page).
ACTION_ROW = f"""
    QFrame#actionRow {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
    }}
    QFrame#actionRow:hover {{
        background: rgba(48, 54, 61, 0.85);
        border-color: {C.SURFACE2};
    }}
"""

# Settings Section
SETTINGS_SECTION = f"""
    QFrame#settingsSection {{
        background: {C.SURFACE0};
        border: 1px solid {C.SURFACE1};
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 14px;
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
        width: 6px;
        margin: 0;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background: {C.SURFACE1};
        min-height: 24px;
        border-radius: 3px;
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
        border-radius: 10px;
        padding: 14px;
        selection-background-color: {C.SURFACE1};
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
