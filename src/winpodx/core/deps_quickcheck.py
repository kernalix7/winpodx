"""First-run quick-check snapshot for the GUI Quick Start dialog (v0.2.1).

Returns a flat dict of strings so the dialog can render bullets without
caring about the underlying probe details. Each probe is best-effort —
on any error the value is the human string ``"unknown"`` so the dialog
stays informative even on a half-broken system.
"""

from __future__ import annotations

import shutil
from typing import Any

from winpodx.core.config import Config


def collect_first_run_checks(cfg: Config) -> dict[str, Any]:
    """Run a tiny suite of probes for the GUI Quick Start summary."""
    from winpodx.core.app import list_available_apps
    from winpodx.core.pod import check_rdp_port, pod_status

    out: dict[str, Any] = {}

    # Backend binary on PATH
    try:
        if shutil.which(cfg.pod.backend):
            out["backend"] = "OK"
        else:
            out["backend"] = f"missing — install {cfg.pod.backend} or change backend in Settings"
    except Exception:  # noqa: BLE001
        out["backend"] = "unknown"

    # FreeRDP 3+ on PATH (any of the known names)
    try:
        if any(shutil.which(c) for c in ("xfreerdp3", "xfreerdp", "wlfreerdp3", "wlfreerdp")):
            out["freerdp"] = "OK"
        else:
            out["freerdp"] = "missing — install freerdp 3+"
    except Exception:  # noqa: BLE001
        out["freerdp"] = "unknown"

    # Pod state
    try:
        s = pod_status(cfg)
        out["pod_state"] = s.state.value if hasattr(s.state, "value") else str(s.state)
    except Exception:  # noqa: BLE001
        out["pod_state"] = "unknown"

    # RDP listener reachable?
    try:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
            out["rdp_port"] = f"open at {cfg.rdp.ip}:{cfg.rdp.port}"
        else:
            out["rdp_port"] = "not reachable yet (Windows may still be booting on first install)"
    except Exception:  # noqa: BLE001
        out["rdp_port"] = "unknown"

    # Discovered apps count
    try:
        out["apps_count"] = len(list_available_apps())
    except Exception:  # noqa: BLE001
        out["apps_count"] = "unknown"

    return out
