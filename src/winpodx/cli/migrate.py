"""Post-upgrade migration wizard for winpodx.

Invoked automatically by ``install.sh`` when an existing winpodx
installation is detected, or manually via ``winpodx migrate``. Shows
version-specific release notes for versions the user has skipped over
and optionally triggers app discovery so the new dynamic-discovery
feature (v0.1.8+) populates the menu immediately.

Version tracking lives in ``~/.config/winpodx/installed_version.txt``.
The first install after v0.1.8 writes that file; earlier installs had
no tracker, so a missing file combined with an existing
``winpodx.toml`` is treated as a pre-tracker upgrade (baseline 0.1.7).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from winpodx import __version__
from winpodx.utils.paths import config_dir

# Marker-file hardening (Wave 2 audit L2): cap read size and regex-validate
# content so a crafted or corrupted ``installed_version.txt`` cannot hang
# the wizard or inject garbage into the version-comparison path.
_MAX_MARKER_BYTES = 64
_MARKER_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}[a-z0-9]*$")

# Per-version release highlights shown to users upgrading from an earlier
# version. Add new entries at the top when cutting a release. Keep each
# note user-facing, not developer-facing — three to six bullets max.
_VERSION_NOTES: dict[str, list[str]] = {
    "0.1.9": [
        "The 14 bundled app profiles were dropped — the app menu now populates "
        "from the apps actually installed in your Windows guest.",
        "First pod boot auto-fires `winpodx app refresh` so the menu fills in "
        "without user intervention.",
        "New `Info` page in the GUI + expanded `winpodx info` CLI showing "
        "System / Display / Dependencies / Pod / Config in one snapshot.",
        "Fixed: `winpodx app refresh` on Windows (script transport rewritten "
        "from `podman cp C:/...` — which can't reach the Windows VM's virtual "
        "disk — to a stdin pipe into `powershell -Command -`).",
        "Fixed: RDP unreachable after host suspend / long idle. winpodx now "
        "auto-restarts TermService when VNC is alive but RDP isn't.",
    ],
    "0.1.8": [
        "Dynamic Windows-app discovery (scan + register apps installed on the container).",
        "New CLI: `winpodx app refresh`",
        "New GUI: 'Refresh Apps' button on the Apps page",
        "UWP / MSIX apps supported via RemoteApp (launch via AUMID)",
        "Migration wizard: `winpodx migrate` (this command)",
    ],
}

# v0.1.9 dropped these 14 bundled profiles. Their .desktop entries from
# 0.1.7/0.1.8 installs are still on disk under
# ~/.local/share/applications/ — migrate offers (prompts, never auto)
# to remove them so the menu stays clean.
_LEGACY_BUNDLED_SLUGS = (
    "access-o365",
    "calc",
    "cmd",
    "excel-o365",
    "explorer",
    "mspaint",
    "notepad",
    "onenote-o365",
    "outlook-o365",
    "powerpoint-o365",
    "powershell",
    "teams",
    "vscode",
    "word-o365",
)

_VERSION_FILE = "installed_version.txt"
# Pre-tracker installs (0.1.7 and earlier) are assumed to be on this
# version when an existing config is present but no marker file exists.
_PRETRACKER_BASELINE = "0.1.7"


def run_migrate(args: argparse.Namespace) -> int:
    """Entry point for ``winpodx migrate``. Returns the process exit code."""
    current = __version__
    installed = _detect_installed_version()

    if installed is None:
        _write_installed_version(current)
        print(f"winpodx {current}: fresh install recorded. No migration needed.")
        return 0

    cur_cmp = _version_tuple(current)[:3]
    inst_cmp = _version_tuple(installed)[:3]

    if inst_cmp >= cur_cmp:
        _write_installed_version(current)
        print(f"winpodx {current}: already current.")
        # v0.1.9.3: even on "already current" still run the idempotent
        # Windows-side apply. Patch versions (0.1.9.x) collapse to the
        # same (0,1,9) tuple under [:3] truncation, so without this an
        # upgrade from 0.1.9.0 -> 0.1.9.2 would skip the apply entirely
        # — exactly the bug kernalix7 hit. Helpers are idempotent so a
        # no-op run is harmless if everything was already applied.
        non_interactive = bool(getattr(args, "non_interactive", False))
        # v0.1.9.5: probe for password drift before attempting apply.
        # If FreeRDP auth fails, point the user at `winpodx pod sync-password`
        # rather than letting them watch all three applies fail in confusing
        # ways.
        _probe_password_sync(non_interactive)
        _apply_runtime_fixes_to_existing_guest(non_interactive)
        return 0

    print(f"winpodx: {installed} -> {current} detected\n")
    _print_whats_new(installed, current)

    non_interactive = bool(getattr(args, "non_interactive", False))
    skip_refresh = bool(getattr(args, "no_refresh", False))

    # v0.1.8 -> 0.1.9: the 14 bundled .desktop entries are now stale.
    # Offer to clean them up (only when we're crossing the 0.1.9 boundary
    # so we don't keep prompting forever).
    if inst_cmp < (0, 1, 9) <= cur_cmp:
        _maybe_cleanup_legacy_bundled(non_interactive)

    # v0.1.9.2: when upgrading TO v0.1.9 or later, proactively apply
    # Windows-side fixes (OEM v7+v8 equivalents) to the existing guest
    # so the user doesn't have to recreate their container. install.bat
    # only runs on a fresh container, so without this step a 0.1.6 -> 0.1.9.2
    # user would never see RDP-timeout / NIC-power / TermService-recovery
    # fixes land on their actual Windows VM.
    if inst_cmp < (0, 1, 9) <= cur_cmp:
        _apply_runtime_fixes_to_existing_guest(non_interactive)

    if skip_refresh:
        print("\nSkipping app discovery (--no-refresh).")
    elif non_interactive:
        print("\nSkipping app discovery (--non-interactive).")
    elif _prompt_yes("\nRun app discovery now? (scans Windows pod for installed apps)"):
        _attempt_refresh()

    _write_installed_version(current)
    print(f"\nMigration complete. Marker updated to {current}.")
    print("Re-run this wizard any time with: winpodx migrate")
    return 0


def _maybe_cleanup_legacy_bundled(non_interactive: bool) -> None:
    """Find pre-0.1.9 winpodx-<bundled-slug>.desktop files and offer to remove them.

    Non-interactive mode lists what would be removed but does NOT delete —
    silent destructive ops in automation paths are bad. The user can re-run
    `winpodx migrate` interactively when ready.
    """
    from winpodx.utils.paths import applications_dir, icons_dir

    apps_dir = applications_dir()
    if not apps_dir.exists():
        return

    stale_desktop: list[Path] = []
    for slug in _LEGACY_BUNDLED_SLUGS:
        candidate = apps_dir / f"winpodx-{slug}.desktop"
        if candidate.exists():
            stale_desktop.append(candidate)

    if not stale_desktop:
        return

    print(
        f"\nFound {len(stale_desktop)} legacy bundled-app entries from a previous "
        "winpodx version (these were removed in 0.1.9):"
    )
    for d in stale_desktop:
        print(f"  - {d.name}")

    if non_interactive:
        print(
            "  (--non-interactive set — skipping cleanup. "
            "Re-run `winpodx migrate` interactively to remove.)"
        )
        return

    if not _prompt_yes("Remove them now?", default=True):
        print("  Skipped — entries left in place.")
        return

    icon_root = icons_dir()
    removed = 0
    for desktop in stale_desktop:
        try:
            desktop.unlink()
            removed += 1
        except OSError as e:
            print(f"  warning: could not remove {desktop}: {e}")
            continue
        # Best-effort matching icon cleanup (won't error if absent).
        for ext_dir in ("scalable/apps", "32x32/apps"):
            for ext in ("svg", "png"):
                icon_file = icon_root / ext_dir / f"{desktop.stem}.{ext}"
                try:
                    icon_file.unlink()
                except OSError:
                    pass
    print(f"  Removed {removed} of {len(stale_desktop)} legacy entries.")


def _detect_installed_version() -> Optional[str]:
    """Return the recorded installed version, 'pre-tracker baseline', or None.

    Priority:
    1. ``installed_version.txt`` if readable.
    2. If ``winpodx.toml`` exists (pre-tracker upgrade), return the
       baseline (``0.1.7``).
    3. Otherwise return ``None`` — treated as a fresh install.
    """
    marker = _read_installed_version()
    if marker:
        return marker

    from winpodx.core.config import Config

    if Config.path().exists():
        return _PRETRACKER_BASELINE
    return None


def _read_installed_version() -> Optional[str]:
    """Return the version string from the marker file, or None if absent/invalid.

    Reads at most ``_MAX_MARKER_BYTES`` and requires the stripped content
    to match ``_MARKER_VERSION_RE``. An oversized or malformed marker is
    treated as absent so the caller falls through to the pre-tracker
    baseline path; a warning is emitted to stderr so the user can clean
    up the file.
    """
    path = config_dir() / _VERSION_FILE
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_MARKER_BYTES + 1)
    except (FileNotFoundError, OSError):
        return None

    if len(raw) > _MAX_MARKER_BYTES:
        print(
            f"warning: {path} exceeds {_MAX_MARKER_BYTES} bytes; ignoring marker.",
            file=sys.stderr,
        )
        return None

    try:
        content = raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        print(f"warning: {path} is not valid UTF-8; ignoring marker.", file=sys.stderr)
        return None

    if not content:
        return None
    if not _MARKER_VERSION_RE.fullmatch(content):
        print(
            f"warning: {path} content {content!r} is not a valid version; ignoring marker.",
            file=sys.stderr,
        )
        return None
    return content


def _write_installed_version(version: str) -> None:
    """Write ``version`` to the marker file, 0644 permissions."""
    path = config_dir() / _VERSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version + "\n", encoding="utf-8")
    try:
        path.chmod(0o644)
    except OSError:
        # Non-fatal; file already exists on POSIX with umask defaults.
        pass


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse ``'0.1.8'`` -> ``(0, 1, 8)``.

    Stops at the first non-integer segment so a pre-release suffix like
    ``0.1.8rc1`` still compares correctly against ``0.1.8``.
    """
    out: list[int] = []
    for segment in v.strip().split("."):
        try:
            out.append(int(segment))
        except ValueError:
            break
    return tuple(out)


def _print_whats_new(installed: str, current: str) -> None:
    """Print per-version notes for every version in ``(installed, current]``."""
    inst = _version_tuple(installed)[:3]
    cur = _version_tuple(current)[:3]

    relevant: list[tuple[tuple[int, ...], str, list[str]]] = []
    for ver, notes in _VERSION_NOTES.items():
        v = _version_tuple(ver)[:3]
        if inst < v <= cur:
            relevant.append((v, ver, notes))
    relevant.sort()

    if not relevant:
        print("(No user-facing release notes for the versions between installed and current.)")
        return

    for _, ver, notes in relevant:
        print(f"What's new in {ver}:")
        for note in notes:
            print(f"  - {note}")
        print()


def _prompt_yes(question: str, default: bool = True) -> bool:
    """Interactive yes/no prompt. Returns True on accept, False on decline/EOF.

    Empty input returns ``default``.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        response = input(f"{question} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not response:
        return default
    return response in ("y", "yes")


def _probe_password_sync(non_interactive: bool) -> None:
    """v0.1.9.5: detect cfg/Windows password drift before apply.

    Fires a tiny no-op PS payload through the FreeRDP RemoteApp channel.
    If the channel returns a parseable result, auth worked and the
    password is in sync. If it raises a "no result file" or auth-flavored
    error, we know cfg.password no longer matches Windows. In that case
    we surface the recovery instructions and let the apply fall through
    naturally (it will fail the same way and the user has the context).
    """
    try:
        from winpodx.core.config import Config
        from winpodx.core.pod import PodState, pod_status
        from winpodx.core.windows_exec import WindowsExecError, run_in_windows
    except ImportError as e:
        print(f"  (skipping password-sync probe: {e})")
        return

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        return
    try:
        if pod_status(cfg).state != PodState.RUNNING:
            return
    except Exception:  # noqa: BLE001
        return

    # v0.2.0.4: gate the probe on wait_for_windows_responsive. Without
    # this, a fresh --purge install (where Windows VM inside QEMU is
    # still booting in the background) makes the probe fire too early,
    # FreeRDP returns rc=147 ERRCONNECT_CONNECT_TRANSPORT_FAILED with a
    # "no result file written" wrapper message, the matcher saw "no
    # result file" and falsely classified it as auth failure → surfaced
    # a bogus "cfg.password does not match Windows" warning on every
    # fresh install. The probe only makes sense when we can actually
    # reach the guest; if not, skip silently and let the apply step
    # (which has its own wait gate) decide.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print("\nProbing Windows-side authentication...")
    if not wait_for_windows_responsive(cfg, timeout=180):
        print("  (probe deferred — guest still booting; will retry on next ensure_ready)")
        return
    try:
        run_in_windows(
            cfg,
            "Write-Output 'sync-check'",
            description="probe-password-sync",
            # FreeRDP handshake + RemoteApp launch + tsclient redirection
            # negotiation can take 30+ seconds on first contact after a
            # cold pod start. 20s was too aggressive and surfaced as
            # "(probe inconclusive: ... timed out after 20s)".
            timeout=60,
        )
    except WindowsExecError as e:
        msg = str(e).lower()
        # v0.2.0.4: tighten error classification. Transport-level
        # failures (rc=131 activation timeout, rc=147 connection reset,
        # "transport_failed", etc.) mean the guest isn't ready — they
        # do NOT mean the password is wrong. Only treat "auth"-flavored
        # FreeRDP errors as drift indicators.
        is_transport_error = (
            "rc=131" in msg
            or "rc=147" in msg
            or "errconnect_connect_transport_failed" in msg
            or "errconnect_activation_timeout" in msg
            or "transport_read_layer" in msg
            or "connection reset" in msg
            or "transport failed" in msg
        )
        is_auth_error = (
            "auth" in msg
            or "logon_failure" in msg
            or "errconnect_logon" in msg
            or "0xc000006d" in msg  # STATUS_LOGON_FAILURE
        )
        if is_auth_error and not is_transport_error:
            print(
                "  WARNING: cfg.password does not match Windows guest's account "
                "password (FreeRDP authentication failed).\n"
                "  This usually means password rotation has been silently failing "
                "for prior winpodx versions (the runtime apply path was broken).\n"
                "\n"
                "  To fix: run `winpodx pod sync-password` and provide the "
                "password Windows currently accepts (typically the original "
                "from your initial setup). The Windows-side apply step below "
                "will fail until you do this."
            )
        else:
            print(f"  (probe inconclusive: {e})")
    else:
        print("  Password sync OK.")


def _apply_runtime_fixes_to_existing_guest(non_interactive: bool) -> None:
    """v0.1.9.2: push OEM v7+v8 equivalents to the guest without recreating it.

    install.bat only runs on dockur's first-boot unattended path — existing
    containers from 0.1.6 / 0.1.7 / 0.1.8 / 0.1.9 / 0.1.9.1 never picked up
    NIC-power, TermService-failure, max_sessions, or RDP-timeout fixes
    shipped in later versions. We pipe the equivalent PowerShell to the
    running guest via the same `podman exec` channel discovery uses.

    Best-effort — log + skip on any failure (pod stopped, exec error, etc.)
    so a transient problem doesn't block the rest of migrate.
    """
    print("\nApplying Windows-side fixes to your existing pod...")
    try:
        from winpodx.core.config import Config
        from winpodx.core.pod import PodState, pod_status
        from winpodx.core.provisioner import (
            _apply_max_sessions,
            _apply_oem_runtime_fixes,
            _apply_rdp_timeouts,
        )
    except ImportError as e:
        print(f"  warning: cannot load provisioner helpers ({e}); skipping.")
        return

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(f"  Skipped: backend {cfg.pod.backend!r} doesn't support runtime apply.")
        return

    try:
        state = pod_status(cfg).state
    except Exception as e:  # noqa: BLE001
        print(f"  warning: cannot probe pod state ({e}); skipping runtime apply.")
        return

    if state != PodState.RUNNING:
        print(f"  Pod is {state.value if hasattr(state, 'value') else state}, not running.")
        if non_interactive:
            print(
                "  Skipping (--non-interactive). "
                "Run `winpodx app run desktop` later — apply fires automatically."
            )
            return
        if not _prompt_yes("  Start the pod now and apply?", default=True):
            print("  Skipped — apply will run automatically next time the pod starts.")
            return
        try:
            from winpodx.core.provisioner import ensure_ready

            ensure_ready(cfg)
            print("  Pod started + Windows-side fixes applied.")
        except Exception as e:  # noqa: BLE001
            print(f"  warning: pod start failed ({e}). Try `winpodx pod start --wait`.")
        return

    # v0.2.0.1: container can be RUNNING while the Windows VM inside
    # QEMU is still booting — that's the fresh-install scenario where
    # `setup` recreates the container then `migrate` runs immediately
    # and every apply collapses with rc=147 connection-reset / rc=131
    # activation-timeout. Wait for the RDP listener to actually accept
    # FreeRDP RemoteApp before firing applies.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print("  Waiting for Windows guest to finish booting (up to 180s)...")
    if not wait_for_windows_responsive(cfg, timeout=180):
        print(
            "  Windows guest still booting after 180s — skipping runtime apply.\n"
            "  Run `winpodx pod apply-fixes` once `winpodx pod status` reports "
            "the pod is fully up, or just launch any app and the apply will "
            "fire automatically."
        )
        return

    # Pod is running — three idempotent applies.
    failures: list[str] = []
    for name, fn in (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
    ):
        try:
            fn(cfg)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {e}")

    if failures:
        print("  warning: some applies failed (others may have succeeded):")
        for f in failures:
            print(f"    - {f}")
        print(
            "  Re-run `winpodx migrate` after fixing the underlying issue, "
            "or recreate the container as a last resort."
        )
    else:
        print(
            "  OK: applied to existing guest (no container recreate needed). "
            "RDP timeouts / NIC power-save / TermService recovery now active."
        )


def _attempt_refresh() -> None:
    """Run discovery; optionally start the pod first if the backend supports it."""
    from winpodx.core.config import Config

    cfg = Config.load()

    try:
        from winpodx.core.discovery import (
            DiscoveryError,
            discover_apps,
            persist_discovered,
        )
    except ImportError:
        print("\n  Discovery unavailable in this build. Skipping.")
        return

    if not _pod_is_running(cfg):
        if not _prompt_yes("  Pod is not running. Start it first? (first boot can take ~1 minute)"):
            print("\n  Skipping refresh. Later: `winpodx pod start --wait && winpodx app refresh`")
            return
        try:
            from winpodx.core.provisioner import ProvisionError, ensure_ready

            print("\n  Starting pod...")
            ensure_ready(cfg)
        except ProvisionError as exc:
            print(f"\n  Could not start pod: {exc}")
            print("  Run `winpodx pod start --wait` manually and try again.")
            return
        except Exception as exc:  # noqa: BLE001 — surface any startup failure
            print(f"\n  Could not start pod: {exc}")
            return

    # v0.2.0.3: discovery hits the same FreeRDP RemoteApp channel as the
    # apply path, so it suffers the same race when Windows VM inside QEMU
    # is still booting. Wait until the guest is responsive (or skip with
    # a useful message) before running the scan; otherwise the user just
    # sees rc=147 connection-reset right after a fresh install.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print("\n  Waiting for Windows guest to be ready (up to 180s)...")
    if not wait_for_windows_responsive(cfg, timeout=180):
        print(
            "  Windows guest still booting — skipping discovery for now.\n"
            "  Re-run later with: winpodx app refresh"
        )
        return

    try:
        print("\n  Scanning Windows pod for installed apps...")
        apps = discover_apps(cfg)
        written = persist_discovered(apps)
        print(f"  Discovered {len(apps)} app(s); wrote {len(written)} profile(s).")
    except DiscoveryError as exc:
        print(f"\n  Discovery failed: {exc}")
        print("  Retry later with: winpodx app refresh")


def _pod_is_running(cfg) -> bool:
    """Lightweight ``{runtime} ps --filter name=...`` check.

    Returns ``False`` for libvirt/manual backends — those don't support
    discovery yet (v0.2.0 guest-agent work), so migrate just surfaces
    whatever error ``core.discovery`` emits when called.
    """
    runtime = cfg.pod.backend
    if runtime not in ("podman", "docker"):
        return False
    try:
        result = subprocess.run(  # noqa: S603 — args is a fixed list
            [
                runtime,
                "ps",
                "--filter",
                f"name={cfg.pod.container_name}",
                "--format",
                "{{.State}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return "running" in result.stdout.lower()
