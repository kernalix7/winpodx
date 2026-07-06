# SPDX-License-Identifier: MIT
"""#690: the tray's PrepareForSleep D-Bus subscription must use a QObject
receiver + SLOT signature.

PySide6's ``QDBusConnection.connect`` has no overload taking a bare Python
callable -- passing one raises ``called with wrong argument types`` at runtime,
which the TypeError guard downgraded to a warning. Net effect: the fast
suspend/resume recovery (#225) never actually subscribed on any PySide6 install
and always fell back to the 30 s poll. These are source-shape guards (same
style as test_gui_refresh_threading.py) because CI has no display / D-Bus.
"""

from __future__ import annotations

from pathlib import Path

TRAY = Path(__file__).resolve().parent.parent / "src" / "winpodx" / "desktop" / "tray.py"


def _src() -> str:
    return TRAY.read_text(encoding="utf-8")


def test_subscription_does_not_pass_bare_callable() -> None:
    src = _src()
    # The broken 6-arg shape ended with the bare function as the last arg.
    assert '"b",\n                _on_prepare_for_sleep,' not in src


def test_subscription_uses_qobject_receiver_and_slot() -> None:
    src = _src()
    assert 'SLOT("onPrepareForSleep(bool)")' in src
    assert "class _SleepListener(QObject):" in src
    assert "@Slot(bool)" in src


def test_receiver_reference_is_durable() -> None:
    # A local-only receiver can be GC'd out from under the D-Bus connection
    # (the #573 lesson); it must be attached to a long-lived owner.
    src = _src()
    assert "tray._winpodx_sleep_listener = _SleepListener()" in src


def test_typeerror_guard_kept() -> None:
    # Defensive guard stays: a future PySide6 overload change must degrade to
    # the 30 s poll, not crash the tray.
    src = _src()
    idx = src.index("PrepareForSleep")
    assert "except TypeError" in src[idx:]
