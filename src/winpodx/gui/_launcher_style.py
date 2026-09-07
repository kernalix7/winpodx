# SPDX-License-Identifier: MIT
"""Fluent QSS helpers for the Start-menu launcher chrome."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QWidget

from winpodx.gui import theme
from winpodx.gui.icons import load_icon as load_chrome_icon

_AVATAR = 24


def launcher_root_qss() -> str:
    return (
        f"QFrame#launcherRoot {{ background: {theme.C.BASE}; "
        f"border: {theme.CARD_BORDER}; border-radius: {theme.RADIUS_L}px; }}"
    )


def pinned_heading_qss() -> str:
    return (
        f"background: transparent; color: {theme.C.TEXT}; "
        f"font-size: {theme.FONT_BODY}px; font-weight: 600;"
    )


def recommended_row_qss() -> str:
    return (
        f"QFrame#recommendedRow {{ background: transparent; border: none; "
        f"border-radius: {theme.RADIUS_M}px; }}"
        f"QFrame#recommendedRow:hover {{ background: {theme.C.SURFACE1}; }}"
        f"QFrame#recommendedRow:focus {{ border: {theme.FOCUS_RING}; }}"
    )


def bottom_bar_qss() -> str:
    return (
        f"QFrame#launcherBottomBar {{ background: transparent; "
        f"border-top: 1px solid {theme.C.OVERLAY0}; }}"
    )


def style_launcher_search(window: QWidget) -> None:
    box = getattr(window, "search_bar", None)
    if box is not None:
        box.setObjectName("navSearch")
        box.setStyleSheet(theme.NAV_SEARCH)
        box.setFixedHeight(36)


def restyle_launcher(window: QWidget, *, avatar: int = _AVATAR) -> None:
    """Re-apply Fluent QSS from ``theme.*`` after a scheme rebuild."""
    root = getattr(window, "_outer", None)
    if root is not None:
        root.setObjectName("launcherRoot")
        root.setStyleSheet(launcher_root_qss())
    style_launcher_search(window)
    for name in ("pinnedHeading", "recommendedHeading"):
        heading = window.findChild(QLabel, name)
        if heading is not None:
            heading.setStyleSheet(pinned_heading_qss())
    all_btn = getattr(window, "all_apps_button", None)
    if all_btn is not None:
        all_btn.setStyleSheet(theme.HYPERLINK_BTN)
    pill_bar = getattr(window, "_pill_bar", None)
    update_pills = getattr(pill_bar, "_update_style", None)
    if callable(update_pills):
        update_pills()
    brand = getattr(window, "_brand_label", None)
    if brand is not None:
        brand.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
    bottom = getattr(window, "_bottom_bar", None)
    if bottom is not None:
        bottom.setStyleSheet(bottom_bar_qss())
    glyphs = {"_winpodx_btn": "desktop", "_gear_btn": "gear"}
    for btn_name in ("_winpodx_btn", "_gear_btn"):
        btn = getattr(window, btn_name, None)
        if btn is not None:
            btn.setStyleSheet(theme.BTN_GHOST)
            btn.setIcon(load_chrome_icon(glyphs[btn_name], theme.C.TEXT, avatar))
    brand_icon = getattr(window, "_brand_icon", None)
    if brand_icon is not None:
        brand_icon.setPixmap(load_chrome_icon("home", theme.C.TEXT, avatar).pixmap(avatar, avatar))
    for frame in window.findChildren(QFrame):
        obj = frame.objectName()
        if obj == "startTile":
            frame.setStyleSheet(theme.START_TILE)
        elif obj == "recommendedRow":
            frame.setStyleSheet(recommended_row_qss())
        name_lbl = getattr(frame, "_name_label", None)
        set_ss = getattr(name_lbl, "setStyleSheet", None)
        if callable(set_ss):
            set_ss(f"color: {theme.C.TEXT}; background: transparent;")
        update = getattr(frame, "_update_style", None)
        if callable(update):
            update()
    for area in window.findChildren(QScrollArea):
        restyle = getattr(area, "restyle", None)
        if callable(restyle):
            restyle()
        update = getattr(area, "_update_style", None)
        if callable(update):
            update()
