# SPDX-License-Identifier: MIT
"""Dashboard \"Running\" live-session list, driven by the existing refresh timer."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr


class _DashboardSessionsMixin:
    """Compact live-session rows shown only while at least one session is open."""

    def _build_running_now(self) -> QFrame:
        from winpodx.gui import theme as theme_mod

        section = QFrame()
        section.setObjectName("runningNow")
        section.setStyleSheet(
            theme_mod.SETTINGS_CARD.replace("QFrame#settingsCard", "QFrame#runningNow")
        )
        lay = QVBoxLayout(section)
        lay.setContentsMargins(
            theme_mod.SPACE_L, theme_mod.SPACE_S, theme_mod.SPACE_L, theme_mod.SPACE_S
        )
        lay.setSpacing(theme_mod.SPACE_S)
        heading = QLabel(tr("Running"))
        heading.setObjectName("runningNowHeading")
        heading.setStyleSheet(
            f"color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_BODY}px; font-weight: 600;"
        )
        lay.addWidget(heading)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.setSpacing(theme_mod.SPACE_XS)
        wrap = QWidget()
        wrap.setLayout(holder)
        lay.addWidget(wrap)
        self._running_now_holder = holder
        self._running_now_section = section
        self._refresh_running_now()
        return section

    def _running_now_title(self, stem: str) -> str:
        namer = getattr(self, "_running_display_name", None)
        if callable(namer):
            return namer(stem)
        for app in getattr(self, "apps", []):
            if app.name == stem:
                return app.full_name
        return stem

    def _refresh_running_now(self) -> None:
        holder = getattr(self, "_running_now_holder", None)
        section = getattr(self, "_running_now_section", None)
        if holder is None or section is None:
            return
        clearer = getattr(self, "_clear_holder", None)
        if callable(clearer):
            clearer(holder)
        else:
            while holder.count():
                item = holder.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
        try:
            from winpodx.core.process import list_active_sessions

            sessions = list_active_sessions()
        except Exception:  # noqa: BLE001 -- dashboard sessions are best-effort
            sessions = []
        section.setVisible(bool(sessions))
        for session in sessions:
            holder.addWidget(
                self._make_running_now_row(session.app_name, getattr(session, "pid", 0))
            )

    def _make_running_now_row(self, app_name: str, pid: int) -> QFrame:
        from winpodx.gui import theme as theme_mod

        row = QFrame()
        row.setObjectName("runningNowRow")
        row.setFixedHeight(48)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme_mod.SPACE_S)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        title = QLabel(self._running_now_title(app_name))
        title.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.TEXT}; "
            f"font-size: {theme_mod.FONT_BODY}px;"
        )
        names.addWidget(title)
        caption = QLabel(tr("PID {pid}").format(pid=pid))
        caption.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.SUBTEXT0}; "
            f"font-size: {theme_mod.FONT_CAPTION}px;"
        )
        names.addWidget(caption)
        lay.addLayout(names, 1)
        btn = QPushButton(tr("Terminate"))
        btn.setStyleSheet(theme_mod.BTN_SECONDARY)
        btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _checked=False, n=app_name: self._terminate_running_now(n))
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _terminate_running_now(self, app_name: str) -> None:
        try:
            from winpodx.core.process import kill_session

            kill_session(app_name)
        except Exception:  # noqa: BLE001 -- terminate is best-effort
            pass
        self._refresh_running_now()
        populate = getattr(self, "_populate_workspace", None)
        if callable(populate):
            populate()
