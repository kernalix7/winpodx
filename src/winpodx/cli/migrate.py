# SPDX-License-Identifier: MIT
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
from winpodx.core.i18n import tr
from winpodx.utils.paths import config_dir

# Marker-file hardening (Wave 2 audit L2): cap read size and regex-validate
# content so a crafted or corrupted ``installed_version.txt`` cannot hang
# the wizard or inject garbage into the version-comparison path.
_MAX_MARKER_BYTES = 64
_MARKER_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}[a-zA-Z0-9.+_-]*$")

# Per-version release highlights shown to users upgrading from an earlier
# version. Add new entries at the top when cutting a release. Keep each
# note user-facing, not developer-facing — three to six bullets max.
_VERSION_NOTES: dict[str, list[str]] = {
    "0.4.0": [
        "Container image is now SHA-pinned. dockur's :latest push cadence no "
        "longer triggers unsolicited container recreates -- updates are opt-in "
        "via `winpodx setup --update-image`. Migrate auto-aligns existing pods "
        "on upgrade (one ~30s recreate on next pod start, volume preserved -- "
        "no ISO redownload).",
        "No more PowerShell console flashes anywhere. WinpodxAgent and "
        "WinpodxMedia HKCU\\Run entries both wrapped under wscript+"
        "hidden-launcher.vbs; legacy flashing fallbacks removed; install.bat "
        "ASCII-only so Windows Script Host doesn't choke on em-dashes; "
        "install.bat's `reg add` replaced with PowerShell Set-ItemProperty so "
        "cmd-quoting can't garble the registry value.",
        "Fresh installs honestly wait for Windows. check_rdp_port now does a "
        "real X.224 handshake instead of TCP-accept, so QEMU slirp accepting "
        "forwards mid-ISO-download no longer fakes 'RDP port open'. "
        "wait_for_windows_responsive returns True with a logged warning if "
        "/health doesn't come up within 60s -- install.sh proceeds via FreeRDP "
        "fallback instead of deadlocking phase 3 for the full 60-minute cap.",
        "Multi-session activation is hands-free. `winpodx pod apply-fixes` and "
        "migrate auto-activate rdprrap if it isn't already on; if ServiceDll "
        "is already patched, the marker is reconciled without cycling "
        "TermService (so the agent doesn't get killed for nothing). New "
        "runtime activator rdprrap-activate.ps1 survives the TermService "
        "restart it triggers; install.bat delegates to the same script for "
        "OEM-time activation -- single source of truth.",
        "App discovery is consistent across first-boot races. Guest-side "
        "readiness gate (waits for AppXSvc + Start Menu .lnk count to "
        "stabilize), host-side transport readiness (waits for agent /health "
        "or RDP), and host-side retry-on-empty. Default discovery timeout "
        "bumped 120s to 180s.",
        "Discovery error classification fixed. ERRINFO_LOGOFF_BY_USER and "
        "ERRINFO_RPC_INITIATED_DISCONNECT no longer mislabeled as 'Pod Not "
        "Running' in the GUI -- new session_disconnected kind with a Retry "
        "dialog instead of a misleading 'Start Pod' prompt.",
        "install.sh more robust. install.bat's per-file copy now verifies + "
        "writes diagnostics to C:\\winpodx\\setup.log; HKCU\\Run uses an "
        "existence-gated PowerShell registration with a flash-but-working "
        "fallback; install.bat ends by spawning the agent directly (HKCU\\Run "
        "only fires once per logon, so registering it doesn't help the "
        "current session).",
        "SELinux + Fedora. `winpodx pod start` no longer fails with "
        "lsetxattr: operation not permitted. The OEM bind mount now copies "
        "into a user-owned ~/.config/winpodx/oem/ so Podman's :Z relabeling "
        "works regardless of install method. bundle_dir() marker check "
        "tightened from any() to all() so partial leftovers don't hijack "
        "path resolution. (Thanks @pgarciaq.)",
        "migrate works on RTM-suffixed pods (0.3.0-RTM1 etc.). Version parser "
        "now extracts leading digits per dot-segment; previously 0.3.0-RTM1 "
        "lex-compared less than 0.3.0, dropping the apply chain entirely.",
        "New docs/design/LIFECYCLE.md (en + ko) -- end-to-end process reference "
        "covering install, Sysprep, migrate, apply chain, multi-session "
        "activation, image pinning, discovery, transport selection, and 7 "
        "common recovery scenarios.",
        "Contributing policy: AI-tool co-author trailers (Cursor, Claude, "
        "Copilot, etc.) are now banned in commit messages. See "
        "CONTRIBUTING.md.",
    ],
    "0.3.1": [
        "Multi-session RDP auto-activates on `winpodx pod apply-fixes` "
        "(and migrate). The 'Select a session to reconnect to' dialog "
        "that appeared on every multi-app launch when rdprrap activation "
        "had failed at OEM time is now self-healed without container "
        "recreate. RDP sessions briefly disconnect (~10 s) during first-"
        "migration activation; subsequent applies are no-ops.",
        "`winpodx pod multi-session on` works at runtime — spawns a "
        "detached activator that survives the TermService restart it "
        "triggers. Existing pods get the activator script staged via "
        "`apply-fixes` (vbs_launchers step pushes it).",
        "install.bat consolidates ~80 lines of inline rdprrap install/"
        "verify/marker logic onto rdprrap-activate.ps1 — single source "
        "of truth shared by OEM-time and runtime activation paths. "
        "OEM bundle 16 → 18.",
        "migrate no longer skips apply-fixes on RTM-suffixed pods. "
        "Version parser was treating `0.3.0-RTM1` as `(0, 3)` instead of "
        "`(0, 3, 0)`, dropping the apply-fixes step. Stale `< 0.1.9` "
        "gate on the cross-version path also removed — chain is "
        "idempotent so it's safe to always run.",
    ],
    "0.3.0": [
        "HTTP guest agent — host→guest commands now ride a bearer-authed "
        "/exec endpoint inside Windows. PowerShell window flashes on "
        "every command are gone.",
        "New `winpodx check` CLI runs nine health probes (pod / RDP / "
        "agent / round-trip / in-guest summary / OEM / disk / …) with "
        "OK / WARN / FAIL / SKIP and per-probe duration. `--json` for "
        "scripting. The GUI Info page mirrors it as a live Health card.",
        "Sidebar transport indicators — small `A` (agent) and `R` (RDP) "
        "dots beside the pod chip turn green/red live so you can see at "
        "a glance which channel the next launch will use.",
        "Modular core split: pod lifecycle, password rotation, "
        "discovery, and host→guest RPC are now separate packages "
        "behind a Transport ABC, with the self-heal-apply loop (the "
        "source of the previous PS-window storm) deleted entirely.",
        "Container recreate required for the in-guest agent to land — "
        "old pods keep working via the FreeRDP fallback channel.",
    ],
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
        print(
            tr("winpodx {version}: fresh install recorded. No migration needed.").format(
                version=current
            )
        )
        return 0

    cur_cmp = _version_tuple(current)[:3]
    inst_cmp = _version_tuple(installed)[:3]

    if inst_cmp >= cur_cmp:
        _write_installed_version(current)
        print(tr("winpodx {version}: already current.").format(version=current))
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
        _ensure_canonical_image_pin(non_interactive)
        _apply_runtime_fixes_to_existing_guest(non_interactive)
        _maybe_auto_migrate_storage(non_interactive)
        return 0

    print(
        tr("winpodx: {installed} -> {current} detected\n").format(
            installed=installed, current=current
        )
    )
    _print_whats_new(installed, current)

    non_interactive = bool(getattr(args, "non_interactive", False))
    skip_refresh = bool(getattr(args, "no_refresh", False))

    # v0.1.8 -> 0.1.9: the 14 bundled .desktop entries are now stale.
    # Offer to clean them up (only when we're crossing the 0.1.9 boundary
    # so we don't keep prompting forever).
    if inst_cmp < (0, 1, 9) <= cur_cmp:
        _maybe_cleanup_legacy_bundled(non_interactive)

    # Always run the idempotent apply chain on a cross-version upgrade.
    # Originally this was gated on `inst_cmp < (0, 1, 9) <= cur_cmp` to
    # back-port OEM v7+v8 fixes to pre-0.1.9 pods, but the chain has
    # since grown (multi_session auto-activate, vbs_launchers, oem_runtime
    # _fixes) and every helper is idempotent — running on a pod that's
    # already at-or-past the relevant fix produces a marker probe + a
    # no-op return, no side effects. Removing the gate ensures users
    # crossing 0.3.0 -> 0.3.x or any future version get newly-added
    # fixes on the existing guest without having to recreate the
    # container.
    _ensure_canonical_image_pin(non_interactive)
    _apply_runtime_fixes_to_existing_guest(non_interactive)
    _maybe_auto_migrate_storage(non_interactive)

    if skip_refresh:
        print(tr("\nSkipping app discovery (--no-refresh)."))
    elif non_interactive:
        print(tr("\nSkipping app discovery (--non-interactive)."))
    elif _prompt_yes(tr("\nRun app discovery now? (scans Windows pod for installed apps)")):
        _attempt_refresh()

    _write_installed_version(current)
    print(tr("\nMigration complete. Marker updated to {version}.").format(version=current))
    print(tr("Re-run this wizard any time with: winpodx migrate"))
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
        tr(
            "\nFound {count} legacy bundled-app entries from a previous winpodx version"
            " (these were removed in 0.1.9):"
        ).format(count=len(stale_desktop))
    )
    for d in stale_desktop:
        print(f"  - {d.name}")

    if non_interactive:
        print(
            tr(
                "  (--non-interactive set — skipping cleanup. "
                "Re-run `winpodx migrate` interactively to remove.)"
            )
        )
        return

    if not _prompt_yes(tr("Remove them now?"), default=True):
        print(tr("  Skipped — entries left in place."))
        return

    icon_root = icons_dir()
    removed = 0
    for desktop in stale_desktop:
        try:
            desktop.unlink()
            removed += 1
        except OSError as e:
            print(tr("  warning: could not remove {path}: {error}").format(path=desktop, error=e))
            continue
        # Best-effort matching icon cleanup (won't error if absent).
        for ext_dir in ("scalable/apps", "32x32/apps"):
            for ext in ("svg", "png"):
                icon_file = icon_root / ext_dir / f"{desktop.stem}.{ext}"
                try:
                    icon_file.unlink()
                except OSError:
                    pass
    print(
        tr("  Removed {removed} of {total} legacy entries.").format(
            removed=removed, total=len(stale_desktop)
        )
    )


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

    Extracts leading digits from each dot-segment so pre-release
    suffixes (``0.1.8rc1``, ``0.3.0-RTM1``, ``0.3.0+dev``) parse to a
    full N-tuple that compares correctly against shipped tuples.
    Without this, ``0.3.0-RTM1`` split on '.' would yield
    ``['0', '3', '0-RTM1']``; the old loop hit ``int('0-RTM1')`` which
    raised, broke out, and returned ``(0, 3)`` — a 2-tuple that lex-
    compared < every ``(0, 3, 0)`` shipped, dropping migrate's
    apply-fixes step on the floor for every RTM-suffixed install.
    """
    out: list[int] = []
    for segment in v.strip().split("."):
        m = re.match(r"^(\d+)", segment)
        if not m:
            break
        out.append(int(m.group(1)))
        if not segment.isdigit():
            # Mixed segment (leading digits + pre-release tag) —
            # extracted the digits, now stop. The remaining tag is a
            # pre-release marker that doesn't carry comparable order
            # for our [:3] comparison purposes.
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
        print(tr("(No user-facing release notes for the versions between installed and current.)"))
        return

    for _, ver, notes in relevant:
        print(tr("What's new in {ver}:").format(ver=ver))
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
        from winpodx.core.transport import TransportError, dispatch
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

    # v0.4.0 (post-rc1): install.sh exports WINPODX_REQUIRE_AGENT=1 while
    # install.bat may still be running in the autologon User session. In that
    # window this probe must NEVER open a FreeRDP session: a single RDP login
    # can kick the autologon session before rdprrap is active, killing
    # install.bat mid-stage. So in require-agent mode we either probe through
    # AgentTransport directly or defer; do not call wait_for_windows_responsive
    # (it can return True with only RDP up) and do not use dispatch() (it can
    # fall back to FreeRDP).
    import os

    if os.environ.get("WINPODX_REQUIRE_AGENT") == "1":
        from winpodx.core.transport.agent import AgentTransport

        print("\nProbing Windows-side authentication...")
        agent = AgentTransport(cfg)
        status = agent.health()
        if not status.available:
            print(
                "  (probe deferred — agent /health not up yet, "
                "skipping FreeRDP fallback to avoid kicking install.bat's "
                "autologon session; password drift will be re-checked on "
                "the next `winpodx migrate` once the agent comes up)"
            )
            return
        try:
            agent.exec(
                "Write-Output 'sync-check'",
                timeout=60,
                description="probe-password-sync",
            )
        except TransportError as e:
            print(f"  (probe inconclusive: {e})")
            return
        print("  Password sync OK.")
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
    if not wait_for_windows_responsive(cfg, timeout=600):
        print("  (probe deferred — guest still booting; will retry on next ensure_ready)")
        return

    # Prefer the v0.3.0 agent transport — its /exec runs the script via
    # CreateNoWindow=$true so no PowerShell window flashes for the user.
    # FreeRDP RemoteApp stays as fallback, but the user-visible flash on
    # GUI startup that 0.3.0 RTM users reported on 2026-05-01 was this
    # probe firing through the legacy channel during pending.resume().
    def _probe_once() -> None:
        try:
            transport = dispatch(cfg)
        except Exception:  # noqa: BLE001 — degrade silently to FreeRDP
            transport = None
        if transport is not None and transport.name == "agent":
            try:
                transport.exec(
                    "Write-Output 'sync-check'",
                    timeout=60,
                    description="probe-password-sync",
                )
            except TransportError as exc:
                raise WindowsExecError(str(exc)) from exc
            return
        # FreeRDP handshake + RemoteApp launch + tsclient redirection
        # negotiation can take 30+ seconds on first contact after a cold
        # pod start. 20s was too aggressive and surfaced as
        # "(probe inconclusive: ... timed out after 20s)".
        run_in_windows(
            cfg,
            "Write-Output 'sync-check'",
            description="probe-password-sync",
            timeout=60,
        )

    try:
        _probe_once()
        return
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


def _ensure_canonical_image_pin(non_interactive: bool) -> None:
    """Align the existing pod's image with what a fresh install would
    write — i.e., the packaged ``DOCKUR_IMAGE_PIN``.

    Pre-this-step, ``cfg.pod.image`` was persisted at first setup
    (``ghcr.io/dockur/windows:latest`` for installs ≤ v0.3.0, since
    PR #62 the docker.io variant), and ``install.sh --main`` left it
    untouched on subsequent upgrades. With ``:latest``, every
    ``podman-compose up`` re-resolved the tag against whatever dockur
    had pushed since — when the resolved digest changed (frequently,
    since dockur's release cadence is daily-ish), podman-compose
    treated the spec as different and *recreated the container*. The
    user's session reported on 2026-05-02 caught dockur mid-bug
    (proc.sh line 137 substring failure) on a fresh ``:latest`` push,
    surfacing as a multi-minute "fresh install" cycle from a working
    pod that had nothing wrong with it.

    Migration semantics: ``winpodx migrate`` should leave the pod in
    the state a *fresh main install would produce*. That means
    ``cfg.pod.image == DOCKUR_IMAGE_PIN``. We rewrite both the
    persisted config and ``compose.yaml`` so the next ``pod start``
    sees the canonical pin.

    Cost: one container recreate on the next ``pod start``. The
    storage volume (``winpodx_winpodx-data``) persists across
    recreates, so dockur's first-boot install marker still exists in
    the volume — no Sysprep, no ISO redownload, ~30 s downtime.
    Idempotent: re-running migrate on an already-pinned config is a
    no-op (string equality check returns False before any rewrite).
    """
    from winpodx.core.config import DOCKUR_IMAGE_PIN, Config

    cfg = Config.load()
    if cfg.pod.image == DOCKUR_IMAGE_PIN:
        return  # already aligned with what a fresh install would write
    if cfg.pod.backend not in ("podman", "docker"):
        return  # libvirt / manual backends don't use the dockur image

    print("\nAligning container image with this winpodx version...")
    print(f"  was: {cfg.pod.image}")
    print(f"  now: {DOCKUR_IMAGE_PIN}")
    cfg.pod.image = DOCKUR_IMAGE_PIN
    cfg.save()

    try:
        from winpodx.core.compose import generate_compose

        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"  warning: compose.yaml regenerate failed ({e})")
        return

    print(
        "  cfg.pod.image + compose.yaml updated.\n"
        "  Next `winpodx pod start` will recreate the container so the new\n"
        "  image pin takes effect (~30 s, storage volume preserved — no ISO\n"
        "  redownload, no Sysprep). Future dockur :latest pushes won't\n"
        "  trigger automatic recreates."
    )


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

    # 0.6.0 item B: delegate the wait-ready → agent-gate → apply-fixes →
    # discovery chain to the single source of truth
    # ``core.provisioner.finish_provisioning``. The migrate-specific gating
    # the old inline code carried is preserved via parameters:
    #
    #   * require_agent=True — the hard agent gate. Every apply helper goes
    #     through dispatch(cfg), which prefers AgentTransport (HTTP to
    #     127.0.0.1:8765) and silently falls back to FreerdpTransport when
    #     /health doesn't answer. On a freshly-booted pod rdprrap multi-
    #     session is NOT yet active (install.bat is still mid-script cycling
    #     TermService), so a new FreeRDP login would KICK the autologon
    #     session install.bat runs in and kill the install mid-stage
    #     (kernalix7's 2026-05-02..04 smoke failures). require_agent=True
    #     makes finish_provisioning raise ProvisionAgentUnavailable rather
    #     than race FreeRDP; we catch it and skip with the same "deferred
    #     until next launch" guidance the old gate printed. The apply chain
    #     is purely additive on a fresh install (OEM v22+ already applied
    #     everything), so skipping is strictly safer than racing.
    #
    #   * with_discovery=True, retries=3 — migrate now folds discovery into
    #     the chain (previously a separate interactive prompt). retries=3
    #     rather than install.sh's 6× because migrate runs against an
    #     already-settled agent more often than not.
    #
    #   * with_reverse_open=False — migrate never set up reverse-open; that
    #     stays install.sh / setup_cmd's job.
    from winpodx.core.provisioner import (
        ProvisionAgentUnavailable,
        finish_provisioning,
    )

    print("  Waiting for Windows guest to finish booting + applying fixes...")
    try:
        results = finish_provisioning(
            cfg,
            wait_timeout=600,
            require_agent=True,
            with_reverse_open=False,
            with_discovery=True,
            retries=3,
        )
    except ProvisionAgentUnavailable:
        print(
            "  Agent not yet up (still on FreeRDP-only channel).\n"
            "  Skipping runtime apply to avoid kicking the autologon\n"
            "  RDP session that install.bat runs in. The OEM bundle on\n"
            "  freshly-created pods already applies the same registry /\n"
            "  service state at first boot, so this is a no-op for fresh\n"
            "  installs. For an existing pod that needs apply-chain to\n"
            "  catch up, run `winpodx pod apply-fixes` once the agent is\n"
            "  up (typically 1-2 minutes after first boot completes)."
        )
        return

    if results.get("wait_ready") == "timeout":
        print(
            "  Windows guest still booting — skipping runtime apply.\n"
            "  Run `winpodx pod apply-fixes` once `winpodx pod status` reports "
            "the pod is fully up, or just launch any app and the apply will "
            "fire automatically."
        )
        return

    apply_results = results.get("apply_fixes", {})
    failures: list[str] = [
        f"{name}: {status}" for name, status in apply_results.items() if status != "ok"
    ]

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

    # Push the refreshed guest *scripts* (agent.ps1, rdprrap, shim, urlacl)
    # when the host has been upgraded past the guest's version stamp. The
    # apply chain above only re-runs the idempotent registry/runtime fixes;
    # it does NOT redeliver /oem. Triggering the autosync here makes a single
    # `winpodx migrate` the complete migration for package / AppImage / flatpak
    # users (whose upgrade path never runs install.sh): host version stamp +
    # storage + registry fixes + guest scripts. No-op when the guest already
    # matches the host (stamp equal) or guest_autosync is off; the same sync
    # also fires on the next `pod start`, so this is best-effort.
    try:
        from winpodx.core.guest_sync import maybe_autosync

        if maybe_autosync(cfg):
            print("  Guest scripts synced to the upgraded host (agent restarted).")
    except Exception as e:  # noqa: BLE001
        print(f"  note: guest script sync deferred to next pod start ({e}).")


def _maybe_auto_migrate_storage(non_interactive: bool) -> None:
    """Auto-migrate `winpodx-data` named volume to a NoCoW bind mount on btrfs.

    Hook: runs at the tail of `winpodx migrate`, after the apply chain
    has settled the existing guest. Conditions for triggering:

    - cfg.pod.backend in {podman, docker}
    - cfg.pod.storage_path is empty (= legacy named-volume mode, hasn't
      been migrated yet)
    - the `winpodx-data` named volume actually exists
    - its mountpoint is on btrfs

    When all four hold, we run the same migration path as
    `winpodx setup --migrate-storage` (stop pod → chattr +C empty
    target → rsync → persist storage_path → remove old volume →
    restart). Interactive runs prompt for confirmation (default Yes);
    non-interactive runs (install.sh post-upgrade) auto-execute, since
    the user is already on btrfs in a degraded state and the whole
    point of running migrate is to apply fixes.

    Best-effort: any failure leaves the named volume intact (the
    migration code preserves the source until copy succeeds), so
    interrupted migrations don't lose Windows.
    """
    try:
        from winpodx.core.config import Config
        from winpodx.core.storage_migration import (
            default_target_path,
            execute_migration,
            get_volume_mountpoint,
            named_volume_exists,
            plan_migration,
        )
        from winpodx.utils.btrfs import detect_path_fs
    except ImportError:
        return  # storage_migration not available in this build

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        return
    if cfg.pod.storage_path:
        return  # already on bind mount

    # Defence-in-depth: refuse to auto-migrate when the default bind-mount
    # target already has data. kernalix7 hit a confusing reproducer on
    # 2026-05-06 where install.sh ran twice in close succession and the
    # second run found the volume `winpodx_winpodx-data` recreated by
    # `podman-compose up` while `cfg.pod.storage_path` had reverted to
    # empty (still investigating root cause — could be a stale install
    # dir loading the OLD `PodConfig` dataclass that lacked the field).
    # The auto-migration then ran a SECOND rsync of 64 GiB into the
    # already-populated bind mount, silently overwriting the first
    # migration's `data.img` (which had `chattr +C` applied via the
    # manual recovery cp) with a fresh CoW-fragmented copy. Whatever
    # cleared `storage_path`, the bind-mount data on disk is the
    # source of truth: if `~/.local/share/winpodx/storage` is non-empty,
    # the migration has already run at least once and re-running would
    # be destructive. We bail out and instruct the user to either
    # restore `storage_path` in `winpodx.toml` or move the populated
    # directory aside before retrying.
    target = default_target_path()
    if target.exists() and any(target.iterdir()):
        print()
        print(
            f"NOTE: skipping auto storage-migration — target {target} is not empty.\n"
            f"  This usually means the migration already completed on a previous\n"
            f"  install but cfg.pod.storage_path is empty in winpodx.toml. To use\n"
            f"  the existing bind mount, set:\n"
            f'    storage_path = "{target}"\n'
            f"  in [pod] of ~/.config/winpodx/winpodx.toml. Or, to start fresh,\n"
            f"  move {target} aside and re-run `winpodx setup --migrate-storage`."
        )
        return

    if not named_volume_exists(cfg.pod.backend):
        return
    mp = get_volume_mountpoint(cfg.pod.backend)
    if mp is None:
        return
    if detect_path_fs(mp) != "btrfs":
        return  # not on btrfs, no benefit

    print()
    print(tr("Detected: legacy 'winpodx-data' volume on btrfs."))
    print(tr("  btrfs Copy-on-Write fragments the Windows raw disk image and"))
    print(tr("  slows pod recreates. Auto-migrating to a per-user bind mount"))
    print(tr("  with NoCoW so the Windows install becomes 5-10× faster to boot."))

    plan_or_err = plan_migration(cfg)
    if isinstance(plan_or_err, str):
        print(tr("  Skipping migration: {detail}").format(detail=plan_or_err))
        print(tr("  Manual retry: winpodx setup --migrate-storage"))
        return

    plan = plan_or_err
    size_gib = plan.source_size_bytes // (1 << 30)
    print(
        f"  Source: {plan.source_mountpoint} ({size_gib} GiB)\n"
        f"  Target: {plan.target_path}\n"
        f"  Cost:   one-time rsync, ~5-10 min on NVMe; pod stopped during copy."
    )

    if not non_interactive:
        if not _prompt_yes(tr("\n  Migrate now?"), default=True):
            print(tr("  Skipped — re-run later with: winpodx setup --migrate-storage"))
            return
    else:
        print(tr("  Running automatically (non-interactive mode)..."))

    print(tr("\nMigrating storage..."))
    result = execute_migration(cfg, plan, start_pod=True)
    if result.status == "ok":
        print(tr("  OK: {detail}").format(detail=result.detail))
        print(tr("  Future pod recreates will be NoCoW (5-10× faster on btrfs)."))
    else:
        print(tr("  FAIL: {detail}").format(detail=result.detail))
        print(tr("  The original named volume is preserved; you can retry with:"))
        print(tr("    winpodx setup --migrate-storage"))


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
        print(tr("\n  Discovery unavailable in this build. Skipping."))
        return

    if not _pod_is_running(cfg):
        if not _prompt_yes(
            tr("  Pod is not running. Start it first? (first boot can take ~1 minute)")
        ):
            print(
                tr("\n  Skipping refresh. Later: `winpodx pod start --wait && winpodx app refresh`")
            )
            return
        try:
            from winpodx.core.provisioner import ProvisionError, ensure_ready

            print(tr("\n  Starting pod..."))
            ensure_ready(cfg)
        except ProvisionError as exc:
            print(tr("\n  Could not start pod: {error}").format(error=exc))
            print(tr("  Run `winpodx pod start --wait` manually and try again."))
            return
        except Exception as exc:  # noqa: BLE001 — surface any startup failure
            print(tr("\n  Could not start pod: {error}").format(error=exc))
            return

    # v0.2.0.3: discovery hits the same FreeRDP RemoteApp channel as the
    # apply path, so it suffers the same race when Windows VM inside QEMU
    # is still booting. Wait until the guest is responsive (or skip with
    # a useful message) before running the scan; otherwise the user just
    # sees rc=147 connection-reset right after a fresh install.
    from winpodx.core.provisioner import wait_for_windows_responsive

    print(tr("\n  Waiting for Windows guest to be ready (up to 180s)..."))
    if not wait_for_windows_responsive(cfg, timeout=600):
        print(
            tr(
                "  Windows guest still booting — skipping discovery for now.\n"
                "  Re-run later with: winpodx app refresh"
            )
        )
        return

    # v0.4.0 (post-rc1): retry up to 3 times with 10s spacing.
    # `_apply_vbs_launchers` (which runs as part of the apply chain just
    # before this prompt) ends by spawning agent-respawn.ps1 detached.
    # The respawn waits ~3s, kills the running agent, then starts a new
    # one. If the user accepts the discovery prompt fast enough, the
    # /exec hits the agent during that kill-then-restart window and gets
    # "Remote end closed connection without response". Three attempts at
    # 10s spacing covers the respawn cycle plus any HKCU\Run-driven
    # restart race; final failure still prints the retry-later hint so
    # the user knows pending-resume isn't silently kicking in here.
    print(tr("\n  Scanning Windows pod for installed apps..."))
    last_exc: DiscoveryError | None = None
    for attempt in (1, 2, 3, 4, 5, 6):
        try:
            apps = discover_apps(cfg)
            written = persist_discovered(apps)
            print(
                tr("  Discovered {count} app(s); wrote {written} profile(s).").format(
                    count=len(apps), written=len(written)
                )
            )
            return
        except DiscoveryError as exc:
            last_exc = exc
            if attempt < 6:
                print(
                    tr(
                        "  attempt {attempt} deferred ({error}); retrying in 10s"
                        " (agent may be respawning)..."
                    ).format(attempt=attempt, error=exc)
                )
                import time as _time

                _time.sleep(10)
    print(tr("\n  Discovery failed: {error}").format(error=last_exc))
    print(tr("  Retry later with: winpodx app refresh"))


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
