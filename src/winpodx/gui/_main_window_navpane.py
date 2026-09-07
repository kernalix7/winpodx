# SPDX-License-Identifier: MIT
"""Windows 11 NavigationView pane mixin for ``WinpodxWindow``.

Builds the left nav pane (profile, search, items, accent pill, pod
controls), the hidden shell page header, compact-rail reflow, and
``_restyle_shell`` so scheme changes re-apply Fluent tokens.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_navpane_toggle import NavToggleMixin
from winpodx.gui._pod_state_chrome import DOT, PILL_H, MiniChip, pod_state_color
from winpodx.gui.icons import load_icon

NAV_COMPACT_BELOW = 1100
_NAV_ICON = 16
_AVATAR = 64
_INDICATOR_H = 16
_ANIM_MS = 150

_NAV_ITEMS: tuple[tuple[str, int, str], ...] = (
    ("Dashboard", 0, "home"),
    ("Applications", 1, "grid"),
    ("Settings", 2, "gear"),
    ("Tools", 3, "clean"),
    ("Terminal / Logs", 4, "prompt"),
    ("Info", 5, "pending"),
    ("Devices", 6, "hardware"),
    ("License", 7, "diamond"),
)


def _app_icon_pixmap(size: int) -> QPixmap | None:
    """Render the bundled app icon into a round ``size`` px pixmap."""
    from winpodx.desktop.icons import bundled_data_path

    icon_path = bundled_data_path("winpodx-icon.svg")
    if icon_path is None:
        return None
    renderer = QSvgRenderer(str(icon_path))
    square = QPixmap(size, size)
    square.fill(Qt.GlobalColor.transparent)
    painter = QPainter(square)
    ds = renderer.defaultSize()
    if ds.width() > 0 and ds.height() > 0:
        scale = min(size / ds.width(), size / ds.height())
        w, h = ds.width() * scale, ds.height() * scale
        renderer.render(painter, QRectF((size - w) / 2, (size - h) / 2, w, h))
    else:
        renderer.render(painter)
    painter.end()

    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    clip = QPainter(rounded)
    clip.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    clip.setClipPath(path)
    clip.drawPixmap(0, 0, square)
    clip.end()
    return rounded


class NavPaneMixin(NavToggleMixin):
    """Windows 11 Settings-style NavigationView chrome."""

    def _build_sidebar(self) -> QFrame:
        pane = QFrame()
        pane.setObjectName("navPane")
        pane.setFixedWidth(theme.NAV_PANE_WIDTH)
        pane.setStyleSheet(theme.NAV_PANE)
        self.sidebar = pane
        self._nav_pane = pane
        self._nav_is_compact = False

        layout = QVBoxLayout(pane)
        layout.setContentsMargins(theme.SPACE_S, theme.SPACE_L, theme.SPACE_S, theme.SPACE_M)
        layout.setSpacing(2)

        layout.addWidget(self._build_nav_toggle(), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._build_profile_block())
        layout.addWidget(self._build_nav_search())
        self._build_nav_items(layout)
        layout.addStretch()
        layout.addWidget(self._build_pod_controls())

        self.nav_indicator = QFrame(pane)
        self.nav_indicator.setObjectName("navIndicator")
        self.nav_indicator.setFixedSize(theme.NAV_INDICATOR, _INDICATOR_H)
        self.nav_indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._nav_indicator_anim = QPropertyAnimation(self.nav_indicator, b"pos", pane)
        self._nav_indicator_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.page_header = self._build_page_header()
        if isinstance(self, QWidget):
            self.page_header.setParent(self)
        self.page_header.hide()
        self._restyle_shell()
        self._move_nav_indicator(0)
        return pane

    def _build_profile_block(self) -> QPushButton:
        logo = QPushButton()
        logo.setObjectName("logoHomeButton")
        logo.setToolTip(tr("Dashboard"))
        logo.setAccessibleName(tr("Dashboard"))
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.setFlat(True)
        logo.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        logo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        logo.setMinimumHeight(_AVATAR + 2 * theme.SPACE_M)
        logo.clicked.connect(lambda: self._switch_page(0))
        row = QHBoxLayout(logo)
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_M, theme.SPACE_S, theme.SPACE_M)
        row.setSpacing(theme.SPACE_M)

        avatar = QLabel()
        avatar.setFixedSize(_AVATAR, _AVATAR)
        avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        pixmap = _app_icon_pixmap(_AVATAR)
        if pixmap is not None:
            avatar.setPixmap(pixmap)
        row.addWidget(avatar)

        text_col = QWidget()
        text_col.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_l = QVBoxLayout(text_col)
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(theme.SPACE_XS)
        title = QLabel("WinPodX")
        title.setObjectName("profileTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_l.addWidget(title)
        status = QWidget()
        status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        status_l = QHBoxLayout(status)
        status_l.setContentsMargins(0, 0, 0, 0)
        status_l.setSpacing(theme.SPACE_XS)
        self._ensure_pod_status(status)
        text_l.addWidget(status)
        row.addWidget(text_col, 1)

        self._profile_button = logo
        self._profile_avatar = avatar
        self._profile_text = text_col
        self._profile_title = title
        return logo

    def _build_nav_search(self) -> QLineEdit:
        box = QLineEdit()
        box.setObjectName("navSearch")
        box.setPlaceholderText(tr("Search apps..."))
        box.setClearButtonEnabled(True)
        box.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        box.textChanged.connect(self._on_nav_search)
        self.nav_search = box
        return box

    def _build_nav_items(self, layout: QVBoxLayout) -> None:
        self.nav_buttons: list[QPushButton] = []
        self._nav_labels: list[str] = []
        self._nav_icon_names: list[str] = []
        for key, idx, icon_name in _NAV_ITEMS:
            label = tr(key)
            btn = QPushButton(label)
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(load_icon(icon_name, theme.C.SUBTEXT1, _NAV_ICON))
            btn.setIconSize(QSize(_NAV_ICON, _NAV_ICON))
            btn.setMinimumHeight(theme.NAV_ITEM_HEIGHT)
            btn.setToolTip(label)
            btn.setAccessibleName(label)
            btn.clicked.connect(lambda _checked=False, i=idx: self._switch_page(i))
            self.nav_buttons.append(btn)
            self._nav_labels.append(label)
            self._nav_icon_names.append(icon_name)
            layout.addWidget(btn)
            if idx == 2:
                layout.addSpacing(theme.SPACE_S)
                divider = QFrame()
                divider.setObjectName("navGroupDivider")
                divider.setFixedHeight(1)
                self._nav_group_divider = divider
                layout.addWidget(divider)
                layout.addSpacing(theme.SPACE_S)
        self.nav_buttons[0].setChecked(True)

    def _build_pod_controls(self) -> QWidget:
        footer = QWidget()
        col = QVBoxLayout(footer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.SPACE_XS)
        divider = QFrame()
        divider.setObjectName("navFooterDivider")
        divider.setFixedHeight(1)
        self._nav_footer_divider = divider
        col.addWidget(divider)
        wrap = QWidget()
        box = QBoxLayout(QBoxLayout.Direction.LeftToRight, wrap)
        box.setContentsMargins(0, theme.SPACE_XS, 0, 0)
        box.setSpacing(theme.SPACE_XS)
        self._pod_ctrl_layout = box
        self._ensure_pod_buttons(wrap)
        col.addWidget(wrap)
        return footer

    def _build_page_header(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("pageHeader")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(
            theme.PAGE_MARGIN_X,
            theme.PAGE_MARGIN_TOP,
            theme.PAGE_MARGIN_X,
            0,
        )
        row.setSpacing(theme.SPACE_L)

        text = QWidget()
        text_l = QVBoxLayout(text)
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(theme.SPACE_XS)
        title = QLabel(tr("Dashboard"))
        title.setObjectName("pageTitle")
        text_l.addWidget(title)
        subtitle = QLabel()
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        subtitle.hide()
        text_l.addWidget(subtitle)
        row.addWidget(text, 1, Qt.AlignmentFlag.AlignTop)

        actions = QWidget()
        actions.setObjectName("pageActions")
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(theme.SPACE_S)
        actions_l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        row.addWidget(actions, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self.page_header_title = title
        self.page_subtitle = subtitle
        self.page_actions = actions
        self._page_meta: dict[int, tuple[str, str, QWidget | None]] = {}
        self._page_actions_current: QWidget | None = None
        return wrap

    def _register_page_header(
        self,
        index: int,
        title: str,
        subtitle: str = "",
        actions: QWidget | None = None,
    ) -> None:
        """Record the pinned shell header for page ``index``."""
        meta = getattr(self, "_page_meta", None)
        if meta is None:
            self._page_meta = {}
            meta = self._page_meta
        meta[index] = (title, subtitle, actions)

    def _ensure_pod_status(self, parent: QWidget) -> None:
        if getattr(self, "pod_dot", None) is not None:
            return
        layout = parent.layout()
        if layout is None:
            layout = QHBoxLayout(parent)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(theme.SPACE_XS)

        pill = QFrame()
        pill.setObjectName("podStatePill")
        pill.setFixedHeight(PILL_H)
        pill_l = QHBoxLayout(pill)
        pill_l.setContentsMargins(theme.SPACE_S, 0, theme.SPACE_S, 0)
        pill_l.setSpacing(theme.SPACE_XS)

        self.pod_dot = QLabel()
        self.pod_dot.setFixedSize(10, 10)
        self.pod_dot.setPixmap(load_icon("dot", theme.C.SUBTEXT0, 10).pixmap(10, 10))
        self.pod_dot.setToolTip(tr("Pod state"))
        self.pod_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        pill_l.addWidget(self.pod_dot)

        self.pod_label = QLabel(tr("checking"))
        self.pod_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        pill_l.addWidget(self.pod_label)
        self._pod_state_pill = pill
        layout.addWidget(pill)

        self.agent_dot = MiniChip("A")
        self.agent_dot.setToolTip(tr("Guest agent (HTTP /health) — probing…"))
        self.agent_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.agent_dot)

        self.rdp_dot = MiniChip("R")
        self.rdp_dot.setToolTip(tr("RDP port (3390) — probing…"))
        self.rdp_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.rdp_dot)
        layout.addStretch(1)

    def _ensure_pod_buttons(self, parent: QWidget) -> None:
        if getattr(self, "btn_start", None) is not None:
            return
        layout = parent.layout()
        if layout is None:
            layout = QHBoxLayout(parent)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(theme.SPACE_XS)

        self.btn_start = QPushButton(tr("Start Pod"))
        self.btn_start.setIcon(load_icon("play", theme.C.TEXT, _NAV_ICON))
        self.btn_start.setIconSize(QSize(_NAV_ICON, _NAV_ICON))
        self.btn_start.setToolTip(tr("Start Pod"))
        self.btn_start.setAccessibleName(tr("Start Pod"))
        self.btn_start.setFixedHeight(theme.NAV_ITEM_HEIGHT)
        self.btn_start.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_start.setObjectName("navFooterItem")
        self.btn_start.setStyleSheet(theme.NAV_ITEM.replace("#navItem", "#navFooterItem"))
        self.btn_start.clicked.connect(self._on_start_pod)
        layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton(tr("Stop Pod"))
        self.btn_stop.setIcon(load_icon("stop", theme.C.TEXT, _NAV_ICON))
        self.btn_stop.setIconSize(QSize(_NAV_ICON, _NAV_ICON))
        self.btn_stop.setToolTip(tr("Stop Pod"))
        self.btn_stop.setAccessibleName(tr("Stop Pod"))
        self.btn_stop.setFixedHeight(theme.NAV_ITEM_HEIGHT)
        self.btn_stop.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_stop.setObjectName("navFooterItem")
        self.btn_stop.setStyleSheet(theme.NAV_ITEM.replace("#navItem", "#navFooterItem"))
        self.btn_stop.clicked.connect(self._on_stop_pod)
        layout.addWidget(self.btn_stop)

    def _ensure_pod_chrome(self, parent: QWidget) -> None:
        """Create pod status + start/stop widgets if the sidebar has not."""
        self._ensure_pod_status(parent)
        self._ensure_pod_buttons(parent)

    def _sync_pod_pill(self, state: str) -> None:
        """Paint ``QFrame#podStatePill`` from a pod-state word."""
        pill = getattr(self, "_pod_state_pill", None)
        if pill is None:
            return
        color = pod_state_color(state)
        pill.setStyleSheet(
            f"QFrame#podStatePill {{ background: {theme.rgba(color, 0.16)}; "
            f"border: none; border-radius: {PILL_H // 2}px; }}"
        )
        pod_dot = getattr(self, "pod_dot", None)
        if pod_dot is not None:
            pod_dot.setFixedSize(DOT, DOT)
            pod_dot.setPixmap(load_icon("dot", color, DOT).pixmap(DOT, DOT))
        self._sync_pod_footer(state)

    def _sync_pod_footer(self, state: str) -> None:
        """Show Start when stopped/unknown; Stop when running/paused/checking."""
        start = getattr(self, "btn_start", None)
        stop = getattr(self, "btn_stop", None)
        if start is None or stop is None:
            return
        show_stop = state in {"running", "paused", "checking"}
        start.setVisible(not show_stop)
        stop.setVisible(show_stop)
        start.setEnabled(not show_stop)
        stop.setEnabled(show_stop)

    def _on_nav_search(self, text: str) -> None:
        if getattr(self, "_nav_search_syncing", False):
            return
        self._nav_search_syncing = True
        try:
            self._switch_page(1)
            box = getattr(self, "search_box", None)
            if box is not None and box.text() != text:
                box.setText(text)
        finally:
            self._nav_search_syncing = False

    def _update_page_header(self, index: int) -> None:
        meta = getattr(self, "_page_meta", {}).get(index)
        if meta is None:
            labels = getattr(self, "_nav_labels", ())
            if not labels or not (0 <= index < len(labels)):
                return
            title, subtitle, actions = labels[index], "", None
        else:
            title, subtitle, actions = meta
        header_title = getattr(self, "page_header_title", None)
        if header_title is not None:
            header_title.setText(title)
        subtitle_lbl = getattr(self, "page_subtitle", None)
        if subtitle_lbl is not None:
            subtitle_lbl.setText(subtitle)
            subtitle_lbl.setVisible(bool(subtitle))
        self._swap_page_actions(actions)

    def _swap_page_actions(self, actions: QWidget | None) -> None:
        slot = getattr(self, "page_actions", None)
        if slot is None:
            return
        layout = slot.layout()
        if layout is None:
            return
        current = getattr(self, "_page_actions_current", None)
        if current is actions:
            if current is not None:
                current.show()
            return
        if current is not None:
            layout.removeWidget(current)
            current.hide()
            current.setParent(self.page_header)
        self._page_actions_current = actions
        if actions is not None:
            actions.setParent(slot)
            layout.addWidget(actions)
            actions.show()

    def _nav_indicator_pos(self, btn: QPushButton) -> QPoint:
        indicator = getattr(self, "nav_indicator", None)
        parent = indicator.parentWidget() if indicator is not None else None
        mapped = btn.mapTo(parent, QPoint(0, 0)) if parent is not None else QPoint(btn.x(), btn.y())
        y = mapped.y() + max((btn.height() - _INDICATOR_H) // 2, 0)
        return QPoint(theme.SPACE_XS, y)

    def _move_nav_indicator(self, index: int, *, animate: bool = True) -> None:
        indicator = getattr(self, "nav_indicator", None)
        buttons = getattr(self, "nav_buttons", None)
        anim = getattr(self, "_nav_indicator_anim", None)
        if indicator is None or not buttons or anim is None:
            return
        if not (0 <= index < len(buttons)):
            return
        target = self._nav_indicator_pos(buttons[index])
        anim.stop()
        anim.setStartValue(indicator.pos())
        anim.setEndValue(target)
        anim.setDuration(_ANIM_MS)
        visible_fn = getattr(self, "isVisible", None)
        host_visible = bool(visible_fn()) if callable(visible_fn) else False
        indicator.show()
        indicator.raise_()
        if not animate or not indicator.isVisible() or not host_visible:
            indicator.move(target)
            return
        anim.start()

    def _sync_nav_indicator(self, *, animate: bool = False) -> None:
        checked = next(
            (i for i, btn in enumerate(getattr(self, "nav_buttons", ())) if btn.isChecked()),
            0,
        )
        self._move_nav_indicator(checked, animate=animate)
        indicator = getattr(self, "nav_indicator", None)
        if indicator is not None:
            indicator.show()
            indicator.raise_()

    def _schedule_nav_indicator_sync(self) -> None:
        QTimer.singleShot(0, lambda: self._sync_nav_indicator(animate=False))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_nav_indicator(animate=False)

    def _apply_nav_pane_width(self, pane: QFrame, pane_w: int) -> None:
        pane.setStyleSheet(
            theme.NAV_PANE + f"\nQFrame#navPane {{ min-width: {pane_w}px; max-width: {pane_w}px; "
            f"border-right: none; }}"
        )
        pane.setFixedWidth(pane_w)

    def _set_nav_compact_mode(self, compact: bool) -> None:
        pane = getattr(self, "sidebar", None) or getattr(self, "_nav_pane", None)
        if pane is None:
            return
        pane_w = theme.NAV_PANE_COMPACT if compact else theme.NAV_PANE_WIDTH
        if getattr(self, "_nav_is_compact", None) is compact and pane.width() == pane_w:
            return
        self._apply_nav_pane_width(pane, pane_w)
        self._nav_is_compact = compact
        inner = max(pane_w - 2 * theme.SPACE_S, 32)
        max_w = inner if compact else 16777215
        search = getattr(self, "nav_search", None)
        if search is not None:
            search.setVisible(not compact)
            search.setMaximumWidth(max_w)
        text = getattr(self, "_profile_text", None)
        if text is not None:
            text.setVisible(not compact)
        self._sync_nav_compact_chrome(compact)
        logo = getattr(self, "_profile_button", None)
        if logo is not None:
            logo.setMaximumWidth(max_w)
        labels = getattr(self, "_nav_labels", ())
        for btn, label in zip(getattr(self, "nav_buttons", ()), labels):
            btn.setToolTip(label)
            btn.setText("" if compact else label)
            btn.setMaximumWidth(max_w)
        ctrl = getattr(self, "_pod_ctrl_layout", None)
        if ctrl is not None:
            direction = (
                QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
            )
            ctrl.setDirection(direction)
        start = getattr(self, "btn_start", None)
        if start is not None:
            start.setText("" if compact else tr("Start Pod"))
        stop = getattr(self, "btn_stop", None)
        if stop is not None:
            stop.setText("" if compact else tr("Stop Pod"))
        if compact:
            pane.setMask(QRegion(0, 0, pane_w, max(pane.height(), 4096)))
        else:
            pane.clearMask()
        layout = pane.layout()
        if layout is not None:
            layout.activate()
        self._sync_nav_indicator(animate=False)
        self._schedule_nav_indicator_sync()

    def _apply_nav_compact(self) -> None:
        pane = getattr(self, "sidebar", None) or getattr(self, "_nav_pane", None)
        if pane is None:
            return
        width_fn = getattr(self, "width", None)
        width = width_fn() if callable(width_fn) else theme.NAV_PANE_WIDTH
        self._set_nav_compact_mode(self._nav_wants_compact(width, NAV_COMPACT_BELOW))

    def _restyle_shell(self) -> None:
        """Re-apply Fluent QSS / inline colours after ``theme.rebuild``."""
        central_fn = getattr(self, "centralWidget", None)
        if callable(central_fn):
            central = central_fn()
            if central is not None:
                central.setStyleSheet(
                    f"QWidget#centralRoot {{ background: {theme.nav_pane_color()}; }}\n"
                    f"QWidget#contentLayer {{ background: {theme.C.BASE}; "
                    f"border-top-left-radius: {theme.RADIUS_L}px; }}\n" + theme.GLOBAL_STYLE
                )
        title_bar = getattr(self, "title_bar", None)
        if title_bar is not None:
            title_bar.restyle()
        pane = getattr(self, "sidebar", None) or getattr(self, "_nav_pane", None)
        if pane is not None:
            compact = getattr(self, "_nav_is_compact", False)
            pane_w = theme.NAV_PANE_COMPACT if compact else theme.NAV_PANE_WIDTH
            self._apply_nav_pane_width(pane, pane_w)
        search = getattr(self, "nav_search", None)
        if search is not None:
            search.setStyleSheet(theme.NAV_SEARCH)
        self._restyle_nav_toggle()
        for btn, icon_name in zip(
            getattr(self, "nav_buttons", ()), getattr(self, "_nav_icon_names", ())
        ):
            btn.setIcon(load_icon(icon_name, theme.C.SUBTEXT1, _NAV_ICON))
            btn.setStyleSheet(theme.NAV_ITEM)
        indicator = getattr(self, "nav_indicator", None)
        if indicator is not None:
            indicator.setStyleSheet(
                f"QFrame#navIndicator {{ background: {theme.C.BLUE}; border: none; "
                f"border-radius: 1px; }}"
            )
        title = getattr(self, "_profile_title", None)
        if title is not None:
            title.setStyleSheet(
                f"color: {theme.C.TEXT}; font-size: {theme.FONT_TITLE}px; font-weight: 600;"
            )
        logo = getattr(self, "_profile_button", None)
        if logo is not None:
            logo.setStyleSheet(
                f"QPushButton#logoHomeButton {{ background: transparent; border: none; "
                f"border-radius: {theme.RADIUS_M}px; text-align: left; }}"
                f"QPushButton#logoHomeButton:hover {{ background: {theme.C.SURFACE0}; }}"
            )
        pod_label = getattr(self, "pod_label", None)
        if pod_label is not None:
            pod_label.setStyleSheet(
                f"background: transparent; color: {theme.C.SUBTEXT1}; "
                f"font-size: {theme.FONT_CAPTION}px;"
            )
        pod_dot = getattr(self, "pod_dot", None)
        if pod_dot is not None:
            pod_dot.setPixmap(load_icon("dot", theme.C.SUBTEXT0, 10).pixmap(10, 10))
        for name in ("agent_dot", "rdp_dot"):
            dot = getattr(self, name, None)
            if dot is not None:
                dot.setStyleSheet(
                    f"background: transparent; color: {theme.C.SUBTEXT0}; "
                    f"font-size: {theme.FONT_CAPTION}px; font-weight: 600;"
                )
        for btn, icon_name in (
            (getattr(self, "btn_start", None), "play"),
            (getattr(self, "btn_stop", None), "stop"),
        ):
            if btn is not None:
                btn.setIcon(load_icon(icon_name, theme.C.SUBTEXT1, _NAV_ICON))
                btn.setStyleSheet(theme.NAV_ITEM.replace("#navItem", "#navFooterItem"))
        divider = getattr(self, "_nav_footer_divider", None)
        if divider is not None:
            divider.setStyleSheet(f"background: {theme.C.SURFACE1}; border: none;")
        group_div = getattr(self, "_nav_group_divider", None)
        if group_div is not None:
            group_div.setStyleSheet(f"background: {theme.C.SURFACE1}; border: none;")
        header_title = getattr(self, "page_header_title", None)
        if header_title is not None:
            header_title.setStyleSheet(
                f"background: transparent; color: {theme.C.TEXT}; "
                f"font-size: {theme.FONT_HERO}px; font-weight: 600;"
            )
        subtitle = getattr(self, "page_subtitle", None)
        if subtitle is not None:
            subtitle.setStyleSheet(
                f"background: transparent; color: {theme.C.SUBTEXT1}; "
                f"font-size: {theme.FONT_CAPTION}px; font-weight: 400;"
            )
        self._sync_pod_pill(getattr(self, "_pod_state", "checking"))
