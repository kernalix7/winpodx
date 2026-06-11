#!/usr/bin/env python3
"""
WinPodX Launcher — Windows 11-style Start Menu for .desktop files tagged 'winpodx'.
Fluent Design System with Acrylic/Mica background, Reveal highlight, animations.
"""

import os
import re
import subprocess
import sys
import threading
import configparser
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
    QObject,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)
try:
    from evdev import InputDevice, ecodes, list_devices
    HAVE_EVDEV = True
except ImportError:
    HAVE_EVDEV = False

try:
    from pynput import keyboard as pynput_kb
    HAVE_PYNPUT = True
except ImportError:
    HAVE_PYNPUT = False

# ---------------------------------------------------------------------------
# Constants — Fluent Design / Mica Dark palette
# ---------------------------------------------------------------------------

WINDOW_WIDTH  = 660
WINDOW_HEIGHT = 560
SEARCH_PLACEHOLDER = "Search apps"
CONFIG_PATH = os.path.expanduser("~/.config/launcher-search.conf")

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    try:
        cfg.read(CONFIG_PATH)
    except Exception:
        pass
    if "Launcher" not in cfg:
        cfg["Launcher"] = {}
    return cfg

def save_config(cfg: configparser.ConfigParser):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        cfg.write(f)

# Mica-dark solid colours
BG_COLOR      = QColor(32,  32,  32)
SURFACE       = QColor(43,  43,  43)
SURFACE_HOVER = QColor(50,  50,  50)
SURFACE_PRESS = QColor(39,  39,  39)
TILE_DEFAULT  = QColor(43,  43,  43)
TILE_HOVER    = QColor(50,  50,  50)
BORDER_SUBTLE = QColor(255, 255, 255, 20)
BORDER_FOCUS  = QColor(96,  205, 255)
TEXT_PRIMARY  = QColor(255, 255, 255)
TEXT_SECONDARY= QColor(255, 255, 255, 140)
ACCENT        = QColor(96,  205, 255)

DESKTOP_DIRS = [
    "/usr/share/applications/",
    os.path.expanduser("~/.local/share/applications/"),
    "/var/lib/flatpak/exports/share/applications/",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications/"),
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Productivity":        ["word", "excel", "powerpoint", "onenote", "outlook",
                             "office", "publisher", "onedrive"],
    "Developer Tools":     ["powershell", "terminal", "command", "cmd", "wt",
                             "bash", "shell"],
    "System":              ["settings", "control panel", "task manager",
                             "file explorer", "explorer"],
    "Media":               ["media player", "solitaire", "games", "vlc", "music"],
    "Browser":             ["chrome", "firefox", "edge", "browser"],
}

CATEGORY_ORDER = [
    "Productivity",
    "Developer Tools",
    "System",
    "Media",
    "Browser",
    "Other",
]

STRIP_EXEC_RE = re.compile(r"\s*%[fFuUbcdDnNickvmV]", re.IGNORECASE)
FONT_FAMILY = '"Segoe UI", "Ubuntu", sans-serif'


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AppEntry:
    filename: str
    name: str = ""
    exec_: str = ""
    icon: str = ""
    categories: str = ""
    comment: str = ""
    category: str = "Other"


# ---------------------------------------------------------------------------
# .desktop file parsing
# ---------------------------------------------------------------------------

def parse_desktop_file(path: str) -> AppEntry | None:
    entry = AppEntry(filename=os.path.basename(path))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if line.startswith("Name=") and not entry.name:
                    entry.name = line[5:].strip()
                elif line.startswith("Exec="):
                    entry.exec_ = line[5:].strip()
                elif line.startswith("Icon="):
                    entry.icon = line[5:].strip()
                elif line.startswith("Categories="):
                    entry.categories = line[11:].strip()
                elif line.startswith("Comment="):
                    entry.comment = line[8:].strip()
    except Exception:
        return None
    if not entry.name or not entry.exec_:
        return None
    entry.exec_ = STRIP_EXEC_RE.sub("", entry.exec_).strip()
    return entry


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------

def assign_category(entry: AppEntry) -> str:
    text = (entry.name + " " + entry.categories).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "Other"


# ---------------------------------------------------------------------------
# App discovery
# ---------------------------------------------------------------------------

def discover_apps() -> list[AppEntry]:
    apps: list[AppEntry] = []

    for d in DESKTOP_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        for fpath in p.glob("*.desktop"):
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "winpodx" not in content.lower():
                continue
            entry = parse_desktop_file(str(fpath))
            if entry is not None:
                entry.category = assign_category(entry)
                apps.append(entry)

    seen: set[str] = set()
    uniq: list[AppEntry] = []
    for a in apps:
        key = a.name.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    return uniq


# ---------------------------------------------------------------------------
# Icon loading
# ---------------------------------------------------------------------------

def load_icon(icon_field: str, fallback: str = "application-x-executable") -> QIcon:
    if not icon_field:
        return QIcon.fromTheme(fallback)
    if icon_field.startswith("/"):
        px = QPixmap(icon_field)
        if not px.isNull():
            return QIcon(px)
    ic = QIcon.fromTheme(icon_field)
    if not ic.isNull():
        return ic
    return QIcon.fromTheme(fallback)


# ---------------------------------------------------------------------------
# Qt signal bridge
# ---------------------------------------------------------------------------

class HotkeySignals(QObject):
    toggled = Signal()

_hk_signals = HotkeySignals()


# ---------------------------------------------------------------------------
# Global hotkey
# ---------------------------------------------------------------------------

def _start_evdev_listener():
    device = None
    for path in list_devices():
        try:
            d = InputDevice(path)
            name = d.name.lower()
            if 'keyboard' in name and 'virtual' not in name:
                device = d
                break
            d.close()
        except Exception:
            continue
    if device is None:
        return
    ctrl_pressed = False
    shift_pressed = False
    CTRL_CODES  = {ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL}
    SHIFT_CODES = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
    D_CODE = ecodes.KEY_D
    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                if event.value == 0:
                    if event.code in CTRL_CODES:    ctrl_pressed  = False
                    elif event.code in SHIFT_CODES: shift_pressed = False
                elif event.value == 1:
                    if event.code in CTRL_CODES:    ctrl_pressed  = True
                    elif event.code in SHIFT_CODES: shift_pressed = True
                    elif event.code == D_CODE and ctrl_pressed and shift_pressed:
                        _hk_signals.toggled.emit()
    except Exception:
        pass
    finally:
        device.close()


def _start_pynput_listener():
    currently_pressed = set()
    CTRL_KEYS  = {"ctrl",  "ctrl_l",  "ctrl_r"}
    SHIFT_KEYS = {"shift", "shift_l", "shift_r"}

    def on_press(key):
        try:
            if hasattr(key, "char") and key.char is not None:
                currently_pressed.add(key.char.lower())
            elif hasattr(key, "name"):
                currently_pressed.add(key.name.lower())
        except Exception:
            pass
        if (bool(currently_pressed & CTRL_KEYS)
                and bool(currently_pressed & SHIFT_KEYS)
                and "d" in currently_pressed):
            currently_pressed.clear()
            _hk_signals.toggled.emit()

    def on_release(key):
        try:
            if hasattr(key, "char") and key.char is not None:
                currently_pressed.discard(key.char.lower())
            elif hasattr(key, "name"):
                currently_pressed.discard(key.name.lower())
        except Exception:
            pass

    with pynput_kb.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def _start_hotkey_listener():
    session_type = os.environ.get("XDG_SESSION_TYPE", "x11")
    if session_type == "wayland" and HAVE_EVDEV:
        _start_evdev_listener()
    elif HAVE_PYNPUT:
        _start_pynput_listener()
    else:
        print("No global hotkey backend. Press F5 in the launcher window.", flush=True)


# ---------------------------------------------------------------------------
# Reveal tile
# ---------------------------------------------------------------------------

class RevealTile(QFrame):
    def __init__(self, entry: AppEntry, launch_cb, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._launch_cb = launch_cb
        self._hovered = False
        self._cursor_pos = QPointF(-1, -1)
        self._hover_progress = 0.0
        self._selected = False

        self.setMinimumSize(130, 108)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        icon = load_icon(entry.icon)
        self._icon_label = QLabel()
        self._icon_label.setPixmap(icon.pixmap(36, 36))
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon_label)

        self._name_label = QLabel(entry.name)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._name_label.setMaximumWidth(180)
        self._name_label.setWordWrap(True)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._name_label.setMaximumHeight(38)
        font = QFont("Segoe UI", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self._name_label.setFont(font)
        self._name_label.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(self._name_label)

        self._hover_anim = QPropertyAnimation(self, b"hover_progress")
        self._hover_anim.setDuration(120)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_selected(self, sel: bool):
        self._selected = sel
        self.update()

    def _get_hover_progress(self): return self._hover_progress
    def _set_hover_progress(self, val):
        self._hover_progress = val
        self.update()
    hover_progress = Property(float, fget=_get_hover_progress, fset=_set_hover_progress)

    def enterEvent(self, event):
        self._hovered = True
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._cursor_pos = QPointF(-1, -1)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        self._cursor_pos = QPointF(event.position())
        if self._hovered: self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._launch_cb(self._entry)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.setClipPath(path)
        if self._hover_progress > 0:
            r = int(TILE_DEFAULT.red()   + (TILE_HOVER.red()   - TILE_DEFAULT.red())   * self._hover_progress)
            g = int(TILE_DEFAULT.green() + (TILE_HOVER.green() - TILE_DEFAULT.green()) * self._hover_progress)
            b = int(TILE_DEFAULT.blue()  + (TILE_HOVER.blue()  - TILE_DEFAULT.blue())  * self._hover_progress)
            painter.fillPath(path, QColor(r, g, b))
        else:
            painter.fillPath(path, QColor(TILE_DEFAULT))
        if self._hovered and self._cursor_pos.x() >= 0:
            gradient = QRadialGradient(self._cursor_pos, 80)
            gradient.setColorAt(0.0, QColor(255, 255, 255, 30))
            gradient.setColorAt(0.5, QColor(255, 255, 255, 10))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, QBrush(gradient))
        painter.setPen(QColor(255, 255, 255, 20) if self._hover_progress < 0.5 else QColor(255, 255, 255, 35))
        painter.drawPath(path)
        if self._selected:
            c = QColor("#60CDFF"); c.setAlpha(200)
            painter.setPen(QPen(c, 2))
            painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


# ---------------------------------------------------------------------------
# Styled scroll area
# ---------------------------------------------------------------------------

class StyledScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 6px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.2); border-radius: 3px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        """)


# ---------------------------------------------------------------------------
# Category pill bar
# ---------------------------------------------------------------------------

class PillBar(QScrollArea):
    def __init__(self, categories, on_select, parent=None):
        super().__init__(parent)
        self._on_select = on_select
        self._buttons: dict[str, QPushButton] = {}
        self._active = "All"

        self.setWidgetResizable(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedHeight(34)
        self.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal { background: transparent; height: 0; }
        """)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._layout = QHBoxLayout(container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

        for cat in ["All"] + CATEGORY_ORDER:
            btn = QPushButton(cat)
            btn.setFont(QFont(FONT_FAMILY, 12))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.clicked.connect(lambda checked=False, c=cat: self._select(c))
            self._buttons[cat] = btn
            self._layout.addWidget(btn)

        self._layout.addStretch()
        self.setWidget(container)
        self._update_style()

    def _select(self, cat: str):
        self._active = cat
        self._update_style()
        self._on_select(cat)

    def _update_style(self):
        for name, btn in self._buttons.items():
            if name == self._active:
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(96,205,255,0.12); color: #FFFFFF;
                        border: 1px solid #60CDFF; border-radius: 6px;
                        padding: 5px 14px; font-size: 12px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #2B2B2B; color: rgba(255,255,255,0.65);
                        border: 1px solid rgba(255,255,255,0.08); border-radius: 6px;
                        padding: 5px 14px; font-size: 12px;
                    }
                    QPushButton:hover {
                        background: #323232; color: #FFFFFF;
                        border: 1px solid rgba(255,255,255,0.15);
                    }
                """)


# ---------------------------------------------------------------------------
# Launch notification
# ---------------------------------------------------------------------------

class LaunchNotification(QWidget):
    def __init__(self):
        self._visible = False
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280, 44)

        container = QFrame(self)
        container.setObjectName("notificationFrame")
        container.setGeometry(0, 0, 280, 44)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(16); shadow.setOffset(0, 4); shadow.setColor(QColor(0, 0, 0, 120))
        container.setGraphicsEffect(shadow)
        container.setStyleSheet("""
            #notificationFrame {
                background: #202020; border: 1px solid rgba(255,255,255,0.1); border-radius: 10px;
            }
        """)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(20, 20)
        layout.addWidget(self._icon_label)

        self._text_label = QLabel()
        self._text_label.setFont(QFont(FONT_FAMILY, 12))
        self._text_label.setStyleSheet("color: #FFFFFF; background: transparent;")
        layout.addWidget(self._text_label)

    def show_for(self, app_name: str, app_icon: str):
        self._text_label.setText(f"Opening {app_name}…")
        px = load_icon(app_icon).pixmap(20, 20)
        if not px.isNull(): self._icon_label.setPixmap(px)
        else: self._icon_label.clear()
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2,
                      geo.y() + geo.height() - self.height() - 16)
        self._visible = True
        self.show(); self.raise_()
        QTimer.singleShot(2000, self.hide_)

    def hide_(self):
        if not self._visible: return
        self._visible = False
        self.hide()


# ---------------------------------------------------------------------------
# Compact mode list item
# ---------------------------------------------------------------------------

class CompactListItem(QFrame):
    def __init__(self, entry: AppEntry, launch_cb):
        super().__init__()
        self._entry = entry
        self._launch_cb = launch_cb
        self._selected = False
        self._hovered = False
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        icon = load_icon(entry.icon)
        icon_lbl = QLabel()
        px = icon.pixmap(24, 24)
        icon_lbl.setPixmap(px if not px.isNull() else QIcon.fromTheme("application-x-executable").pixmap(24, 24))
        icon_lbl.setFixedSize(24, 24)
        layout.addWidget(icon_lbl)

        name_lbl = QLabel(entry.name)
        name_lbl.setFont(QFont(FONT_FAMILY, 12))
        name_lbl.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(name_lbl, 1)
        self._update_style()

    def set_selected(self, sel: bool):
        self._selected = sel
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet("CompactListItem { background: rgba(96,205,255,0.15); border-radius: 6px; outline: none; }")
        elif self._hovered:
            self.setStyleSheet("CompactListItem { background: #2B2B2B; border-radius: 6px; outline: none; }")
        else:
            self.setStyleSheet("CompactListItem { background: transparent; border-radius: 6px; outline: none; }")

    def enterEvent(self, event):
        self._hovered = True; self._update_style(); super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False; self._update_style(); super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._launch_cb(self._entry)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._launch_cb(self._entry)
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Main launcher window
# ---------------------------------------------------------------------------

class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._visible = False
        self._apps: list[AppEntry] = []
        self._active_category = "All"
        self._search_text = ""
        self._nav_index = -1
        self._nav_state = 0
        self._pill_focus = -1
        self._settings_mode = False

        self.setWindowTitle("WinPodX Launcher")
        self.setWindowIcon(load_icon("winpodx", "application-x-executable"))
        self.setObjectName("LauncherWindow")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setMinimumSize(WINDOW_WIDTH, 52)

        self._notification = LaunchNotification()
        QApplication.instance().installEventFilter(self)
        QGuiApplication.instance().focusWindowChanged.connect(self._on_focus_changed)

        self._build_ui()
        if self._compact_mode:
            self._content_stack.setCurrentIndex(1)
            self._pill_bar.setVisible(False)
            self._outer_layout.setSpacing(0)
        _hk_signals.toggled.connect(self.toggle)
        self._reload_apps()
        if not self._compact_mode:
            self._set_window_height(WINDOW_HEIGHT)

    def _set_window_height(self, h: int):
        self.setFixedSize(WINDOW_WIDTH, h)
        self._outer.setGeometry(0, 0, WINDOW_WIDTH, h)
        self._center_window()

    def _center_window(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.x() + (geo.width()  - self.width())  // 2,
                      geo.y() + (geo.height() - self.height()) // 2)

    def _on_focus_changed(self, focused):
        if not self._visible:
            return
        own = self.windowHandle()
        if focused is own:
            return
        if focused is not None:
            p = focused.transientParent()
            while p is not None:
                if p == own:
                    return
                p = p.transientParent()
        self.hide_()

    def _build_ui(self):
        outer = QFrame(self)
        self._outer = outer
        outer.setObjectName("outerFrame")
        # Mica-style: subtle vertical gradient — lighter top, darker bottom
        outer.setStyleSheet("""
            QFrame#outerFrame {
                background: #202020;
                border-radius: 12px;
            }
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40); shadow.setOffset(0, 12); shadow.setColor(QColor(0, 0, 0, 180))
        outer.setGraphicsEffect(shadow)

        layout = QVBoxLayout(outer)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(8)

        # ── Search bar ────────────────────────────────────────────────────
        search_container = QFrame()
        search_container.setFixedHeight(38)
        search_container.setStyleSheet("""
            QFrame {
                background: #2B2B2B;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 6px;
            }
        """)
        search_row = QHBoxLayout(search_container)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(0)

        winpodx_icon = load_icon("winpodx", "application-x-executable")
        self._winpodx_btn = QPushButton()
        self._winpodx_btn.setIcon(winpodx_icon)
        self._winpodx_btn.setIconSize(QSize(22, 22))
        self._winpodx_btn.setFixedSize(42, 38)
        self._winpodx_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._winpodx_btn.setToolTip("WinPodX")
        self._winpodx_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 6px 0 0 6px; padding: 0; }
            QPushButton:hover { background: #323232; }
            QPushButton:pressed { background: #272727; }
        """)
        self._winpodx_btn.clicked.connect(self._launch_winpodx)
        search_row.addWidget(self._winpodx_btn)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search_bar.setFont(QFont("Segoe UI", 11))
        self.search_bar.setFixedHeight(38)
        self.search_bar.setStyleSheet("""
            QLineEdit {
                background: transparent; color: #FFFFFF; border: none;
                padding: 9px 8px; font-size: 13px;
                selection-background-color: #60CDFF; selection-color: #000000;
            }
            QLineEdit:focus { background: #313131; }
        """)
        self.search_bar.textChanged.connect(self._on_search)
        self.search_bar.returnPressed.connect(self._on_return)
        search_row.addWidget(self.search_bar, 1)

        gear_icon = QIcon("/home/ayaan.mirza/icons/settings.svg")
        self._gear_btn = QPushButton()
        self._gear_btn.setIcon(gear_icon)
        self._gear_btn.setIconSize(QSize(20, 20))
        self._gear_btn.setFixedSize(38, 38)
        self._gear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gear_btn.setToolTip("Settings")
        self._gear_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 0 6px 6px 0; }
            QPushButton:hover { background: #323232; }
            QPushButton:pressed { background: #272727; }
        """)
        self._gear_btn.clicked.connect(self._on_gear_clicked)
        search_row.addWidget(self._gear_btn)

        layout.addWidget(search_container)

        # ── Category pills ────────────────────────────────────────────────
        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.setSpacing(0)

        self._pill_bar = PillBar(CATEGORY_ORDER, self._set_category)
        self._pill_bar.setFixedHeight(36)
        pill_row.addWidget(self._pill_bar, 1)

        layout.addLayout(pill_row)
        self._outer_layout = layout

        cfg = load_config()
        self._reset_search_on_open = cfg["Launcher"].getboolean("reset_search_on_open", fallback=False)
        self._compact_mode = cfg["Launcher"].getboolean("compact_mode", fallback=False)
        self._compact_index = -1

        # ── Scrollable app grid ───────────────────────────────────────────
        scroll = StyledScrollArea()
        self._grid_container = QWidget()
        self._grid_container.setObjectName("gridContainer")
        self._grid_container.setStyleSheet("background: transparent;")
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(8)
        self._grid_layout.setContentsMargins(2, 2, 2, 2)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for _c in range(4):
            self._grid_layout.setColumnStretch(_c, 1)

        self._scroll_area = scroll
        scroll.setWidget(self._grid_container)
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(scroll)

        # ── Compact list view ─────────────────────────────────────────────
        self._compact_scroll = StyledScrollArea()
        self._compact_container = QWidget()
        self._compact_container.setStyleSheet("background: transparent;")
        self._compact_layout = QVBoxLayout(self._compact_container)
        self._compact_layout.setContentsMargins(4, 4, 4, 4)
        self._compact_layout.setSpacing(2)
        self._compact_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._compact_scroll.setWidget(self._compact_container)
        self._content_stack.addWidget(self._compact_scroll)

        layout.addWidget(self._content_stack)

    def _rebuild_content(self):
        if self._compact_mode:
            self._rebuild_compact()
        else:
            self._rebuild_grid()

    def _rebuild_compact(self):
        while self._compact_layout.count():
            item = self._compact_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        if self._settings_mode:
            self._rebuild_compact_settings()
            return

        if not self._search_text:
            self._compact_scroll.setFixedHeight(0)
            self._content_stack.setFixedHeight(0)
            self._outer_layout.setContentsMargins(7, 7, 7, 7)
            self._set_window_height(52)
            return

        apps = self._filtered_apps()
        shown = 0
        for app in apps:
            if shown >= 5: break
            self._compact_layout.addWidget(CompactListItem(app, self._launch_app))
            shown += 1
        if shown == 0:
            lbl = QLabel("No apps found")
            lbl.setStyleSheet("color: rgba(255,255,255,0.4); padding: 16px; font-size: 12px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._compact_layout.addWidget(lbl)
            shown = 1

        item_h = 42
        h = min(shown, 5) * item_h + 8
        self._compact_scroll.setFixedHeight(h)
        self._content_stack.setFixedHeight(h)
        self._outer_layout.setContentsMargins(7, 7, 7, 7)
        self._set_window_height(52 + h)

    def _rebuild_compact_settings(self):
        items = [
            ("Clear search on open", self._reset_search_on_open, lambda: (self._toggle_reset_search(not self._reset_search_on_open), self._rebuild_content())),
            ("Compact mode", True, lambda: self._toggle_compact(False)),
            ("Refresh apps", None, self._reload_apps),
        ]
        for text, checked, callback in items:
            self._compact_layout.addWidget(self._make_compact_setting_item(text, checked, callback))
        h = len(items) * 42 + 8
        self._compact_scroll.setFixedHeight(h)
        self._content_stack.setFixedHeight(h)
        self._outer_layout.setContentsMargins(7, 7, 7, 7)
        self._set_window_height(52 + h)

    def _make_tile(self, entry: AppEntry) -> RevealTile:
        return RevealTile(entry, self._launch_app)

    def _filtered_apps(self) -> list[AppEntry]:
        apps = self._apps
        if self._active_category != "All":
            apps = [a for a in apps if a.category == self._active_category]
        if self._search_text:
            lower = self._search_text.lower()
            apps = [a for a in apps if lower in a.name.lower()]
        return apps

    def _rebuild_grid(self):
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._outer_layout.setSpacing(8)
        self._outer_layout.setContentsMargins(7, 7, 7, 7)
        self._content_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_stack.setMinimumHeight(0)
        self._content_stack.setMaximumHeight(16777215)
        self._compact_scroll.setFixedHeight(0)
        self._set_window_height(WINDOW_HEIGHT)

        apps = self._filtered_apps()
        cols = 4
        for idx, app in enumerate(apps):
            tile = self._make_tile(app)
            tile.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._grid_layout.addWidget(tile, idx // cols, idx % cols)

        if not apps:
            empty = QLabel("No apps found")
            empty.setFont(QFont(FONT_FAMILY, 12))
            empty.setStyleSheet("color: rgba(255,255,255,0.4); padding: 40px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid_layout.addWidget(empty, 0, 0, 1, cols)

        self._grid_layout.setRowStretch(len(apps) // cols + 1, 1)

    def _is_in_grid(self, obj) -> bool:
        while obj:
            if obj is self._grid_container: return True
            obj = obj.parent()
        return False

    def _focus_tile(self, idx: int):
        self._nav_state = 2
        self._nav_index = idx
        for i in range(self._grid_layout.count()):
            item = self._grid_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), RevealTile):
                item.widget().set_selected(i == idx)
        item = self._grid_layout.itemAt(idx)
        if item and item.widget():
            w = item.widget()
            w.setFocus()
            self._scroll_area.ensureWidgetVisible(w, 0, 20)

    def _show_settings_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #2B2B2B; border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px; padding: 6px;
            }
            QMenu::item {
                color: #FFFFFF; padding: 8px 32px 8px 12px; border-radius: 4px;
                font-size: 12px;
            }
            QMenu::item:selected { background: rgba(96,205,255,0.12); }
        """)
        action1 = menu.addAction("Clear search on open")
        action1.setCheckable(True)
        action1.setChecked(self._reset_search_on_open)
        action1.triggered.connect(self._toggle_reset_search)
        action2 = menu.addAction("Compact mode")
        action2.setCheckable(True)
        action2.setChecked(self._compact_mode)
        action2.triggered.connect(self._toggle_compact)
        menu.addSeparator()
        action3 = menu.addAction("Refresh apps")
        action3.triggered.connect(self._reload_apps)
        menu.exec(self._gear_btn.mapToGlobal(QPoint(0, self._gear_btn.height())))

    def _on_gear_clicked(self):
        if self._compact_mode:
            if self._settings_mode:
                self._exit_settings_mode()
            else:
                self._show_settings_compact()
        else:
            self._show_settings_menu()

    def _show_settings_compact(self):
        self.search_bar.blockSignals(True)
        self.search_bar.clear()
        self.search_bar.blockSignals(False)
        self._search_text = ""
        self._settings_mode = True
        self._rebuild_content()

    def _exit_settings_mode(self):
        if not self._settings_mode: return
        self._settings_mode = False
        self._rebuild_content()

    @staticmethod
    def _make_compact_setting_item(text: str, checked, callback) -> QFrame:
        item = QFrame()
        item.setFixedHeight(40)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)
        name_lbl = QLabel(text)
        name_lbl.setFont(QFont(FONT_FAMILY, 12))
        name_lbl.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(name_lbl, 1)
        if checked is not None:
            chk = QLabel("✓" if checked else "")
            chk.setFont(QFont(FONT_FAMILY, 12))
            chk.setStyleSheet("color: #60CDFF;")
            layout.addWidget(chk)
        item.setStyleSheet("""
            QFrame { background: transparent; border-radius: 6px; outline: none; }
            QFrame:hover { background: #2B2B2B; }
        """)
        if callback:
            item.mousePressEvent = lambda ev, cb=callback: cb()
        return item

    def _toggle_compact(self, enabled: bool):
        self._settings_mode = False
        self._compact_mode = enabled
        self._content_stack.setCurrentIndex(1 if enabled else 0)
        self._pill_bar.setVisible(not enabled)
        self._outer_layout.setSpacing(0 if enabled else 8)
        try:
            self._rebuild_content()
        except Exception:
            import traceback; traceback.print_exc()
        cfg = load_config()
        cfg["Launcher"]["compact_mode"] = "true" if enabled else "false"
        save_config(cfg)

    def _toggle_reset_search(self, enabled: bool):
        self._reset_search_on_open = enabled
        cfg = load_config()
        cfg["Launcher"]["reset_search_on_open"] = "true" if enabled else "false"
        save_config(cfg)

    def _on_search(self, text: str):
        if self._settings_mode:
            self._settings_mode = False
        self._search_text = text.strip()
        self._nav_index = -1
        self._nav_state = 0
        self._compact_index = -1
        self._clear_pill_focus()
        self._clear_selection()
        self._gear_btn.setVisible(not bool(self._search_text))
        self._rebuild_content()

    def _on_return(self):
        if self._compact_mode and not self._search_text:
            return
        apps = self._filtered_apps()
        if apps: self._launch_app(apps[0])

    def _set_category(self, cat: str):
        self._active_category = cat
        self._nav_state = 0
        self._nav_index = -1
        self._clear_pill_focus()
        self._rebuild_content()

    def _launch_app(self, entry: AppEntry):
        self.hide_()
        try:
            subprocess.Popen(entry.exec_, shell=True, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._notification.show_for(entry.name, entry.icon)
        except Exception as exc:
            print(f"Failed to launch {entry.name}: {exc}", file=sys.stderr)

    def _launch_winpodx(self):
        self.hide_()
        try:
            subprocess.Popen("winpodx gui", shell=True, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._notification.show_for("WinPodX", "winpodx")
        except Exception as exc:
            print(f"Failed to launch WinPodX: {exc}", file=sys.stderr)

    def _reload_apps(self):
        self._apps = discover_apps()
        self._rebuild_content()

    def toggle(self):
        if self._visible: self.hide_()
        else: self.show_()

    def show_(self):
        if self._reset_search_on_open:
            self.search_bar.clear()
        self._reload_apps()
        self._visible = True
        self._nav_state = 0
        self._nav_index = -1
        self._clear_pill_focus()
        self._center_window()
        self.show(); self.raise_(); self.activateWindow()
        self.search_bar.setFocus()

    def hide_(self):
        if not self._visible: return
        self._visible = False
        self.hide()

    def eventFilter(self, obj, event):
        if not self._visible:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                if not (isinstance(obj, QWidget) and self.isAncestorOf(obj)):
                    self.hide_()
            return super().eventFilter(obj, event)

        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()

        if key == Qt.Key.Key_Escape:
            if self._compact_mode and self._settings_mode:
                self._exit_settings_mode(); return True
            if self._compact_mode and self._search_text:
                self.search_bar.clear(); return True
            self.hide_(); return True

        if obj is self.search_bar:
            if key == Qt.Key.Key_Down:
                if self._pill_bar.isVisible():
                    self._nav_state = 1
                    self._pill_focus = 0
                    self._update_pill_styles()
                    self._focus_pill_button(0)
                elif self._compact_mode:
                    self._focus_compact_item(0)
                else:
                    apps = self._filtered_apps()
                    if apps:
                        self._nav_state = 2
                        self._focus_tile(0)
                return True
            if key == Qt.Key.Key_Up:
                if self._pill_bar.isVisible():
                    self._nav_state = 1
                    self._pill_focus = len(self._pill_bar._buttons) - 1
                    self._update_pill_styles()
                    self._focus_pill_button(self._pill_focus)
                elif self._compact_mode:
                    self._focus_compact_item(self._compact_item_count() - 1)
                else:
                    apps = self._filtered_apps()
                    if apps:
                        self._nav_state = 2
                        self._focus_tile(len(apps) - 1)
                return True
            return super().eventFilter(obj, event)

        if self._nav_state == 1 and self._is_in_pills(obj):
            pill_keys = list(self._pill_bar._buttons.keys())
            if key == Qt.Key.Key_Right:
                if self._pill_focus < len(pill_keys) - 1:
                    self._pill_focus += 1
                    self._update_pill_styles()
                    self._focus_pill_button(self._pill_focus)
                return True
            if key == Qt.Key.Key_Left:
                if self._pill_focus > 0:
                    self._pill_focus -= 1
                    self._update_pill_styles()
                    self._focus_pill_button(self._pill_focus)
                else:
                    self._nav_state = 0
                    self._clear_pill_focus()
                    self.search_bar.setFocus()
                return True
            if key == Qt.Key.Key_Down:
                self._clear_pill_focus()
                if self._compact_mode:
                    self._focus_compact_item(0)
                else:
                    self._nav_state = 2
                    apps = self._filtered_apps()
                    if apps: self._focus_tile(0)
                return True
            if key == Qt.Key.Key_Up:
                self._nav_state = 0
                self._clear_pill_focus()
                self.search_bar.setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._pill_bar._select(pill_keys[self._pill_focus])
                self._nav_state = 0
                self._clear_pill_focus()
                self.search_bar.setFocus()
                return True
            self._nav_state = 0
            self._clear_pill_focus()
            self.search_bar.setFocus()
            QApplication.sendEvent(self.search_bar, event)
            return True

        if self._compact_mode and self._is_in_compact(obj):
            count = self._compact_item_count()
            if key == Qt.Key.Key_Down:
                if self._compact_index < count - 1:
                    self._focus_compact_item(self._compact_index + 1)
                return True
            if key == Qt.Key.Key_Up:
                if self._compact_index > 0:
                    self._focus_compact_item(self._compact_index - 1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._compact_layout.itemAt(self._compact_index)
                if item and item.widget() and isinstance(item.widget(), CompactListItem):
                    item.widget()._launch_cb(item.widget()._entry)
                return True
            self._compact_index = -1
            self.search_bar.setFocus()
            QApplication.sendEvent(self.search_bar, event)
            return True

        if self._nav_state == 2 and self._is_in_grid(obj):
            cols = 4
            apps = self._filtered_apps()
            if key == Qt.Key.Key_Right:
                nxt = self._nav_index + 1
                if nxt < len(apps): self._focus_tile(nxt)
                return True
            if key == Qt.Key.Key_Left:
                nxt = self._nav_index - 1
                if nxt >= 0: self._focus_tile(nxt)
                return True
            if key == Qt.Key.Key_Down:
                nxt = self._nav_index + cols
                if nxt < len(apps): self._focus_tile(nxt)
                return True
            if key == Qt.Key.Key_Up:
                nxt = self._nav_index - cols
                if nxt >= 0:
                    self._focus_tile(nxt)
                elif self._pill_bar.isVisible():
                    self._nav_state = 1
                    self._clear_selection()
                    self._pill_focus = max(0, self._pill_focus)
                    self._update_pill_styles()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if 0 <= self._nav_index < len(apps):
                    self._launch_app(apps[self._nav_index])
                return True
            self._nav_index = -1
            self._clear_selection()
            self.search_bar.setFocus()
            QApplication.sendEvent(self.search_bar, event)
            return True

        return super().eventFilter(obj, event)

    def _clear_selection(self):
        for i in range(self._grid_layout.count()):
            item = self._grid_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), RevealTile):
                item.widget().set_selected(False)

    def _is_in_pills(self, obj) -> bool:
        while obj:
            if obj is self._pill_bar: return True
            obj = obj.parent()
        return False

    def _is_in_compact(self, obj) -> bool:
        while obj:
            if obj is self._compact_container: return True
            obj = obj.parent()
        return False

    def _compact_item_count(self) -> int:
        count = 0
        for i in range(self._compact_layout.count()):
            item = self._compact_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), (CompactListItem, QLabel)):
                count += 1
        return count

    def _focus_compact_item(self, idx: int):
        self._compact_index = idx
        for i in range(self._compact_layout.count()):
            item = self._compact_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), CompactListItem):
                item.widget().set_selected(False)
        target = None
        actual = -1
        for i in range(self._compact_layout.count()):
            item = self._compact_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), (CompactListItem, QLabel)):
                actual += 1
                if actual == idx:
                    target = item.widget()
                    break
        if target:
            target.setFocus()
            if isinstance(target, CompactListItem):
                target.set_selected(True)
            self._compact_scroll.ensureWidgetVisible(target, 0, 20)

    def _update_pill_styles(self):
        if self._pill_focus < 0: return
        keys = list(self._pill_bar._buttons.keys())
        if self._pill_focus >= len(keys): return
        for name, btn in self._pill_bar._buttons.items():
            if name == keys[self._pill_focus]:
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(96,205,255,0.12); color: #FFFFFF;
                        border: 2px solid #60CDFF; border-radius: 6px;
                        padding: 5px 14px; font-size: 12px;
                    }
                """)
            else:
                self._pill_bar._update_style()

    def _focus_pill_button(self, idx: int):
        keys = list(self._pill_bar._buttons.keys())
        if 0 <= idx < len(keys):
            self._pill_bar._buttons[keys[idx]].setFocus()

    def _clear_pill_focus(self):
        self._pill_focus = -1
        self._pill_bar._update_style()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.hide_()
        elif event.key() == Qt.Key.Key_F5: self.toggle()
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("WinPodX")
    app.setApplicationName("WinPodX Launcher")

    win = LauncherWindow()

    t = threading.Thread(target=_start_hotkey_listener, daemon=True)
    t.start()

    print("WinPodX Launcher started. Press Ctrl+Shift+D to open.", flush=True)
    win.show_()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
