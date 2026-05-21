# SPDX-License-Identifier: MIT
"""Interactive setup wizard, no external dependencies."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from winpodx.core.compose import (
    _build_compose_content,
    _build_compose_template,
    _find_oem_dir,
    _yaml_escape,
)
from winpodx.core.compose import generate_compose as _generate_compose
from winpodx.core.compose import generate_compose_to as _generate_compose_to
from winpodx.core.compose import generate_password as _generate_password
from winpodx.core.config import Config
from winpodx.utils.agent_token import ensure_agent_token, stage_token_to_oem
from winpodx.utils.compat import import_winapps_config
from winpodx.utils.deps import check_all
from winpodx.utils.paths import config_dir

COMPOSE_TIMEOUT_DEFAULT_SECS = 1800
COMPOSE_TIMEOUT_ENV_VAR = "WINPODX_COMPOSE_TIMEOUT_SECS"

__all__ = [
    "_build_compose_content",
    "_build_compose_template",
    "_container_exists_on_backend",
    "_ensure_oem_token_staged",
    "_find_oem_dir",
    "_generate_compose",
    "_generate_compose_to",
    "_generate_password",
    "_heal_missing_container_if_needed",
    "_resolve_credentials",
    "_yaml_escape",
    "handle_rotate_password",
    "handle_setup",
]


def _compose_timeout_secs() -> int | None:
    """Return compose command timeout seconds, or None for no timeout."""
    raw_timeout = os.environ.get(COMPOSE_TIMEOUT_ENV_VAR)
    if raw_timeout is None:
        timeout = COMPOSE_TIMEOUT_DEFAULT_SECS
    else:
        try:
            timeout = int(raw_timeout)
        except ValueError:
            timeout = COMPOSE_TIMEOUT_DEFAULT_SECS

    if timeout < 0:
        timeout = COMPOSE_TIMEOUT_DEFAULT_SECS
    if timeout == 0:
        return None
    return timeout


def _ensure_oem_token_staged() -> None:
    """Generate the host token and stage it into the OEM bind mount.

    Both the host-side ``~/.config/winpodx/agent_token.txt`` and the
    container-side ``<oem_dir>/agent_token.txt`` are written with mode
    0600. dockur copies ``/oem/*`` into ``C:\\OEM\\`` at first boot;
    ``agent.ps1`` then reads ``C:\\OEM\\agent_token.txt`` to bind its
    listener with bearer auth.

    Errors during OEM staging are non-fatal: the host token is still
    generated, and a warning is printed so the user can investigate.
    """
    ensure_agent_token()
    try:
        oem_dir = Path(_find_oem_dir())
        if oem_dir.exists():
            stage_token_to_oem(oem_dir)
    except OSError as e:
        print(f"  warning: could not stage agent token to OEM dir ({e})")


def _ask(prompt: str, default: str = "") -> str:
    """Prompt for input, returning default on EOF for non-TTY environments."""
    try:
        return input(prompt).strip() or default
    except EOFError:
        return default


def _update_image_pin() -> int:
    """Pull docker.io/dockurr/windows:latest, resolve its digest, pin
    cfg.pod.image to it, and regenerate compose.yaml. Returns the
    process exit code (0 on success, non-zero on failure).

    Cost on next ``pod start``: container recreate (volume preserved,
    ~30 s, no ISO redownload). Idempotent: if the resolved digest
    matches the current pin, prints a no-op message and returns 0.
    """
    import shutil
    import subprocess

    from winpodx.core.config import Config

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(f"--update-image only supports podman/docker (got {cfg.pod.backend!r}).")
        return 2

    backend = cfg.pod.backend
    if not shutil.which(backend):
        print(f"`{backend}` not found in PATH.")
        return 2

    # Pick the upstream tag matching host architecture. aarch64 hosts
    # (Raspberry Pi 5, Ampere, Graviton, …) get the Windows-on-ARM
    # image; everything else gets the x86_64 build. See
    # ``core/config.py:_default_pod_image`` for the matching rule on
    # fresh installs.
    import platform as _platform

    if _platform.machine() == "aarch64":
        upstream_tag = "docker.io/dockurr/windows-arm:latest"
    else:
        upstream_tag = "docker.io/dockurr/windows:latest"

    print(f"Pulling {upstream_tag}...")
    try:
        subprocess.run(
            [backend, "pull", upstream_tag],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"FAIL: pull exit={e.returncode}")
        return 3

    # Resolve the now-local image's repo-digest. RepoDigests is a list
    # of `<repo>@sha256:...` references — we filter to the docker.io
    # one so the pin always uses the canonical registry path.
    print("Resolving image digest...")
    try:
        result = subprocess.run(
            [
                backend,
                "image",
                "inspect",
                upstream_tag,
                "-f",
                "{{json .RepoDigests}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"FAIL: image inspect exit={e.returncode}: {e.stderr.strip()}")
        return 3

    import json

    try:
        digests = json.loads(result.stdout.strip()) or []
    except json.JSONDecodeError:
        digests = []
    pinned = next((d for d in digests if d.startswith("docker.io/")), None)
    if not pinned and digests:
        pinned = digests[0]
    if not pinned or "@sha256:" not in pinned:
        print(f"FAIL: no usable digest in RepoDigests={digests!r}")
        return 3

    if cfg.pod.image == pinned:
        print(f"Image already pinned to {pinned}. Nothing to do.")
        return 0

    print(f"Old pin: {cfg.pod.image}")
    print(f"New pin: {pinned}")
    cfg.pod.image = pinned
    cfg.save()

    try:
        from winpodx.core.compose import generate_compose

        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: compose.yaml regenerate ({e})")
        return 3

    print(
        "OK: cfg.pod.image + compose.yaml updated.\n"
        "Next `winpodx pod start` recreates the container with the new image\n"
        "(~30 s, storage volume preserved — no ISO redownload, no Sysprep)."
    )
    return 0


def _decide_storage_mode(cfg: Config, *, non_interactive: bool) -> None:
    """Resolve ``cfg.pod.storage_path`` for the about-to-be-generated compose.

    See the call site in ``handle_setup`` for the three-case decision.
    Mutates ``cfg`` in place; the caller saves + regenerates compose.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    # Case 1: already set explicitly. Trust the user.
    if cfg.pod.storage_path:
        return

    from winpodx.core.storage_migration import (
        default_target_path,
        get_volume_mountpoint,
        resolve_named_volume,
    )
    from winpodx.utils.btrfs import detect_path_fs, disable_cow_on_path

    # Case 2: returning user with an existing named volume. Don't touch
    # compose mode (stays named-volume); just warn if they're on btrfs.
    # Probe both the compose-prefixed (`winpodx_winpodx-data`) and bare
    # (`winpodx-data`) names so we don't silently misclassify the user
    # as "fresh install" when their compose-managed volume lives under
    # the prefixed name.
    resolved = resolve_named_volume(cfg.pod.backend)
    if resolved is not None:
        mp = get_volume_mountpoint(cfg.pod.backend, resolved)
        if mp is not None and detect_path_fs(mp) == "btrfs":
            print()
            print(f"  Note: existing {resolved!r} volume is on btrfs ({mp}).")
            print("    btrfs Copy-on-Write fragments the Windows raw disk image and")
            print("    slows pod recreates. To migrate to a NoCoW bind mount, run:")
            print("        winpodx setup --migrate-storage")
            print("    (~5-10 min, preserves the Windows install)")
            print()
        return

    # Case 3: fresh install. Pick the default bind-mount path, create the
    # directory, and disable CoW if it's on btrfs BEFORE the volume gets
    # populated by the next compose-up.
    target = default_target_path()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # If we can't create the dir, fall back to named volume (don't
        # set storage_path) so install still proceeds.
        print(f"  Note: could not create {target} ({e}); using named volume instead.")
        return

    fs = detect_path_fs(target)
    if fs == "btrfs":
        status, detail = disable_cow_on_path(target)
        if status == "disabled":
            print(f"  btrfs detected — applied chattr +C on {target}")
            print("    Windows raw disk image will be NoCoW from first boot.")
        elif status == "already_off":
            # silent — already done in a previous setup run
            pass
        elif status == "failed":
            print(f"  Note: btrfs detected but chattr +C failed ({detail}).")
            print("    Pod will work, but VM disk operations may be slow.")
            print("    You can retry manually: chattr +C", target)
    cfg.pod.storage_path = str(target)


def _handle_migrate_storage(args: argparse.Namespace) -> int:
    """Move storage from the legacy ``winpodx-data`` named volume to a host
    bind mount, applying ``chattr +C`` automatically on btrfs.

    Returns the process exit code (0 ok / 2 misconfig / 3 runtime fail).
    See :mod:`winpodx.core.storage_migration` for the full sequence.
    """

    from winpodx.core.storage_migration import (
        MigrationPlan,
        default_target_path,
        execute_migration,
        plan_migration,
    )

    cfg = Config.load()

    # Build pre-flight plan or surface a clear error.
    target_override = getattr(args, "migrate_storage_target", None)
    target = Path(target_override).expanduser() if target_override else default_target_path()

    plan_or_error = plan_migration(cfg, target=target)
    if isinstance(plan_or_error, str):
        print(f"--migrate-storage: {plan_or_error}")
        return 2

    plan: MigrationPlan = plan_or_error

    # User confirmation unless --yes (acked) was passed. Migration moves
    # tens of gigabytes of disk + recreates the pod; we don't want to
    # surprise anyone.
    print()
    print("Storage migration plan:")
    print(f"  Source:  podman volume {plan.source_volume!r}")
    print(f"           {plan.source_mountpoint}  (~{plan.source_size_bytes // (1 << 30)} GiB)")
    print(f"  Target:  {plan.target_path}  (fs={plan.target_fs})")
    if plan.chattr_will_run:
        print("  chattr:  +C will run on the target (btrfs NoCoW for new files)")
    if plan.free_bytes_target >= 0:
        print(f"  Free:    {plan.free_bytes_target // (1 << 30)} GiB available at target")
    print()
    print("Cost: rsync -aS of the entire volume. ~5-10 min on NVMe; longer on")
    print("spinning rust. The pod is stopped for the duration.")
    print()

    if not getattr(args, "yes", False) and not getattr(args, "non_interactive", False):
        if not _ask("Proceed? (y/N): ").lower().startswith("y"):
            print("Aborted.")
            return 0

    print("\nMigrating...")
    result = execute_migration(cfg, plan, start_pod=True)
    if result.status == "ok":
        print(f"OK: {result.detail}")
        return 0
    print(f"FAIL: {result.detail}")
    return 3


def _container_exists_on_backend(cfg: Config) -> bool:
    """Return True if the container named ``cfg.pod.container_name`` exists.

    Uses ``<backend> ps -a --format '{{.Names}}'`` rather than a richer
    backend probe so the check works even when libpod is wedged. Any
    subprocess error is treated as "unknown" → returns False so the
    caller's recovery path runs (recreating an already-present container
    is idempotent — ensure_ready / start_pod just no-op on a healthy pod).
    """
    import subprocess

    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return False
    try:
        result = subprocess.run(
            [backend, "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return cfg.pod.container_name in names


def _heal_missing_container_if_needed(cfg: Config) -> None:
    """Recreate the container if config exists but container is gone.

    The "half-uninstalled" state: a previous ``uninstall.sh`` (or some
    external cleanup) removed the podman/docker container but kept
    ``winpodx.toml`` + ``compose.yaml``. Without this guard the next
    step in install.sh (``pod wait-ready``) fails with
    ``Error: no such container ...`` and the user has to do a
    ``--purge`` reinstall to recover. ``ensure_ready`` regenerates the
    container from the existing compose.yaml idempotently.
    """
    from winpodx.core.pod import PodState, pod_status

    try:
        state = pod_status(cfg).state
    except Exception as e:  # noqa: BLE001
        print(f"  warning: could not probe pod state: {e}")
        state = None

    if state in (PodState.STOPPED, PodState.ERROR, None) and not _container_exists_on_backend(cfg):
        print(
            f"  Container '{cfg.pod.container_name}' is missing — "
            f"creating it from existing config + compose.yaml."
        )
        try:
            from winpodx.core.provisioner import ensure_ready

            ensure_ready(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"  WARNING: could not start pod: {e}")
            print("  Try a full reinstall: uninstall.sh --purge then install.sh")


def _resolve_credentials(cfg: Config, *, non_interactive: bool, config_existed: bool) -> None:
    """Set cfg.rdp user/password/ip for the current setup run.

    Three branches:
    * `non_interactive=True` — generate fresh credentials. Used by install.sh
      and the no-config path.
    * `config_existed=True` and cfg already carries credentials — preserve
      them. Re-running `winpodx setup` to bump cores/RAM must not silently
      overwrite the working password: dockur's USERNAME/PASSWORD env vars
      only apply on first boot, so a new password in the host config would
      desync from the Windows guest account and lock the user out (#216).
    * fresh interactive install — prompt for user / password / ip.
    """
    from datetime import datetime, timezone

    if non_interactive:
        cfg.rdp.user = "User"
        cfg.rdp.password = _generate_password()
        cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
        cfg.rdp.ip = "127.0.0.1"
        return

    if config_existed and cfg.rdp.user and cfg.rdp.password:
        print(
            f"Existing credentials for user {cfg.rdp.user!r} preserved. "
            "Use `winpodx rotate-password` to change the Windows password."
        )
        return

    cfg.rdp.user = _ask("Windows username [User]: ", default="User")
    import getpass

    try:
        entered_pw = getpass.getpass("Windows password (Enter for random): ")
    except EOFError:
        entered_pw = ""
    cfg.rdp.password = entered_pw or _generate_password()
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
    if cfg.pod.backend == "manual":
        cfg.rdp.ip = _ask("Windows IP address: ")
    else:
        cfg.rdp.ip = _ask("Windows IP [127.0.0.1]: ", default="127.0.0.1")


def handle_setup(args: argparse.Namespace) -> None:
    """Run the setup wizard."""
    import sys

    if getattr(args, "update_image", False):
        sys.exit(_update_image_pin())

    if getattr(args, "migrate_storage", False):
        sys.exit(_handle_migrate_storage(args))

    backend = args.backend
    non_interactive = args.non_interactive

    # Non-TTY stdin (pipe, /dev/null, CI) → force non-interactive mode so that
    # every input() call uses its default without raising EOFError.
    if not non_interactive and not sys.stdin.isatty():
        non_interactive = True

    print("=== winpodx setup ===\n")

    print("Checking dependencies...")
    deps = check_all()
    for name, dep in deps.items():
        status = "OK" if dep.found else "MISSING"
        print(f"  {name:<15} [{status}] {dep.note}")

    if not deps["freerdp"].found:
        print("\nFreeRDP 3+ is required. Install it and try again.")
        raise SystemExit(1)

    kvm_ok = Path("/dev/kvm").exists()
    kvm_status = "OK" if kvm_ok else "MISSING"
    print(f"  {'kvm':<15} [{kvm_status}] Hardware virtualization")
    print()

    existing = import_winapps_config()
    if existing and not non_interactive:
        answer = _ask("Found existing winapps.conf. Import settings? (Y/n): ").lower()
        if answer in ("", "y", "yes"):
            existing.save()
            print(f"Config saved to {Config.path()}")
            return

    config_existed = Config.path().exists()
    if config_existed:
        cfg = Config.load()
        if non_interactive:
            print(f"Existing config found at {Config.path()}, skipping setup.")
            _ensure_oem_token_staged()
            # Half-uninstalled detection. If cfg points at a podman/docker
            # pod but the container is gone (user did `uninstall.sh`
            # without --keep, or some external cleanup), the next step
            # in install.sh's flow (`pod wait-ready`) would fail with
            # "no such container". Trigger ensure_ready now so install.sh
            # has something to wait on. ensure_ready handles the compose
            # regeneration + container creation idempotently.
            if cfg.pod.backend in ("podman", "docker"):
                _heal_missing_container_if_needed(cfg)
            return
    else:
        cfg = Config()

    if backend:
        cfg.pod.backend = backend
    elif non_interactive:
        cfg.pod.backend = "podman" if shutil.which("podman") else "docker"
    else:
        available = []
        if deps.get("podman") and deps["podman"].found:
            available.append("podman")
        if deps.get("docker") and deps["docker"].found:
            available.append("docker")
        if deps.get("virsh") and deps["virsh"].found:
            available.append("libvirt")
        available.append("manual")

        if len(available) == 1 and available[0] == "manual":
            print("No container/VM backends found. Install podman or docker.")

        print(f"Available backends: {', '.join(available)}")
        choice = _ask(f"Select backend [{available[0]}]: ", default=available[0])
        if choice in available:
            cfg.pod.backend = choice
        else:
            print(f"Invalid choice: {choice}")
            raise SystemExit(1)

    # Apply --win-version before the cfg is saved. PodConfig.__post_init__
    # normalises whitespace/case, rejects YAML-breaking characters, and
    # warns when the value is off the curated allowlist (it still passes
    # through to dockur — see #178). Re-run __post_init__ explicitly
    # because direct attribute assignment bypasses dataclass validation.
    win_version_arg = getattr(args, "win_version", None)
    if win_version_arg:
        cfg.pod.win_version = win_version_arg
        cfg.pod.__post_init__()

    _resolve_credentials(cfg, non_interactive=non_interactive, config_existed=config_existed)

    if cfg.pod.backend in ("podman", "docker"):
        # v0.2.1: detect host specs and pick a tier preset (low/mid/high)
        # so defaults match the user's machine instead of always being
        # 4-core / 4 GB. Non-interactive mode applies the recommendation
        # directly; interactive mode shows it as the suggested default.
        from winpodx.utils.specs import detect_host_specs, recommend_tier

        host = detect_host_specs()
        tier = recommend_tier(host)
        if non_interactive:
            cfg.pod.cpu_cores = tier.cpu_cores
            cfg.pod.ram_gb = tier.ram_gb
        else:
            print(
                f"\nHost specs: {host.cpu_threads} CPU threads, "
                f"{host.ram_gb} GB RAM. Recommended tier: {tier.label} "
                f"(VM: {tier.cpu_cores} cores, {tier.ram_gb} GB)."
            )
            cpu_input = _ask(f"CPU cores [{tier.cpu_cores}]: ")
            try:
                cfg.pod.cpu_cores = int(cpu_input) if cpu_input else tier.cpu_cores
            except ValueError:
                print(f"Invalid number, using default: {tier.cpu_cores}")
                cfg.pod.cpu_cores = tier.cpu_cores
            ram_input = _ask(f"RAM (GB) [{tier.ram_gb}]: ")
            try:
                cfg.pod.ram_gb = int(ram_input) if ram_input else tier.ram_gb
            except ValueError:
                print(f"Invalid number, using default: {tier.ram_gb}")
                cfg.pod.ram_gb = tier.ram_gb

        # Pick a storage mode for podman/docker before compose is
        # rendered. Three cases:
        #   1. cfg.pod.storage_path already set → keep it (returning user
        #      who already migrated, or someone setting it by hand).
        #   2. legacy named volume already present → leave storage_path
        #      empty so compose keeps using `winpodx-data`. We surface a
        #      warning if that volume is on btrfs so the user knows
        #      `winpodx setup --migrate-storage` is available.
        #   3. fresh install (no existing volume) → set storage_path to
        #      the per-user default `~/.local/share/winpodx/storage`,
        #      create the directory, and `chattr +C` on btrfs so the
        #      Windows raw disk image inherits NoCoW from day one.
        _decide_storage_mode(cfg, non_interactive=non_interactive)

        _generate_compose(cfg)
        _recreate_container(cfg)

    if cfg.pod.backend == "libvirt" and not non_interactive:
        cfg.pod.vm_name = _ask("VM name [RDPWindows]: ", default="RDPWindows")

    from winpodx.display.scaling import detect_raw_scale, detect_scale_factor

    detected_scale = detect_scale_factor()
    if detected_scale != 100:
        print(f"\nDetected display scale: {detected_scale}%")
        cfg.rdp.scale = detected_scale

    raw = detect_raw_scale()
    detected_dpi = round(raw * 100)
    if detected_dpi > 100:
        print(f"Detected Windows DPI: {detected_dpi}%")
        cfg.rdp.dpi = detected_dpi

    cfg.save()
    print(f"\nConfig saved to {Config.path()}")

    # Surface the auto-detected tuning profile so the user can see what
    # `winpodx setup` decided about their host (#215). The detection is
    # pure /proc reads; safe to call regardless of backend.
    from winpodx.utils.specs import (
        detect_tuning_capability,
        format_tuning_summary,
        recommend_tuning_profile,
    )

    cap = detect_tuning_capability(vm_cpu_cores=cfg.pod.cpu_cores, vm_ram_gb=cfg.pod.ram_gb)
    profile = recommend_tuning_profile(cap, user_pref=cfg.pod.tuning_profile)
    print("\n[Tuning]")
    print(format_tuning_summary(cap, profile))

    _ensure_oem_token_staged()

    # v0.2.0.2: stamp installed_version.txt so a follow-up `winpodx migrate`
    # doesn't misclassify this fresh install as a "pre-tracker upgrade from
    # 0.1.7". Migrate's pre-tracker fallback (config exists but no marker
    # → assume baseline 0.1.7) is only correct for actual upgrades from
    # before the marker existed; for fresh `--purge` reinstalls it produces
    # a bogus "0.1.7 -> X detected" run that re-applies all the migration
    # steps unnecessarily. Only write if absent so an actual upgrade flow
    # (where the user manually ran setup over an existing install) isn't
    # downgraded.
    from winpodx import __version__ as _winpodx_version

    marker_path = config_dir() / "installed_version.txt"
    if not marker_path.exists():
        try:
            marker_path.write_text(_winpodx_version + "\n", encoding="utf-8")
        except OSError as e:
            print(f"  warning: could not stamp install marker ({e})")

    # v0.1.9: bundled profiles were removed. Desktop entries are now created
    # by `winpodx app refresh` (auto-fired on first pod boot via
    # provisioner.ensure_ready). Until the user's first launch, only the
    # winpodx GUI itself appears in the menu.
    _register_all_desktop_entries()

    print("\n" + "=" * 40)
    print(" Setup Complete")
    print("=" * 40)
    print(f"  Backend:  {cfg.pod.backend}")
    print(f"  User:     {cfg.rdp.user}")
    print(f"  IP:       {cfg.rdp.ip}")
    print(f"  Scale:    {cfg.rdp.scale}%")
    dpi_str = f"{cfg.rdp.dpi}%" if cfg.rdp.dpi > 0 else "auto"
    print(f"  DPI:      {dpi_str}")
    if cfg.pod.backend in ("podman", "docker"):
        print(f"  CPU:      {cfg.pod.cpu_cores} cores")
        print(f"  RAM:      {cfg.pod.ram_gb} GB")
        compose_path = config_dir() / "compose.yaml"
        print(f"  Compose:  {compose_path}")
    print()
    print("Apps are now in your application menu.")
    print("Just click any app. winpodx handles the rest automatically.")


def _recreate_container(cfg: Config) -> None:
    """Stop existing container and start fresh with new compose config."""
    import subprocess as sp

    compose_path = config_dir() / "compose.yaml"
    backend = cfg.pod.backend

    compose_cmd: list[str] | None = None
    if backend == "podman":
        if shutil.which("podman-compose"):
            compose_cmd = ["podman-compose"]
        else:
            try:
                sp.run(
                    ["podman", "compose", "version"],
                    capture_output=True,
                    check=True,
                )
                compose_cmd = ["podman", "compose"]
            except (FileNotFoundError, sp.CalledProcessError):
                pass
    elif backend == "docker":
        if shutil.which("docker-compose"):
            compose_cmd = ["docker-compose"]
        else:
            compose_cmd = ["docker", "compose"]

    if not compose_cmd:
        print("Compose command not found, skipping container recreation.")
        return

    print("\nRecreating container with new settings...")
    compose_timeout = _compose_timeout_secs()
    # compose down may fail on fresh setup when no container exists yet
    down = sp.run(
        [*compose_cmd, "down"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=compose_timeout,
    )
    if down.returncode != 0 and down.stderr:
        stderr = down.stderr.strip()
        if stderr and "no such" not in stderr.lower():
            print(f"  Warning: compose down returned {down.returncode}: {stderr}")
    timeout_text = "no cap" if compose_timeout is None else f"{compose_timeout}s"
    print(
        f"Running compose up -d (timeout {timeout_text}). "
        "First-time image pull can take 10+ minutes on slow connections."
    )
    result = sp.run(
        [*compose_cmd, "up", "-d"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=compose_timeout,
    )
    if result.returncode == 0:
        print("Container started.")
    else:
        msg = result.stderr.strip()
        print(f"Failed to start container: {msg}")
        raise RuntimeError(f"Container start failed: {msg}")


def handle_rotate_password(args: argparse.Namespace) -> None:
    """Rotate the Windows RDP password atomically via a temp-file swap."""
    import os
    import tempfile
    from datetime import datetime, timezone

    from winpodx.core.pod import PodState, pod_status
    from winpodx.core.provisioner import _change_windows_password

    cfg = Config.load()

    if cfg.pod.backend not in ("podman", "docker"):
        print("Password rotation is only supported for podman/docker backends.")
        raise SystemExit(1)

    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        print("Container is not running. Start it first: winpodx pod start --wait")
        raise SystemExit(1)

    new_password = _generate_password()
    old_password = cfg.rdp.password
    old_password_updated = cfg.rdp.password_updated

    print("Changing Windows user password...")
    if not _change_windows_password(cfg, new_password):
        print("Failed to change Windows password. Is the container fully booted?")
        raise SystemExit(1)

    # Validate compose template before touching on-disk config
    compose_path = config_dir() / "compose.yaml"
    compose_path.parent.mkdir(parents=True, exist_ok=True)

    cfg.rdp.password = new_password
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()

    fd, tmp_compose = tempfile.mkstemp(
        dir=compose_path.parent, prefix=".compose-rotate-", suffix=".tmp"
    )
    try:
        os.close(fd)
        _generate_compose_to(cfg, Path(tmp_compose))

        cfg.save()
        os.replace(tmp_compose, str(compose_path))
    except Exception:
        Path(tmp_compose).unlink(missing_ok=True)
        cfg.rdp.password = old_password
        cfg.rdp.password_updated = old_password_updated
        print("Password rotation failed; config and compose were not modified.")
        raise

    print("Password rotated successfully.")
    print(f"New password saved to {Config.path()}")


def _register_all_desktop_entries() -> None:
    """Register all app definitions as .desktop entries."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import install_winpodx_icon, update_icon_cache

    install_winpodx_icon()

    apps = list_available_apps()
    for app_info in apps:
        install_desktop_entry(app_info)

    if apps:
        update_icon_cache()
        print(f"Registered {len(apps)} apps in desktop environment.")
