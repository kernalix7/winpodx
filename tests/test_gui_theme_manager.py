# SPDX-License-Identifier: MIT
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from winpodx.gui import theme  # noqa: E402
from winpodx.gui.theme_manager import ThemeManager, detect_system_scheme, instance  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _restore_theme_and_singleton() -> None:
    import winpodx.gui.theme_manager as tm

    previous = theme.current_scheme()
    tm._INSTANCE = None
    yield
    tm._INSTANCE = None
    theme.rebuild(previous)


def test_detect_system_scheme_honours_env_light(monkeypatch: pytest.MonkeyPatch, qapp) -> None:
    monkeypatch.setenv("WINPODX_COLOR_SCHEME", "light")

    assert detect_system_scheme(qapp) == "light"


def test_detect_system_scheme_honours_env_dark(monkeypatch: pytest.MonkeyPatch, qapp) -> None:
    monkeypatch.setenv("WINPODX_COLOR_SCHEME", "dark")

    assert detect_system_scheme(qapp) == "dark"


def test_detect_system_scheme_follows_style_hints_dark(
    monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    from PySide6.QtCore import Qt

    monkeypatch.delenv("WINPODX_COLOR_SCHEME", raising=False)
    hints = qapp.styleHints()
    if not hasattr(hints, "setColorScheme"):
        pytest.skip("QStyleHints.setColorScheme requires Qt 6.8+")
    previous = hints.colorScheme()
    try:
        hints.setColorScheme(Qt.ColorScheme.Dark)
        # Offscreen Qt plugins often ignore setColorScheme(); still exercise
        # the Dark branch when the platform does not honour the setter.
        if hints.colorScheme() != Qt.ColorScheme.Dark:
            monkeypatch.setattr(hints, "colorScheme", lambda: Qt.ColorScheme.Dark)
        assert detect_system_scheme(qapp) == "dark"
    finally:
        hints.setColorScheme(previous)


def test_detect_system_scheme_unknown_with_light_palette(
    monkeypatch: pytest.MonkeyPatch, qapp
) -> None:
    from PySide6.QtCore import Qt

    monkeypatch.delenv("WINPODX_COLOR_SCHEME", raising=False)
    hints = qapp.styleHints()
    if not hasattr(hints, "setColorScheme"):
        pytest.skip("QStyleHints.setColorScheme requires Qt 6.8+")
    previous_scheme = hints.colorScheme()
    previous_palette = QPalette(qapp.palette())
    try:
        hints.setColorScheme(Qt.ColorScheme.Unknown)
        light = QPalette(qapp.palette())
        light.setColor(QPalette.ColorRole.Window, QColor("#F3F3F3"))
        qapp.setPalette(light)
        assert detect_system_scheme(qapp) == "light"
    finally:
        qapp.setPalette(previous_palette)
        hints.setColorScheme(previous_scheme)


def test_set_scheme_emits_scheme_changed_and_rebuilds(qapp) -> None:
    received: list[str] = []
    manager = ThemeManager()
    manager.scheme_changed.connect(received.append)

    manager.set_scheme("light")

    assert received == ["light"]
    assert theme.current_scheme() == "light"
    assert theme.C.BASE == "#F3F3F3"


def test_start_applies_palette_for_detected_scheme(monkeypatch: pytest.MonkeyPatch, qapp) -> None:
    monkeypatch.setenv("WINPODX_COLOR_SCHEME", "dark")
    manager = ThemeManager()

    manager.start(qapp)

    window = qapp.palette().color(QPalette.ColorRole.Window).name().upper()
    assert window == "#1F1F1F"
    assert theme.current_scheme() == "dark"


def test_instance_returns_the_same_manager() -> None:
    first = instance()
    second = instance()

    assert first is second
    assert isinstance(first, ThemeManager)


def test_start_uses_the_windows_ui_face_at_the_desktop_point_size(qapp) -> None:
    from PySide6.QtGui import QFont, QFontDatabase

    # Given: the desktop (KDE/GNOME) set a monospace app font at 10pt
    qapp.setFont(QFont("Monoplex KR Nerd", 10))

    # When
    ThemeManager().start(qapp)

    # Then: Segoe UI when installed, else the bundled Selawik (OFL); size follows the desktop
    families = QFontDatabase.families()
    expected = "Segoe UI" if "Segoe UI" in families else "Selawik"
    assert qapp.font().family() == expected
    assert qapp.font().pointSize() == 10
    assert "font-family" not in theme.GLOBAL_STYLE


def test_ui_font_falls_back_to_the_desktop_face_when_nothing_better_exists(monkeypatch) -> None:
    from PySide6.QtGui import QFont

    monkeypatch.setattr(theme, "_ui_font_family", lambda: None)
    base = QFont("Monoplex KR Nerd", 11)

    chosen = theme.ui_font(base)

    assert chosen.family() == "Monoplex KR Nerd"
    assert chosen.pointSize() == 11
