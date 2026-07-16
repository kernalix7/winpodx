# SPDX-License-Identifier: MIT
"""Detached window-setup helper dispatch (#472 relist + #702 icon)."""

from __future__ import annotations

import winpodx.core.rdp as rdp
from winpodx.desktop.window_setup import main


def _patch(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(rdp, "_apply_window_icon", lambda wc, ic: calls.append(("icon", wc, ic)))
    monkeypatch.setattr(rdp, "_relist_uwp_taskbar", lambda wc: calls.append(("relist", wc)))
    return calls


def test_uwp_with_icon_runs_both(monkeypatch):
    calls = _patch(monkeypatch)
    rc = main(["prog", "winpodx-foo", "--icon", "/tmp/x.png", "--uwp"])
    assert rc == 0
    assert ("relist", "winpodx-foo") in calls
    assert ("icon", "winpodx-foo", "/tmp/x.png") in calls


def test_win32_icon_only_no_relist(monkeypatch):
    # A classic Win32 app (no --uwp) gets the icon but not the SKIP_TASKBAR clear.
    calls = _patch(monkeypatch)
    rc = main(["prog", "winpodx-bar", "--icon", "/tmp/y.png"])
    assert rc == 0
    assert ("icon", "winpodx-bar", "/tmp/y.png") in calls
    assert not any(c[0] == "relist" for c in calls)


def test_no_icon_no_uwp_is_noop(monkeypatch):
    calls = _patch(monkeypatch)
    rc = main(["prog", "winpodx-baz"])
    assert rc == 0
    assert calls == []
