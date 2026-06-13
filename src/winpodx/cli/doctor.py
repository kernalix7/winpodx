# SPDX-License-Identifier: MIT
"""``winpodx doctor`` -- diagnose common winpodx state issues (#255 PR 6).

Read-only diagnostic. Walks a small set of checks for things that
commonly leave users stuck (half-installed state, orphan containers,
stale autostart entries, broken deps) and prints a per-check report
with a severity tag and the suggested next command.

Output format mirrors ``apt`` / ``brew doctor``:

    [OK]   freerdp 3.x present at /usr/bin/xfreerdp3
    [WARN] tray autostart entry references missing binary
           Suggested: winpodx uninstall && winpodx setup
    [FAIL] container winpodx-windows exists but config is missing
           Suggested: winpodx uninstall --purge --yes

By default doctor never mutates state -- the suggested commands are
printed for the user to copy. With ``--fix`` doctor additionally runs an
idempotent auto-remediation for every finding that carries a known fixer
(see ``_FIXERS``), then re-probes that single check and reports whether it
is now ``fixed`` or ``still failing``. Findings with no registered fixer
are reported as "no auto-fix available". Each fixer is a no-op when the
underlying state is already healthy.

Exit codes:
    0 -- no FAIL findings (warnings may be present)
    1 -- one or more FAIL findings

Designed to be cheap (< 2 s on a healthy install): every subprocess
probe has a short timeout, and the network never gets touched.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from winpodx.core.i18n import tr

# Oldest FreeRDP 3.x without the RemoteApp/RAIL window-mapping bugs that leave
# an app connected but its window never shown (#546). 3.5.x and earlier are
# affected; warn (not fail) below this, since the binary still works otherwise.
_FREERDP_RAIL_FLOOR = (3, 6, 0)


@dataclass(frozen=True)
class Finding:
    severity: str  # "ok" | "warn" | "fail"
    title: str
    detail: str = ""
    suggestion: str = ""
    # When set, ``--fix`` looks this id up in ``_FIXERS`` and runs the
    # registered remediation for warn/fail findings. ``None`` means "no
    # auto-fix available" -- doctor only prints the suggestion.
    fix_id: str | None = None

    def severity_tag(self) -> str:
        return {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}.get(self.severity, "[?]   ")


def handle_doctor(args: argparse.Namespace) -> None:
    """Run all checks + print the report. Exit 1 on any FAIL finding.

    Flags
    -----
    --json   Serialise the Finding list to JSON (severity, title, detail,
             suggestion, fix_id) instead of the human-readable report.
    --quick  Skip slow probes (container health / guest exec) and run only
             the cheap local checks: freerdp, kvm, backend-on-PATH,
             config-state, pending-setup, autostart, initialized-flag,
             stale-locks, missing-desktop-entries.
             Useful for quick pre-flight checks where a 10-second timeout
             on ``podman ps`` would be disruptive.
    --fix    After collecting findings, run an idempotent remediation for
             every warn/fail finding that carries a registered fixer
             (``fix_id`` in ``_FIXERS``), then re-probe that single check
             and report ``fixed`` / ``still failing``. Findings without a
             fixer report "no auto-fix available". Each fixer is a no-op
             when the state is already healthy. Implies the slow probes
             so guest-touching fixes (dead-agent, oem-drift) are reachable.
    """
    emit_json: bool = getattr(args, "json", False)
    quick: bool = getattr(args, "quick", False)
    do_fix: bool = getattr(args, "fix", False)

    findings = _collect_findings(quick=quick, do_fix=do_fix)

    if emit_json:
        import json

        payload = [
            {
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "suggestion": f.suggestion,
                "fix_id": f.fix_id,
            }
            for f in findings
            if f is not None
        ]
        print(json.dumps(payload, indent=2))
        fail_count = sum(1 for f in findings if f is not None and f.severity == "fail")
        if fail_count:
            sys.exit(1)
        return

    print()
    print("=== WinPodX doctor ===")
    if quick and not do_fix:
        print(tr("(--quick: container-health probe skipped)"))
    print()
    fail_count = 0
    warn_count = 0
    for f in findings:
        if f is None:
            continue
        if f.severity == "fail":
            fail_count += 1
        elif f.severity == "warn":
            warn_count += 1
        print(f"{f.severity_tag()} {f.title}")
        if f.detail:
            print(f"        {f.detail}")
        if f.suggestion:
            print(tr("        Suggested: {suggestion}").format(suggestion=f.suggestion))

    if do_fix:
        fail_count, warn_count = _run_fixes(findings)

    print()
    if fail_count:
        print(
            tr("Summary: {fail_count} FAIL, {warn_count} WARN").format(
                fail_count=fail_count, warn_count=warn_count
            )
        )
        sys.exit(1)
    elif warn_count:
        print(
            tr("Summary: {warn_count} WARN, no FAIL — WinPodX is mostly OK.").format(
                warn_count=warn_count
            )
        )
    else:
        print(tr("Summary: all checks passed."))


def _collect_findings(*, quick: bool, do_fix: bool) -> list[Finding]:
    """Run every check and return the (non-None) findings.

    ``--fix`` implies the slow probes regardless of ``--quick`` so the
    guest-touching fixers (dead-agent, oem-drift) actually have a finding
    to act on.
    """
    findings: list[Finding] = []

    # --- cheap / always-on checks ---
    findings.append(_check_install_source())
    findings.append(_check_freerdp())
    findings.append(_check_kvm())
    findings.extend(_check_container_backend())
    findings.append(_check_config_state())
    findings.append(_check_pending_setup())
    findings.append(_check_autostart_entry())
    findings.append(_check_initialized_flag())
    findings.append(_check_stale_locks())
    findings.append(_check_missing_desktop_entries())

    # --- slow probes (container health / guest exec): skipped by --quick,
    # but forced on by --fix so the guest-touching fixers are reachable. ---
    if not quick or do_fix:
        findings.extend(_check_container_health())
        findings.append(_check_agent_health())
        findings.append(_check_oem_drift())

    return [f for f in findings if f is not None]


# -----------------------------------------------------------------------
# Individual checks. Each returns a single Finding or a list of them.
# -----------------------------------------------------------------------


def _check_install_source() -> Finding:
    try:
        from winpodx.utils.install_source import detect

        src = detect()
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "install source detection failed", detail=str(e))
    if src.kind == "unknown":
        return Finding(
            "warn",
            "install source not detected",
            detail=src.label,
            suggestion="Reinstall via curl install.sh or your distro's package manager.",
        )
    return Finding("ok", f"install source: {src.label}")


def _check_freerdp() -> Finding:
    # Delegate to winpodx.utils.deps.check_freerdp so doctor sees the same
    # set of binaries the launcher does (xfreerdp3 / xfreerdp / sdl-freerdp3
    # / sdl-freerdp + Flatpak). Pre-0.6.0 doctor only looked for the first
    # two and reported MISSING on hosts that had the others.
    from winpodx.utils.deps import check_freerdp

    dep = check_freerdp()
    if dep.found:
        # Best-effort version string for the human reader; a failure to run
        # --version doesn't downgrade the finding (binary exists, that's the
        # signal we care about for doctor).
        version_line = ""
        ver: tuple[int, int, int] | None = None
        try:
            result = subprocess.run(
                [dep.path, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            blob = (result.stdout or "") + (result.stderr or "")
            version_line = result.stdout.splitlines()[0] if result.stdout else ""
            m = re.search(r"FreeRDP version\s+(\d+)\.(\d+)\.(\d+)", blob)
            if m:
                ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        # Old FreeRDP 3.x RAIL: 3.5.x and earlier have window-ordering bugs that
        # leave a RemoteApp connected but its window never mapped (only
        # "xf_Pointer: Invalid appWindow" spam) -- see #546 (Ubuntu/Budgie 24.04
        # ships FreeRDP 3.5.1 via apt). Advisory only: the binary works for
        # full-desktop / many apps, so this is a warn, not a fail.
        if ver is not None and ver[0] == 3 and ver < _FREERDP_RAIL_FLOOR:
            shown = ".".join(str(p) for p in ver)
            floor = ".".join(str(p) for p in _FREERDP_RAIL_FLOOR)
            return Finding(
                "warn",
                f"freerdp {shown} is old; RemoteApp windows may not appear (#546)",
                detail=version_line,
                suggestion=(
                    f"Upgrade FreeRDP to >= {floor} (newer distro package / PPA), or run "
                    "the winpodx AppImage, which bundles a newer FreeRDP. Symptom: the "
                    "app connects but no window appears ('Invalid appWindow' in stderr)."
                ),
            )
        return Finding("ok", f"freerdp present at {dep.path}", detail=version_line)
    return Finding(
        "fail",
        "freerdp not found on PATH",
        detail=dep.note
        or "Looked for xfreerdp3 / xfreerdp / sdl-freerdp3 / sdl-freerdp; none resolved.",
        suggestion="Install via your distro package manager (freerdp / freerdp3 / freerdp-x11).",
    )


def _check_kvm() -> Finding:
    # Delegate to winpodx.utils.deps.check_kvm so doctor keys off the same
    # /dev/kvm signal as the setup wizard + GUI Quick Start.
    from winpodx.utils.deps import check_kvm

    dep = check_kvm()
    if dep.found:
        return Finding("ok", f"{dep.path} present")
    return Finding(
        "fail",
        "/dev/kvm not present",
        detail=(
            "Hardware virtualization is disabled, missing kvm module, "
            "or your user lacks the kvm group."
        ),
        suggestion=(
            "Run `winpodx setup-host --apply` (fixes the kvm group + module "
            "load via one pkexec prompt); if /dev/kvm is still absent, enable "
            "VT-x / AMD-V (SVM) in your BIOS/UEFI."
        ),
    )


def _check_container_backend() -> list[Finding]:
    """Probe the configured backend + verify it resolves."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception as e:  # noqa: BLE001
        return [Finding("warn", "config could not be loaded", detail=str(e))]

    backend = cfg.pod.backend
    if backend == "manual":
        return [Finding("ok", "backend = manual (no container management)")]
    path = shutil.which(backend)
    if path is None:
        return [
            Finding(
                "fail",
                f"configured backend {backend!r} not on PATH",
                suggestion=(
                    f"Install {backend} or change backend via `winpodx config set pod.backend ...`."
                ),
            )
        ]
    return [Finding("ok", f"backend {backend!r} at {path}")]


def _check_config_state() -> Finding:
    """Detect half-installed state: binary present but config missing,
    or vice versa."""
    from winpodx.core.config import Config

    config_path = Config.path()
    binary_path = shutil.which("winpodx")
    if binary_path and not config_path.exists():
        return Finding(
            "warn",
            "winpodx binary present but config missing",
            detail=f"binary: {binary_path}; expected config: {config_path}",
            suggestion=(
                "Run `winpodx setup` (or `winpodx gui` for the graphical first-run prompt)."
            ),
        )
    if config_path.exists() and not binary_path:
        return Finding(
            "fail",
            "config present but winpodx binary not on PATH",
            detail=f"config: {config_path}; PATH binary: missing",
            suggestion="Reinstall WinPodX via curl install.sh or your distro's package manager.",
        )
    if not binary_path and not config_path.exists():
        return Finding(
            "warn",
            "WinPodX not installed (binary + config both absent)",
            suggestion="Install via `curl ... install.sh | bash` or distro package manager.",
        )
    return Finding("ok", "binary + config both present")


def _check_container_health() -> list[Finding]:
    """Check whether a container exists and matches what config expects."""
    try:
        from winpodx.core.config import Config
    except Exception:  # noqa: BLE001
        return []
    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return []
    if cfg.pod.backend not in ("podman", "docker"):
        return []
    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return []
    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [
            Finding(
                "warn",
                f"could not query {cfg.pod.backend} ps",
                suggestion=f"Check that {cfg.pod.backend} is functional.",
            )
        ]

    findings: list[Finding] = []
    container_name = cfg.pod.container_name
    found = False
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if name == container_name:
            found = True
            findings.append(Finding("ok", f"container {container_name} state: {state.lower()}"))
            break
    if not found:
        findings.append(
            Finding(
                "warn",
                f"container {container_name} not found",
                detail=(
                    "Config references a container that doesn't exist "
                    "(may be intentional if you haven't run setup yet)."
                ),
                suggestion="Run `winpodx pod start` or `winpodx setup` to create it.",
            )
        )
    return findings


def _check_pending_setup() -> Finding:
    """Half-installed marker from install.sh -- means a prior install
    didn't finish wait-ready / migrate / discovery."""
    from winpodx.utils.paths import config_dir

    pending = config_dir() / ".pending_setup"
    if not pending.exists():
        return Finding("ok", "no pending install steps")
    try:
        steps = pending.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        steps = []
    return Finding(
        "warn",
        f"pending setup steps detected ({len(steps)} item(s))",
        detail=", ".join(steps) if steps else "(marker present but empty)",
        suggestion=(
            "Run any `winpodx <cmd>` to auto-resume, or `winpodx pod wait-ready` to retry manually."
        ),
    )


def _check_autostart_entry() -> Finding:
    """Tray autostart entry referencing a missing binary is a common
    leftover after a botched uninstall."""
    from winpodx.utils.paths import config_dir

    autostart = config_dir().parent / "autostart" / "winpodx-tray.desktop"
    if not autostart.exists():
        return Finding("ok", "no autostart entry (or none expected)")
    binary = shutil.which("winpodx")
    if binary is None:
        return Finding(
            "fail",
            "autostart entry references a missing winpodx binary",
            detail=str(autostart),
            suggestion="Run `winpodx uninstall` to clean up the autostart entry.",
        )
    return Finding("ok", "autostart entry present and binary resolves")


def _check_initialized_flag() -> Finding:
    """First-run prompt fires when cfg.pod.initialized is False. Surface
    as info so users know whether the prompt is expected on next run."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return Finding("warn", "could not read initialized flag (config load failed)")
    if cfg.pod.initialized:
        return Finding("ok", "cfg.pod.initialized = true (no first-run prompt expected)")
    return Finding(
        "warn",
        "cfg.pod.initialized = false (first-run prompt will fire on next CLI/GUI launch)",
        suggestion="Run `winpodx setup` to silence the prompt and provision the guest.",
    )


# -----------------------------------------------------------------------
# Remediable checks. Each sets ``fix_id`` on the warn/fail Finding so
# ``--fix`` can dispatch the matching fixer in ``_FIXERS``.
# -----------------------------------------------------------------------


def _dead_lock_files() -> list:
    """Return the run-dir ``.cproc`` files whose recorded PID is dead.

    A ``.cproc`` is a winapps-compatible process marker: ``<app>.cproc``
    in ``~/.local/share/winpodx/run/`` containing the FreeRDP child PID. A
    marker whose PID is no longer a live FreeRDP process we spawned is stale
    (the session died without the reaper cleaning up, or the PID was reused)
    and is safe to purge. Liveness is decided by
    ``process.is_freerdp_pid`` -- the same single-source-of-truth check the
    session reaper uses -- so a dead PID *and* a reused-by-something-else PID
    both count as stale.
    """
    from winpodx.core.process import is_freerdp_pid
    from winpodx.utils.paths import data_dir

    run_dir = data_dir() / "run"
    if not run_dir.exists():
        return []
    dead = []
    for path in sorted(run_dir.glob("*.cproc")):
        try:
            raw = path.read_text(encoding="utf-8").strip()
            pid = int(raw) if raw else None
        except (OSError, ValueError):
            # Unreadable / malformed marker -- treat as stale (no live PID).
            dead.append(path)
            continue
        if pid is None or not is_freerdp_pid(pid):
            dead.append(path)
    return dead


def _check_stale_locks() -> Finding:
    """Stale ``.cproc`` lock files (dead owning PID) in the run dir.

    Host-only -- unit-testable. Auto-fixable via ``stale_locks``.
    """
    dead = _dead_lock_files()
    if not dead:
        return Finding("ok", "no stale lock files in run dir")
    return Finding(
        "warn",
        f"{len(dead)} stale lock file(s) in run dir (dead owning PID)",
        detail=", ".join(p.name for p in dead),
        suggestion="Run `winpodx doctor --fix` to purge them.",
        fix_id="stale_locks",
    )


def _desktop_entry_path(app) -> "object":
    """Installed ``.desktop`` path for an app (matches ``install_desktop_entry``)."""
    from winpodx.utils.paths import applications_dir

    return applications_dir() / f"winpodx-{app.name}.desktop"


def _apps_missing_desktop_entries() -> list:
    """Return AppInfo objects that have no ``.desktop`` file installed.

    Skips apps the user has hidden: hiding an app deliberately removes its
    ``.desktop`` entry (see ``core.app.set_app_hidden``), so a hidden app
    legitimately has none. Counting those as "missing" made ``doctor`` flag
    every app the user cleaned out of the launcher and offer ``--fix`` to
    re-register them — which would silently un-hide them all (#535).
    """
    from winpodx.core.app import list_available_apps

    missing = []
    for app in list_available_apps():
        if getattr(app, "hidden", False):
            continue
        if not _desktop_entry_path(app).exists():
            missing.append(app)
    return missing


def _check_missing_desktop_entries() -> Finding:
    """Apps in the index with no installed ``.desktop`` file.

    Host-only -- unit-testable. Auto-fixable via ``missing_desktop_entries``.
    """
    try:
        missing = _apps_missing_desktop_entries()
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "could not enumerate apps", detail=str(e))
    if not missing:
        return Finding("ok", "all registered apps have desktop entries")
    return Finding(
        "warn",
        f"{len(missing)} app(s) missing a desktop entry",
        detail=", ".join(a.name for a in missing),
        suggestion="Run `winpodx doctor --fix` (or `winpodx app install <name>`) to re-register.",
        fix_id="missing_desktop_entries",
    )


def _pod_running(cfg) -> bool:
    """True when the configured container exists and is in the running state."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False
    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return False
    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if name == cfg.pod.container_name and state.lower() == "running":
            return True
    return False


def _check_agent_health() -> Finding:
    """Dead agent: pod is RUNNING but the in-guest agent ``/health`` is down.

    Guest-touching -- smoke-gated. Auto-fixable via ``dead_agent`` (kick the
    in-guest ``WinpodxAgentKeepAlive`` keep-alive task / agent-restart path).
    Only flagged when the pod is actually running; a stopped pod is a
    different (expected) state handled by the container-health check.
    """
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return None  # type: ignore[return-value]
    if not _pod_running(cfg):
        return None  # type: ignore[return-value]
    try:
        from winpodx.core.agent import AgentClient

        AgentClient(cfg).health()
    except Exception as e:  # noqa: BLE001
        return Finding(
            "fail",
            "pod is running but the guest agent /health is down",
            detail=str(e),
            suggestion=(
                "Run `winpodx doctor --fix` to kick the keep-alive, or `winpodx pod restart`."
            ),
            fix_id="dead_agent",
        )
    return Finding("ok", "guest agent /health responding")


def _check_oem_drift() -> Finding:
    """OEM-version drift: host ``oem_bundle`` stamp newer than the guest's.

    Guest-touching -- smoke-gated. Auto-fixable via ``oem_drift`` (run
    ``guest_sync.maybe_autosync`` / ``sync_guest``). Mirrors
    ``guest_sync.guest_sync_needed`` but reports info rather than syncing.
    """
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return None  # type: ignore[return-value]
    if cfg.pod.backend not in ("podman", "docker"):
        return None  # type: ignore[return-value]
    if not _pod_running(cfg):
        return None  # type: ignore[return-value]
    try:
        from winpodx.core.guest_sync import host_version, read_guest_version

        guest = read_guest_version(cfg)
        host = host_version()
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "could not read guest version stamp", detail=str(e))
    if guest is None:
        # No stamp -> fresh / pre-stamp pod. maybe_autosync deliberately does
        # NOT sync here (it would race first-boot), so this is not a drift
        # finding -- report OK and let the normal start path stamp it.
        return Finding("ok", "guest version stamp absent (fresh/pre-stamp pod)")
    if guest == host:
        return Finding("ok", f"guest version current ({guest.oem_bundle})")
    return Finding(
        "warn",
        "guest is older than the host (oem-version drift)",
        detail=f"guest oem_bundle={guest.oem_bundle}, host oem_bundle={host.oem_bundle}",
        suggestion="Run `winpodx doctor --fix` to sync, or `winpodx guest sync --force`.",
        fix_id="oem_drift",
    )


# -----------------------------------------------------------------------
# Fixers. Each is idempotent + a no-op when the state is already healthy
# (the re-probe after dispatch confirms). Return (ok, message).
# -----------------------------------------------------------------------


def _fix_stale_locks() -> tuple[bool, str]:
    """Purge run-dir ``.cproc`` files whose owning PID is dead. Host-only."""
    dead = _dead_lock_files()
    if not dead:
        return True, "no stale lock files to purge"
    purged = 0
    for path in dead:
        try:
            path.unlink(missing_ok=True)
            purged += 1
        except OSError as e:
            return False, f"failed to remove {path.name}: {e}"
    return True, f"purged {purged} stale lock file(s)"


def _fix_missing_desktop_entries() -> tuple[bool, str]:
    """Re-register desktop entries for apps that are missing one. Host-only.

    Reuses the existing desktop-entry install path
    (``desktop.entry.install_desktop_entry`` + icon + MIME), the same one
    ``winpodx app install`` and refresh's ``_register_desktop_entries`` use.
    Each install is idempotent (it overwrites the entry + icon in place), so
    re-running against an already-installed app is harmless.
    """
    try:
        missing = _apps_missing_desktop_entries()
    except Exception as e:  # noqa: BLE001
        return False, f"could not enumerate apps: {e}"
    if not missing:
        return True, "no missing desktop entries"

    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import update_icon_cache
    from winpodx.desktop.mime import register_mime_types

    registered = 0
    failed: list[str] = []
    for app in missing:
        try:
            install_desktop_entry(app)
            if app.mime_types:
                register_mime_types(app)
            registered += 1
        except Exception as e:  # noqa: BLE001
            failed.append(f"{app.name} ({e})")
    # Refresh the icon cache once after the batch (cheap, idempotent).
    try:
        update_icon_cache()
    except Exception:  # noqa: BLE001 -- cache refresh is best-effort
        pass
    if failed:
        return False, f"re-registered {registered}, failed: {', '.join(failed)}"
    return True, f"re-registered {registered} desktop entry(ies)"


# PowerShell: start the keep-alive watchdog task now. The task itself is
# idempotent (it relaunches agent.ps1 only when none is running -- it never
# kills a healthy agent), so triggering it on a healthy guest is a no-op.
_KICK_KEEPALIVE_PS = (
    "Start-ScheduledTask -TaskName 'WinpodxAgentKeepAlive' "
    "-ErrorAction SilentlyContinue; "
    "Write-Output 'keepalive-kicked'"
)


def _fix_dead_agent() -> tuple[bool, str]:
    """Kick the guest keep-alive to revive a dead agent. Guest-touching.

    Triggers the in-guest ``WinpodxAgentKeepAlive`` scheduled task (run it
    now) over the transport. With the agent down, ``run_via_transport``
    falls back to FreeRDP RemoteApp, so the kick still reaches the guest.
    The keep-alive task is idempotent (relaunches the agent only when none
    is running), so this is a no-op against a healthy agent. Then poll
    ``/health`` to confirm the agent came back.
    """
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception as e:  # noqa: BLE001
        return False, f"config load failed: {e}"
    if cfg.pod.backend not in ("podman", "docker"):
        return False, f"backend {cfg.pod.backend!r} has no guest agent"

    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    try:
        run_via_transport(cfg, _KICK_KEEPALIVE_PS, timeout=60, description="doctor-fix-agent-kick")
    except WindowsExecError as e:
        return False, f"could not reach guest to kick keep-alive: {e}"

    # Confirm the agent answers /health again before declaring success.
    from winpodx.core.guest_sync import _wait_agent_back

    if _wait_agent_back(cfg, timeout=120):
        return True, "keep-alive kicked; agent /health is back"
    return False, "keep-alive kicked but agent did not return within 120s"


def _fix_oem_drift() -> tuple[bool, str]:
    """Sync the guest when the host OEM bundle is newer. Guest-touching.

    Delegates to ``guest_sync.maybe_autosync`` (honours ``guest_autosync``
    and is a no-op when the guest is already current). ``maybe_autosync``
    gates on agent health + only syncs a present-and-older stamp, so this is
    safe to run unconditionally.
    """
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception as e:  # noqa: BLE001
        return False, f"config load failed: {e}"
    from winpodx.core.guest_sync import GuestSyncError, maybe_autosync

    try:
        synced = maybe_autosync(cfg)
    except GuestSyncError as e:
        return False, f"guest sync failed: {e}"
    if synced:
        return True, "guest synced to host version"
    return True, "guest already current (no sync needed)"


# fix_id -> fixer callable. Adding a remediable check is two edits: set
# ``fix_id`` on the warn/fail Finding it returns, and register the fixer here.
_FIXERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "stale_locks": _fix_stale_locks,
    "missing_desktop_entries": _fix_missing_desktop_entries,
    "dead_agent": _fix_dead_agent,
    "oem_drift": _fix_oem_drift,
}

# fix_id -> the check function used to re-probe after a fix. Re-running the
# single check confirms ``fixed`` vs ``still failing`` without re-walking the
# whole report.
_REPROBES: dict[str, Callable[[], Finding | None]] = {
    "stale_locks": _check_stale_locks,
    "missing_desktop_entries": _check_missing_desktop_entries,
    "dead_agent": _check_agent_health,
    "oem_drift": _check_oem_drift,
}


def _run_fixes(findings: list[Finding]) -> tuple[int, int]:
    """Dispatch fixers for remediable findings, re-probe, and report.

    Iterates the collected findings; for each warn/fail finding it either
    dispatches the registered fixer (and re-runs that single check to
    confirm) or notes that no auto-fix is available. Returns the
    ``(fail_count, warn_count)`` recomputed from the post-fix state so the
    summary + exit code reflect what remains broken.
    """
    remediable = [f for f in findings if f.severity in ("warn", "fail")]
    print()
    print(tr("=== auto-fix (--fix) ==="))
    if not remediable:
        print(tr("Nothing to fix -- no warn/fail findings."))
        return 0, 0

    for f in remediable:
        if f.fix_id is None or f.fix_id not in _FIXERS:
            print(tr("[skip] {title}: no auto-fix available").format(title=f.title))
            continue
        fixer = _FIXERS[f.fix_id]
        try:
            ok, message = fixer()
        except Exception as e:  # noqa: BLE001
            print(tr("[fail] {title}: fixer raised: {error}").format(title=f.title, error=e))
            continue

        # Re-probe the single check to confirm the post-fix state.
        reprobe = _REPROBES.get(f.fix_id)
        resolved = ok
        if reprobe is not None:
            after = reprobe()
            resolved = after is None or after.severity == "ok"

        if resolved:
            print(tr("[fixed] {title}: {message}").format(title=f.title, message=message))
        else:
            print(tr("[still failing] {title}: {message}").format(title=f.title, message=message))

    # Recompute counts from a fresh probe of the remediable checks so the
    # summary reflects the post-fix reality (host-only fixes re-probe
    # cheaply; guest-touching re-probes hit the agent once).
    fail_count = 0
    warn_count = 0
    for f in findings:
        if f.fix_id in _REPROBES:
            after = _REPROBES[f.fix_id]()
            sev = after.severity if after is not None else "ok"
        else:
            sev = f.severity
        if sev == "fail":
            fail_count += 1
        elif sev == "warn":
            warn_count += 1
    return fail_count, warn_count
