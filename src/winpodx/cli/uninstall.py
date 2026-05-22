# SPDX-License-Identifier: MIT
"""Consolidated ``winpodx uninstall`` -- apt-style preview + cleanup (#255 PR 3).

Replaces the pre-#255 ``_cmd_uninstall`` that only swept user data dirs.
Now reaches parity with ``uninstall.sh`` (process kills, container stop,
listener teardown, autostart removal, install-dir cleanup for curl
installs) and, when run on a package-installed system, prompts the user
to also remove the system package via sudo.

Behavioural split mirrors ``apt remove`` vs ``apt purge``:

* ``winpodx uninstall``         -- stop container (keep disk + volume),
                                    keep config, kill running winpodx
                                    processes + listener, scrub UI
                                    surface (desktops / icons / data /
                                    autostart / curl install dir).
* ``winpodx uninstall --purge`` -- everything above PLUS container rm,
                                    podman volume rm, storage bind-mount
                                    contents rm, config dir rm.

Both modes detect install source via :mod:`winpodx.utils.install_source`
and offer to run the matching ``sudo apt remove`` / ``sudo dnf remove`` /
``sudo pacman -Rns`` at the end. Packaging postrm hooks pass
``--no-package-prompt`` to suppress that step (the package is already
being removed by the time the hook fires).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from winpodx.utils.install_source import InstallSource


@dataclass
class _Step:
    name: str
    description: str
    runner: object  # Callable -> int (count of items removed)


def handle_uninstall(args: argparse.Namespace) -> None:
    """Top-level uninstall entry point."""
    from winpodx.utils.install_source import detect

    purge = bool(args.purge)
    yes = bool(args.yes)
    no_package_prompt = bool(args.no_package_prompt)

    install_source = detect()

    preview = _build_preview(purge=purge, install_source=install_source)
    print(preview)

    if not yes:
        try:
            answer = input("\nContinue? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(2)

    print()
    removed = _run_cleanup_steps(purge=purge)
    print(f"\nUninstall complete ({removed} items removed).")

    if no_package_prompt:
        return

    if install_source.kind in ("apt", "dnf", "zypper", "pacman"):
        _offer_package_removal(install_source)
    elif install_source.kind == "curl":
        print(
            "\nCurl-installed binary already removed (~/.local/bin/winpodx-app/)."
            if purge
            else "\nCurl-installed binary at ~/.local/bin/winpodx-app/ kept "
            "(re-run with --purge to also remove it)."
        )
    elif install_source.kind == "source":
        print("\nSource / pip install detected. Remove the package with:")
        print(f"  {install_source.removal_command}")
    else:
        # unknown: stay silent rather than guess wrong.
        pass


# -----------------------------------------------------------------------
# Preview generation.
# -----------------------------------------------------------------------


def _build_preview(*, purge: bool, install_source: InstallSource) -> str:
    """Render the apt-style preview block shown before confirm."""
    lines = ["The following will be removed:", ""]
    lines.append("  [user-state]")
    lines.append("    * Desktop entries (~/.local/share/applications/winpodx-*.desktop)")
    lines.append("    * Icons (winpodx-* in ~/.local/share/icons/)")
    lines.append("    * Data dir (~/.local/share/winpodx/)")
    lines.append("    * Runtime dir ($XDG_RUNTIME_DIR/winpodx/)")
    lines.append("    * Autostart entry (~/.config/autostart/winpodx-tray.desktop)")

    lines.append("")
    lines.append("  [running processes]")
    lines.append("    * Tray + GUI (pkill 'python.*winpodx', 'winpodx-app')")
    lines.append("    * Reverse-open listener (winpodx host-open stop-listener)")

    lines.append("")
    lines.append("  [container]")
    if purge:
        lines.append("    * Container winpodx-windows -- stop AND remove")
        lines.append("    * Podman volume winpodx-data -- remove (~50 GB Windows disk image)")
        lines.append("    * Storage bind-mount contents -- wipe")
    else:
        lines.append("    * Container winpodx-windows -- stop (keep disk for reinstall)")
        lines.append("    * Podman volume -- KEEP")

    if install_source.kind == "curl":
        lines.append("")
        lines.append("  [install dir]")
        lines.append("    * ~/.local/bin/winpodx-app/ (curl-installed bundle)")

    lines.append("")
    lines.append("  [config]")
    if purge:
        lines.append("    * ~/.config/winpodx/ (winpodx.toml + compose.yaml + OEM cache)")
    else:
        lines.append("    * ~/.config/winpodx/ -- KEEP (reinstall picks up where you left off)")

    if install_source.kind in ("apt", "dnf", "zypper", "pacman"):
        lines.append("")
        lines.append("  [system package]")
        lines.append(f"    * {install_source.package_name or 'winpodx'} -- will prompt for sudo")

    return "\n".join(lines)


# -----------------------------------------------------------------------
# Cleanup steps.
# -----------------------------------------------------------------------


def _run_cleanup_steps(*, purge: bool) -> int:
    """Execute all cleanup steps in order. Returns total items removed."""
    removed = 0

    removed += _kill_winpodx_processes()
    removed += _stop_reverse_open_listener()
    removed += _stop_container(remove=purge)
    if purge:
        removed += _remove_podman_volume()
        removed += _wipe_storage_path()
    removed += _remove_desktop_entries()
    removed += _remove_icons()
    removed += _remove_data_dir()
    removed += _remove_runtime_dir()
    removed += _remove_autostart_entry()
    removed += _remove_curl_install_dir()
    if purge:
        removed += _remove_config_dir()

    return removed


def _kill_winpodx_processes() -> int:
    """pkill the tray + GUI + helper processes. Counts processes killed
    (best-effort -- pkill returns 0 even when no matches in some shells,
    so we return 1 per pattern that found a match)."""
    count = 0
    for pattern in ("python.*winpodx", "winpodx-app"):
        try:
            result = subprocess.run(
                ["pkill", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        # pkill returns 0 when at least one process was killed.
        if result.returncode == 0:
            count += 1
    if count:
        print(f"  Killed {count} winpodx process pattern(s)")
    return count


def _stop_reverse_open_listener() -> int:
    """Ask the reverse-open daemon to stop. No-op if it isn't running."""
    winpodx_bin = shutil.which("winpodx") or str(Path.home() / ".local" / "bin" / "winpodx")
    if not Path(winpodx_bin).exists():
        return 0
    try:
        result = subprocess.run(
            [winpodx_bin, "host-open", "stop-listener"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    if result.returncode == 0 and "stopped" in (result.stdout + result.stderr).lower():
        print("  Stopped reverse-open listener")
        return 1
    return 0


def _stop_container(*, remove: bool) -> int:
    """Stop (and optionally rm) the dockur container."""
    from winpodx.core.config import Config

    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return 0
    if cfg.pod.backend not in ("podman", "docker"):
        return 0

    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return 0
    container_name = cfg.pod.container_name

    # Check container exists at all.
    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    if container_name not in result.stdout.split():
        return 0

    count = 0
    try:
        subprocess.run(
            [runtime, "stop", container_name], capture_output=True, timeout=30, check=False
        )
        print(f"  Stopped container {container_name}")
        count += 1
        if remove:
            subprocess.run(
                [runtime, "rm", container_name], capture_output=True, timeout=10, check=False
            )
            print(f"  Removed container {container_name}")
            count += 1
    except subprocess.TimeoutExpired:
        print(f"  WARNING: container {container_name} stop/rm timed out")
    return count


def _remove_podman_volume() -> int:
    """Remove the winpodx-data named volume (purge only)."""
    from winpodx.core.config import Config

    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return 0
    if cfg.pod.backend not in ("podman", "docker"):
        return 0
    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return 0
    try:
        result = subprocess.run(
            [runtime, "volume", "rm", "-f", "winpodx-data"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    if result.returncode == 0:
        print("  Removed podman volume winpodx-data")
        return 1
    return 0


def _wipe_storage_path() -> int:
    """Empty the storage bind-mount directory (purge only)."""
    from winpodx.core.config import Config

    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return 0
    raw = (cfg.pod.storage_path or "").strip()
    if not raw:
        return 0
    bind_path = Path(raw).expanduser()
    if not bind_path.is_dir():
        return 0
    count = 0
    for item in bind_path.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            count += 1
        except OSError:
            pass
    if count:
        print(f"  Wiped {count} item(s) under storage path {bind_path}")
    return count


def _remove_desktop_entries() -> int:
    from winpodx.utils.paths import applications_dir

    app_dir = applications_dir()
    if not app_dir.exists():
        return 0
    entries = list(app_dir.glob("winpodx-*.desktop"))
    for f in entries:
        try:
            f.unlink()
        except OSError:
            pass
    if entries:
        print(f"  Removed {len(entries)} desktop entries")
    return len(entries)


def _remove_icons() -> int:
    from winpodx.utils.paths import icons_dir

    base = icons_dir()
    if not base.exists():
        return 0
    count = 0
    for icon in base.rglob("winpodx-*"):
        try:
            icon.unlink()
            count += 1
        except OSError:
            pass
    if count:
        print(f"  Removed {count} icons")
    return count


def _remove_data_dir() -> int:
    from winpodx.utils.paths import data_dir

    dd = data_dir()
    if not dd.exists():
        return 0
    try:
        shutil.rmtree(dd)
    except OSError:
        return 0
    print(f"  Removed {dd}")
    return 1


def _remove_runtime_dir() -> int:
    from winpodx.utils.paths import runtime_dir

    rd = runtime_dir()
    if not rd.exists():
        return 0
    try:
        shutil.rmtree(rd)
    except OSError:
        return 0
    print(f"  Removed {rd}")
    return 1


def _remove_autostart_entry() -> int:
    from winpodx.utils.paths import config_dir

    autostart = config_dir().parent / "autostart" / "winpodx-tray.desktop"
    if not autostart.exists():
        return 0
    try:
        autostart.unlink()
    except OSError:
        return 0
    print(f"  Removed autostart entry {autostart}")
    return 1


def _remove_curl_install_dir() -> int:
    """Clean curl-install artifacts: bundle dir + launcher symlinks.

    The bundle dir and the launcher symlinks are removed independently
    -- previously the symlink cleanup was gated on the bundle dir
    removal succeeding, so a botched earlier uninstall that wiped only
    the dir would leave ``~/.local/bin/winpodx`` +
    ``~/.local/bin/winpodx-run`` pointing at nothing forever (and
    ``winpodx`` still on PATH, calling into a python wrapper that
    can't find the bundle).
    """
    count = 0
    install_dir = Path.home() / ".local" / "bin" / "winpodx-app"
    if install_dir.is_dir():
        try:
            shutil.rmtree(install_dir)
            print(f"  Removed curl install dir {install_dir}")
            count += 1
        except OSError as e:
            print(f"  WARNING: could not remove {install_dir}: {e}")

    # Launcher symlinks + wrapper script -- clean independently of the
    # bundle dir. install.sh creates these even if a prior uninstall
    # already wiped the bundle dir.
    for name in ("winpodx", "winpodx-run"):
        link = Path.home() / ".local" / "bin" / name
        if link.is_symlink() or link.is_file():
            try:
                link.unlink()
                print(f"  Removed launcher {link}")
                count += 1
            except OSError as e:
                print(f"  WARNING: could not remove {link}: {e}")
    return count


def _remove_config_dir() -> int:
    from winpodx.utils.paths import config_dir

    cd = config_dir()
    if not cd.exists():
        return 0
    try:
        shutil.rmtree(cd)
    except OSError:
        return 0
    print(f"  Removed {cd}")
    return 1


# -----------------------------------------------------------------------
# Package-manager removal prompt.
# -----------------------------------------------------------------------


def _offer_package_removal(install_source: InstallSource) -> None:
    """Prompt user to run the matching sudo apt/dnf/pacman remove."""
    cmd = install_source.removal_command
    if not cmd:
        return
    print("\nAlso remove the system package via sudo? [y/N]:")
    print(f"  Will run: {cmd}")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        print(f"Skipped. Run manually with: {cmd}")
        return
    try:
        subprocess.run(cmd, shell=True, check=False)
    except Exception as e:  # noqa: BLE001
        print(f"sudo exec failed: {e}")
