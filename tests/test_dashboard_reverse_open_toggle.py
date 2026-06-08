# SPDX-License-Identifier: MIT
"""The Dashboard reverse-open checkbox must actually start/stop the listener.

Regression for the 0.6.0 report on #425: the Settings-panel checkbox started
the daemon on tick, but the Dashboard home card's checkbox only persisted
cfg.reverse_open.enabled -- so a user who enabled it there saw "Enabled" yet
the daemon stayed down and had to be started by hand. _on_reverse_open_toggled
now mirrors the panel and drives _cmd_start_listener / _cmd_stop_listener.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

import winpodx.cli.host_open as ho  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.gui._main_window_dashboard import DashboardMixin  # noqa: E402


class _Harness(DashboardMixin):
    """Bare host exposing only what _on_reverse_open_toggled reads."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg


def _stub_handlers(monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(ho, "_cmd_start_listener", lambda a: calls.append(("start", a.json)) or 0)
    monkeypatch.setattr(ho, "_cmd_stop_listener", lambda a: calls.append(("stop", a.json)) or 0)
    return calls


def test_toggle_on_persists_and_starts_listener(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = _stub_handlers(monkeypatch)
    cfg = Config()
    _Harness(cfg)._on_reverse_open_toggled(True)
    assert cfg.reverse_open.enabled is True
    assert Config.load().reverse_open.enabled is True  # persisted
    assert calls == [("start", False)]


def test_toggle_off_persists_and_stops_listener(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = _stub_handlers(monkeypatch)
    cfg = Config()
    cfg.reverse_open.enabled = True
    _Harness(cfg)._on_reverse_open_toggled(False)
    assert cfg.reverse_open.enabled is False
    assert calls == [("stop", False)]


def test_toggle_start_failure_is_quiet_and_keeps_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def _boom(_a):
        raise RuntimeError("guest down")

    monkeypatch.setattr(ho, "_cmd_start_listener", _boom)
    cfg = Config()
    # Must not raise even when the listener start fails (guest not up yet).
    _Harness(cfg)._on_reverse_open_toggled(True)
    # Flag stays persisted so the next pod bringup starts the listener.
    assert cfg.reverse_open.enabled is True
