# SPDX-License-Identifier: MIT
"""Category-chip labels and live Fluent QSS for the Applications segmented row."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu, QPushButton

from winpodx.core.i18n import tr
from winpodx.gui import theme

_MAX_CATEGORY_CHIPS = 8


def chip_label(name: str, count: int) -> str:
    return f"{name} {count}"


def chip_category(btn: QPushButton) -> str | None:
    getter = getattr(btn, "property", None)
    if not callable(getter):
        return None
    stored = getter("category")
    if stored is None:
        return None
    return str(stored)


def category_chip_qss() -> str:
    from winpodx.gui import theme as theme_mod

    return (
        theme_mod.FILTER_CHIP
        + f"""
    QPushButton:checked {{
        color: {theme_mod.C.TEXT};
        border-color: {theme_mod.C.BLUE};
        border-bottom: 2px solid {theme_mod.C.BLUE};
        background: {theme_mod.C.SURFACE0};
        font-weight: 500;
    }}
    """
    )


class LibraryChipsMixin:
    """Segmented category row with per-chip counts for the Applications page."""

    def _category_counts(self) -> tuple[int, dict[str, int]]:
        visible = self._visible_apps()
        counts: dict[str, int] = {}
        for app in visible:
            for cat in app.categories:
                counts[cat] = counts.get(cat, 0) + 1
        return len(visible), counts

    def _sync_category_chip_counts(self) -> None:
        n_all, counts = self._category_counts()
        more_btn = getattr(self, "_category_more_btn", None)
        for btn in getattr(self, "_category_btns", []):
            if btn is more_btn:
                continue
            stored = chip_category(btn)
            if stored is None:
                continue
            if stored == "":
                btn.setText(chip_label("All", n_all))
            else:
                btn.setText(chip_label(stored, counts.get(stored, 0)))

    def _build_category_chips(self) -> None:
        cats: set[str] = set()
        for app in self.apps:
            cats.update(app.categories)
        cats_sorted = sorted(cats)
        n_all, counts = self._category_counts()

        all_btn = QPushButton(chip_label("All", n_all))
        all_btn.setProperty("category", "")
        all_btn.setCheckable(True)
        all_btn.setChecked(True)
        all_btn.setStyleSheet(category_chip_qss())
        all_btn.clicked.connect(lambda: self._set_category(""))
        self._category_row.addWidget(all_btn)
        self._category_btns.append(all_btn)

        for cat in cats_sorted[:_MAX_CATEGORY_CHIPS]:
            btn = QPushButton(chip_label(cat, counts.get(cat, 0)))
            btn.setProperty("category", cat)
            btn.setCheckable(True)
            btn.setStyleSheet(category_chip_qss())
            btn.clicked.connect(lambda _, c=cat: self._set_category(c))
            self._category_row.addWidget(btn)
            self._category_btns.append(btn)

        overflow = cats_sorted[_MAX_CATEGORY_CHIPS:]
        if overflow:
            more_btn = QPushButton(tr("+{n} more").format(n=len(overflow)))
            more_btn.setCheckable(True)
            more_btn.setStyleSheet(category_chip_qss())
            more_btn.setToolTip(tr("More categories"))
            menu = QMenu(more_btn)
            menu.setStyleSheet(theme.GLOBAL_STYLE)
            for cat in overflow:
                menu.addAction(cat, lambda _=False, c=cat: self._set_category(c))
            more_btn.setMenu(menu)
            self._category_row.addWidget(more_btn)
            self._category_btns.append(more_btn)
            self._category_more_btn = more_btn
            self._overflow_categories = list(overflow)
        else:
            self._category_more_btn = None
            self._overflow_categories = []

        self._category_row.addStretch()

    def _set_category(self, category: str) -> None:
        self._active_category = category
        more_btn = getattr(self, "_category_more_btn", None)
        overflow = getattr(self, "_overflow_categories", [])
        for btn in self._category_btns:
            if btn is more_btn:
                btn.setChecked(category in overflow)
                continue
            stored = chip_category(btn)
            if stored is not None:
                btn.setChecked(stored == category)
            else:
                btn.setChecked((category == "" and btn.text() == "All") or btn.text() == category)
        self._filter_apps(self.search_box.text())
