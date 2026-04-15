"""CLI handlers for pod management — no external dependencies."""

from __future__ import annotations

import argparse
import sys


def handle_pod(args: argparse.Namespace) -> None:
    """Route pod subcommands."""
    cmd = args.pod_command
    if cmd == "start":
        _start(args.wait, args.timeout)
    elif cmd == "stop":
        _stop()
    elif cmd == "status":
        _status()
    elif cmd == "restart":
        _restart()
    else:
        print("Usage: winpodx pod {start|stop|status|restart}")
        sys.exit(1)


def _start(wait: bool, timeout: int) -> None:
    from winpodx.core.pod import PodState, get_backend, start_pod
    from winpodx.core.provisioner import _ensure_compose, _ensure_config
    from winpodx.desktop.notify import notify_pod_started

    timeout = max(1, min(3600, timeout))
    cfg = _ensure_config()
    if cfg.pod.backend in ("podman", "docker"):
        _ensure_compose(cfg)

    print(f"Starting pod (backend: {cfg.pod.backend})...")
    status = start_pod(cfg)

    if status.state == PodState.RUNNING:
        print(f"Pod is running at {status.ip}")
        notify_pod_started(status.ip)
    elif status.state == PodState.STARTING:
        if wait:
            print(f"Waiting for RDP at {status.ip}:{cfg.rdp.port}...")
            backend = get_backend(cfg)
            if backend.wait_for_ready(timeout):
                print("Pod is ready!")
                notify_pod_started(status.ip)
            else:
                print("Timeout waiting for RDP.", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Pod is starting... RDP not yet available at {status.ip}")
            print("Use 'winpodx pod start --wait' to wait for readiness.")
    else:
        print(f"Failed to start pod: {status.error}", file=sys.stderr)
        sys.exit(1)


def _stop() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import stop_pod
    from winpodx.core.process import list_active_sessions
    from winpodx.desktop.notify import notify_pod_stopped

    cfg = Config.load()
    sessions = list_active_sessions()
    if sessions:
        names = ", ".join(s.app_name for s in sessions)
        print(f"Active sessions: {names}")
        answer = input("Stop pod anyway? (y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            return

    print("Stopping pod...")
    stop_pod(cfg)
    print("Pod stopped.")
    notify_pod_stopped()


def _status() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import pod_status
    from winpodx.core.process import list_active_sessions

    cfg = Config.load()
    s = pod_status(cfg)

    print(f"Backend:  {cfg.pod.backend}")
    print(f"State:    {s.state.value}")
    print(f"IP:       {s.ip or 'N/A'}")
    print(f"RDP Port: {cfg.rdp.port}")

    sessions = list_active_sessions()
    if sessions:
        print(f"Sessions: {len(sessions)} active")
        for sess in sessions:
            print(f"  - {sess.app_name} (PID {sess.pid})")

    if s.error:
        print(f"Error:    {s.error}")


def _restart() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, start_pod, stop_pod

    cfg = Config.load()
    print("Restarting pod...")
    stop_pod(cfg)
    status = start_pod(cfg)

    if status.state in (PodState.RUNNING, PodState.STARTING):
        print("Pod restarted.")
    else:
        print(f"Failed to restart: {status.error}", file=sys.stderr)
        sys.exit(1)
