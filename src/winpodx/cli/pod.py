# SPDX-License-Identifier: MIT
"""CLI handlers for pod management."""

from __future__ import annotations

import argparse
import re
import sys
import threading

from winpodx.cli.main import _emit_deprecation as _deprecate_pod
from winpodx.core.i18n import tr


def handle_pod(args: argparse.Namespace) -> None:
    """Route pod subcommands.

    Lifecycle subcommands (start / stop / status / restart / recreate /
    wait-ready) are the canonical home and carry no deprecation.

    Guest-side and install subcommands have moved to ``winpodx guest`` and
    ``winpodx install`` respectively.  Their old ``pod <x>`` forms remain
    registered so existing scripts keep working, but each emits a one-line
    deprecation notice to stderr before delegating to the shared handler.
    """
    cmd = args.pod_command
    if cmd == "start":
        _start(args.wait, args.timeout, tuning_override=getattr(args, "tuning", None))
    elif cmd == "stop":
        _stop()
    elif cmd == "status":
        _status()
    elif cmd == "restart":
        _restart()
    elif cmd == "recreate":
        _recreate(wipe_storage=getattr(args, "wipe_storage", False))
    elif cmd == "wait-ready":
        _wait_ready(args.timeout, getattr(args, "logs", False), getattr(args, "verbose", False))
    # --- deprecated aliases: guest-side operations ---
    elif cmd == "apply-fixes":
        _deprecate_pod("pod apply-fixes", "guest apply-fixes")
        _apply_fixes()
    elif cmd == "sync-password":
        _deprecate_pod("pod sync-password", "guest sync-password")
        _sync_password(getattr(args, "non_interactive", False))
    elif cmd == "multi-session":
        _deprecate_pod("pod multi-session", "guest multi-session")
        _multi_session(args.action)
    elif cmd == "recover-oem":
        _deprecate_pod("pod recover-oem", "guest recover-oem")
        _recover_oem()
    elif cmd == "sync-guest":
        _deprecate_pod("pod sync-guest", "guest sync")
        _sync_guest(force=getattr(args, "force", False))
    # --- deprecated aliases: install / storage operations ---
    elif cmd == "install-status":
        _deprecate_pod("pod install-status", "install status")
        from winpodx.cli.pod_install_status import handle as handle_install_status

        sys.exit(handle_install_status(args))
    elif cmd == "install-resume":
        _deprecate_pod("pod install-resume", "install resume")
        from winpodx.cli.pod_install_resume import handle as handle_install_resume

        sys.exit(handle_install_resume(args))
    elif cmd == "grow-disk":
        _deprecate_pod("pod grow-disk", "install grow-disk")
        _grow_disk(
            target_size=getattr(args, "size", None),
            increment=getattr(args, "increment", None),
            extend_only=getattr(args, "extend_only", False),
            assume_yes=getattr(args, "yes", False),
        )
    elif cmd == "disk-usage":
        _deprecate_pod("pod disk-usage", "install disk-usage")
        _disk_usage()
    else:
        print(tr("Usage: winpodx pod {start|stop|status|restart|recreate|wait-ready}"))
        sys.exit(1)


def _start(wait: bool, timeout: int, tuning_override: str | None = None) -> None:
    from winpodx.core.pod import PodState, get_backend, start_pod
    from winpodx.core.provisioner import _ensure_compose, _ensure_config
    from winpodx.desktop.notify import notify_pod_started

    timeout = max(1, min(3600, timeout))
    cfg = _ensure_config()
    # #245: ``--tuning`` is a one-shot override of cfg.pod.tuning_profile.
    # The override never reaches disk -- we mutate the in-memory cfg only,
    # so _ensure_compose() regenerates compose.yaml with the new ARGUMENTS
    # for this invocation but the user's persisted preference is intact.
    if tuning_override is not None and tuning_override != cfg.pod.tuning_profile:
        print(
            tr("Overriding tuning_profile for this run: {old} -> {new}").format(
                old=repr(cfg.pod.tuning_profile), new=repr(tuning_override)
            )
        )
        cfg.pod.tuning_profile = tuning_override
    if cfg.pod.backend in ("podman", "docker"):
        _ensure_compose(cfg)

    print(tr("Starting pod (backend: {backend})...").format(backend=cfg.pod.backend))
    status = start_pod(cfg)

    if status.state == PodState.RUNNING:
        print(tr("Pod is running at {ip}").format(ip=status.ip))
        notify_pod_started(status.ip)
    elif status.state == PodState.STARTING:
        if wait:
            print(tr("Waiting for RDP at {ip}:{port}...").format(ip=status.ip, port=cfg.rdp.port))
            backend = get_backend(cfg)
            if backend.wait_for_ready(timeout):
                print(tr("Pod is ready!"))
                notify_pod_started(status.ip)
            else:
                print(tr("Timeout waiting for RDP."), file=sys.stderr)
                sys.exit(1)
        else:
            print(tr("Pod is starting... RDP not yet available at {ip}").format(ip=status.ip))
            print(tr("Use 'winpodx pod start --wait' to wait for readiness."))
    else:
        print(tr("Failed to start pod: {error}").format(error=status.error), file=sys.stderr)
        sys.exit(1)

    # Start the reverse-open listener daemon if the feature is on. The
    # listener watches a host directory for guest-written request files
    # (the FreeRDP drive redirect exposes ~/.local/share/winpodx/
    # reverse-open/incoming/ to the guest), so it can be useful even
    # before the guest comes up. Lifecycle.start_listener is idempotent
    # — if it's already running we just log that and move on.
    _maybe_start_reverse_open_listener(cfg)


def _maybe_start_reverse_open_listener(cfg) -> None:  # type: ignore[no-untyped-def]
    if not getattr(cfg.reverse_open, "enabled", False):
        return
    try:
        from winpodx.cli.host_open import (
            _apps_json,
            _listener_config,
            _seen_uuids_path,
        )
        from winpodx.reverse_open.lifecycle import (
            ListenerStartFailed,
            is_listener_running,
            start_listener,
        )
    except Exception:  # noqa: BLE001 — import surface should never break pod start
        return
    if is_listener_running() is not None:
        return
    listener_cfg = _listener_config(cfg)
    try:
        listener_cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
        listener_cfg.incoming_dir.chmod(0o700)
        pid = start_listener(listener_cfg, _apps_json(), _seen_uuids_path())
        print(tr("  reverse-open listener: started (pid {pid})").format(pid=pid))
    except (ListenerStartFailed, OSError) as exc:
        print(
            tr("  reverse-open listener: start failed ({error})").format(error=exc),
            file=sys.stderr,
        )


def _stop() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import stop_pod
    from winpodx.core.process import list_active_sessions
    from winpodx.desktop.notify import notify_pod_stopped

    cfg = Config.load()
    sessions = list_active_sessions()
    if sessions:
        names = ", ".join(s.app_name for s in sessions)
        print(tr("Active sessions: {names}").format(names=names))
        answer = input(tr("Stop pod anyway? (y/N): ")).strip().lower()
        if answer not in ("y", "yes"):
            return

    # Tear down the reverse-open listener BEFORE the pod itself —
    # the listener is per-pod (spawns guest apps on the host) and
    # has nothing to do once the pod is gone.
    try:
        from winpodx.reverse_open.lifecycle import stop_listener

        if stop_listener():
            print(tr("Reverse-open listener stopped."))
    except Exception:  # noqa: BLE001
        pass

    print(tr("Stopping pod..."))
    stop_pod(cfg)
    print(tr("Pod stopped."))
    notify_pod_stopped()


def _status() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import pod_status
    from winpodx.core.process import list_active_sessions

    cfg = Config.load()
    s = pod_status(cfg)

    print(tr("Backend:  {backend}").format(backend=cfg.pod.backend))
    print(tr("State:    {state}").format(state=s.state.value))
    print(tr("IP:       {ip}").format(ip=s.ip or "N/A"))
    print(tr("RDP Port: {port}").format(port=cfg.rdp.port))

    sessions = list_active_sessions()
    if sessions:
        print(tr("Sessions: {count} active").format(count=len(sessions)))
        for sess in sessions:
            print(tr("  - {app_name} (PID {pid})").format(app_name=sess.app_name, pid=sess.pid))

    if s.error:
        print(tr("Error:    {error}").format(error=s.error))


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
            tr(
                "Backend {backend} doesn't support runtime apply. "
                "Recreate your container after upgrading to pick up Windows-side fixes."
            ).format(backend=repr(cfg.pod.backend))
        )
        sys.exit(2)

    state = pod_status(cfg).state
    if state != PodState.RUNNING:
        print(tr("Pod is {state}, not running.").format(state=state.value))
        print(tr("Start the pod first with: winpodx pod start --wait"))
        sys.exit(2)

    # v0.2.0.1: container RUNNING != Windows VM accepting FreeRDP yet.
    # Wait for the guest to finish booting before firing applies, so a
    # user running `winpodx pod apply-fixes` right after a fresh start
    # doesn't see 3×60s of timeout cascades.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print(tr("Waiting for Windows guest to finish booting (up to 180s)..."))
    if not wait_for_windows_responsive(cfg, timeout=600):
        print(
            tr(
                "Windows guest still booting after 180s. Wait a bit longer, "
                "then re-run `winpodx pod apply-fixes`."
            )
        )
        sys.exit(2)

    print(tr("Applying Windows-side runtime fixes..."))
    results = apply_windows_runtime_fixes(cfg)

    failures = []
    for name, status_str in results.items():
        marker = "OK" if status_str == "ok" else "FAIL"
        print(f"  [{marker}] {name}: {status_str}")
        if status_str != "ok":
            failures.append(name)

    if failures:
        print(
            tr(
                "\n{fail_count} of {total} apply(s) failed. "
                "Check `winpodx info` and try again, or recreate the container."
            ).format(fail_count=len(failures), total=len(results))
        )
        sys.exit(3)
    print(tr("\nAll fixes applied to existing guest (no container recreate needed)."))


def _sync_password(non_interactive: bool) -> None:
    """v0.1.9.5: rescue path when cfg.password no longer matches Windows.

    Use case: prior releases (0.1.0 through 0.1.9.4) ran password rotation
    via a broken ``podman exec ... powershell.exe net user`` path that
    never actually reached the Windows VM. Host-side cfg.password has
    drifted while Windows still has whatever the original install.bat /
    OEM unattend.xml set it to. Symptom: FreeRDP launches fail with auth
    error.

    Two transports can carry the reset:

    - ``AgentTransport`` (preferred) — bearer-token authed; works even
      when cfg.rdp.password no longer matches Windows because the agent
      doesn't care about the user password. No recovery password needed.
    - ``FreeRDP RemoteApp`` (fallback) — needs the user's "last known
      working" password to authenticate, then runs ``net user`` to set
      the account back to cfg.rdp.password.

    Agent-first matters here for the same reason as ``rotate-password``:
    on cachyos the FreeRDP path can hang at 45 s due to a broken
    bidirectional drive redirect. Without the agent path, a user whose
    rotation broke had no way out.
    """
    import getpass
    import os

    from winpodx.core.config import Config
    from winpodx.core.transport import dispatch
    from winpodx.core.transport.base import TransportAuthError, TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(
            tr("sync-password not supported for backend {backend}.").format(
                backend=repr(cfg.pod.backend)
            )
        )
        sys.exit(2)

    if not cfg.rdp.password:
        print(tr("No password set in cfg — nothing to sync to."))
        sys.exit(2)

    target_pw = cfg.rdp.password.replace("'", "''")
    user = cfg.rdp.user.replace("'", "''")
    payload = f"& net user '{user}' '{target_pw}' | Out-Null\nWrite-Output 'password reset'\n"

    # Try the agent first — sidesteps the broken FreeRDP drive redirect
    # on cachyos and avoids prompting for a recovery password the user
    # may not remember.
    try:
        transport = dispatch(cfg, prefer="agent")
    except TransportUnavailable:
        transport = None

    if transport is not None:
        print(tr("Resetting Windows account password via agent..."))
        try:
            result = transport.exec(payload, description="sync-password", timeout=90)
        except TransportAuthError as e:
            print(tr("FAIL: agent rejected the request (auth): {error}").format(error=e))
            print(
                tr(
                    "\nThe agent's bearer token doesn't match what's on the guest. "
                    "This is config drift, not a transient channel failure — fix the "
                    "token mismatch (reinstall agent or re-run install.bat) before "
                    "retrying."
                )
            )
            sys.exit(3)
        except TransportUnavailable as e:
            # Health probe passed but exec failed — treat as fall-through.
            print(
                tr("Agent became unreachable mid-call ({error}); falling back to FreeRDP.").format(
                    error=e
                )
            )
            transport = None
        else:
            if result.rc != 0:
                print(
                    tr("FAIL: password reset script failed (rc={rc}): {stderr}").format(
                        rc=result.rc, stderr=result.stderr.strip()
                    )
                )
                sys.exit(3)
            print(tr("OK: Windows account password is now in sync with winpodx config."))
            print(tr("Password rotation will now work normally."))
            return

    # Agent unavailable — fall back to FreeRDP RemoteApp, which needs a
    # recovery password to authenticate.
    if non_interactive:
        recovery_pw = os.environ.get("WINPODX_RECOVERY_PASSWORD", "")
        if not recovery_pw:
            print(tr("ERROR: --non-interactive requires WINPODX_RECOVERY_PASSWORD env var."))
            sys.exit(2)
    else:
        print(
            tr(
                "winpodx will authenticate once with a recovery password (the password "
                "Windows currently accepts), then reset the Windows account to the "
                "value in your winpodx config."
            )
        )
        print()
        print(tr("Common recovery passwords to try:"))
        print(tr("  - The password from your original setup (compose.yml PASSWORD env)"))
        print(tr("  - The first password you set when winpodx was installed"))
        print()
        recovery_pw = getpass.getpass(tr("Recovery password (input hidden): "))
        if not recovery_pw:
            print(tr("Aborted."))
            sys.exit(2)

    # Build a temporary Config copy with the recovery password so
    # run_in_windows uses the right credentials for FreeRDP auth.
    rescue_cfg = Config.load()
    rescue_cfg.rdp.password = recovery_pw

    print(tr("Authenticating with recovery password and resetting Windows account..."))
    try:
        result = run_in_windows(rescue_cfg, payload, description="sync-password", timeout=120)
    except WindowsExecError as e:
        print(tr("FAIL: channel failure with recovery password: {error}").format(error=e))
        print(
            tr(
                "\nThe recovery password didn't authenticate either. Options:\n"
                "  1. Try sync-password again with a different recovery password.\n"
                "  2. Open `winpodx app run desktop` and reset manually:\n"
                "       net user {user} <password from `winpodx config show`>\n"
                "  3. As a last resort, recreate the container with `podman rm -f` + "
                "`winpodx pod start --wait`."
            ).format(user=cfg.rdp.user)
        )
        sys.exit(3)

    if result.rc != 0:
        print(
            tr("FAIL: password reset script failed (rc={rc}): {stderr}").format(
                rc=result.rc, stderr=result.stderr.strip()
            )
        )
        sys.exit(3)

    print(tr("OK: Windows account password is now in sync with winpodx config."))
    print(tr("Password rotation will now work normally."))


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
        print(
            tr("multi-session not supported for backend {backend}.").format(
                backend=repr(cfg.pod.backend)
            )
        )
        sys.exit(2)

    if action == "on":
        _multi_session_enable(cfg)
    elif action == "off":
        _multi_session_disable(cfg)
    elif action == "status":
        _multi_session_status(cfg)
    else:  # argparse already restricts choices, but be explicit
        print(tr("Unknown multi-session action: {action}").format(action=repr(action)))
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

    print(tr("Queuing multi-session activation (detached)..."))
    try:
        result = run_via_transport(cfg, payload, description="multi-session-enable", timeout=60)
    except WindowsExecError as e:
        print(tr("FAIL: channel failure: {error}").format(error=e))
        sys.exit(3)

    output = (result.stdout or "").strip()
    if result.rc == 2 and "NOT-STAGED" in output:
        print(
            tr(
                "rdprrap-activate.ps1 not staged in the guest.\n"
                "Run `winpodx pod apply-fixes` first — its vbs_launchers step "
                "pushes the activation script. Then re-run "
                "`winpodx pod multi-session on`."
            )
        )
        sys.exit(2)
    if result.rc != 0:
        print(
            tr("FAIL: rc={rc}: {detail}").format(
                rc=result.rc, detail=result.stderr.strip() or output
            )
        )
        sys.exit(3)

    print(tr("OK: activation queued."))
    print(
        tr(
            "rdprrap-activate.ps1 will run rdprrap-installer + restart TermService.\n"
            "TermService restart will briefly disconnect any active RDP sessions; "
            "reconnect after ~10s.\n"
            "After reconnecting, run `winpodx pod multi-session status` "
            "(or `winpodx pod apply-fixes`) to confirm activation."
        )
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

    print(tr("Disabling multi-session RDP via rdprrap..."))
    try:
        result = run_via_transport(cfg, payload, description="multi-session-disable", timeout=120)
    except WindowsExecError as e:
        print(tr("FAIL: channel failure: {error}").format(error=e))
        sys.exit(3)

    output = (result.stdout or "").strip()
    if output:
        print(output)
    if result.rc == 0:
        print(tr("OK: multi-session disabled."))
    elif result.rc == 2:
        print(
            tr(
                "rdprrap-conf was not found in the guest. The OEM v6+ bundle "
                "installs it; older containers may need to be recreated to "
                "pick up the install.bat staging step."
            )
        )
        sys.exit(2)
    else:
        print(
            tr("FAIL: rdprrap-conf rc={rc}: {stderr}").format(
                rc=result.rc, stderr=result.stderr.strip()
            )
        )
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

    print(tr("Querying multi-session status..."))
    try:
        result = run_via_transport(cfg, payload, description="multi-session-status", timeout=60)
    except WindowsExecError as e:
        print(tr("FAIL: channel failure: {error}").format(error=e))
        sys.exit(3)

    output = (result.stdout or "").strip()
    if output:
        print(output)
    if result.rc != 0:
        print(tr("FAIL: rc={rc}: {stderr}").format(rc=result.rc, stderr=result.stderr.strip()))
        sys.exit(3)


def _restart() -> None:
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, start_pod, stop_pod

    cfg = Config.load()
    print(tr("Restarting pod..."))
    stop_pod(cfg)
    status = start_pod(cfg)

    if status.state in (PodState.RUNNING, PodState.STARTING):
        print(tr("Pod restarted."))
    else:
        print(tr("Failed to restart: {error}").format(error=status.error), file=sys.stderr)
        sys.exit(1)


def _recreate(*, wipe_storage: bool) -> None:
    """Regenerate compose.yaml + destroy and re-create the container (#254).

    Differs from ``_restart`` in two ways:

    * Regenerates ``compose.yaml`` from the current config before bringing
      the container up, so first-boot env knobs (language / region /
      keyboard / timezone / edition / backend) that were edited via
      ``winpodx config set`` actually reach dockur.
    * Optionally wipes the Windows storage volume / bind-mount when
      ``--wipe-storage`` is set, so dockur re-runs the full Windows install
      (necessary for language / edition changes to actually take effect --
      dockur honors those env vars only on the initial install, so without
      wiping the disk the changes silently no-op past first boot).
    """
    from winpodx.core.compose import generate_compose
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, start_pod, stop_pod

    cfg = Config.load()

    if wipe_storage:
        # Refuse to run with a non-empty storage_path that points outside
        # winpodx's owned roots -- a typo'd config could ``rm -rf`` the
        # wrong directory. ``_sanitise_storage_path`` in config.py
        # already coerces dangerous values to "" at load time, so by the
        # time we get here a non-empty value is winpodx-owned. We still
        # confirm explicitly because wipe_storage is destructive.
        print(
            tr(
                "WARNING: --wipe-storage will destroy the Windows disk image."
                " Type 'WIPE' to confirm: "
            ),
            end="",
            flush=True,
        )
        try:
            answer = input().strip()
        except EOFError:
            answer = ""
        if answer != "WIPE":
            print(tr("Aborted (no confirmation)."))
            sys.exit(2)

    print(tr("Stopping pod..."))
    stop_pod(cfg)

    if wipe_storage:
        _wipe_pod_storage(cfg)

    print(tr("Regenerating compose.yaml from current config..."))
    try:
        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(tr("Failed to regenerate compose.yaml: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)

    print(tr("Starting pod with new compose..."))
    status = start_pod(cfg)

    if status.state in (PodState.RUNNING, PodState.STARTING):
        if wipe_storage:
            print(
                tr(
                    "Pod recreated with fresh storage. Windows reinstall will "
                    "take ~5-10 minutes (ISO download + Sysprep + OEM apply); "
                    "watch progress with `winpodx pod wait-ready --logs`."
                )
            )
        else:
            print(
                tr(
                    "Pod recreated. Container picked up the new compose; "
                    "note that dockur applies language / region / keyboard / "
                    "edition only on the initial Windows install, so those "
                    "specific knobs require --wipe-storage to actually reach "
                    "the guest. Timezone, backend, and runtime knobs apply "
                    "without a wipe."
                )
            )
    else:
        print(tr("Failed to start: {error}").format(error=status.error), file=sys.stderr)
        sys.exit(1)


def _wipe_pod_storage(cfg) -> None:  # type: ignore[no-untyped-def]
    """Destroy the Windows disk image so dockur re-runs the install.

    Two storage regimes (see ``compose._render_storage_blocks``):

    * Named volume mode (``cfg.pod.storage_path == ""``): the legacy
      ``winpodx-data`` named volume holds the raw disk. We remove it
      via ``podman volume rm`` / ``docker volume rm``.
    * Bind-mount mode (``cfg.pod.storage_path`` set): the disk lives
      at an explicit host path winpodx owns. We ``rm -rf`` that path's
      contents (preserving the directory itself + its ``chattr +C``
      attribute on btrfs, set by ``setup --migrate-storage``).

    Only callable from the ``--wipe-storage`` path of ``pod recreate``,
    which prompts for a typed confirmation first.
    """
    import shutil
    import subprocess as sp
    from pathlib import Path

    raw_storage = (cfg.pod.storage_path or "").strip()

    if not raw_storage:
        backend = cfg.pod.backend
        volume_name = "winpodx-data"
        if backend == "podman":
            cmd = ["podman", "volume", "rm", "-f", volume_name]
        elif backend == "docker":
            cmd = ["docker", "volume", "rm", "-f", volume_name]
        else:
            print(
                tr(
                    "  Backend {backend} has no named-volume wipe path; "
                    "manually destroy the guest disk and re-run setup."
                ).format(backend=repr(backend))
            )
            return
        result = sp.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print(tr("  Removed volume {volume}.").format(volume=volume_name))
        else:
            stderr = result.stderr.strip()
            if "no such" in stderr.lower():
                print(tr("  Volume {volume} already absent.").format(volume=volume_name))
            else:
                print(
                    tr("  WARNING: volume rm returned {rc}: {stderr}").format(
                        rc=result.returncode, stderr=stderr
                    )
                )
        return

    bind_path = Path(raw_storage).expanduser()
    if not bind_path.is_dir():
        print(tr("  Bind-mount path {path} is absent; nothing to wipe.").format(path=bind_path))
        return
    print(tr("  Wiping bind-mount contents under {path} ...").format(path=bind_path))
    for item in bind_path.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError as e:
            print(tr("  WARNING: could not remove {item}: {error}").format(item=item, error=e))


def _wait_for_oem_reboot(cfg, timeout: int) -> bool:  # type: ignore[no-untyped-def]
    """Poll for ``C:\\winpodx\\oem_reboot_pending.txt`` to disappear.

    Phase 4 of wait-ready. ``install.bat`` schedules a final
    ``shutdown /r /t 15`` after writing this marker, so the guest
    does one extra Windows boot to pick up registry edits that only
    take effect after reboot (Modern Standby off, NIC binding, etc.).
    A RunOnce key clears the marker on the second boot. We poll its
    absence via the agent transport.

    Returns:
        True when the marker is absent (either it was never written
        because OEM didn't schedule a reboot, or the second boot
        completed and the RunOnce fired). False when the timeout
        expired with the marker still present.

    The function is intentionally lenient about probe failures:
    transient agent /exec errors (the guest reboot itself takes the
    agent down) are retried until the timeout. A genuine "marker
    never went away" outcome only happens when shutdown failed or
    RunOnce didn't fire, which is rare enough that the caller's
    WARN-level message is the right UX.
    """
    import time as _time

    from winpodx.core.transport.agent import AgentTransport
    from winpodx.core.transport.base import TransportError

    # Probe interval -- 5s keeps the loop snappy without flooding the
    # agent socket during the actual reboot window (when /exec is
    # rejecting connections anyway).
    interval = 5
    # Initial settling: install.bat issues `shutdown /r /t 15`, so the
    # guest takes ~15s to start the reboot. Sleep 5s before the first
    # probe so we don't see the marker as "absent" from a stale agent
    # connection that pre-dates install.bat's write of the file.
    _time.sleep(5)

    transport = AgentTransport(cfg)
    deadline = _time.monotonic() + max(15, int(timeout))
    # Grace window for the marker to APPEAR.
    # `wait_for_windows_responsive` (phase 3) returns OK as soon as the
    # agent /health endpoint answers, but install.bat may still be
    # ahead of the marker-write line at that moment (TermService cycle,
    # .activation_status update, etc., before the shutdown block). Give
    # the marker `appear_grace` seconds to show up before treating
    # "never seen" as "this is an upgrade path with no OEM-scheduled
    # reboot".
    appear_grace_deadline = _time.monotonic() + 30
    saw_marker_at_least_once = False
    consecutive_absent = 0

    while _time.monotonic() < deadline:
        try:
            result = transport.exec(
                "if (Test-Path 'C:\\winpodx\\oem_reboot_pending.txt') { exit 1 } else { exit 0 }",
                timeout=10,
            )
            if result.rc == 1:
                saw_marker_at_least_once = True
                consecutive_absent = 0
            elif result.rc == 0:
                consecutive_absent += 1
                if saw_marker_at_least_once and consecutive_absent >= 2:
                    # Marker was present then disappeared -- RunOnce
                    # fired post-reboot. Done.
                    return True
        except TransportError:
            # Agent rejecting connections -- reboot in progress.
            consecutive_absent = 0
        # No marker ever observed past the grace window: existing install
        # upgraded in-place (the guest already did its OEM reboot long ago, so
        # the marker never reappears). Exit on the agent's clock regardless of
        # whether the latest probe returned rc==0 or raised -- a transitioning
        # agent on an upgrade must NOT pin us to the full timeout (the hang
        # that made `wait-ready --logs` look frozen at [4/4] on re-install).
        if not saw_marker_at_least_once and _time.monotonic() >= appear_grace_deadline:
            return True
        _time.sleep(interval)

    return False


# Wget progress line format dockur prints during Windows ISO download:
#
#   6488064K ........ ........ ........ ........ 78% 4.55M 21m22s
#                                                %    speed remaining
#
# We only match the "remaining" form (space-separated time after speed),
# not the "= elapsed" form (4.55M=21m22s) that wget prints when it
# hasn't seen enough samples for an ETA yet. The space anchor before
# the time group is load-bearing -- it discriminates the two forms.
_WGET_ETA_RE = re.compile(r"\d+%\s+\d+\.?\d*[KMG]?\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\s*$")


def _parse_wget_eta_secs(line: str) -> int | None:
    """Extract wget's "remaining time" estimate (seconds) from a dockur
    progress line. Returns None if the line doesn't carry a usable ETA
    (no match, or all-zero groups).
    """
    m = _WGET_ETA_RE.search(line)
    if m is None:
        return None
    h, mn, s = m.groups()
    secs = (int(h) if h else 0) * 3600 + (int(mn) if mn else 0) * 60 + (int(s) if s else 0)
    return secs if secs > 0 else None


# A dockur/wget progress line carries a percentage + speed + (eta|elapsed):
#   6488064K ........ ........ ........ ........ 78% 4.55M 21m22s
#   8257536K ........ .......                   100% 34.0M=4m27s
# Capture percent, speed, and the trailing time token (remaining, or
# "=elapsed" once complete) so the non-verbose drain can collapse the flood
# of these lines into a single in-place progress bar.
# percent, speed, optional '=' (done marker), optional time token. wget
# writes the time either space-separated ("4.55M 21m22s" = ETA remaining) or
# '='-joined ("34.0M=4m27s" = total once complete), so accept both.
_WGET_PROGRESS_RE = re.compile(r"(\d{1,3})%\s+(\d+\.?\d*[KMG]?)(=)?\s*(\S*)\s*$")

# Container log lines that flood without telling the user anything
# actionable -- suppressed in the clean (non-verbose) drain.
_CONTAINER_NOISE = ("BdsDxe:", "mknod: /dev/net/tun")


def _format_wget_progress(line: str) -> tuple[int, str] | None:
    """Parse a dockur/wget progress line into ``(percent, clean_text)``.

    Returns ``None`` when the line isn't a wget progress line. Used only in
    the clean (non-verbose) drain, which prints the text at percentage
    milestones so the hundreds of raw wget lines collapse to a tidy handful.
    """
    m = _WGET_PROGRESS_RE.search(line)
    if m is None:
        return None
    pct_s, speed, done_marker, time_tok = (
        m.group(1),
        m.group(2),
        m.group(3),
        (m.group(4) or ""),
    )
    try:
        pct = max(0, min(100, int(pct_s)))
    except ValueError:
        return None
    width = 22
    filled = pct * width // 100
    bar = "#" * filled + "-" * (width - filled)
    unit = speed[-1:].upper()
    speed_s = f"{speed[:-1]} {unit}B/s" if unit in ("K", "M", "G") else f"{speed} B/s"
    if not time_tok:
        tail_s = ""
    elif done_marker:
        tail_s = f"  done in {time_tok}"
    else:
        tail_s = f"  ETA {time_tok}"
    return pct, f"  Downloading Windows ISO  [{bar}] {pct:3d}%  {speed_s}{tail_s}"


class _LiveLine:
    """A single transient terminal line that updates in place and erases.

    Used for the clean (non-verbose) install view: the Windows-download
    progress + transient "still booting" status overwrite one line via
    carriage-return, so the screen doesn't scroll with hundreds of dockur/wget
    lines. Permanent lines (phase markers, dockur milestones) go to stdout
    normally; this only owns the transient line.

    Writes to ``/dev/tty`` directly, NOT stdout: install.sh pipes wait-ready
    through ``tee``, so stdout isn't a TTY and ``\\r`` control codes would land
    in the captured log file. /dev/tty reaches the real terminal regardless,
    and never pollutes the tee'd capture. Disabled (no-op) when /dev/tty can't
    be opened (headless / non-interactive) or in verbose mode.
    """

    def __init__(self, enabled: bool) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._tty = None
        if not enabled:
            return
        try:
            self._tty = open("/dev/tty", "w")  # noqa: SIM115 — closed in close()
        except OSError:
            self._tty = None

    @property
    def usable(self) -> bool:
        return self._tty is not None

    def set(self, text: str) -> None:
        """Render *text* as the transient line, overwriting the previous one."""
        if self._tty is None:
            return
        with self._lock:
            try:
                # \r to column 0, \033[K clears to end of line.
                self._tty.write("\r\033[K" + text)
                self._tty.flush()
                self._active = True
            except (OSError, ValueError):
                self._tty = None

    def clear(self) -> None:
        """Erase the transient line (call before printing a permanent line)."""
        if self._tty is None or not self._active:
            return
        with self._lock:
            try:
                self._tty.write("\r\033[K")
                self._tty.flush()
            except (OSError, ValueError):
                pass
            self._active = False

    def close(self) -> None:
        self.clear()
        if self._tty is not None:
            try:
                self._tty.close()
            except OSError:
                pass
            self._tty = None


def _wait_ready(timeout: int, show_logs: bool, verbose: bool = False) -> None:
    """v0.2.0.5: multi-phase wait for the Windows VM to finish first-boot.

    Polls four checkpoints with elapsed-time stamps so the user sees
    progress instead of a silent multi-minute hang on `curl install.sh`:

      [1/4] Container running                  (e.g. 5s)
      [2/4] RDP port open                      (typically 30-90s)
      [3/4] Windows ready (RemoteApp probes OK) (typically 2-8min on first boot)
      [4/4] OEM reboot pass complete           (typically 30-90s on fresh install)

    With ``--logs``, container stdout is tailed in a background thread
    and surfaced as ``[container] ...`` lines so the user can see Windows
    actually doing work (Sysprep, OEM apply, etc.) instead of a black
    box.

    The deadline is dynamic when ``--logs`` is on: wget's ETA from the
    Windows ISO download is parsed in the log-drain thread and used to
    extend the wait window if the user's connection is slow enough that
    the static timeout would expire mid-download (xiyeming #126: 86min
    ISO download exceeded the 60min default). No upper bound -- we
    trust the ETA wget itself reports. If the download genuinely
    stalls, no new ETA arrives, the deadline stops moving, and the
    wait expires naturally on the last extension. No-op for fast
    connections (their ETA always fits inside the current deadline).
    """
    import time as _time
    from subprocess import PIPE, Popen

    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, check_rdp_port, pod_status
    from winpodx.core.provisioner import wait_for_windows_responsive

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(
            tr("wait-ready not supported for backend {backend} (podman/docker only).").format(
                backend=repr(cfg.pod.backend)
            )
        )
        sys.exit(2)

    timeout = max(60, min(7200, int(timeout)))
    start = _time.monotonic()

    # Mutable deadline so the log-drain thread can extend it when slow
    # download progress is observed. No ceiling -- we trust wget's
    # ETA. A genuinely stalled download stops producing ETA lines, so
    # the deadline stops advancing and the wait expires naturally.
    # The buffer is wider than the post-download Sysprep / OEM apply
    # actually needs (typically 2-8min) because real-world downloads
    # have brief network blips and a transient stall shouldn't fail
    # the wait outright -- we'd rather wait an extra ~hour than mark
    # a recoverable hiccup as a hard timeout.
    _BOOT_BUFFER_SECS = 3600  # 60min slack for blips + post-download boot
    _ANNOUNCE_STEP_SECS = 600  # only re-announce when deadline jumps 10min+
    deadline_state = {
        "value": start + timeout,
        "last_announced": None,
        # Set once phase 1 sees the container RUNNING. After that point any
        # wget-ETA line in the drained log is *stale* history (`--logs
        # --tail 100` replays the original first-boot download), not a live
        # download, so it must NOT extend the deadline. Without this guard an
        # upgrade over an already-provisioned guest inflated the deadline to
        # tens of minutes off old log lines, and phase 4 then blocked that
        # whole window.
        "container_running": False,
    }

    def _maybe_extend_deadline(eta_remaining_secs: int) -> None:
        """Extend deadline if wget ETA suggests the current one will
        expire before the download finishes. Idempotent + threadsafe
        enough (single-key dict writes are atomic under the GIL; we
        accept eventual consistency)."""
        # Once the container is running there is no live ISO download; any
        # ETA line is replayed history. Ignore it.
        if deadline_state["container_running"]:
            return
        now = _time.monotonic()
        new_deadline = now + eta_remaining_secs + _BOOT_BUFFER_SECS
        if new_deadline <= deadline_state["value"]:
            return
        deadline_state["value"] = new_deadline
        last = deadline_state["last_announced"]
        if last is None or new_deadline - last >= _ANNOUNCE_STEP_SECS:
            total_min = int((new_deadline - start) / 60)
            eta_min = max(1, eta_remaining_secs // 60)
            print(
                tr(
                    "[winpodx] Slow Windows ISO download detected "
                    "(~{eta_min}m remaining). "
                    "Extending wait to {total_min}m total."
                ).format(eta_min=eta_min, total_min=total_min)
            )
            deadline_state["last_announced"] = new_deadline

    def elapsed() -> str:
        s = int(_time.monotonic() - start)
        return f"{s // 60:02d}:{s % 60:02d}"

    log_proc: Popen | None = None
    log_stop = threading.Event()

    # Self-erasing transient line for the clean (non-verbose) view. Owns the
    # download progress + boot heartbeat; permanent lines go through say().
    _live = _LiveLine(enabled=show_logs and not verbose)

    def say(text: str) -> None:
        """Print a permanent line, erasing the transient live line first."""
        _live.clear()
        print(text)

    # On an ESTABLISHED pod (upgrade / re-run) the container has been up for a
    # while, so `--tail 100` would replay the ORIGINAL first-boot ISO-download +
    # image-build log — misleading noise that, under --verbose, looks like a
    # re-download even though nothing is downloading (the maintainer hit this on
    # `install.sh` upgrades). Detect "already past first-boot" via RDP being
    # reachable and replay NO history (`--tail 0`); a genuine fresh boot (RDP
    # not up yet) keeps `--tail 100` so the in-progress download is visible.
    _already_up = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0)
    _log_tail = "0" if _already_up else "100"

    if show_logs:
        try:
            # --tail surfaces recent context (Windows ISO download, current boot
            # stage) on a fresh boot; 0 on an established pod so we don't replay
            # stale first-boot history. Both stdout and stderr are drained
            # because dockur splits progress across both (download bytes/sec on
            # one, boot phase on the other).
            log_proc = Popen(
                [cfg.pod.backend, "logs", "-f", "--tail", _log_tail, cfg.pod.container_name],
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                bufsize=1,
            )

            # Rewrite dockur's container-internal VNC web URL to the host
            # port the user can actually reach. dockur prints
            # `visit http://127.0.0.1:8006/ to view the screen` from inside
            # its container, where 8006 IS the listening port; from the
            # host that port is mapped to cfg.pod.vnc_port (8007 by default
            # — see core/pod/compose.py). Without this rewrite, the line
            # streamed via `--logs` told the user to visit a port that's
            # only reachable inside the container, and copy-pasting the
            # URL into a browser silently failed (xiyeming reported this
            # 2026-05-05 in #118).
            vnc_port = cfg.pod.vnc_port

            def _rewrite_vnc_url(line: str) -> str:
                if vnc_port == 8006:
                    return line
                return line.replace("127.0.0.1:8006", f"127.0.0.1:{vnc_port}")

            # dockur's `Warning: you are using the BTRFS filesystem for
            # /storage` fires on every btrfs host regardless of whether
            # we've applied chattr +C — it just reads df. With
            # storage_path set, NoCoW is in effect so the warning is
            # cosmetic; rewrite it to a one-liner so install.sh output
            # doesn't look alarming. Without storage_path (legacy named
            # volume), keep the warning visible so the user knows to
            # migrate.
            suppress_btrfs_warning = bool(cfg.pod.storage_path)

            # Last printed download percentage (clean mode), so we emit a
            # progress line only every few percent instead of on every wget
            # tick. Shared list so the closure can mutate it.
            _last_pct: list[int] = [-100]

            def _drain(stream) -> None:  # type: ignore[no-untyped-def]
                if stream is None:
                    return
                for line in stream:
                    if log_stop.is_set():
                        break
                    line = line.rstrip()
                    if not line:
                        continue
                    if (
                        suppress_btrfs_warning
                        and "you are using the BTRFS filesystem for /storage" in line
                    ):
                        line = "(btrfs warning suppressed: NoCoW bind mount in use)"
                    # Always watch dockur's wget ETA to extend the deadline
                    # (#126), whether or not we render the line.
                    eta_secs = _parse_wget_eta_secs(line)
                    if eta_secs is not None:
                        _maybe_extend_deadline(eta_secs)

                    if verbose:
                        # --verbose: raw, every container line, permanent.
                        print(f"       [container] {_rewrite_vnc_url(line)}")
                        continue

                    # Clean (default). The download progress + transient boot
                    # chatter live on ONE self-erasing line (_live); only
                    # meaningful dockur milestones (the `>` lines) become
                    # permanent output.
                    prog = _format_wget_progress(line)
                    if prog is not None:
                        if _live.usable:
                            _live.set(prog[1])  # in-place, self-erasing
                        else:
                            pct, text = prog  # no TTY: fall back to >=3% lines
                            if pct >= _last_pct[0] + 3 or pct >= 100:
                                print(text)
                                _last_pct[0] = pct
                        continue
                    if any(noise in line for noise in _CONTAINER_NOISE):
                        # UEFI boot-loader / tun spam: keep a transient
                        # heartbeat so the screen isn't dead, but never scroll.
                        _live.set("  Windows is booting...")
                        continue
                    # A real dockur line (e.g. "> Extracting Windows image"):
                    # erase the transient line, print it permanently.
                    _live.clear()
                    print(f"       [container] {_rewrite_vnc_url(line)}")

            threading.Thread(target=_drain, args=(log_proc.stdout,), daemon=True).start()
            threading.Thread(target=_drain, args=(log_proc.stderr,), daemon=True).start()
        except (FileNotFoundError, OSError) as e:
            print(tr("       (could not tail container logs: {error})").format(error=e))
            log_proc = None

    try:
        # --- [1/4] Container running ---
        say(
            tr("[1/4] Waiting for container to start...      ({elapsed})").format(elapsed=elapsed())
        )
        while _time.monotonic() < deadline_state["value"]:
            try:
                if pod_status(cfg).state == PodState.RUNNING:
                    # Freeze the deadline against stale replayed wget lines.
                    deadline_state["container_running"] = True
                    say(
                        tr("      OK Container running                   ({elapsed})").format(
                            elapsed=elapsed()
                        )
                    )
                    break
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(2)
        else:
            say(
                tr("      FAIL Timeout waiting for container       ({elapsed})").format(
                    elapsed=elapsed()
                )
            )
            sys.exit(3)

        # --- [2/4] RDP port open ---
        say(
            tr("[2/4] Waiting for Windows RDP service...     ({elapsed})").format(elapsed=elapsed())
        )
        while _time.monotonic() < deadline_state["value"]:
            if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
                say(
                    tr("      OK RDP port {port} open                  ({elapsed})").format(
                        port=cfg.rdp.port, elapsed=elapsed()
                    )
                )
                break
            _time.sleep(3)
        else:
            say(
                tr("      FAIL Timeout waiting for RDP port        ({elapsed})").format(
                    elapsed=elapsed()
                )
            )
            sys.exit(3)

        # --- [3/4] FreeRDP RemoteApp activation ---
        say(
            tr("[3/4] Waiting for Windows activation...      ({elapsed})").format(elapsed=elapsed())
        )
        remaining = max(60, int(deadline_state["value"] - _time.monotonic()))
        if wait_for_windows_responsive(cfg, timeout=remaining):
            say(
                tr("      OK Windows ready                         ({elapsed})").format(
                    elapsed=elapsed()
                )
            )
        else:
            say(
                tr(
                    "      FAIL Timeout waiting for Windows ready   ({elapsed})\n"
                    "      Run `winpodx pod status` later and re-run "
                    "`winpodx pod wait-ready` once the container is fully up."
                ).format(elapsed=elapsed())
            )
            sys.exit(3)

        # --- [4/4] OEM reboot pass ---
        #
        # install.bat schedules a `shutdown /r /t 15` after writing
        # `C:\winpodx\oem_reboot_pending.txt`, so the guest does one
        # extra Windows boot to pick up registry edits that don't take
        # effect until reboot (Modern Standby off via
        # PlatformAoAcOverride, NIC binding tweaks, etc.). The
        # RunOnce key clears the marker on the second boot. Phase 4
        # polls the marker's absence and re-asserts Windows
        # responsiveness so install.sh's next steps (apply-fixes,
        # discovery) don't run mid-reboot.
        #
        # If the marker never existed (existing install upgraded
        # in-place, or OEM v < the one that adds it), we skip phase 4
        # silently — `apply-fixes` will surface the pending-reboot
        # condition separately if it matters.
        say(
            tr("[4/4] Waiting for OEM reboot pass...         ({elapsed})").format(elapsed=elapsed())
        )
        # Phase 4 is a quick marker poll, not a download -- cap it hard at
        # 180s regardless of any download-inflated deadline so an already-
        # provisioned guest (upgrade path: marker never reappears) can't
        # block for the whole ISO-sized window.
        remaining = min(180, max(60, int(deadline_state["value"] - _time.monotonic())))
        if _wait_for_oem_reboot(cfg, timeout=remaining):
            say(
                tr("      OK OEM reboot pass complete             ({elapsed})").format(
                    elapsed=elapsed()
                )
            )
        else:
            say(
                tr(
                    "      WARN OEM reboot pass marker still pending ({elapsed})\n"
                    "      Registry changes that need a reboot may not be active "
                    "yet. Run `winpodx pod restart` once installs.sh finishes."
                ).format(elapsed=elapsed())
            )
    finally:
        log_stop.set()
        _live.close()
        if log_proc is not None:
            try:
                log_proc.terminate()
                log_proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    log_proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    # Guest now responsive. If the host has been upgraded since this guest
    # was provisioned, push the refreshed guest artifacts (agent.ps1,
    # urlacl, rdprrap/shim, registry fixes) instead of forcing a reinstall.
    # Gated on `initialized` so a fresh first-boot install -- whose guest is
    # already current -- doesn't sync redundantly; idempotent regardless.
    if getattr(cfg.pod, "initialized", False) and getattr(cfg.pod, "guest_autosync", True):
        try:
            from winpodx.core.guest_sync import maybe_autosync

            if maybe_autosync(cfg):
                print(tr("      Guest synced to the upgraded host (agent restarting)."))
        except Exception as e:  # noqa: BLE001 -- never fail wait-ready on sync
            say(tr("      WARN guest auto-sync skipped: {error}").format(error=e))


def _recover_oem() -> None:
    """Recover from a failed dockur OEM-copy by re-staging C:\\OEM\\ manually.

    Workaround for #287: in some host environments dockur's first-boot
    ``/oem -> C:\\OEM\\`` copy silently fails, leaving the guest with
    no ``install.bat``, no agent, and no rdprrap. The host then sees
    port 8765 RST and ``winpodx pod wait-ready`` times out.

    This command:

    1. Verifies the container is running and ``/oem/install.bat`` is
       present inside the container (i.e. host-side OEM mount is OK
       and only the guest-side copy is what failed).
    2. Tars ``/oem`` to ``/storage/oem.tar.gz`` inside the container.
    3. Starts a one-shot Python HTTP server on container port 8766
       (reachable from the Windows guest via QEMU's NAT gateway
       ``10.0.2.2``).
    4. Prints the exact PowerShell commands the user must paste into
       the noVNC console to download, extract, and run ``install.bat``.

    We do not push to the guest automatically because the failure mode
    of #287 leaves the agent dead -- there is no working host->guest
    channel. noVNC PowerShell paste is the only reliable path until the
    agent is up.
    """
    import shutil as _shutil
    import subprocess
    import time

    from winpodx.core.provisioner import _ensure_config

    cfg = _ensure_config()
    container = cfg.pod.container_name
    backend_name = cfg.pod.backend

    if backend_name not in ("podman", "docker"):
        print(
            tr(
                "Error: recover-oem only supports podman/docker backends "
                "(current: {backend}). For libvirt/manual backends, "
                "copy /oem into the guest manually."
            ).format(backend=backend_name)
        )
        sys.exit(1)

    cmd = backend_name
    if not _shutil.which(cmd):
        print(tr("Error: {cmd} not found on PATH.").format(cmd=cmd))
        sys.exit(1)

    print(
        tr("[winpodx] Checking container '{container}' is running...").format(container=container)
    )
    try:
        result = subprocess.run(
            [
                cmd,
                "ps",
                "--filter",
                f"name={container}",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print(tr("Error: '{cmd} ps' timed out (10s).").format(cmd=cmd))
        sys.exit(1)
    if container not in result.stdout:
        print(
            tr(
                "Error: container '{container}' not running."
                " Start it first with 'winpodx pod start'."
            ).format(container=container)
        )
        sys.exit(1)

    print(tr("[winpodx] Verifying /oem/install.bat exists inside container..."))
    try:
        check = subprocess.run(
            [cmd, "exec", container, "sh", "-c", "test -f /oem/install.bat"],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print(tr("Error: container exec timed out (10s)."))
        sys.exit(1)
    if check.returncode != 0:
        print(
            tr(
                "Error: /oem/install.bat not found inside container. "
                "The host-side OEM mount itself is missing -- recreate "
                "the pod with 'winpodx pod recreate' before retrying."
            )
        )
        sys.exit(1)

    # Tar /oem into a DEDICATED serve dir (not /storage) so the HTTP
    # server below exposes only oem.tar.gz -- never /storage/data.img
    # (the multi-GB Windows disk) or anything else in /storage. The OEM
    # tarball contains agent_token.txt; the guest already holds that
    # token (it's at C:\OEM\agent_token.txt), so this doesn't cross a
    # new trust boundary, but serving the whole /storage would.
    serve_dir = "/tmp/winpodx-recover"
    print(
        tr("[winpodx] Tarring /oem into {serve_dir}/oem.tar.gz inside container...").format(
            serve_dir=serve_dir
        )
    )
    try:
        subprocess.run(
            [
                cmd,
                "exec",
                container,
                "sh",
                "-c",
                f"rm -rf {serve_dir} && mkdir -p {serve_dir} && cd / && "
                f"tar czf {serve_dir}/oem.tar.gz oem && ls -la {serve_dir}/oem.tar.gz",
            ],
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        print(tr("Error: tar failed (rc={rc}).").format(rc=e.returncode))
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(tr("Error: tar timed out (60s)."))
        sys.exit(1)

    # Port 8766: one above the winpodx agent port (8765) so anyone
    # debugging with `lsof -i :876*` sees both. Container-internal only;
    # not forwarded to the host. Reachable from the Windows guest via
    # QEMU's NAT gateway 10.0.2.2:8766. Serves only the dedicated
    # recover dir (one file), not /storage.
    print(tr("[winpodx] Starting HTTP server on container port 8766..."))
    # Best-effort cleanup of any prior server on 8766.
    subprocess.run(
        [
            cmd,
            "exec",
            container,
            "sh",
            "-c",
            "pkill -f 'http.server 8766' 2>/dev/null; true",
        ],
        capture_output=True,
        timeout=5,
    )
    time.sleep(1)
    # `-d` detaches the exec so the server keeps running after we return.
    subprocess.run(
        [
            cmd,
            "exec",
            "-d",
            container,
            "sh",
            "-c",
            f"cd {serve_dir} && nohup python3 -m http.server 8766 "
            ">/tmp/recover-oem-http.log 2>&1 &",
        ],
        timeout=10,
    )
    time.sleep(2)

    print()
    print("=" * 70)
    print(tr("Paste these commands into the Windows guest via noVNC PowerShell:"))
    print()
    print("  noVNC URL: http://127.0.0.1:8007/")
    print()
    print(tr("  # Download OEM bundle from the container via the guest's default"))
    print(tr("  # gateway (QEMU slirp = 10.0.2.2, podman bridge = 10.89.0.1, etc.)"))
    print(
        "  $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        "Sort-Object RouteMetric | Select-Object -First 1).NextHop"
    )
    print('  Invoke-WebRequest "http://${gw}:8766/oem.tar.gz" -OutFile C:\\oem.tar.gz')
    print()
    print(tr("  # Extract to C:\\OEM\\ (Windows 10/11 ships bsdtar in System32)"))
    print("  cd C:\\")
    print("  tar -xzf C:\\oem.tar.gz")
    print()
    print(tr("  # Verify: should list install.bat, agent\\, rdprrap\\, scripts\\, etc."))
    print("  dir C:\\OEM")
    print()
    print(tr("  # Run install.bat -- guest will reboot at the end"))
    print("  C:\\OEM\\install.bat")
    print()
    print("=" * 70)
    print()
    print(tr("After the post-install reboot, on this host:"))
    print("  winpodx pod wait-ready")
    print("  winpodx check")
    print()
    print(tr("To stop the HTTP server when finished:"))
    print(f"  {cmd} exec {container} pkill -f 'http.server 8766'")


def _disk_usage() -> None:
    """Print the guest C: volume size / free / used%."""
    from winpodx.core.config import Config
    from winpodx.core.disk import get_guest_disk_usage

    cfg = Config.load()
    usage = get_guest_disk_usage(cfg)
    if usage is None:
        print(
            tr(
                "Could not read guest disk usage (is the pod running and the agent up?).\n"
                "Try `winpodx pod wait-ready` first."
            )
        )
        sys.exit(1)

    def _gib(n: int) -> str:
        return f"{n / (1024**3):.1f} GiB"

    cap = cfg.pod.disk_max_size or tr("host free space")
    print(tr("Windows C: drive"))
    print(
        tr("  configured disk_size : {size}  (max: {cap})").format(size=cfg.pod.disk_size, cap=cap)
    )
    print(tr("  total                : {total}").format(total=_gib(usage.total_bytes)))
    print(tr("  free                 : {free}").format(free=_gib(usage.free_bytes)))
    print(
        tr("  used                 : {used}  ({pct:.1f}%)").format(
            used=_gib(usage.used_bytes), pct=usage.used_pct
        )
    )
    if cfg.pod.disk_autogrow:
        print(
            tr(
                "  auto-grow            : on at {threshold}% (+{increment} per step, idle only)"
            ).format(
                threshold=cfg.pod.disk_autogrow_threshold_pct,
                increment=cfg.pod.disk_autogrow_increment,
            )
        )
    else:
        print(tr("  auto-grow            : off"))


def _grow_disk(
    *,
    target_size: str | None,
    increment: str | None,
    extend_only: bool,
    assume_yes: bool,
) -> None:
    """Grow the Windows virtual disk and extend C: to fill it (#318)."""
    from winpodx.core.config import Config
    from winpodx.core.disk import (
        DiskError,
        compute_grow_target,
        extend_guest_system_volume,
        grow_disk,
    )

    cfg = Config.load()

    # --extend-only: skip the resize, just extend C: into existing
    # unallocated space (used to finish a grow whose guest wasn't up yet).
    if extend_only:
        print(tr("Extending C: into unallocated space..."))
        if extend_guest_system_volume(cfg):
            print(tr("C: extended (or already at max)."))
        else:
            print(tr("Failed to extend C: -- see logs; the guest may be unreachable."))
            sys.exit(1)
        return

    try:
        new_size = compute_grow_target(cfg, target_size=target_size, increment=increment)
    except DiskError as e:
        print(tr("Cannot grow disk: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)

    if not assume_yes:
        print(
            tr(
                "Grow Windows disk {old_size} -> {new_size}?\n"
                "This stops the pod, recreates the container so dockur grows the\n"
                "virtual disk, then extends C: to fill it. Existing Windows data is\n"
                "preserved. Type 'y' to continue: "
            ).format(old_size=cfg.pod.disk_size, new_size=new_size),
            end="",
            flush=True,
        )
        try:
            if input().strip().lower() not in ("y", "yes"):
                print(tr("Aborted."))
                sys.exit(2)
        except EOFError:
            print(tr("Aborted (no confirmation)."))
            sys.exit(2)

    try:
        result = grow_disk(cfg, target_size=target_size, increment=increment)
    except DiskError as e:
        print(tr("Grow failed: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)

    print(
        tr("Disk grown {old_size} -> {new_size}.").format(
            old_size=result.old_size, new_size=result.new_size
        )
    )
    if result.partition_extended:
        print(tr("C: extended to fill the new space."))
    elif result.note:
        print(result.note)


def _sync_guest(*, force: bool) -> None:
    """Push refreshed guest artifacts into the running guest (#guest-sync)."""
    from winpodx.core.config import Config
    from winpodx.core.guest_sync import (
        GuestSyncError,
        host_version,
        read_guest_version,
        sync_guest,
    )

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(
            tr("sync-guest only supports podman/docker, not {backend}.").format(
                backend=repr(cfg.pod.backend)
            )
        )
        sys.exit(1)

    hv = host_version()
    gv = read_guest_version(cfg)
    print(
        tr("host:  winpodx {version}, OEM bundle {oem}").format(
            version=hv.winpodx, oem=hv.oem_bundle
        )
    )
    if gv is None:
        print(tr("guest: version stamp not found (will sync)"))
    else:
        print(
            tr("guest: winpodx {version}, OEM bundle {oem}").format(
                version=gv.winpodx, oem=gv.oem_bundle
            )
        )

    print(tr("Syncing guest (deliver /oem, urlacl, runtime fixes, restart agent)..."))
    try:
        results = sync_guest(cfg, force=force)
    except GuestSyncError as e:
        print(tr("Sync failed: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)

    for step, outcome in results.items():
        mark = "OK  " if outcome == "ok" or outcome.startswith("skipped") else "FAIL"
        print(f"  [{mark}] {step}: {outcome}")

    if any(v.startswith("failed") for v in results.values()):
        print(tr("\nSome steps failed -- re-run `winpodx pod sync-guest` once the guest is up."))
        sys.exit(1)
    print(tr("\nGuest synced. The agent restarts in ~5s; run `winpodx check` to confirm."))
