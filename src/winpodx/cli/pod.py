# SPDX-License-Identifier: MIT
"""CLI handlers for pod management."""

from __future__ import annotations

import argparse
import re
import sys


def handle_pod(args: argparse.Namespace) -> None:
    """Route pod subcommands."""
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
    elif cmd == "apply-fixes":
        _apply_fixes()
    elif cmd == "sync-password":
        _sync_password(getattr(args, "non_interactive", False))
    elif cmd == "multi-session":
        _multi_session(args.action)
    elif cmd == "wait-ready":
        _wait_ready(args.timeout, getattr(args, "logs", False))
    elif cmd == "install-status":
        from winpodx.cli.pod_install_status import handle as handle_install_status

        sys.exit(handle_install_status(args))
    elif cmd == "install-resume":
        from winpodx.cli.pod_install_resume import handle as handle_install_resume

        sys.exit(handle_install_resume(args))
    elif cmd == "recover-oem":
        _recover_oem()
    else:
        print(
            "Usage: winpodx pod {start|stop|status|restart|recreate|apply-fixes|"
            "sync-password|multi-session|wait-ready|install-status|install-resume|"
            "recover-oem}"
        )
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
            f"Overriding tuning_profile for this run: "
            f"{cfg.pod.tuning_profile!r} -> {tuning_override!r}"
        )
        cfg.pod.tuning_profile = tuning_override
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
        print(f"  reverse-open listener: started (pid {pid})")
    except (ListenerStartFailed, OSError) as exc:
        print(f"  reverse-open listener: start failed ({exc})", file=sys.stderr)


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

    # Tear down the reverse-open listener BEFORE the pod itself —
    # the listener is per-pod (spawns guest apps on the host) and
    # has nothing to do once the pod is gone.
    try:
        from winpodx.reverse_open.lifecycle import stop_listener

        if stop_listener():
            print("Reverse-open listener stopped.")
    except Exception:  # noqa: BLE001
        pass

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
        print(f"sync-password not supported for backend {cfg.pod.backend!r}.")
        sys.exit(2)

    if not cfg.rdp.password:
        print("No password set in cfg — nothing to sync to.")
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
        print("Resetting Windows account password via agent...")
        try:
            result = transport.exec(payload, description="sync-password", timeout=30)
        except TransportAuthError as e:
            print(f"FAIL: agent rejected the request (auth): {e}")
            print(
                "\nThe agent's bearer token doesn't match what's on the guest. "
                "This is config drift, not a transient channel failure — fix the "
                "token mismatch (reinstall agent or re-run install.bat) before "
                "retrying."
            )
            sys.exit(3)
        except TransportUnavailable as e:
            # Health probe passed but exec failed — treat as fall-through.
            print(f"Agent became unreachable mid-call ({e}); falling back to FreeRDP.")
            transport = None
        else:
            if result.rc != 0:
                print(
                    f"FAIL: password reset script failed (rc={result.rc}): {result.stderr.strip()}"
                )
                sys.exit(3)
            print("OK: Windows account password is now in sync with winpodx config.")
            print("Password rotation will now work normally.")
            return

    # Agent unavailable — fall back to FreeRDP RemoteApp, which needs a
    # recovery password to authenticate.
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
            "WARNING: --wipe-storage will destroy the Windows disk image. Type 'WIPE' to confirm: ",
            end="",
            flush=True,
        )
        try:
            answer = input().strip()
        except EOFError:
            answer = ""
        if answer != "WIPE":
            print("Aborted (no confirmation).")
            sys.exit(2)

    print("Stopping pod...")
    stop_pod(cfg)

    if wipe_storage:
        _wipe_pod_storage(cfg)

    print("Regenerating compose.yaml from current config...")
    try:
        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"Failed to regenerate compose.yaml: {e}", file=sys.stderr)
        sys.exit(1)

    print("Starting pod with new compose...")
    status = start_pod(cfg)

    if status.state in (PodState.RUNNING, PodState.STARTING):
        if wipe_storage:
            print(
                "Pod recreated with fresh storage. Windows reinstall will "
                "take ~5-10 minutes (ISO download + Sysprep + OEM apply); "
                "watch progress with `winpodx pod wait-ready --logs`."
            )
        else:
            print(
                "Pod recreated. Container picked up the new compose; "
                "note that dockur applies language / region / keyboard / "
                "edition only on the initial Windows install, so those "
                "specific knobs require --wipe-storage to actually reach "
                "the guest. Timezone, backend, and runtime knobs apply "
                "without a wipe."
            )
    else:
        print(f"Failed to start: {status.error}", file=sys.stderr)
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
                f"  Backend {backend!r} has no named-volume wipe path; "
                "manually destroy the guest disk and re-run setup."
            )
            return
        result = sp.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print(f"  Removed volume {volume_name}.")
        else:
            stderr = result.stderr.strip()
            if "no such" in stderr.lower():
                print(f"  Volume {volume_name} already absent.")
            else:
                print(f"  WARNING: volume rm returned {result.returncode}: {stderr}")
        return

    bind_path = Path(raw_storage).expanduser()
    if not bind_path.is_dir():
        print(f"  Bind-mount path {bind_path} is absent; nothing to wipe.")
        return
    print(f"  Wiping bind-mount contents under {bind_path} ...")
    for item in bind_path.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError as e:
            print(f"  WARNING: could not remove {item}: {e}")


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
                if not saw_marker_at_least_once and _time.monotonic() >= appear_grace_deadline:
                    # No marker ever observed in the grace window: this
                    # is an existing install upgraded in-place with an
                    # OEM version that pre-dates the reboot mechanism.
                    # Nothing to wait for.
                    return True
        except TransportError:
            # Agent rejecting connections -- reboot in progress.
            consecutive_absent = 0
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


def _wait_ready(timeout: int, show_logs: bool) -> None:
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
    }

    def _maybe_extend_deadline(eta_remaining_secs: int) -> None:
        """Extend deadline if wget ETA suggests the current one will
        expire before the download finishes. Idempotent + threadsafe
        enough (single-key dict writes are atomic under the GIL; we
        accept eventual consistency)."""
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
                f"[winpodx] Slow Windows ISO download detected "
                f"(~{eta_min}m remaining). "
                f"Extending wait to {total_min}m total."
            )
            deadline_state["last_announced"] = new_deadline

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
                    # Watch dockur's wget progress for slow downloads --
                    # extend the wait deadline when the ETA suggests the
                    # static timeout would expire mid-download (#126).
                    eta_secs = _parse_wget_eta_secs(line)
                    if eta_secs is not None:
                        _maybe_extend_deadline(eta_secs)
                    print(f"       [container] {_rewrite_vnc_url(line)}")

            threading.Thread(target=_drain, args=(log_proc.stdout,), daemon=True).start()
            threading.Thread(target=_drain, args=(log_proc.stderr,), daemon=True).start()
        except (FileNotFoundError, OSError) as e:
            print(f"       (could not tail container logs: {e})")
            log_proc = None

    try:
        # --- [1/4] Container running ---
        print(f"[1/4] Waiting for container to start...      ({elapsed()})")
        while _time.monotonic() < deadline_state["value"]:
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

        # --- [2/4] RDP port open ---
        print(f"[2/4] Waiting for Windows RDP service...     ({elapsed()})")
        while _time.monotonic() < deadline_state["value"]:
            if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
                print(f"      OK RDP port {cfg.rdp.port} open                  ({elapsed()})")
                break
            _time.sleep(3)
        else:
            print(f"      FAIL Timeout waiting for RDP port        ({elapsed()})")
            sys.exit(3)

        # --- [3/4] FreeRDP RemoteApp activation ---
        print(f"[3/4] Waiting for Windows activation...      ({elapsed()})")
        remaining = max(60, int(deadline_state["value"] - _time.monotonic()))
        if wait_for_windows_responsive(cfg, timeout=remaining):
            print(f"      OK Windows ready                         ({elapsed()})")
        else:
            print(
                f"      FAIL Timeout waiting for Windows ready   ({elapsed()})\n"
                "      Run `winpodx pod status` later and re-run "
                "`winpodx pod wait-ready` once the container is fully up."
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
        print(f"[4/4] Waiting for OEM reboot pass...         ({elapsed()})")
        remaining = max(60, int(deadline_state["value"] - _time.monotonic()))
        if _wait_for_oem_reboot(cfg, timeout=remaining):
            print(f"      OK OEM reboot pass complete             ({elapsed()})")
        else:
            print(
                f"      WARN OEM reboot pass marker still pending ({elapsed()})\n"
                "      Registry changes that need a reboot may not be active "
                "yet. Run `winpodx pod restart` once installs.sh finishes."
            )
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
            f"Error: recover-oem only supports podman/docker backends "
            f"(current: {backend_name}). For libvirt/manual backends, "
            f"copy /oem into the guest manually."
        )
        sys.exit(1)

    cmd = backend_name
    if not _shutil.which(cmd):
        print(f"Error: {cmd} not found on PATH.")
        sys.exit(1)

    print(f"[winpodx] Checking container '{container}' is running...")
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
        print(f"Error: '{cmd} ps' timed out (10s).")
        sys.exit(1)
    if container not in result.stdout:
        print(
            f"Error: container '{container}' not running. Start it first with 'winpodx pod start'."
        )
        sys.exit(1)

    print("[winpodx] Verifying /oem/install.bat exists inside container...")
    try:
        check = subprocess.run(
            [cmd, "exec", container, "sh", "-c", "test -f /oem/install.bat"],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("Error: container exec timed out (10s).")
        sys.exit(1)
    if check.returncode != 0:
        print(
            "Error: /oem/install.bat not found inside container. "
            "The host-side OEM mount itself is missing -- recreate "
            "the pod with 'winpodx pod recreate' before retrying."
        )
        sys.exit(1)

    print("[winpodx] Tarring /oem into /storage/oem.tar.gz inside container...")
    try:
        subprocess.run(
            [
                cmd,
                "exec",
                container,
                "sh",
                "-c",
                "cd / && tar czf /storage/oem.tar.gz oem && ls -la /storage/oem.tar.gz",
            ],
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error: tar failed (rc={e.returncode}).")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: tar timed out (60s).")
        sys.exit(1)

    # Port 8766: one above the winpodx agent port (8765) so anyone
    # debugging with `lsof -i :876*` sees both. Container-internal only;
    # not forwarded to the host. Reachable from the Windows guest via
    # QEMU's NAT gateway 10.0.2.2:8766.
    print("[winpodx] Starting HTTP server on container port 8766...")
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
            "cd /storage && nohup python3 -m http.server 8766 >/tmp/recover-oem-http.log 2>&1 &",
        ],
        timeout=10,
    )
    time.sleep(2)

    print()
    print("=" * 70)
    print("Paste these commands into the Windows guest via noVNC PowerShell:")
    print()
    print("  noVNC URL: http://127.0.0.1:8007/")
    print()
    print("  # Download OEM bundle from container (10.0.2.2 = QEMU NAT gateway)")
    print("  Invoke-WebRequest http://10.0.2.2:8766/oem.tar.gz -OutFile C:\\oem.tar.gz")
    print()
    print("  # Extract to C:\\OEM\\ (Windows 10/11 ships bsdtar in System32)")
    print("  cd C:\\")
    print("  tar -xzf C:\\oem.tar.gz")
    print()
    print("  # Verify: should list install.bat, agent\\, rdprrap\\, scripts\\, etc.")
    print("  dir C:\\OEM")
    print()
    print("  # Run install.bat -- guest will reboot at the end")
    print("  C:\\OEM\\install.bat")
    print()
    print("=" * 70)
    print()
    print("After the post-install reboot, on this host:")
    print("  winpodx pod wait-ready")
    print("  winpodx check")
    print()
    print("To stop the HTTP server when finished:")
    print(f"  {cmd} exec {container} pkill -f 'http.server 8766'")
