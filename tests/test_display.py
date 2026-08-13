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


def test_session_type_fallback_wayland_precedes_x11(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "tty")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert session_type() == "wayland"


def test_session_type_unknown_without_display_variables(monkeypatch):
    for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    assert session_type() == "unknown"


def test_desktop_environment_uses_leading_segment(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE:GNOME")
    assert desktop_environment() == "kde"


def test_desktop_environment_substring_then_session_fallback(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    assert desktop_environment() == "cinnamon"

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "unmapped")
    monkeypatch.setenv("DESKTOP_SESSION", "CustomDE")
    assert desktop_environment() == "customde"


def test_desktop_environment_unknown(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    assert desktop_environment() == "unknown"


def test_has_wayland_freerdp_checks_versioned_then_legacy(monkeypatch):
    from winpodx.display import detector

    calls = []

    def fake_which(name):
        calls.append(name)
        return "/usr/bin/wlfreerdp" if name == "wlfreerdp" else None

    monkeypatch.setattr(detector.shutil, "which", fake_which)
    assert detector.has_wayland_freerdp() is True
    assert calls == ["wlfreerdp3", "wlfreerdp"]


def test_display_info_reports_detected_values(monkeypatch):
    from winpodx.display import detector

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-2")
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(detector, "session_type", lambda: "wayland")
    monkeypatch.setattr(detector, "desktop_environment", lambda: "sway")
    monkeypatch.setattr(detector, "has_wayland_freerdp", lambda: True)
    assert detector.display_info() == {
        "session_type": "wayland",
        "desktop_environment": "sway",
        "wayland_display": "wayland-2",
        "x11_display": ":1",
        "wayland_freerdp": "yes",
    }
