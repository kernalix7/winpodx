"""Pending-setup tracker (v0.2.1).

Records steps that ``install.sh`` couldn't complete on first run so the
next CLI / GUI invocation can finish them silently rather than leaving
the user with a half-installed pod.

State file: ``~/.config/winpodx/.pending_setup`` — one step ID per line.
Step IDs:
- ``wait_ready``  — Windows VM didn't finish first-boot in install.sh's
  60-minute budget.
- ``migrate``     — ``winpodx migrate`` failed (usually because guest
  was still booting).
- ``discovery``   — ``winpodx app refresh`` failed.

The resume path runs in this order: wait → migrate's apply step → app
refresh. Each step removes itself from the pending list on success.
"""

from __future__ import annotations

import logging
from pathlib import Path

from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)

_PENDING_FILE = ".pending_setup"
_VALID_STEPS = frozenset({"wait_ready", "migrate", "discovery"})


def _path() -> Path:
    return Path(config_dir()) / _PENDING_FILE


def has_pending() -> bool:
    """Return True when at least one step is recorded as pending."""
    p = _path()
    if not p.exists():
        return False
    try:
        return any(line.strip() in _VALID_STEPS for line in p.read_text().splitlines())
    except OSError:
        return False


def list_pending() -> list[str]:
    """Return the current pending steps in the order they should run."""
    p = _path()
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen = set()
    ordered = []
    # Canonical order: wait_ready first, then migrate, then discovery.
    for step in ("wait_ready", "migrate", "discovery"):
        if step in raw and step in _VALID_STEPS and step not in seen:
            ordered.append(step)
            seen.add(step)
    return ordered


def remove_step(step: str) -> None:
    """Remove ``step`` from the pending list. Deletes the file when empty."""
    p = _path()
    if not p.exists():
        return
    try:
        remaining = [
            line.strip()
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() in _VALID_STEPS and line.strip() != step
        ]
    except OSError:
        return
    if not remaining:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        return
    try:
        p.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    except OSError as e:
        log.debug("could not rewrite pending file: %s", e)


def clear() -> None:
    """Remove the pending file entirely (call after full success)."""
    try:
        _path().unlink(missing_ok=True)
    except OSError:
        pass


def resume(printer=print) -> None:
    """Run the pending steps in order, removing each on success.

    Best-effort — never raises. ``printer`` is a callable that takes a
    single string (defaults to ``print``); the GUI passes a function
    that streams to its log panel.
    """
    pending = list_pending()
    if not pending:
        return

    from winpodx.core.config import Config

    cfg = Config.load()
    printer(f"[winpodx] Resuming {len(pending)} pending setup step(s): {', '.join(pending)}")

    if "wait_ready" in pending:
        try:
            from winpodx.core.provisioner import wait_for_windows_responsive

            printer("[winpodx] Waiting for Windows guest to be responsive (up to 5 min)...")
            if wait_for_windows_responsive(cfg, timeout=300):
                remove_step("wait_ready")
                printer("[winpodx] Windows guest is ready.")
            else:
                printer("[winpodx] Guest still booting — leaving pending for next invocation.")
                return  # No point trying migrate/discovery if guest isn't ready.
        except Exception as e:  # noqa: BLE001
            printer(f"[winpodx] wait_ready resume failed: {e}")
            return

    if "migrate" in pending:
        try:
            from winpodx.core.provisioner import apply_windows_runtime_fixes

            printer("[winpodx] Re-running runtime apply...")
            results = apply_windows_runtime_fixes(cfg)
            if all(v == "ok" for v in results.values()):
                remove_step("migrate")
                printer("[winpodx] Runtime apply complete.")
            else:
                printer(f"[winpodx] Apply partial: {results} — leaving pending.")
        except Exception as e:  # noqa: BLE001
            printer(f"[winpodx] migrate resume failed: {e}")

    if "discovery" in pending:
        try:
            from winpodx.cli.app import _register_desktop_entries
            from winpodx.core.discovery import discover_apps, persist_discovered

            printer("[winpodx] Discovering installed Windows apps...")
            apps = discover_apps(cfg, timeout=180)
            persist_discovered(apps)
            if apps:
                _register_desktop_entries(apps)
            remove_step("discovery")
            printer(f"[winpodx] Discovery complete — {len(apps)} app(s) registered.")
        except Exception as e:  # noqa: BLE001
            printer(f"[winpodx] discovery resume failed: {e}")
