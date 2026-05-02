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
    elif cmd == "multi-session":
        _multi_session(args.action)
    elif cmd == "wait-ready":
        _wait_ready(args.timeout, getattr(args, "logs", False))
    else:
        print(
            "Usage: winpodx pod {start|stop|status|restart|apply-fixes|"
            "sync-password|multi-session|wait-ready}"
        )
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

    # v0.2.0.1: container RUNNING != Windows VM accepting FreeRDP yet.
    # Wait for the guest to finish booting before firing applies, so a
    # user running `winpodx pod apply-fixes` right after a fresh start
    # doesn't see 3×60s of timeout cascades.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print("Waiting for Windows guest to finish booting (up to 180s)...")
    if not wait_for_windows_responsive(cfg, timeout=180):
        print(
            "Windows guest still booting after 180s. Wait a bit longer, "
            "then re-run `winpodx pod apply-fixes`."
        )
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


def _multi_session(action: str) -> None:
    """Toggle bundled rdprrap multi-session RDP at runtime.

    Activation needs to patch ``HKLM\\...\\TermService\\Parameters\\
    ServiceDll`` and then cycle TermService so the new DLL loads. The
    cycle kills every active RDP session, including the agent's own
    user session — so an inline ``/exec`` can't drive the activation:
    the agent dies before the response can return.

    ``enable`` therefore spawns ``rdprrap-activate.ps1`` *detached*
    via wscript+hidden-launcher.vbs (same pattern agent-respawn.ps1
    uses) and returns immediately. The detached script runs
    ``rdprrap-installer install`` with retries, restarts TermService,
    verifies ``ServiceDll`` flipped to ``termwrap.dll``, and writes
    the outcome to ``C:\\winpodx\\rdprrap\\.activation_status``. The
    user reconnects after the brief disconnect; the agent auto-starts
    via HKCU\\Run; subsequent ``status`` / ``apply-fixes`` calls read
    the marker.

    ``status`` is a marker probe — same source the provisioner's
    multi_session apply step uses, so output is consistent across
    surfaces.

    ``disable`` still calls ``rdprrap-conf --disable`` inline.
    Disable just clears the registry patch; TermService doesn't need
    to be cycled until the next reboot, so the agent's session is
    safe.

    Existing v0.3.0-RTM1 pods get rdprrap-activate.ps1 staged on the
    next ``winpodx pod apply-fixes`` (vbs_launchers step pushes it).
    Container recreate is no longer required.
    """
    from winpodx.core.config import Config

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(f"multi-session not supported for backend {cfg.pod.backend!r}.")
        sys.exit(2)

    if action == "on":
        _multi_session_enable(cfg)
    elif action == "off":
        _multi_session_disable(cfg)
    elif action == "status":
        _multi_session_status(cfg)
    else:  # argparse already restricts choices, but be explicit
        print(f"Unknown multi-session action: {action!r}")
        sys.exit(2)


def _multi_session_enable(cfg) -> None:  # type: ignore[no-untyped-def]
    """Spawn rdprrap-activate.ps1 detached + return immediately."""
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    target_dir = "C:\\Users\\Public\\winpodx\\launchers"
    activate_ps1 = f"{target_dir}\\rdprrap-activate.ps1"
    hidden_vbs = f"{target_dir}\\hidden-launcher.vbs"

    # Verify the activation script is staged. If not, surface a clear
    # action ("run apply-fixes first") instead of a silent no-op.
    payload = (
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$activate = '{activate_ps1}'",
                f"$hidden = '{hidden_vbs}'",
                "if (-not (Test-Path -LiteralPath $activate)) {",
                "    Write-Output 'NOT-STAGED'",
                "    exit 2",
                "}",
                "if (-not (Test-Path -LiteralPath $hidden)) {",
                "    Write-Output 'NOT-STAGED'",
                "    exit 2",
                "}",
                # -Detached makes the script wait 2s before TermService cycle
                # so this /exec response can return before the agent's user
                # session dies. install.bat invokes the same script WITHOUT
                # -Detached (synchronous OEM-time path).
                "$startArgs = @($hidden, 'powershell.exe', '-NoProfile',",
                "         '-ExecutionPolicy', 'Bypass', '-File', $activate,",
                "         '-Detached')",
                "Start-Process wscript.exe -ArgumentList $startArgs | Out-Null",
                "Write-Output 'QUEUED'",
                "exit 0",
            ]
        )
        + "\n"
    )

    print("Queuing multi-session activation (detached)...")
    try:
        result = run_via_transport(cfg, payload, description="multi-session-enable", timeout=20)
    except WindowsExecError as e:
        print(f"FAIL: channel failure: {e}")
        sys.exit(3)

    output = (result.stdout or "").strip()
    if result.rc == 2 and "NOT-STAGED" in output:
        print(
            "rdprrap-activate.ps1 not staged in the guest.\n"
            "Run `winpodx pod apply-fixes` first — its vbs_launchers step "
            "pushes the activation script. Then re-run "
            "`winpodx pod multi-session on`."
        )
        sys.exit(2)
    if result.rc != 0:
        print(f"FAIL: rc={result.rc}: {result.stderr.strip() or output}")
        sys.exit(3)

    print("OK: activation queued.")
    print(
        "rdprrap-activate.ps1 will run rdprrap-installer + restart TermService.\n"
        "TermService restart will briefly disconnect any active RDP sessions; "
        "reconnect after ~10s.\n"
        "After reconnecting, run `winpodx pod multi-session status` "
        "(or `winpodx pod apply-fixes`) to confirm activation."
    )


def _multi_session_disable(cfg) -> None:  # type: ignore[no-untyped-def]
    """Inline disable via rdprrap-conf — safe (no TermService cycle)."""
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    candidates = [
        r"C:\winpodx\rdprrap\rdprrap-conf.exe",
        r"C:\OEM\rdprrap\rdprrap-conf.exe",
        r"C:\OEM\rdprrap-conf.exe",
        r"C:\Program Files\rdprrap\rdprrap-conf.exe",
    ]
    payload_lines = ["$rdprrap = $null"]
    for path in candidates:
        payload_lines.append(
            f"if (-not $rdprrap -and (Test-Path '{path}')) {{ $rdprrap = '{path}' }}"
        )
    payload_lines += [
        "if (-not $rdprrap) {",
        "    Write-Output 'rdprrap-conf not found in any expected path'",
        "    exit 2",
        "}",
        "& $rdprrap --disable",
        "exit $LASTEXITCODE",
    ]
    payload = "\n".join(payload_lines) + "\n"

    print("Disabling multi-session RDP via rdprrap...")
    try:
        result = run_via_transport(cfg, payload, description="multi-session-disable", timeout=45)
    except WindowsExecError as e:
        print(f"FAIL: channel failure: {e}")
        sys.exit(3)

    output = (result.stdout or "").strip()
    if output:
        print(output)
    if result.rc == 0:
        print("OK: multi-session disabled.")
    elif result.rc == 2:
        print(
            "rdprrap-conf was not found in the guest. The OEM v6+ bundle "
            "installs it; older containers may need to be recreated to "
            "pick up the install.bat staging step."
        )
        sys.exit(2)
    else:
        print(f"FAIL: rdprrap-conf rc={result.rc}: {result.stderr.strip()}")
        sys.exit(3)


def _multi_session_status(cfg) -> None:  # type: ignore[no-untyped-def]
    """Probe the activation_status marker + tail install.log on failure."""
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    payload = (
        "\n".join(
            [
                "$marker = 'C:\\winpodx\\rdprrap\\.activation_status'",
                "$logPath = 'C:\\winpodx\\rdprrap\\install.log'",
                "if (-not (Test-Path -LiteralPath $marker)) {",
                "    Write-Output 'no activation marker; pre-OEM-v15 install or never activated'",
                "    Write-Output 'run `winpodx pod multi-session on` to activate at runtime'",
                "    exit 0",
                "}",
                "$status = (Get-Content -LiteralPath $marker -ErrorAction"
                " SilentlyContinue | Select-Object -First 1)",
                'Write-Output ("rdprrap status: $status")',
                "if ($status -ne 'enabled' -and (Test-Path -LiteralPath $logPath)) {",
                "    Write-Output '--- install.log tail ---'",
                "    Get-Content -LiteralPath $logPath -Tail 30 -ErrorAction SilentlyContinue",
                "    Write-Output '--- end install.log ---'",
                "}",
                "exit 0",
            ]
        )
        + "\n"
    )

    print("Querying multi-session status...")
    try:
        result = run_via_transport(cfg, payload, description="multi-session-status", timeout=20)
    except WindowsExecError as e:
        print(f"FAIL: channel failure: {e}")
        sys.exit(3)

    output = (result.stdout or "").strip()
    if output:
        print(output)
    if result.rc != 0:
        print(f"FAIL: rc={result.rc}: {result.stderr.strip()}")
        sys.exit(3)


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


def _wait_ready(timeout: int, show_logs: bool) -> None:
    """v0.2.0.5: multi-phase wait for the Windows VM to finish first-boot.

    Polls three checkpoints with elapsed-time stamps so the user sees
    progress instead of a silent multi-minute hang on `curl install.sh`:

      [1/3] Container running                  (e.g. 5s)
      [2/3] RDP port open                      (typically 30-90s)
      [3/3] Windows ready (RemoteApp probes OK) (typically 2-8min on first boot)

    With ``--logs``, container stdout is tailed in a background thread
    and surfaced as ``[container] ...`` lines so the user can see Windows
    actually doing work (Sysprep, OEM apply, etc.) instead of a black
    box.
    """
    import threading
    import time as _time
    from subprocess import PIPE, Popen

    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, check_rdp_port, pod_status
    from winpodx.core.provisioner import wait_for_windows_responsive

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(f"wait-ready not supported for backend {cfg.pod.backend!r} (podman/docker only).")
        sys.exit(2)

    timeout = max(60, min(7200, int(timeout)))
    start = _time.monotonic()

    def elapsed() -> str:
        s = int(_time.monotonic() - start)
        return f"{s // 60:02d}:{s % 60:02d}"

    log_proc: Popen | None = None
    log_stop = threading.Event()
    if show_logs:
        try:
            # v0.2.0.7: --tail 100 so the user sees recent context (Windows
            # ISO download, current boot stage) instead of nothing — dockur
            # may have already printed minutes of progress before wait-ready
            # runs. Both stdout and stderr are drained because dockur's
            # progress output is split across both streams (download
            # bytes/sec on one, boot phase on the other).
            log_proc = Popen(
                [cfg.pod.backend, "logs", "-f", "--tail", "100", cfg.pod.container_name],
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                bufsize=1,
            )

            def _drain(stream) -> None:  # type: ignore[no-untyped-def]
                if stream is None:
                    return
                for line in stream:
                    if log_stop.is_set():
                        break
                    line = line.rstrip()
                    if line:
                        print(f"       [container] {line}")

            threading.Thread(target=_drain, args=(log_proc.stdout,), daemon=True).start()
            threading.Thread(target=_drain, args=(log_proc.stderr,), daemon=True).start()
        except (FileNotFoundError, OSError) as e:
            print(f"       (could not tail container logs: {e})")
            log_proc = None

    try:
        # --- [1/3] Container running ---
        print(f"[1/3] Waiting for container to start...      ({elapsed()})")
        deadline = start + timeout
        while _time.monotonic() < deadline:
            try:
                if pod_status(cfg).state == PodState.RUNNING:
                    print(f"      OK Container running                   ({elapsed()})")
                    break
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(2)
        else:
            print(f"      FAIL Timeout waiting for container       ({elapsed()})")
            sys.exit(3)

        # --- [2/3] RDP port open ---
        print(f"[2/3] Waiting for Windows RDP service...     ({elapsed()})")
        while _time.monotonic() < deadline:
            if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
                print(f"      OK RDP port {cfg.rdp.port} open                  ({elapsed()})")
                break
            _time.sleep(3)
        else:
            print(f"      FAIL Timeout waiting for RDP port        ({elapsed()})")
            sys.exit(3)

        # --- [3/3] FreeRDP RemoteApp activation ---
        print(f"[3/3] Waiting for Windows activation...      ({elapsed()})")
        remaining = max(60, int(deadline - _time.monotonic()))
        if wait_for_windows_responsive(cfg, timeout=remaining):
            print(f"      OK Windows ready                         ({elapsed()})")
        else:
            print(
                f"      FAIL Timeout waiting for Windows ready   ({elapsed()})\n"
                "      Run `winpodx pod status` later and re-run "
                "`winpodx pod wait-ready` once the container is fully up."
            )
            sys.exit(3)
    finally:
        log_stop.set()
        if log_proc is not None:
            try:
                log_proc.terminate()
                log_proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    log_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
