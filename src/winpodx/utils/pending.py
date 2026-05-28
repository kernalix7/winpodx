# SPDX-License-Identifier: MIT
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
    from winpodx.core.provisioner import finish_provisioning

    cfg = Config.load()
    printer(f"[winpodx] Resuming {len(pending)} pending setup step(s): {', '.join(pending)}")

    # 0.6.0 item B: the wait-ready → apply-fixes → discovery chain this used
    # to assemble by hand (with discover_apps + persist + register inline)
    # now lives in the single source of truth ``finish_provisioning``. The
    # historical pending-step distinctions still drive which result we use
    # to clear which marker:
    #   * wait_ready  ← results["wait_ready"]
    #   * migrate     ← results["apply_fixes"] (all helpers "ok")
    #   * discovery   ← results["discovery"]
    # Parameters preserve the old behaviour: 300s wait (NOT 3600), soft
    # agent settle (require_agent=False), no reverse-open, discovery with a
    # 3× retry. Discovery always runs in the helper; we only clear the
    # discovery marker when it was actually pending.
    def _on_progress(stage: str, detail: str) -> None:
        printer(f"[winpodx] {stage}: {detail}")

    try:
        results = finish_provisioning(
            cfg,
            wait_timeout=300,
            require_agent=False,
            with_reverse_open=False,
            with_discovery=True,
            retries=3,
            on_progress=_on_progress,
        )
    except Exception as e:  # noqa: BLE001 — resume is best-effort, never raises
        printer(f"[winpodx] resume failed: {e}")
        return

    if results.get("wait_ready") == "timeout":
        printer("[winpodx] Guest still booting — leaving pending for next invocation.")
        return  # No point clearing migrate/discovery if the guest isn't ready.
    if "wait_ready" in pending:
        remove_step("wait_ready")
        printer("[winpodx] Windows guest is ready.")

    if "migrate" in pending:
        apply_results = results.get("apply_fixes", {})
        if apply_results and all(v == "ok" for v in apply_results.values()):
            remove_step("migrate")
            printer("[winpodx] Runtime apply complete.")
        else:
            printer(f"[winpodx] Apply partial: {apply_results} — leaving pending.")

    if "discovery" in pending:
        discovery = results.get("discovery", "")
        if isinstance(discovery, str) and discovery.startswith("failed:"):
            printer(f"[winpodx] discovery resume failed ({discovery}) — leaving pending.")
        else:
            remove_step("discovery")
            printer(f"[winpodx] Discovery complete — {discovery}.")
