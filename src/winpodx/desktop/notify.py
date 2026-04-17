"""Desktop notification integration via D-Bus / libnotify."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def _sanitize(text: str) -> str:
    """Remove control characters, escape HTML, and limit length for safe display."""
    cleaned = "".join(c for c in text if c.isprintable())
    # Escape HTML to prevent markup injection in notification bodies
    cleaned = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return cleaned[:200]


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
    # 5 s cap: on Wayland sessions without a notification daemon, notify-send
    # can block indefinitely waiting for a D-Bus reply. That would stall CLI
    # commands (e.g. `winpodx app run`) past their visible work, so cut it off.
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except FileNotFoundError:
        pass  # notify-send not available
    except subprocess.TimeoutExpired:
        log.debug("notify-send timed out after 5s (no notification daemon?)")


def notify_pod_started(ip: str) -> None:
    send_notification("winpodx", f"Windows pod started at {ip}")


def notify_pod_stopped() -> None:
    send_notification("winpodx", "Windows pod stopped")


def notify_app_launched(app_name: str) -> None:
    send_notification("winpodx", f"{app_name} launched")


def notify_error(message: str) -> None:
    send_notification("winpodx Error", message, urgency="critical")
