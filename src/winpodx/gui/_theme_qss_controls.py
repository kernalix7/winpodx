# SPDX-License-Identifier: MIT
"""WinUI inner-control QSS. Merged into ``build_styles``; not a public API."""

from __future__ import annotations

from pathlib import Path


def _icon_url(name: str) -> str:
    return Path(__file__).with_name("icons").joinpath(name).as_posix()


def _scheme_icon_url(name: str, *, is_dark: bool) -> str:
    if is_dark:
        dark = Path(__file__).with_name("icons") / "dark" / name
        if dark.is_file():
            return dark.as_posix()
    return _icon_url(name)


def build_control_styles() -> dict[str, str]:
    """Return inner-control QSS tokens for the active palette."""
    from winpodx.gui import theme as t

    C = t.C
    p = t._PALETTE
    is_dark = t.current_scheme() == t.SCHEME_DARK
    stroke_top = "rgba(255, 255, 255, 0.093)" if is_dark else "rgba(0, 0, 0, 0.058)"
    stroke_bottom = "rgba(255, 255, 255, 0.070)" if is_dark else "rgba(0, 0, 0, 0.162)"
    accent_bottom = "rgba(0, 0, 0, 0.40)" if is_dark else "rgba(0, 0, 0, 0.30)"
    elevation = f"border: 1px solid {stroke_top}; border-bottom: 1px solid {stroke_bottom};"
    focus_ring = f"2px solid {C.TEXT}"
    chevron_down = _icon_url("chevron-down-dark.svg" if is_dark else "chevron-down-light.svg")
    chevron_up = _icon_url("chevron-up-dark.svg" if is_dark else "chevron-up-light.svg")
    check_url = _scheme_icon_url("checkmark-on-accent.svg", is_dark=is_dark)
    radio_url = _scheme_icon_url("radio-dot.svg", is_dark=is_dark)
    disabled_accent = t.rgba(C.BLUE, 0.36)

    tooltip = f"""
    QToolTip {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_S}px;
        padding: 6px 8px;
        font-size: {t.FONT_CAPTION}px;
    }}
"""
    menu = f"""
    QMenu {{
        background: {C.SURFACE0};
        color: {C.TEXT};
        border: 1px solid {p.card_stroke};
        border-radius: {t.RADIUS_L}px;
        padding: 4px;
    }}
    QMenu::item {{
        min-height: 32px;
        padding: 0px 12px;
        border-radius: {t.RADIUS_S}px;
        margin: 2px 4px;
    }}
    QMenu::item:selected {{
        background: {p.subtle_hover};
        color: {C.TEXT};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p.divider};
        margin: 4px 8px;
    }}
    QMenu::icon {{
        width: 16px;
        height: 16px;
    }}
    QMenu::indicator {{
        width: 16px;
        height: 16px;
    }}
"""
    checkbox = f"""
    QCheckBox {{
        color: {C.SUBTEXT1};
        font-size: {t.FONT_BODY}px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 20px;
        height: 20px;
        border-radius: {t.RADIUS_S}px;
        border: 1px solid {p.card_stroke};
        border-bottom: 1px solid {stroke_bottom};
        background: {p.control_fill};
    }}
    QCheckBox::indicator:hover {{
        background: {p.control_hover};
    }}
    QCheckBox::indicator:checked {{
        background: {C.BLUE};
        border: 1px solid {C.BLUE};
        image: url({check_url});
    }}
    QCheckBox::indicator:disabled {{
        background: {p.control_disabled};
        border-color: {p.card_stroke};
    }}
    QCheckBox::indicator:checked:disabled {{
        background: {disabled_accent};
        border-color: {disabled_accent};
    }}
    QCheckBox:disabled {{
        color: {p.text_disabled};
    }}
    QCheckBox:focus {{
        border: {focus_ring};
        border-radius: {t.RADIUS_XS}px;
    }}
    /* checkmark-on-accent.svg */
"""
    radio = f"""
    QRadioButton {{
        color: {C.SUBTEXT1};
        font-size: {t.FONT_BODY}px;
        spacing: 8px;
    }}
    QRadioButton::indicator {{
        width: 20px;
        height: 20px;
        border-radius: 10px;
        border: 1px solid {p.card_stroke};
        background: {p.control_fill};
    }}
    QRadioButton::indicator:hover {{
        background: {p.control_hover};
    }}
    QRadioButton::indicator:checked {{
        background: {C.BLUE};
        border: 1px solid {C.BLUE};
        image: url({radio_url});
    }}
    QRadioButton:focus {{
        border: {focus_ring};
        border-radius: {t.RADIUS_XS}px;
    }}
"""
    list_widget = f"""
    QListWidget, QListView, QTreeView {{
        background: transparent;
        color: {C.TEXT};
        border: none;
        outline: none;
        padding: 4px;
    }}
    QListWidget::item, QListView::item, QTreeView::item {{
        min-height: 36px;
        padding: 0px 12px;
        border-radius: {t.RADIUS_S}px;
    }}
    QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
        background: {p.subtle_hover};
    }}
    QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected {{
        background: {p.nav_selected};
        color: {C.TEXT};
        border-left: 3px solid {C.BLUE};
        margin-left: 4px;
    }}
"""
    progress = f"""
    QProgressBar {{
        background: {C.SURFACE1};
        border: none;
        border-radius: {t.RADIUS_XS}px;
        min-height: 4px;
        max-height: 4px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {C.BLUE};
        border-radius: {t.RADIUS_XS}px;
    }}
"""
    slider = f"""
    QSlider::groove:horizontal {{
        height: 4px;
        background: {C.SURFACE1};
        border-radius: {t.RADIUS_XS}px;
    }}
    QSlider::sub-page:horizontal {{
        background: {C.BLUE};
        border-radius: {t.RADIUS_XS}px;
    }}
    QSlider::handle:horizontal {{
        width: 20px;
        height: 20px;
        margin: -8px 0px;
        background: {C.BLUE};
        border: 4px solid {C.SURFACE0};
        border-radius: 10px;
    }}
    QSlider::groove:vertical {{
        width: 4px;
        background: {C.SURFACE1};
        border-radius: {t.RADIUS_XS}px;
    }}
    QSlider::sub-page:vertical {{
        background: {C.BLUE};
        border-radius: {t.RADIUS_XS}px;
    }}
    QSlider::handle:vertical {{
        width: 20px;
        height: 20px;
        margin: 0px -8px;
        background: {C.BLUE};
        border: 4px solid {C.SURFACE0};
        border-radius: 10px;
    }}
"""
    spin = f"""
    QSpinBox, QDoubleSpinBox {{
        background: {p.control_fill};
        color: {C.TEXT};
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 20px 0px 12px;
        font-size: {t.FONT_BODY}px;
        min-height: 32px;
        selection-background-color: {C.BLUE};
        selection-color: {C.CRUST};
    }}
    QSpinBox:hover, QDoubleSpinBox:hover {{
        background: {p.control_hover};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: {focus_ring};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 16px;
        border: none;
        background: transparent;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 16px;
        border: none;
        background: transparent;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({chevron_up});
        width: 12px;
        height: 8px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({chevron_down});
        width: 12px;
        height: 8px;
    }}
"""
    tab_bar = f"""
    QTabBar::tab {{
        background: transparent;
        color: {C.SUBTEXT1};
        border: none;
        min-height: 32px;
        padding: 0px 12px;
    }}
    QTabBar::tab:selected {{
        color: {C.TEXT};
        border-bottom: 3px solid {C.BLUE};
    }}
"""
    dialog = f"""
    QDialog, QMessageBox {{
        background: {C.MANTLE};
        color: {C.TEXT};
    }}
    QLabel {{
        background: transparent;
        color: {C.TEXT};
    }}
    QMessageBox QLabel {{
        font-size: {t.FONT_TITLE}px;
        font-weight: 600;
    }}
    QDialogButtonBox {{
        background: {p.control_fill};
        border-top: 1px solid {p.divider};
        padding: 24px;
    }}
    QDialogButtonBox QPushButton {{
        background: {p.control_fill};
        color: {C.TEXT};
        font-size: {t.FONT_BODY}px;
        font-weight: 400;
        {elevation}
        border-radius: {t.RADIUS_M}px;
        padding: 0px 16px;
        min-height: 32px;
        min-width: 96px;
    }}
    QDialogButtonBox QPushButton:hover {{
        background: {p.control_hover};
    }}
    QDialogButtonBox QPushButton:pressed {{
        background: {p.control_pressed};
        border-bottom: 1px solid {stroke_top};
    }}
    QDialogButtonBox QPushButton:default {{
        background: {C.BLUE};
        color: {C.CRUST};
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-bottom: 1px solid {accent_bottom};
    }}
    QDialogButtonBox QPushButton:focus {{
        border: {focus_ring};
    }}
{tooltip}
"""
    return {
        "CHECKBOX": checkbox,
        "RADIO": radio,
        "LIST_WIDGET": list_widget,
        "PROGRESS": progress,
        "SLIDER": slider,
        "SPIN_BOX": spin,
        "TAB_BAR": tab_bar,
        "DIALOG": dialog,
        "GLOBAL_CONTROLS": (
            tooltip + menu + checkbox + radio + list_widget + progress + slider + spin + tab_bar
        ),
    }
