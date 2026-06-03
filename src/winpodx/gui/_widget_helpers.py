# SPDX-License-Identifier: MIT
"""Pure widget-construction helpers extracted from main_window.py.

These functions have no dependency on ``WinpodxWindow`` state — they take
a target widget or an ``AppInfo`` and return a configured Qt widget. They
were ``@staticmethod`` on ``WinpodxWindow`` historically; pulling them
into a module-level home keeps ``main_window.py`` focused on orchestration
and lets the helpers be reused from future GUI screens without dragging
the main-window class along.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.gui.theme import (
    BTN_PRIMARY,
    BTN_SECONDARY,
    DIALOG,
    EMPTY_STATE,
    FONT_BODY,
    FONT_CAPTION,
    PAGE_SUBTITLE,
    PAGE_TITLE,
    RADIUS_M,
    SECTION_LABEL,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    C,
    avatar_color,
)

# Toast colors keyed by kind. Background is the accent at low alpha (set via
# the stylesheet rgba), text is the accent itself for contrast on MANTLE.
_TOAST_ACCENT = {
    "info": C.BLUE,
    "success": C.GREEN,
    "error": C.RED,
    "warn": C.PEACH,
}


def add_shadow(
    widget: QWidget,
    blur: int = 16,
    y: int = 3,
    alpha: int = 45,
) -> None:
    """Apply subtle drop shadow for depth effect."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)


def make_source_badge(app: AppInfo) -> QLabel | None:
    """Pill badge marking app provenance: Detected (from scan) vs Bundled.

    Returns ``None`` when ``AppInfo.source`` is absent (older cores) or
    equals an unrecognised provenance, so legacy apps stay unannotated.
    """
    source = getattr(app, "source", "bundled")
    if source == "discovered":
        text = tr("Detected")
        bg = C.SAPPHIRE
        fg = C.CRUST
    elif source == "bundled":
        text = tr("Bundled")
        bg = C.SURFACE2
        fg = C.SUBTEXT1
    else:
        return None

    badge = QLabel(text)
    badge.setStyleSheet(
        f"background: {bg}; color: {fg};"
        " border-radius: 7px;"
        " font-size: 9px; font-weight: bold;"
        " padding: 2px 7px;"
        " letter-spacing: 0px;"
    )
    return badge


def make_app_avatar(app: AppInfo, size: int, *, radius: int, font_size: int) -> QLabel:
    """Build the avatar label for an app row/card.

    When ``app.icon_path`` points at a real PNG / SVG, render the icon
    scaled to ``size`` with a subtle surface background for contrast.
    Otherwise fall back to the colored single-letter avatar (legacy look
    for apps without a discovered icon).
    """
    avatar = QLabel()
    avatar.setFixedSize(size, size)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

    icon_path = (app.icon_path or "").strip()
    pixmap: QPixmap | None = None
    if icon_path and Path(icon_path).is_file():
        pad = max(4, size // 7)
        inner = size - pad * 2
        try:
            if icon_path.lower().endswith(".svg"):
                renderer = QSvgRenderer(icon_path)
                if renderer.isValid():
                    pm = QPixmap(inner, inner)
                    pm.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pm)
                    renderer.render(painter)
                    painter.end()
                    pixmap = pm
            else:
                pm = QPixmap(icon_path)
                if not pm.isNull():
                    pixmap = pm.scaled(
                        inner,
                        inner,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        except Exception:  # noqa: BLE001
            pixmap = None

    if pixmap is not None and not pixmap.isNull():
        avatar.setPixmap(pixmap)
        avatar.setStyleSheet(f"background: {C.SURFACE1}; border-radius: {radius}px; padding: 0px;")
        return avatar

    # Fallback: legacy colored letter avatar.
    color = avatar_color(app.name)
    letter = app.full_name[0].upper() if app.full_name else "?"
    avatar.setText(letter)
    avatar.setStyleSheet(
        f"background: {color};"
        f" color: {C.CRUST};"
        f" border-radius: {radius}px;"
        f" font-size: {font_size}px; font-weight: bold;"
    )
    return avatar


def show_toast(
    parent: QWidget,
    message: str,
    *,
    kind: str = "info",
    msecs: int = 3500,
) -> None:
    """Show a transient, non-blocking notification over ``parent``.

    The toast is a frameless child label anchored to the bottom-centre of
    ``parent`` that auto-dismisses after ``msecs``. Used for action feedback
    (app launched, setting saved, op failed) so the user gets confirmation
    without a modal. ``kind`` is one of info / success / warn / error.

    No telemetry, no persistence -- purely a visual ack.

    Defensive: a toast is a non-critical visual ack, so if ``parent`` isn't a
    real QWidget (e.g. a test stub or a headless caller) this is a no-op
    rather than an error -- never crash a real action over a missed toast.
    """
    if not isinstance(parent, QWidget):
        return
    accent = _TOAST_ACCENT.get(kind, C.BLUE)
    toast = QLabel(message, parent)
    toast.setObjectName("winpodxToast")
    toast.setWordWrap(True)
    toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
    toast.setStyleSheet(
        f"QLabel#winpodxToast {{"
        f" background: {C.SURFACE0}; color: {accent};"
        f" border: 1px solid {accent}; border-radius: {RADIUS_M}px;"
        f" font-size: {FONT_BODY}px; font-weight: 600; padding: 8px 16px; }}"
    )
    toast.adjustSize()
    add_shadow(toast, blur=18, y=4, alpha=70)

    def _reposition() -> None:
        pw, ph = parent.width(), parent.height()
        tw = min(toast.sizeHint().width(), pw - 48)
        toast.setFixedWidth(tw)
        toast.adjustSize()
        toast.move((pw - toast.width()) // 2, ph - toast.height() - 56)

    _reposition()
    toast.show()
    toast.raise_()
    QTimer.singleShot(msecs, toast.deleteLater)


class BusyDialog(QDialog):
    """Modal "this is working" dialog for long-running operations.

    Shows a message, an indeterminate progress bar, and an optional
    "typically takes ~N" hint so the user knows the app isn't frozen. Pass
    ``cancellable=True`` to add a Cancel button; connect to :attr:`cancelled`
    (a plain callback list via :meth:`on_cancel`) to react. The caller runs
    the actual work on a worker thread and calls :meth:`finish` (or just
    ``accept()``/``close()``) when done.
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        eta_hint: str = "",
        cancellable: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet(DIALOG)
        self._cancel_cbs: list[Callable[[], None]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        self._msg = QLabel(message)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_BODY}px;")
        layout.addWidget(self._msg)

        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        layout.addWidget(bar)

        if eta_hint:
            hint = QLabel(eta_hint)
            hint.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: {FONT_CAPTION}px;")
            layout.addWidget(hint)

        if cancellable:
            row = QHBoxLayout()
            row.addStretch(1)
            self._cancel_btn = QPushButton(tr("Cancel"))
            self._cancel_btn.setStyleSheet(BTN_SECONDARY)
            self._cancel_btn.clicked.connect(self._on_cancel_clicked)
            row.addWidget(self._cancel_btn)
            layout.addLayout(row)

    def set_message(self, message: str) -> None:
        self._msg.setText(message)

    def on_cancel(self, cb: Callable[[], None]) -> None:
        self._cancel_cbs.append(cb)

    def _on_cancel_clicked(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText(tr("Cancelling..."))
        for cb in self._cancel_cbs:
            cb()

    def finish(self) -> None:
        """Close the dialog (safe to call from the GUI thread on completion)."""
        self.accept()


def make_warning_callout(text: str, *, level: str = "warn") -> QFrame:
    """An inline warning/danger banner for dangerous-action panels.

    ``level`` is "warn" (peach) or "danger" (red). Renders a tinted box with
    a coloured left border and an icon glyph -- use it above a destructive
    control (disk wipe, PCI passthrough, container recreate) so the risk is
    visible *before* the user acts, not buried in a modal body.
    """
    accent = C.RED if level == "danger" else C.PEACH
    glyph = "⚠"  # warning sign
    frame = QFrame()
    frame.setObjectName("winpodxCallout")
    frame.setStyleSheet(
        f"QFrame#winpodxCallout {{"
        f" background: {C.SURFACE0};"
        f" border: 1px solid {accent};"
        f" border-left: 3px solid {accent};"
        f" border-radius: {RADIUS_M}px; }}"
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(12, 10, 12, 10)
    row.setSpacing(10)
    icon = QLabel(glyph)
    icon.setStyleSheet(f"color: {accent}; font-size: 15px; font-weight: bold;")
    icon.setAlignment(Qt.AlignmentFlag.AlignTop)
    row.addWidget(icon)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {C.SUBTEXT1}; font-size: {FONT_CAPTION}px;")
    row.addWidget(label, 1)
    return frame


def make_page_heading(title: str, subtitle: str = "") -> QWidget:
    """Build a consistent page title/subtitle block."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(PAGE_TITLE)
    layout.addWidget(title_lbl)

    if subtitle:
        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setWordWrap(True)
        subtitle_lbl.setStyleSheet(PAGE_SUBTITLE)
        layout.addWidget(subtitle_lbl)

    return holder


def make_section_label(text: str) -> QLabel:
    """Build the compact uppercase section label used on dense pages."""
    label = QLabel(text)
    label.setStyleSheet(SECTION_LABEL)
    return label


def make_empty_panel(
    title: str,
    body: str = "",
    *,
    action_label: str = "",
    action_cb: Callable[[], None] | None = None,
) -> QFrame:
    """Build a deliberate empty/loading/error state panel."""
    frame = QFrame()
    frame.setObjectName("emptyState")
    frame.setStyleSheet(EMPTY_STATE)

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
    layout.setSpacing(SPACE_S)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    title_lbl = QLabel(title)
    title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_lbl.setWordWrap(True)
    title_lbl.setStyleSheet(
        f"background: transparent; color: {C.SUBTEXT1}; "
        f"font-size: {FONT_BODY}px; font-weight: bold;"
    )
    layout.addWidget(title_lbl)

    if body:
        body_lbl = QLabel(body)
        body_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )
        layout.addWidget(body_lbl)

    if action_label and action_cb is not None:
        layout.addSpacing(SPACE_M)
        btn = QPushButton(action_label)
        btn.setStyleSheet(BTN_PRIMARY)
        btn.clicked.connect(action_cb)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    frame.setMinimumHeight(148)
    layout.setContentsMargins(SPACE_XL, SPACE_L, SPACE_XL, SPACE_L)
    return frame


def actionable_error(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    actions: list[str] | None = None,
    detail: str = "",
) -> str:
    """Show an error with actionable buttons; return the clicked button label.

    ``actions`` is an ordered list of button labels (e.g.
    ``["View logs", "Retry", "Close"]``); the FIRST is the default/accept
    button and the LAST is the reject/escape button. Returns the label the
    user clicked so the caller can branch (open the Logs page, retry the op,
    or just dismiss). This replaces bare ``QMessageBox.critical`` calls that
    leave the user with a dead-end "OK".
    """
    labels = actions or [tr("Close")]
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    buttons: dict = {}
    for i, label in enumerate(labels):
        role = (
            QMessageBox.ButtonRole.AcceptRole
            if i == 0
            else QMessageBox.ButtonRole.RejectRole
            if i == len(labels) - 1
            else QMessageBox.ButtonRole.ActionRole
        )
        buttons[box.addButton(label, role)] = label
    box.exec()
    return buttons.get(box.clickedButton(), labels[-1])
