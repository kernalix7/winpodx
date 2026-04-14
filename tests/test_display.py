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
