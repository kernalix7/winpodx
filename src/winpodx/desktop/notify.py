# SPDX-License-Identifier: MIT
"""Desktop notification integration via D-Bus / libnotify."""

from __future__ import annotations

import logging
import subprocess

from winpodx.core.i18n import tr

log = logging.getLogger(__name__)


def _sanitize(text: str) -> str:
    """Strip control chars, truncate to 200, then HTML-escape (order preserves entities)."""
    cleaned = "".join(c for c in text if c.isprintable())
    cleaned = cleaned[:200]
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_notification(
    title: str,
    body: str,
    icon: str = "winpodx",
    urgency: str = "normal",
) -> None:
    """Send a desktop notification using notify-send."""
    cmd = [
        "notify-send",
        f"--urgency={urgency}",
        f"--icon={icon}",
        "--app-name=winpodx",
        _sanitize(title),
        _sanitize(body),
    ]
    # 5s cap: notify-send may block on Wayland sessions without a daemon.
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except FileNotFoundError:
        pass  # notify-send not available
    except subprocess.TimeoutExpired:
        log.debug("notify-send timed out after 5s (no notification daemon?)")


def notify_pod_started(ip: str) -> None:
    send_notification("WinPodX", tr("Windows pod started at {ip}").format(ip=ip))


def notify_pod_stopped() -> None:
    send_notification("WinPodX", tr("Windows pod stopped"))


def notify_error(message: str) -> None:
    send_notification("WinPodX Error", message, urgency="critical")


def notify_pod_unresponsive(ip: str) -> None:
    """Initial transition notification — pod went from RUNNING to UNRESPONSIVE.

    Fires once when the tray / GUI status poller observes the state change.
    The follow-up auto-recovery is launched in parallel; success / failure
    is reported via ``notify_pod_recovered`` or
    ``notify_pod_needs_manual_restart``.
    """
    send_notification(
        "WinPodX",
        tr(
            "Windows pod at {ip} stopped responding to RDP. "
            "Attempting to restart the RDP service in the guest..."
        ).format(ip=ip),
        urgency="normal",
    )


def notify_pod_recovered() -> None:
    """Auto-recovery succeeded — TermService was cycled and RDP came back."""
    send_notification(
        "WinPodX",
        tr("Windows pod recovered (RDP service restarted in the guest). No action needed."),
        urgency="low",
    )


def notify_pod_needs_manual_restart(detail: str = "") -> None:
    """Auto-recovery failed — direct the user at ``winpodx pod restart``.

    `detail` is one of the ``RecoveryAction`` failure modes (agent
    unreachable, RDP still down after TermService cycle) and is appended
    so the user has a hint at why the cheap recovery didn't help.
    """
    body = tr(
        "Windows pod is alive but not responding to RDP, and auto-recovery "
        "didn't bring it back. Run `winpodx pod restart` to recycle the "
        "container."
    )
    if detail:
        body += f"\n({detail})"
    send_notification("WinPodX", body, urgency="critical")
