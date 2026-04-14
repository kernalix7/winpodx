"""Desktop notification integration via D-Bus / libnotify."""

from __future__ import annotations

import subprocess


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
    try:
        subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        pass  # notify-send not available


def notify_pod_started(ip: str) -> None:
    send_notification("winpodx", f"Windows pod started at {ip}")


def notify_pod_stopped() -> None:
    send_notification("winpodx", "Windows pod stopped")


def notify_app_launched(app_name: str) -> None:
    send_notification("winpodx", f"{app_name} launched")


def notify_error(message: str) -> None:
    send_notification("winpodx Error", message, urgency="critical")
