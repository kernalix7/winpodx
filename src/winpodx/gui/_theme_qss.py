# SPDX-License-Identifier: MIT
"""Fluent QSS catalogue. Private; ``theme.rebuild`` is the public entry."""

from __future__ import annotations

from pathlib import Path


def build_styles() -> dict[str, str]:
    """Return every rebuildable QSS / border / accent string for the active palette."""
    from winpodx.gui import theme as t
    from winpodx.gui._theme_qss_controls import build_control_styles

    C = t.C
    p = t._PALETTE
    rgba = t.rgba
    focus_ring = f"2px solid {C.TEXT}"
    focus_ring_on_accent = f"2px solid {C.CRUST}"
    focus_ring_on_selection = f"2px solid {C.TEXT}"
    controls = build_control_styles()
    global_controls = controls.pop("GLOBAL_CONTROLS")
    card_border = f"1px solid {p.card_stroke}"
    card_border_hover = f"1px solid {C.BLUE}"
    is_dark = t.current_scheme() == t.SCHEME_DARK
    stroke_top = "rgba(255, 255, 255, 0.093)" if is_dark else "rgba(0, 0, 0, 0.058)"
    stroke_bottom = "rgba(255, 255, 255, 0.070)" if is_dark else "rgba(0, 0, 0, 0.162)"
    accent_bottom = "rgba(0, 0, 0, 0.40)" if is_dark else "rgba(0, 0, 0, 0.30)"
    elevation = f"border: 1px solid {stroke_top}; border-bottom: 1px solid {stroke_bottom};"
    tool_accent = p.tool_accent
    chevron_file = (
        "chevron-down-dark.svg" if t.current_scheme() == t.SCHEME_DARK else "chevron-down-light.svg"
    )
    combo_chevron = Path(__file__).with_name("icons").joinpath(chevron_file).as_posix()
    return {
        "CARD_BORDER": card_border,
        "CARD_BORDER_HOVER": card_border_hover,
        "FOCUS_RING": focus_ring,
        "FOCUS_RING_ON_ACCENT": focus_ring_on_accent,
        "FOCUS_RING_ON_SELECTION": focus_ring_on_selection,
        "ACCENT_GREEN": C.GREEN,
        "ACCENT_GREEN_HOVER": p.success_hover,
        "ACCENT_GREEN_PRESSED": p.success_pressed,
        "TOOL_ACCENT": tool_accent,
        "TOOL_ICON_BG": rgba(tool_accent, 0.12),
        "TOOL_ICON_BORDER": rgba(tool_accent, 0.28),
        "TOOL_ICON_FG": p.tool_icon_fg,
        "GLOBAL_STYLE": f"""
    * {{ background: transparent; }}
    * {{ outline: none; }}
    QLabel {{ background: transparent; }}
{global_controls}
{controls["DIALOG"]}
""",
        "POD_CHIP": f"""
    QFrame#podChip {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
        min-height: 32px;
        max-height: 32px;
    }}
""",
        "POD_STATE_PILL": """
    QFrame#podStatePill {
        border: none;
        border-radius: 11px;
        min-height: 22px;
        max-height: 22px;
    }
""",
        "TRANSPORT_CHIP": f"""
    QLabel {{
        font-size: 12px;
        font-weight: 600;
        border-radius: {t.RADIUS_S}px;
        min-width: 16px;
        max-width: 16px;
        min-height: 16px;
        max-height: 16px;
    }}
""",
        "POD_CTRL": f"""
    QPushButton {{
        background: transparent;
        color: {C.SUBTEXT0};
        border: none;
        border-radius: {t.RADIUS_S}px;
        padding: 4px 8px;
        font-size: 16px;
        min-width: 26px;
        max-height: 24px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {p.subtle_hover};
    }}
    QPushButton:disabled {{
        color: {C.OVERLAY0};
    }}
""",
        "STATUS_BANNER_WARN": f"""
    QFrame#statusBanner {{
        background: {C.SURFACE0};
        border-bottom: 1px solid {p.divider};
        min-height: 36px;
        max-height: 36px;
    }}
""",
        "INPUT": f"""
    QLineEdit {{
        background: {p.control_fill};
        color: {C.TEXT};
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 12px;
        font-size: {t.FONT_BODY}px;
        min-height: 32px;
        selection-background-color: {C.BLUE};
        selection-color: {C.CRUST};
    }}
    QLineEdit:hover {{
        background: {p.control_hover};
    }}
    QLineEdit:focus {{
        background: {p.control_input_active};
        border-bottom: 2px solid {C.BLUE};
        padding-bottom: 0px;
    }}
    QLineEdit:read-only {{
        background: transparent;
        color: {C.OVERLAY0};
        border-color: transparent;
    }}
""",
        "COMBO": f"""
    QComboBox {{
        background: {p.control_fill};
        color: {C.TEXT};
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 30px 0px 12px;
        font-size: {t.FONT_BODY}px;
        min-height: 32px;
    }}
    QComboBox:hover {{
        background: {p.control_hover};
    }}
    QComboBox:on {{
        background: {p.control_pressed};
    }}
    QComboBox:focus {{
        border-bottom: 2px solid {C.BLUE};
    }}
    QComboBox::drop-down {{
        width: 32px;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url({combo_chevron});
        width: 12px;
        height: 8px;
        margin-right: 12px;
    }}
    QComboBox QAbstractItemView {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_L}px;
        selection-background-color: {p.nav_selected};
        selection-color: {C.TEXT};
        outline: none;
        padding: 4px;
    }}
""",
        "SEARCH_BAR": f"""
    QLineEdit {{
        background: {p.control_fill};
        color: {C.TEXT};
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 16px 0px 12px;
        font-size: {t.FONT_BODY}px;
        min-height: 32px;
    }}
    QLineEdit:hover {{
        background: {p.control_hover};
    }}
    QLineEdit:focus {{
        background: {p.control_input_active};
        border-bottom: 2px solid {C.BLUE};
    }}
""",
        "BTN_PRIMARY": f"""
    QPushButton {{
        background: {C.BLUE};
        color: {C.CRUST};
        font-size: {t.FONT_BODY}px;
        font-weight: 500;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-bottom: 1px solid {accent_bottom};
        border-radius: {t.RADIUS_M}px;
        padding: 0px 16px;
        min-height: 32px;
    }}
    QPushButton:hover {{ background: {C.SKY}; }}
    QPushButton:pressed {{
        background: {C.SAPPHIRE};
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }}
    QPushButton:disabled {{
        background: {p.control_disabled};
        color: {p.text_disabled};
        border: 1px solid transparent;
    }}
    QPushButton:focus {{
        border: {focus_ring_on_accent};
    }}
""",
        "BTN_SECONDARY": f"""
    QPushButton {{
        background: {p.control_fill};
        color: {C.TEXT};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 16px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        background: {p.control_hover};
    }}
    QPushButton:pressed {{
        background: {p.control_pressed};
        border-bottom: 1px solid {stroke_top};
    }}
    QPushButton:disabled {{
        background: {p.control_disabled};
        color: {p.text_disabled};
        border: 1px solid transparent;
    }}
    QPushButton:focus {{
        border: {focus_ring};
    }}
""",
        "BTN_ACCENT": f"""
    QPushButton {{
        background: {C.GREEN};
        color: {C.CRUST};
        font-size: {t.FONT_BODY}px;
        font-weight: 500;
        border: 1px solid {C.GREEN};
        border-radius: {t.RADIUS_M}px;
        padding: 0px 16px;
        min-height: 32px;
    }}
    QPushButton:hover {{ background: {p.success_hover}; }}
    QPushButton:pressed {{ background: {p.success_pressed}; }}
    QPushButton:disabled {{
        background: {C.SURFACE1};
        color: {C.OVERLAY0};
        border: 1px solid transparent;
    }}
    QPushButton:focus {{
        border: {focus_ring_on_accent};
    }}
""",
        "BTN_DANGER": f"""
    QPushButton {{
        background: transparent;
        color: {C.RED};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
        border: 1px solid {rgba(C.RED, 0.40)};
        border-radius: {t.RADIUS_M}px;
        padding: 0px 12px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        background: {rgba(C.RED, 0.12)};
        color: {C.RED};
        border-color: {C.RED};
    }}
    QPushButton:pressed {{
        background: {C.RED};
        color: {C.CRUST};
    }}
    QPushButton:disabled {{
        color: {C.OVERLAY0};
        border-color: {p.card_stroke};
        background: transparent;
    }}
    QPushButton:focus {{
        border: {focus_ring};
    }}
""",
        "BTN_GHOST": f"""
    QPushButton {{
        background: transparent;
        color: {C.TEXT};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
        border: none;
        border-radius: {t.RADIUS_S}px;
        padding: 0px 12px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {p.subtle_hover};
    }}
    QPushButton:pressed {{
        color: {C.TEXT};
        background: {p.control_pressed};
    }}
    QPushButton:checked {{
        color: {C.TEXT};
        background: {p.nav_selected};
    }}
    QPushButton:disabled {{
        color: {C.OVERLAY0};
    }}
    QPushButton:focus {{
        border: {focus_ring};
    }}
    QPushButton:checked:focus {{
        border: {focus_ring_on_selection};
    }}
""",
        "FILTER_CHIP": f"""
    QPushButton {{
        background: {p.control_fill};
        color: {C.SUBTEXT0};
        font-size: {t.FONT_CAPTION}px;
        font-weight: 400;
        border: 1px solid {p.card_stroke};
        border-radius: 16px;
        padding: 0px 16px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {p.control_hover};
    }}
    QPushButton:checked {{
        color: {C.TEXT};
        border-color: {C.BLUE};
        background: {p.nav_selected};
        font-weight: 500;
    }}
""",
        "VIEW_TOGGLE": f"""
    QPushButton {{
        background: {p.control_fill};
        color: {C.OVERLAY0};
        font-size: {t.FONT_BODY}px;
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_S}px;
        padding: 0px 10px;
        min-width: 32px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        color: {C.TEXT};
        background: {p.subtle_hover};
    }}
    QPushButton:checked {{
        color: {C.TEXT};
        background: {p.nav_selected};
    }}
""",
        "APP_TILE": f"""
    QFrame#appTile {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
    QFrame#appTile:hover {{
        background: {p.control_hover};
        border: {card_border_hover};
    }}
    QFrame#appTile:focus {{
        border: {focus_ring};
    }}
    QFrame#appTileBtn:focus {{
        border: {focus_ring};
    }}
""",
        "ACTION_ROW": f"""
    QFrame#actionRow {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
    QFrame#actionRow:hover {{
        background: {p.control_hover};
        border-color: {C.SURFACE2};
    }}
""",
        "SETTINGS_SECTION": f"""
    QFrame#settingsSection {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
""",
        "SECTION_CARD": f"""
    QFrame#settingsSection {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
""",
        "EMPTY_STATE": f"""
    QFrame#emptyState {{
        background: {C.SURFACE0};
        border: 1px dashed {C.SURFACE2};
        border-radius: {t.RADIUS_L}px;
    }}
""",
        "SECTION_LABEL": f"""
    QLabel {{
        background: transparent;
        color: {C.TEXT};
        font-size: {t.FONT_SUBHEAD}px;
        font-weight: 600;
    }}
""",
        "PAGE_TITLE": f"""
    QLabel {{
        background: transparent;
        color: {C.TEXT};
        font-size: {t.FONT_TITLE}px;
        font-weight: 600;
    }}
""",
        "PAGE_SUBTITLE": f"""
    QLabel {{
        background: transparent;
        color: {C.SUBTEXT1};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
    }}
""",
        "BADGE": f"""
    QLabel {{
        border-radius: {t.RADIUS_S}px;
        padding: 2px 7px;
        font-size: {t.FONT_CAPTION}px;
        font-weight: 500;
    }}
""",
        "CHECKBOX": controls["CHECKBOX"],
        "RADIO": controls["RADIO"],
        "LIST_WIDGET": controls["LIST_WIDGET"],
        "PROGRESS": controls["PROGRESS"],
        "SLIDER": controls["SLIDER"],
        "SPIN_BOX": controls["SPIN_BOX"],
        "TAB_BAR": controls["TAB_BAR"],
        "SCROLL_AREA": f"""
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px 2px 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {C.OVERLAY1};
        min-height: 32px;
        border-radius: 1px;
        margin-left: 6px;
    }}
    QScrollBar:vertical:hover {{
        background: {p.subtle_hover};
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {C.SUBTEXT0};
        border-radius: 3px;
        margin-left: 2px;
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
        height: 10px;
        margin: 0 2px 2px 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {C.OVERLAY1};
        min-width: 32px;
        border-radius: 1px;
        margin-top: 6px;
    }}
    QScrollBar:horizontal:hover {{
        background: {p.subtle_hover};
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {C.SUBTEXT0};
        border-radius: 3px;
        margin-top: 2px;
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {{
        background: none;
    }}
""",
        "TERMINAL": f"""
    QTextEdit {{
        background: {C.MANTLE};
        color: {C.TEXT};
        font-family: 'JetBrains Mono', 'Fira Code',
                     'Cascadia Code', monospace;
        font-size: 12px;
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_L}px;
        padding: 14px;
        selection-background-color: {C.SURFACE1};
    }}
""",
        "LOG_VIEWER": f"""
    QTextEdit {{
        background: {C.MANTLE};
        color: {C.TEXT};
        font-family: 'JetBrains Mono', 'Fira Code',
                     'Cascadia Code', monospace;
        font-size: 12px;
        border: none;
        padding: 0;
        selection-background-color: {C.SURFACE1};
    }}
""",
        "PLAIN_TEXT": f"""
    QPlainTextEdit {{
        background: {C.MANTLE};
        color: {C.TEXT};
        font-family: 'JetBrains Mono', 'Fira Code',
                     'Cascadia Code', monospace;
        font-size: 12px;
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_L}px;
        padding: 12px;
        selection-background-color: {C.SURFACE1};
    }}
""",
        "DIALOG": controls["DIALOG"],
        "INFO_BAR": f"""
    QWidget#infoBar {{
        background: {C.BASE};
        border-top: 1px solid {p.divider};
        min-height: 32px;
        max-height: 32px;
    }}
""",
        "SIDEBAR": f"""
    QFrame#sideBar {{
        background: {p.nav_pane};
        border-right: 1px solid {p.divider};
    }}
""",
        "NAV_ITEM": f"""
    QPushButton#navItem {{
        background: transparent;
        color: {C.SUBTEXT0};
        border: none;
        border-radius: {t.RADIUS_M}px;
        padding: 0px 12px;
        text-align: left;
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
        min-height: 36px;
    }}
    QPushButton#navItem:hover {{
        background: {p.nav_hover};
        color: {C.TEXT};
    }}
    QPushButton#navItem:pressed {{
        background: {p.subtle_hover};
        color: {C.SUBTEXT1};
    }}
    QPushButton#navItem:checked {{
        background: {p.nav_selected};
        color: {C.TEXT};
    }}
    QPushButton#navItem:focus {{
        border: {focus_ring};
    }}
    QPushButton#navItem:checked:focus {{
        border: {focus_ring_on_selection};
    }}
""",
        "TOP_STRIP": f"""
    QWidget#topStrip {{
        background: {C.BASE};
        border-bottom: 1px solid {p.divider};
        min-height: 52px;
        max-height: 52px;
    }}
""",
        "NAV_PANE": f"""
    QFrame#navPane {{
        background: {p.nav_pane};
        border-right: 1px solid {p.divider};
        min-width: {t.NAV_PANE_WIDTH}px;
        max-width: {t.NAV_PANE_WIDTH}px;
    }}
""",
        "NAV_SEARCH": f"""
    QLineEdit#navSearch {{
        background: {p.control_fill};
        color: {C.TEXT};
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_M}px;
        padding: 0px 12px;
        font-size: {t.FONT_BODY}px;
        min-height: 32px;
    }}
    QLineEdit#navSearch:hover {{
        background: {p.control_hover};
    }}
    QLineEdit#navSearch:focus {{
        border-color: {C.BLUE};
    }}
""",
        "SETTINGS_CARD": f"""
    QFrame#settingsCard {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
""",
        "SETTINGS_CARD_HOVER": f"""
    QFrame#settingsCard {{
        background: {C.SURFACE0};
        border: {card_border};
        border-radius: {t.RADIUS_L}px;
    }}
    QFrame#settingsCard:hover {{
        background: {p.control_hover};
        border: {card_border_hover};
    }}
""",
        "TOGGLE_SWITCH": f"""
    QCheckBox {{
        color: {C.TEXT};
        font-size: {t.FONT_BODY}px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: {t.TOGGLE_W}px;
        height: {t.TOGGLE_H}px;
        border-radius: 10px;
        border: 1px solid {p.card_stroke};
    }}
    QCheckBox::indicator:unchecked {{
        background: qradialgradient(cx:0.25, cy:0.5, radius:0.48, fx:0.25, fy:0.5,
            stop:0 {C.SURFACE0}, stop:0.55 {C.SURFACE0},
            stop:0.62 {C.OVERLAY1}, stop:1 {C.OVERLAY1});
    }}
    QCheckBox::indicator:checked {{
        background: qradialgradient(cx:0.75, cy:0.5, radius:0.48, fx:0.75, fy:0.5,
            stop:0 {C.CRUST}, stop:0.55 {C.CRUST},
            stop:0.62 {C.BLUE}, stop:1 {C.BLUE});
        border-color: {C.BLUE};
    }}
""",
        "HYPERLINK_BTN": f"""
    QPushButton {{
        background: transparent;
        color: {C.BLUE};
        font-size: {t.FONT_BODY}px;
        border: none;
        border-radius: {t.RADIUS_S}px;
        padding: 0px 4px;
        min-height: 32px;
        text-align: left;
    }}
    QPushButton:hover {{
        color: {C.SKY};
        background: {p.subtle_hover};
    }}
    QPushButton:pressed {{
        color: {C.SAPPHIRE};
    }}
    QPushButton:focus {{
        border: {focus_ring};
    }}
""",
        "START_TILE": f"""
    QFrame#startTile, QFrame#appTileBtn {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {t.RADIUS_S}px;
    }}
    QFrame#startTile:hover, QFrame#appTileBtn:hover {{
        background: {p.subtle_hover};
        border: 1px solid {rgba(C.BLUE, 0.35)};
    }}
    QFrame#startTile[pressed="true"], QFrame#appTileBtn[pressed="true"] {{
        background: {p.control_pressed};
    }}
    QFrame#startTile:focus, QFrame#appTileBtn:focus {{
        border: {focus_ring};
    }}
""",
        "PAGE_HEADER": f"""
    QLabel#pageHeader {{
        background: transparent;
        color: {C.TEXT};
        font-size: {t.FONT_TITLE}px;
        font-weight: 600;
    }}
""",
        "BREADCRUMB": f"""
    QLabel#breadcrumb {{
        background: transparent;
        color: {C.SUBTEXT1};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
    }}
""",
    }
