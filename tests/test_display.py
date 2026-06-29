# SPDX-License-Identifier: MIT
"""Tests for display detection."""

from winpodx.display.detector import desktop_environment, session_type


def test_session_type_x11(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert session_type() == "x11"


def test_session_type_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert session_type() == "wayland"


def test_session_type_fallback_display(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert session_type() == "x11"


def test_desktop_environment_gnome(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert desktop_environment() == "gnome"


def test_desktop_environment_kde(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert desktop_environment() == "kde"


def test_qt_dpr_returns_none_off_main_thread():
    # QGuiApplication.screens() is GUI-thread-only; calling it from a worker
    # thread emits "setParent: ... different thread" and can SIGABRT the process
    # (GUI InfoWorker running gather_info off-thread). The guard must short-
    # circuit to None on any non-main thread so callers fall back to subprocess
    # detection instead of touching Qt.
    import threading

    from winpodx.display.scaling import _qt_max_device_pixel_ratio

    result: list[float | None] = []

    def worker():
        result.append(_qt_max_device_pixel_ratio())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert result == [None]
