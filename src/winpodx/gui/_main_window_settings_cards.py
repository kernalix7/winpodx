# SPDX-License-Identifier: MIT
"""Windows 11 Settings group/card builders for ``SettingsPageMixin``."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui._settings_card import _card_qss, make_settings_card, make_settings_group_parts
from winpodx.gui._widget_helpers import mark_fluid_wrap
from winpodx.gui.icons import load_icon


def _heading_qss() -> str:
    from winpodx.gui import theme as t

    return (
        f"background: transparent; color: {t.C.TEXT}; "
        f"font-size: {t.FONT_SUBHEAD}px; font-weight: 600;"
    )


def _caption_qss() -> str:
    from winpodx.gui import theme as t

    return (
        f"background: transparent; color: {t.C.SUBTEXT1}; "
        f"font-size: {t.FONT_CAPTION}px; font-weight: 400;"
    )


def _style_settings_field(widget: QWidget) -> None:
    """Size a field control for a Win11 SettingsCard action slot."""
    from winpodx.gui import theme as t

    if isinstance(widget, QLineEdit):
        widget.setMinimumWidth(240)
        widget.setMaximumWidth(360)
        widget.setStyleSheet(t.INPUT)
        widget.setFixedHeight(t.CONTROL_HEIGHT)
    elif isinstance(widget, QComboBox):
        widget.setMinimumWidth(240)
        widget.setMaximumWidth(360)
        widget.setStyleSheet(t.COMBO)
        widget.setFixedHeight(t.CONTROL_HEIGHT)
    elif isinstance(widget, QCheckBox):
        widget.setText("")
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setMinimumHeight(t.HIT_TARGET)
        widget.setStyleSheet("")
        widget.update()
    elif isinstance(widget, QSpinBox):
        widget.setMinimumHeight(t.CONTROL_HEIGHT)
        widget.setStyleSheet(t.SPIN_BOX)
    elif isinstance(widget, QSlider):
        widget.setMinimumHeight(t.CONTROL_HEIGHT)
        widget.setStyleSheet(t.SLIDER)


def _add_group_chrome(
    layout: QVBoxLayout,
    icon_name: str | None,
    heading_text: str,
    subtitle: str,
) -> None:
    from winpodx.gui import theme as t

    insert_at = max(layout.count() - 1, 0)
    heading = QLabel(heading_text)
    heading.setObjectName("settingsGroupHeading")
    heading.setStyleSheet(_heading_qss())
    if icon_name:
        row_host = QWidget()
        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.SPACE_S)
        icon_lbl = QLabel()
        icon_lbl.setObjectName("settingsGroupIcon")
        icon_lbl.setProperty("iconName", icon_name)
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setPixmap(load_icon(icon_name, t.C.TEXT, 16).pixmap(16, 16))
        icon_lbl.setStyleSheet("background: transparent;")
        row.addWidget(icon_lbl)
        row.addWidget(heading)
        row.addStretch()
        layout.insertWidget(insert_at, row_host)
        insert_at += 1
    else:
        layout.insertWidget(insert_at, heading)
        insert_at += 1
    if subtitle:
        caption = QLabel(subtitle)
        caption.setObjectName("settingsGroupCaption")
        caption.setWordWrap(True)
        mark_fluid_wrap(caption)
        caption.setStyleSheet(_caption_qss())
        layout.insertWidget(insert_at, caption)


def _first_sentence(tooltip: str) -> str:
    flat = " ".join(line.strip() for line in tooltip.splitlines() if line.strip())
    head, sep, _rest = flat.partition(". ")
    return head + sep.strip() if sep else flat


def _new_settings_group() -> tuple[QFrame, QVBoxLayout, QVBoxLayout]:
    return make_settings_group_parts()


class SettingsCardsMixin:
    """Win11 SettingsCard group builders + scheme restyle."""

    @staticmethod
    def _split_settings_title_icon(title: str) -> tuple[str | None, str]:
        """Return the in-house icon name and display title for old glyph-prefixed labels."""
        prefixes = {
            "▣  ": "rdp",
            "▨  ": "hardware",
            "◨  ": "performance",
            "🌐  ": "globe",
        }
        for prefix, icon_name in prefixes.items():
            if title.startswith(prefix):
                return icon_name, title[len(prefix) :]
        return None, title

    def _remember_settings_group(self, group: QFrame) -> None:
        groups = getattr(self, "_settings_groups", None)
        if groups is None:
            self._settings_groups = []
            groups = self._settings_groups
        groups.append(group)

    def _settings_action_row(self, title: str, widget: QWidget) -> QFrame:
        """Wrap a free-form control as a SettingsCard action row."""
        _style_settings_field(widget)
        return make_settings_card("", title, action=widget)

    def _settings_card(
        self,
        title: str,
        subtitle: str,
        fields: list[tuple[str, QWidget]],
    ) -> QFrame:
        """Build a settings group: heading + one SettingsCard row per field."""
        group, chrome, stack = _new_settings_group()
        icon_name, heading_text = self._split_settings_title_icon(title)
        _add_group_chrome(chrome, icon_name, heading_text, subtitle)
        for label, widget in fields:
            _style_settings_field(widget)
            stack.addWidget(
                make_settings_card("", label, _first_sentence(widget.toolTip()), action=widget)
            )
        self._remember_settings_group(group)
        return group

    def _settings_card_shell(
        self,
        icon_name: str,
        title: str,
        subtitle: str,
    ) -> tuple[QFrame, QVBoxLayout]:
        """Build a settings group whose body is one SettingsCard surface."""
        from winpodx.gui import theme as t

        group, chrome, stack = _new_settings_group()
        _, heading_text = self._split_settings_title_icon(title)
        _add_group_chrome(chrome, icon_name, heading_text, subtitle)
        body = QFrame()
        body.setObjectName("settingsCard")
        body.setStyleSheet(t.SETTINGS_CARD)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(t.SPACE_L, t.SPACE_L, t.SPACE_L, t.SPACE_L)
        body_layout.setSpacing(t.SPACE_M)
        stack.addWidget(body)
        self._remember_settings_group(group)
        return group, body_layout

    def _restyle_settings(self) -> None:
        """Re-apply group/card/heading QSS after ``theme.rebuild``."""
        from winpodx.gui import theme as t

        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None:
            scroll.setStyleSheet(t.SCROLL_AREA)
        save_btn = getattr(self, "_settings_save_btn", None)
        if save_btn is not None:
            save_btn.setStyleSheet(t.BTN_PRIMARY)
            save_btn.setMinimumHeight(t.CONTROL_HEIGHT)
            if save_btn.property("dirty"):
                self._mark_settings_dirty()
        for attr, qss in (
            ("_btn_enable_updates", t.BTN_PRIMARY),
            ("_btn_disable_updates", t.BTN_DANGER),
            ("_btn_retry_updates", t.BTN_SECONDARY),
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setStyleSheet(qss)
                btn.setMinimumHeight(t.CONTROL_HEIGHT)
        summary = getattr(self, "budget_summary_label", None)
        if summary is not None:
            summary.setStyleSheet(
                f"color: {t.C.SUBTEXT0}; background: transparent; font-size: {t.FONT_CAPTION}px;"
            )
        warning = getattr(self, "budget_warning_label", None)
        if warning is not None:
            warning.setStyleSheet(
                f"color: {t.C.YELLOW}; background: transparent; "
                f"font-size: {t.FONT_CAPTION}px; padding: {t.SPACE_XS}px {t.SPACE_S}px;"
            )
        tuning = getattr(self, "tuning_summary_label", None)
        if tuning is not None:
            tuning.setStyleSheet(
                f"background: transparent; font-family: 'JetBrainsMono Nerd Font', "
                f"'Cascadia Code', 'Fira Code', monospace; "
                f"font-size: {t.FONT_CAPTION}px; color: {t.C.SUBTEXT1};"
            )

        seen: set[int] = set()
        roots: list[QWidget] = []
        page = getattr(self, "_settings_page", None)
        if page is not None:
            roots.append(page)
        roots.extend(getattr(self, "_settings_groups", []))
        for root in roots:
            rid = id(root)
            if rid in seen:
                continue
            seen.add(rid)
            self._restyle_settings_tree(root)

    def _restyle_settings_tree(self, root: QWidget) -> None:
        from winpodx.gui import theme as t

        for heading in root.findChildren(QLabel, "settingsGroupHeading"):
            heading.setStyleSheet(_heading_qss())
        for caption in root.findChildren(QLabel, "settingsGroupCaption"):
            caption.setStyleSheet(_caption_qss())
        for icon_lbl in root.findChildren(QLabel, "settingsGroupIcon"):
            name = icon_lbl.property("iconName")
            if name:
                icon_lbl.setPixmap(load_icon(name, t.C.TEXT, 16).pixmap(16, 16))
        for card in root.findChildren(QFrame, "settingsCard"):
            card.setStyleSheet(_card_qss(card.objectName()))
            card.setGraphicsEffect(None)
            title_lbl = getattr(card, "title_label", None)
            if title_lbl is not None:
                title_lbl.setStyleSheet(
                    f"background: transparent; color: {t.C.TEXT}; "
                    f"font-size: {t.FONT_BODY}px; font-weight: 400;"
                )
            desc_lbl = getattr(card, "desc_label", None)
            if desc_lbl is not None:
                desc_lbl.setStyleSheet(
                    f"background: transparent; color: {t.C.SUBTEXT1}; "
                    f"font-size: {t.FONT_CAPTION}px; font-weight: 400;"
                )
            action = getattr(card, "action_widget", None)
            if action is not None:
                _style_settings_field(action)
        for edit in root.findChildren(QLineEdit):
            edit.setStyleSheet(t.INPUT)
            edit.setFixedHeight(t.CONTROL_HEIGHT)
        for combo in root.findChildren(QComboBox):
            combo.setStyleSheet(t.COMBO)
            combo.setFixedHeight(t.CONTROL_HEIGHT)
        for box in root.findChildren(QCheckBox):
            box.update()
        for spin in root.findChildren(QSpinBox):
            spin.setStyleSheet(t.SPIN_BOX)
        for slider in root.findChildren(QSlider):
            slider.setStyleSheet(t.SLIDER)
        for btn in root.findChildren(QPushButton):
            btn.setMinimumHeight(t.CONTROL_HEIGHT)
            btn.setMaximumHeight(t.CONTROL_HEIGHT)
