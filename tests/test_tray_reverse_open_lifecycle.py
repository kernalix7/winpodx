# SPDX-License-Identifier: MIT
"""#691: reverse-open listener lifecycle must be owned by the tray.

Source-shape guards (CI has no display): the tray must (a) self-heal the
listener at startup -- previously only ``pod start`` / the app-launch
``ensure_ready`` path did, so a GUI/tray-only start left Windows->Linux
"Open with" silently dead -- and (b) stop the listener deliberately on Quit
instead of relying on the ``pkill python.*winpodx.*gui`` collateral kill
(the double-forked daemon inherits its parent's argv, so it only matched
when it happened to be started from the GUI).
"""

from __future__ import annotations

from pathlib import Path

TRAY = Path(__file__).resolve().parent.parent / "src" / "winpodx" / "desktop" / "tray.py"


def _src() -> str:
    return TRAY.read_text(encoding="utf-8")


def test_tray_startup_ensures_listener() -> None:
    src = _src()
    assert "ensure_listener_running(cfg)" in src
    # off the UI thread -- ensure forks + waits for a ready sentinel
    idx = src.index("def _ensure_reverse_open_listener")
    spawn = "threading.Thread(target=_ensure_reverse_open_listener, daemon=True).start()"
    assert spawn in src[idx:]


def test_quit_stops_listener_before_pkill() -> None:
    src = _src()
    stop_at = src.index("stop_listener()")
    pkill_at = src.index('"pkill"')
    assert stop_at < pkill_at, "Quit must stop the listener deliberately, before the pkill"
