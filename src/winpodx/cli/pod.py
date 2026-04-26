"""CLI handlers for pod management."""

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
    elif cmd == "apply-fixes":
        _apply_fixes()
    elif cmd == "sync-password":
        _sync_password(getattr(args, "non_interactive", False))
    else:
        print("Usage: winpodx pod {start|stop|status|restart|apply-fixes|sync-password}")
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


def _apply_fixes() -> None:
    """v0.1.9.3: standalone idempotent runtime apply for existing guests.

    Use case: user upgraded from 0.1.6/0.1.7/0.1.8/0.1.9.x but migrate
    short-circuited with "already current" so the OEM v7+v8 runtime
    fixes never landed on their actual Windows VM. This command pushes
    them all in one shot. Safe to re-run any time — every helper is
    idempotent.
    """
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, pod_status
    from winpodx.core.provisioner import apply_windows_runtime_fixes

    cfg = Config.load()

    if cfg.pod.backend not in ("podman", "docker"):
        print(
            f"Backend {cfg.pod.backend!r} doesn't support runtime apply. "
            "Recreate your container after upgrading to pick up Windows-side fixes."
        )
        sys.exit(2)

    state = pod_status(cfg).state
    if state != PodState.RUNNING:
        print(f"Pod is {state.value}, not running.")
        print("Start the pod first with: winpodx pod start --wait")
        sys.exit(2)

    print("Applying Windows-side runtime fixes...")
    results = apply_windows_runtime_fixes(cfg)

    failures = []
    for name, status_str in results.items():
        marker = "OK" if status_str == "ok" else "FAIL"
        print(f"  [{marker}] {name}: {status_str}")
        if status_str != "ok":
            failures.append(name)

    if failures:
        print(
            f"\n{len(failures)} of {len(results)} apply(s) failed. "
            "Check `winpodx info` and try again, or recreate the container."
        )
        sys.exit(3)
    print("\nAll fixes applied to existing guest (no container recreate needed).")


def _sync_password(non_interactive: bool) -> None:
    """v0.1.9.5: rescue path when cfg.password no longer matches Windows.

    Use case: prior releases (0.1.0 through 0.1.9.4) ran password rotation
    via a broken `podman exec ... powershell.exe net user` path that never
    actually reached the Windows VM. Host-side cfg.password has drifted
    while Windows still has whatever the original install.bat / OEM
    unattend.xml set it to. Symptom: FreeRDP launches fail with auth
    error.

    This command authenticates ONCE with a user-supplied "last known
    working" password (typically the original from initial setup, or the
    value in compose.yml's PASSWORD env var), then runs `net user` inside
    Windows to reset the account password to the current cfg.password.
    On success, password rotation works normally going forward (now that
    v0.1.9.5 has migrated `_change_windows_password` to FreeRDP RemoteApp).
    """
    import getpass
    import os

    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(f"sync-password not supported for backend {cfg.pod.backend!r}.")
        sys.exit(2)

    if not cfg.rdp.password:
        print("No password set in cfg — nothing to sync to.")
        sys.exit(2)

    if non_interactive:
        recovery_pw = os.environ.get("WINPODX_RECOVERY_PASSWORD", "")
        if not recovery_pw:
            print("ERROR: --non-interactive requires WINPODX_RECOVERY_PASSWORD env var.")
            sys.exit(2)
    else:
        print(
            "winpodx will authenticate once with a recovery password (the password "
            "Windows currently accepts), then reset the Windows account to the "
            "value in your winpodx config."
        )
        print()
        print("Common recovery passwords to try:")
        print("  - The password from your original setup (compose.yml PASSWORD env)")
        print("  - The first password you set when winpodx was installed")
        print()
        recovery_pw = getpass.getpass("Recovery password (input hidden): ")
        if not recovery_pw:
            print("Aborted.")
            sys.exit(2)

    # Build a temporary Config copy with the recovery password so
    # run_in_windows uses the right credentials for FreeRDP auth.
    rescue_cfg = Config.load()
    rescue_cfg.rdp.password = recovery_pw

    target_pw = cfg.rdp.password.replace("'", "''")
    user = cfg.rdp.user.replace("'", "''")
    payload = f"& net user '{user}' '{target_pw}' | Out-Null\nWrite-Output 'password reset'\n"

    print("Authenticating with recovery password and resetting Windows account...")
    try:
        result = run_in_windows(rescue_cfg, payload, description="sync-password", timeout=45)
    except WindowsExecError as e:
        print(f"FAIL: channel failure with recovery password: {e}")
        print(
            "\nThe recovery password didn't authenticate either. Options:\n"
            "  1. Try sync-password again with a different recovery password.\n"
            "  2. Open `winpodx app run desktop` and reset manually:\n"
            f"       net user {cfg.rdp.user} <password from `winpodx config show`>\n"
            "  3. As a last resort, recreate the container with `podman rm -f` + "
            "`winpodx pod start --wait`."
        )
        sys.exit(3)

    if result.rc != 0:
        print(f"FAIL: password reset script failed (rc={result.rc}): {result.stderr.strip()}")
        sys.exit(3)

    print("OK: Windows account password is now in sync with winpodx config.")
    print("Password rotation will now work normally.")


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
