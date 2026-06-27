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
from winpodx.core.i18n import tr
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
        print(tr("  warning: could not stage agent token to OEM dir ({error})").format(error=e))


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
        print(
            tr("--update-image only supports podman/docker (got {backend}).").format(
                backend=repr(cfg.pod.backend)
            )
        )
        return 2

    backend = cfg.pod.backend
    if not shutil.which(backend):
        print(tr("`{backend}` not found in PATH.").format(backend=backend))
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

    print(tr("Pulling {tag}...").format(tag=upstream_tag))
    try:
        subprocess.run(
            [backend, "pull", upstream_tag],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(tr("FAIL: pull exit={rc}").format(rc=e.returncode))
        return 3

    # Resolve the now-local image's repo-digest. RepoDigests is a list
    # of `<repo>@sha256:...` references — we filter to the docker.io
    # one so the pin always uses the canonical registry path.
    print(tr("Resolving image digest..."))
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
        print(
            tr("FAIL: image inspect exit={rc}: {stderr}").format(
                rc=e.returncode, stderr=e.stderr.strip()
            )
        )
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
        print(tr("FAIL: no usable digest in RepoDigests={digests}").format(digests=repr(digests)))
        return 3

    if cfg.pod.image == pinned:
        print(tr("Image already pinned to {pinned}. Nothing to do.").format(pinned=pinned))
        return 0

    print(tr("Old pin: {pin}").format(pin=cfg.pod.image))
    print(tr("New pin: {pin}").format(pin=pinned))
    cfg.pod.image = pinned
    cfg.save()

    try:
        from winpodx.core.compose import generate_compose

        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(tr("FAIL: compose.yaml regenerate ({error})").format(error=e))
        return 3

    print(
        tr(
            "OK: cfg.pod.image + compose.yaml updated.\n"
            "Next `winpodx pod start` recreates the container with the new image\n"
            "(~30 s, storage volume preserved — no ISO redownload, no Sysprep)."
        )
    )
    return 0


def _decide_storage_mode(
    cfg: Config, *, non_interactive: bool, explicit_target: Path | None = None
) -> None:
    """Resolve ``cfg.pod.storage_path`` for the about-to-be-generated compose.

    See the call site in ``handle_setup`` for the three-case decision.
    Mutates ``cfg`` in place; the caller saves + regenerates compose.

    ``explicit_target`` (``winpodx setup --storage-path`` / install.sh
    ``--storage-dir``, #646) picks the bind-mount location for a *fresh*
    install — e.g. a roomier partition. It gets the same fresh-target prep
    (mkdir + btrfs NoCoW + SSD emulation) as the default path. Relocating an
    *existing* install is out of scope here — that's ``--migrate-storage``.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    # Case 1: already set explicitly. Trust the user.
    if cfg.pod.storage_path:
        if explicit_target is not None:
            print(
                tr(
                    "  Note: storage is already configured ({path}); ignoring "
                    "--storage-path. To relocate an existing install, use "
                    "`winpodx setup --migrate-storage --migrate-storage-target`."
                ).format(path=cfg.pod.storage_path)
            )
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
        if explicit_target is not None:
            print(
                tr(
                    "  Note: an existing {volume} volume was found; ignoring "
                    "--storage-path. To move the install to {target}, use "
                    "`winpodx setup --migrate-storage --migrate-storage-target {target}`."
                ).format(volume=repr(resolved), target=explicit_target)
            )
            return
        mp = get_volume_mountpoint(cfg.pod.backend, resolved)
        if mp is not None and detect_path_fs(mp) == "btrfs":
            print()
            print(
                tr("  Note: existing {volume} volume is on btrfs ({mp}).").format(
                    volume=repr(resolved), mp=mp
                )
            )
            print(tr("    btrfs Copy-on-Write fragments the Windows raw disk image and"))
            print(tr("    slows pod recreates. To migrate to a NoCoW bind mount, run:"))
            print("        winpodx setup --migrate-storage")
            print(tr("    (~5-10 min, preserves the Windows install)"))
            print()
        return

    # Case 3: fresh install. Pick the bind-mount path (the user's
    # --storage-path if given, else the per-user default), create the
    # directory, and disable CoW if it's on btrfs BEFORE the volume gets
    # populated by the next compose-up.
    target = explicit_target if explicit_target is not None else default_target_path()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # If we can't create the dir, fall back to named volume (don't
        # set storage_path) so install still proceeds.
        print(
            tr("  Note: could not create {path} ({error}); using named volume instead.").format(
                path=target, error=e
            )
        )
        return

    fs = detect_path_fs(target)
    if fs == "btrfs":
        status, detail = disable_cow_on_path(target)
        if status == "disabled":
            print(tr("  btrfs detected — applied chattr +C on {path}").format(path=target))
            print(tr("    Windows raw disk image will be NoCoW from first boot."))
        elif status == "already_off":
            # silent — already done in a previous setup run
            pass
        elif status == "failed":
            print(
                tr("  Note: btrfs detected but chattr +C failed ({detail}).").format(detail=detail)
            )
            print(tr("    Pod will work, but VM disk operations may be slow."))
            print(tr("    You can retry manually: chattr +C"), target)
    cfg.pod.storage_path = str(target)

    # SSD emulation default (#606): if the host storage device is non-rotational,
    # present the guest disk as an SSD too (TRIM + no scheduled defrag). Only
    # flip ON for a confirmed SSD; HDD / undetectable keeps the HDD default.
    from winpodx.utils.btrfs import host_storage_is_ssd

    if host_storage_is_ssd(target) is True:
        cfg.pod.ssd = True
        print(tr("  Host storage is an SSD — the Windows disk will emulate SSD (TRIM, no defrag)."))


def _stage_win_iso(cfg: Config, iso_path: str | None) -> None:
    """Stage a user-provided Windows ISO as dockur's ``<storage>/custom.iso`` (#647).

    MUST run after :func:`_decide_storage_mode` (so ``storage_path`` is
    resolved) and BEFORE ``_recreate_container`` does ``compose up`` — dockur's
    ``findFile()`` looks for ``custom.iso`` the moment the container boots, so
    the ISO has to already be in place or dockur downloads Windows anyway
    (the #647 bug: install.sh staged it *after* the container had started).

    Reflink-copies where the filesystem supports it (btrfs/xfs → instant, no
    extra space). No-op on the legacy named-volume layout (no host storage dir).
    """
    if not iso_path:
        return
    import shutil
    import subprocess

    src = Path(iso_path).expanduser()
    if not src.is_file():
        print(tr("--win-iso: no such file: {path}").format(path=src))
        return
    storage = (cfg.pod.storage_path or "").strip()
    if not storage:
        print(
            tr(
                "--win-iso: needs the bind-mount storage layout — the legacy named "
                "volume has no host directory to stage into. Run "
                "`winpodx setup --migrate-storage` first. Skipping (Windows will download)."
            )
        )
        return
    storage_dir = Path(storage).expanduser()
    storage_dir.mkdir(parents=True, exist_ok=True)
    dst = storage_dir / "custom.iso"
    if src.resolve() == dst.resolve():
        print(tr("--win-iso: already staged at {dst}").format(dst=dst))
        return
    print(tr("Staging local ISO → {dst} (dockur installs from it; no download)…").format(dst=dst))
    try:
        # reflink where supported (btrfs/xfs); falls back to a full copy.
        subprocess.run(["cp", "--reflink=auto", str(src), str(dst)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        shutil.copyfile(src, dst)
    gib = dst.stat().st_size / (1024**3)
    print(tr("  Local ISO staged ({gib:.1f} GB).").format(gib=gib))


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
        print(tr("--migrate-storage: {detail}").format(detail=plan_or_error))
        return 2

    plan: MigrationPlan = plan_or_error

    # User confirmation unless --yes (acked) was passed. Migration moves
    # tens of gigabytes of disk + recreates the pod; we don't want to
    # surprise anyone.
    print()
    print(tr("Storage migration plan:"))
    print(tr("  Source:  podman volume {volume}").format(volume=repr(plan.source_volume)))
    print(
        tr("           {mountpoint}  (~{size_gib} GiB)").format(
            mountpoint=plan.source_mountpoint,
            size_gib=plan.source_size_bytes // (1 << 30),
        )
    )
    print(tr("  Target:  {path}  (fs={fs})").format(path=plan.target_path, fs=plan.target_fs))
    if plan.chattr_will_run:
        print(tr("  chattr:  +C will run on the target (btrfs NoCoW for new files)"))
    if plan.free_bytes_target >= 0:
        print(
            tr("  Free:    {free_gib} GiB available at target").format(
                free_gib=plan.free_bytes_target // (1 << 30)
            )
        )
    print()
    print(tr("Cost: rsync -aS of the entire volume. ~5-10 min on NVMe; longer on"))
    print(tr("spinning rust. The pod is stopped for the duration."))
    print()

    if not getattr(args, "yes", False) and not getattr(args, "non_interactive", False):
        if not _ask(tr("Proceed? (y/N): ")).lower().startswith("y"):
            print(tr("Aborted."))
            return 0

    print(tr("\nMigrating..."))
    result = execute_migration(cfg, plan, start_pod=True)
    if result.status == "ok":
        print(tr("OK: {detail}").format(detail=result.detail))
        return 0
    print(tr("FAIL: {detail}").format(detail=result.detail))
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
        print(tr("  warning: could not probe pod state: {error}").format(error=e))
        state = None

    if state in (PodState.STOPPED, PodState.ERROR, None) and not _container_exists_on_backend(cfg):
        print(
            tr(
                "  Container '{container}' is missing — "
                "creating it from existing config + compose.yaml."
            ).format(container=cfg.pod.container_name)
        )
        try:
            from winpodx.core.provisioner import ensure_ready

            ensure_ready(cfg)
        except Exception as e:  # noqa: BLE001
            print(tr("  WARNING: could not start pod: {error}").format(error=e))
            print(tr("  Try a full reinstall: uninstall.sh --purge then install.sh"))


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
        cfg.rdp.user = "WPX-User"
        cfg.rdp.password = _generate_password()
        cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
        cfg.rdp.ip = "127.0.0.1"
        return

    if config_existed and cfg.rdp.user and cfg.rdp.password:
        print(
            tr(
                "Existing credentials for user {user} preserved. "
                "Use `winpodx rotate-password` to change the Windows password."
            ).format(user=repr(cfg.rdp.user))
        )
        return

    cfg.rdp.user = _ask(tr("Windows username [WPX-User]: "), default="WPX-User")
    import getpass

    try:
        entered_pw = getpass.getpass(tr("Windows password (Enter for random): "))
    except EOFError:
        entered_pw = ""
    cfg.rdp.password = entered_pw or _generate_password()
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
    if cfg.pod.backend == "manual":
        cfg.rdp.ip = _ask(tr("Windows IP address: "))
    else:
        cfg.rdp.ip = _ask(tr("Windows IP [127.0.0.1]: "), default="127.0.0.1")


def _prompt_edition_locale_tuning(cfg: Config) -> None:
    """Wizard prompts for the knobs the GUI Settings page exposes but the
    CLI wizard historically skipped (#255 PR 7): Windows edition, UI
    language, regional format, keyboard layout, and the host tuning
    profile. Each reaches dockur as an env var and only takes effect on
    the initial Windows install. Enter accepts the shown default.
    """
    print(tr("\n--- Edition / locale / tuning (Enter = keep default) ---"))

    # Windows edition. The prompt hint is derived from WIN_VERSION_LABELS so
    # adding/removing a curated edition lights up here automatically.
    from winpodx.core.config import known_win_version_codes

    _editions_hint = ", ".join(known_win_version_codes())
    edition = _ask(
        tr("Windows edition ({editions}) [{default}]: ").format(
            editions=_editions_hint, default=cfg.pod.win_version
        ),
        default=cfg.pod.win_version,
    )
    cfg.pod.win_version = edition

    # UI language (dockur LANGUAGE env). Full English name, e.g. "English",
    # "German", "Korean". dockur maps it to the matching ISO at download.
    cfg.pod.language = _ask(
        tr("Windows UI language [{default}]: ").format(default=cfg.pod.language),
        default=cfg.pod.language,
    )

    # Regional format (dockur REGION env). BCP-47, e.g. en-US, en-GB,
    # ko-KR. Default en-001 = "English (World)"; suggest the more common
    # en-US for US date / number formatting (#293).
    cfg.pod.region = _ask(
        tr("Regional format (BCP-47, e.g. en-US) [{default}]: ").format(default=cfg.pod.region),
        default=cfg.pod.region,
    )

    # Keyboard layout (dockur KEYBOARD env). BCP-47, e.g. en-US, de-DE.
    cfg.pod.keyboard = _ask(
        tr("Keyboard layout (BCP-47, e.g. en-US) [{default}]: ").format(default=cfg.pod.keyboard),
        default=cfg.pod.keyboard,
    )

    # Host tuning profile (#215 / #245). auto = detect + apply every safe
    # KVM tweak the host supports; performance = auto + force CPU pinning
    # / no-balloon; safe = Tier-1 only; off = dockur defaults; manual =
    # reserved.
    tuning = (
        _ask(
            tr("Tuning profile (auto/performance/safe/off/manual) [{default}]: ").format(
                default=cfg.pod.tuning_profile
            ),
            default=cfg.pod.tuning_profile,
        )
        .strip()
        .lower()
    )
    if tuning in ("auto", "performance", "safe", "off", "manual"):
        cfg.pod.tuning_profile = tuning
    else:
        print(
            tr("  unknown profile {profile}, keeping {current}").format(
                profile=repr(tuning), current=repr(cfg.pod.tuning_profile)
            )
        )

    # Re-run validation so any normalization (win_version casing, etc.)
    # lands before compose generation.
    cfg.pod.__post_init__()


def _run_full_provision(cfg: Config) -> None:
    """Drive the post-container-create provisioning so a standalone
    `winpodx setup` finishes like a complete install instead of stopping at
    "container created".

    0.6.0 item B: this is now a thin wrapper over the single source of truth
    ``core.provisioner.finish_provisioning`` (the same chain ``winpodx
    provision``, migrate, and pending.resume run). The manual per-stage
    assembly that used to live here — wait-ready, apply-fixes, discovery,
    reverse-open — moved into the helper, parameter-gated. We pass the same
    parameters the old inline code used: 3600s wait, soft agent settle
    (require_agent=False so a slow first boot doesn't crash setup), discovery
    with 6× retry, reverse-open gated on cfg.reverse_open.enabled.

    Skipped for non-podman/docker backends (the helper short-circuits too).
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    print("\n" + "=" * 40)
    print(tr(" Provisioning Windows (first boot)"))
    print("=" * 40)
    print(tr("First install downloads ~7.5GB ISO + runs Sysprep + OEM apply."))
    print(tr("This can take ~5-10 min (longer on slow connections).\n"))

    from winpodx.core.provisioner import finish_provisioning

    def _on_progress(stage: str, detail: str) -> None:
        print(tr("  [{stage}] {detail}").format(stage=stage, detail=detail))

    def _rich_wait(_cfg: Config, timeout: int) -> bool:
        # Interactive `winpodx setup` shows the same live download/boot
        # progress (clean self-erasing line, not verbose) the pre-0.6.0
        # _run_full_provision had via `_wait_ready(3600, show_logs=True)`.
        # Injected so core/provisioner stays cli-free.
        from winpodx.cli.pod import _wait_ready

        try:
            _wait_ready(timeout, show_logs=True, verbose=False)
            return True
        except SystemExit as exc:
            return exc.code in (0, None)

    results = finish_provisioning(
        cfg,
        wait_timeout=3600,
        require_agent=False,
        with_reverse_open=getattr(cfg.reverse_open, "enabled", False),
        with_discovery=True,
        retries=2,
        on_progress=_on_progress,
        wait_fn=_rich_wait,
    )

    if results.get("wait_ready") == "timeout":
        print(
            tr(
                "\n  wait-ready did not complete. Remaining steps will "
                "auto-resume on your next `winpodx app run` / `winpodx gui`."
            )
        )


def handle_setup(args: argparse.Namespace) -> None:
    """Run the setup wizard."""
    import sys

    if getattr(args, "update_image", False):
        sys.exit(_update_image_pin())

    if getattr(args, "migrate_storage", False):
        sys.exit(_handle_migrate_storage(args))

    backend = args.backend

    # #255: ``winpodx setup`` defaults to non-interactive (auto). The
    # ``--customize`` flag opts into the wizard. ``--non-interactive``
    # is a deprecated alias kept for back-compat with install.sh and
    # other scripted callers; both interpretations resolve to the same
    # auto mode.
    customize = bool(getattr(args, "customize", False))
    non_interactive = not customize

    # Non-TTY stdin (pipe, /dev/null, CI) -> force non-interactive even
    # if --customize was passed: every input() call would otherwise
    # raise EOFError when defaults are read.
    if customize and not sys.stdin.isatty():
        non_interactive = True
        customize = False

    print(tr("=== WinPodX setup ===\n"))

    print(tr("Checking dependencies..."))
    # probe_daemons: verify the container backends actually answer, not just
    # that their CLI is on PATH (#395 — docker CLI present but DOCKER_HOST
    # pointed at a dead podman socket).
    deps = check_all(probe_daemons=True)
    # `kvm` is now part of check_all() (0.6.0 item D), so the inline probe
    # this block used to carry is gone -- the iteration below covers it.
    for name, dep in deps.items():
        if not dep.found:
            status = "MISSING"
        elif dep.daemon_reachable is False:
            status = "DAEMON DOWN"
        else:
            status = "OK"
        print(f"  {name:<15} [{status}] {dep.note}")

    if not deps["freerdp"].found:
        print(tr("\nFreeRDP 3+ is required. Install it and try again."))
        raise SystemExit(1)

    print()

    existing = import_winapps_config()
    if existing and not non_interactive:
        answer = _ask(tr("Found existing winapps.conf. Import settings? (Y/n): ")).lower()
        if answer in ("", "y", "yes"):
            existing.save()
            print(tr("Config saved to {path}").format(path=Config.path()))
            return

    config_existed = Config.path().exists()
    if config_existed:
        cfg = Config.load()
        if non_interactive:
            print(tr("Existing config found at {path}, skipping setup.").format(path=Config.path()))
            # #341: an existing config IS a completed setup -- flip the
            # first-run flag here too. Without this, a config written before
            # the `initialized` flag existed (or any config with it still
            # False) makes the first-run prompt fire on *every* invocation:
            # the user picks "Auto", setup short-circuits on this branch,
            # returns without marking initialized, and the prompt comes back
            # next time. Persist only when it actually changes.
            # A targeted `winpodx setup --freerdp-source <x>` on an existing
            # config still persists the preference (this branch otherwise
            # short-circuits before the main apply below).
            _fr_src = getattr(args, "freerdp_source", None)
            _mm = getattr(args, "multimon", None)
            changed = False
            if not cfg.pod.initialized:
                cfg.pod.initialized = True
                changed = True
            if _fr_src and _fr_src != cfg.rdp.freerdp_source:
                cfg.rdp.freerdp_source = _fr_src
                cfg.rdp.__post_init__()
                changed = True
            if _mm and _mm != cfg.rdp.multimon:
                cfg.rdp.multimon = _mm
                cfg.rdp.__post_init__()
                changed = True
            if changed:
                cfg.save()
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

    if backend or non_interactive:
        # Explicit --backend wins; non-interactive auto-picks via the same
        # priority + podman-version gate install.sh's Automatic-mode picker
        # uses, so post-install setup wizards and curl|bash agree on what
        # to run on Ubuntu 22.04 (#271) etc. See backend/select.choose_backend.
        from winpodx.backend.select import choose_backend

        cfg.pod.backend = choose_backend(prefer=backend, deps=deps)
    else:
        available = []
        if deps.get("podman") and deps["podman"].found:
            available.append("podman")
        if deps.get("docker") and deps["docker"].found:
            available.append("docker")
        available.append("manual")

        if len(available) == 1 and available[0] == "manual":
            print(tr("No container/VM backends found. Install podman or docker."))

        print(tr("Available backends: {backends}").format(backends=", ".join(available)))
        choice = _ask(
            tr("Select backend [{default}]: ").format(default=available[0]),
            default=available[0],
        )
        if choice in available:
            cfg.pod.backend = choice
        else:
            print(tr("Invalid choice: {choice}").format(choice=choice))
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

    # Persist the FreeRDP source preference (native / flatpak / auto) chosen in
    # install.sh Custom mode or via `winpodx setup --freerdp-source`. "auto"
    # (the default) prefers the Flatpak client when present; an explicit value
    # forces that client. RDPConfig.__post_init__ validates the value.
    freerdp_source_arg = getattr(args, "freerdp_source", None)
    if freerdp_source_arg:
        cfg.rdp.freerdp_source = freerdp_source_arg
        cfg.rdp.__post_init__()

    # Persist the multi-monitor RAIL strategy chosen via `winpodx setup
    # --multimon`. Defaults to "span" (RDPConfig default); an explicit value
    # overrides. RDPConfig.__post_init__ validates the value.
    multimon_arg = getattr(args, "multimon", None)
    if multimon_arg:
        cfg.rdp.multimon = multimon_arg
        cfg.rdp.__post_init__()

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
                tr(
                    "\nHost specs: {cpu_threads} CPU threads, "
                    "{ram_gb} GB RAM. Recommended tier: {label} "
                    "(VM: {cores} cores, {vm_ram} GB)."
                ).format(
                    cpu_threads=host.cpu_threads,
                    ram_gb=host.ram_gb,
                    label=tier.label,
                    cores=tier.cpu_cores,
                    vm_ram=tier.ram_gb,
                )
            )
            cpu_input = _ask(tr("CPU cores [{default}]: ").format(default=tier.cpu_cores))
            try:
                cfg.pod.cpu_cores = int(cpu_input) if cpu_input else tier.cpu_cores
            except ValueError:
                print(tr("Invalid number, using default: {default}").format(default=tier.cpu_cores))
                cfg.pod.cpu_cores = tier.cpu_cores
            ram_input = _ask(tr("RAM (GB) [{default}]: ").format(default=tier.ram_gb))
            try:
                cfg.pod.ram_gb = int(ram_input) if ram_input else tier.ram_gb
            except ValueError:
                print(tr("Invalid number, using default: {default}").format(default=tier.ram_gb))
                cfg.pod.ram_gb = tier.ram_gb

        # Timezone prompt (#254 phase 2). Empty input or empty
        # cfg.pod.timezone defers to autodetect at compose time. The
        # detection is cheap; we surface the resolved value here so
        # users can see what would land on the guest. Non-interactive
        # mode leaves cfg.pod.timezone alone (default "" -> autodetect).
        if not non_interactive:
            from winpodx.utils.locale import detect_timezone

            detected = detect_timezone()
            current = cfg.pod.timezone or detected
            tz_input = _ask(
                tr("Windows timezone (IANA name, e.g. Asia/Seoul) [{default}]: ").format(
                    default=current
                ),
                default=current,
            )
            # User answered with the same value we showed as default ->
            # store the detected IANA name explicitly so a later config
            # show / GUI display reflects the user's confirmation rather
            # than re-running detection every compose. Edge case: user
            # may have explicitly entered "" intending autodetect; the
            # _ask helper coerces that to the default, so we lose that
            # signal. Acceptable trade-off -- explicit autodetect is
            # available via the upcoming `winpodx config set
            # pod.timezone --auto` shorthand or hand-editing the TOML.
            cfg.pod.timezone = tz_input
            cfg.pod.__post_init__()

        # Edition / locale / tuning wizard (#255 PR 7 -- the knobs the
        # GUI Settings page already exposes, now reachable from the CLI
        # wizard too). All reach dockur as env vars and apply on the
        # initial Windows install that _recreate_container triggers
        # below. Non-interactive mode leaves the cfg defaults untouched.
        if not non_interactive and cfg.pod.backend in ("podman", "docker"):
            _prompt_edition_locale_tuning(cfg)

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
        _storage_path_arg = getattr(args, "storage_path", None)
        _explicit_storage = Path(_storage_path_arg).expanduser() if _storage_path_arg else None
        _decide_storage_mode(
            cfg, non_interactive=non_interactive, explicit_target=_explicit_storage
        )

        # Stage a user-provided ISO into <storage>/custom.iso BEFORE compose up
        # so dockur picks it up instead of downloading Windows (#647). Must come
        # after _decide_storage_mode (storage_path resolved) + before
        # _recreate_container below.
        _stage_win_iso(cfg, getattr(args, "win_iso", None))

        # #395: bail out with an actionable message if the selected backend's
        # daemon isn't reachable, rather than letting compose fail with a
        # confusing "Cannot connect to the Docker daemon" traceback. `deps`
        # was probed with probe_daemons=True above.
        sel = deps.get(cfg.pod.backend)
        if sel is not None and sel.found and sel.daemon_reachable is False:
            print()
            print(
                tr("Cannot use the {backend} backend: {hint}").format(
                    backend=cfg.pod.backend, hint=sel.note
                )
            )
            print(
                tr(
                    "Fix the daemon (above) and re-run `winpodx setup`, or switch "
                    "backend with `winpodx config set pod.backend <podman|docker>`."
                )
            )
            raise SystemExit(1)

        _generate_compose(cfg)
        _recreate_container(cfg)

    from winpodx.display.scaling import detect_raw_scale, detect_scale_factor

    detected_scale = detect_scale_factor()
    if detected_scale != 100:
        print(tr("\nDetected display scale: {scale}%").format(scale=detected_scale))
        cfg.rdp.scale = detected_scale

    raw = detect_raw_scale()
    detected_dpi = round(raw * 100)
    if detected_dpi > 100:
        print(tr("Detected Windows DPI: {dpi}%").format(dpi=detected_dpi))
        cfg.rdp.dpi = detected_dpi

    # #255: mark this install as initialized so the first-run prompt
    # doesn't fire again. Setting this on the cfg in memory before save
    # ensures the flag round-trips through TOML.
    cfg.pod.initialized = True
    cfg.save()
    print(tr("\nConfig saved to {path}").format(path=Config.path()))

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
    print(tr("\n[Tuning]"))
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
            print(tr("  warning: could not stamp install marker ({error})").format(error=e))

    # v0.1.9: bundled profiles were removed. Desktop entries are now created
    # by `winpodx app refresh` (auto-fired on first pod boot via
    # provisioner.ensure_ready). Until the user's first launch, only the
    # winpodx GUI itself appears in the menu.
    _register_all_desktop_entries()

    print("\n" + "=" * 40)
    print(tr(" Setup Complete"))
    print("=" * 40)
    print(tr("  Backend:  {backend}").format(backend=cfg.pod.backend))
    print(tr("  User:     {user}").format(user=cfg.rdp.user))
    print(tr("  IP:       {ip}").format(ip=cfg.rdp.ip))
    print(tr("  Scale:    {scale}%").format(scale=cfg.rdp.scale))
    dpi_str = f"{cfg.rdp.dpi}%" if cfg.rdp.dpi > 0 else tr("auto")
    print(tr("  DPI:      {dpi}").format(dpi=dpi_str))
    if cfg.pod.backend in ("podman", "docker"):
        print(tr("  CPU:      {cores} cores").format(cores=cfg.pod.cpu_cores))
        print(tr("  RAM:      {ram_gb} GB").format(ram_gb=cfg.pod.ram_gb))
        compose_path = config_dir() / "compose.yaml"
        print(tr("  Compose:  {path}").format(path=compose_path))
    print()

    # Full-provision flow: a standalone `winpodx setup` should finish
    # like a complete install (wait for Windows first-boot + apply-fixes +
    # discover apps + reverse-open), not stop at "container created".
    #
    # 0.6.0 item B: the `--create-only` flag is gone. install.sh now runs
    # `winpodx setup ... && winpodx provision --verbose`: setup creates the
    # container and the dedicated `provision` command runs the chain once.
    # To avoid running the chain twice in that flow, install.sh exports
    # WINPODX_NO_PROVISION=1 so setup skips its own provision tail and leaves
    # it to the explicit `winpodx provision` call. A standalone `winpodx
    # setup` (no env var) still finishes the full flow. Non-container
    # backends short-circuit (the helper has nothing to do for the manual backend).
    import os

    skip_provision = os.environ.get("WINPODX_NO_PROVISION") == "1"
    if cfg.pod.backend not in ("podman", "docker") or skip_provision:
        print(tr("Apps are now in your application menu."))
        print(tr("Just click any app. WinPodX handles the rest automatically."))
        if cfg.pod.backend in ("podman", "docker") and skip_provision:
            print(
                tr(
                    "\nWindows is booting in the background. "
                    "Run `winpodx provision` to finish (wait-ready + apply-fixes "
                    "+ discovery + reverse-open), or just `winpodx app run desktop`."
                )
            )
    else:
        _run_full_provision(cfg)
        print(tr("\nSetup + provisioning complete. Launch with `winpodx app run desktop`."))


def _recreate_container(cfg: Config) -> None:
    """Stop existing container and start fresh with new compose config.

    Thin AppImage (#363 mitigation, 0.6.0 item A): every ``sp.run`` here
    passes ``env=host_env()`` so the host container runtime + the host
    helpers it spawns (``systemd-run`` / ``netavark`` / ``aardvark-dns``)
    load HOST libcrypto / libssl rather than the bundled ones still on
    the AppImage's ``LD_LIBRARY_PATH``. Outside an AppImage ``host_env()``
    returns None -> ``env=None`` -> inherit (unchanged behaviour). The
    ``shutil.which`` calls below find the host binaries directly because
    the Thin AppImage no longer bundles a container stack to shadow them.
    """
    import subprocess as sp

    from winpodx.backend._hostenv import host_env

    compose_path = config_dir() / "compose.yaml"
    backend = cfg.pod.backend
    env = host_env()

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
                    env=env,
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
        # No compose provider → the container is never created, and setup later
        # fails with the cryptic `no such container "winpodx-windows"` (#644).
        # Say so loudly + actionably instead of silently skipping.
        if backend == "podman":
            print(
                tr(
                    "ERROR: no compose provider found. winpodx creates the Windows "
                    "container via compose, but neither `podman-compose` nor the "
                    "`podman compose` plugin is installed, so the container can't be "
                    "created (this is what later surfaces as "
                    "'no such container \"winpodx-windows\"', #644).\n"
                    "  Install it, then re-run `winpodx setup`:\n"
                    "    Debian/Ubuntu:  sudo apt install podman-compose\n"
                    "    Fedora:         sudo dnf install podman-compose\n"
                    "    openSUSE:       sudo zypper install podman-compose\n"
                    "    (fallback:      pipx install podman-compose)"
                )
            )
        else:
            print(
                tr(
                    "ERROR: no compose provider found for the docker backend. Install "
                    "Docker Compose (the `docker compose` plugin or `docker-compose`), "
                    "then re-run `winpodx setup`."
                )
            )
        return

    print(tr("\nRecreating container with new settings..."))
    compose_timeout = _compose_timeout_secs()
    # compose down may fail on fresh setup when no container exists yet
    down = sp.run(
        [*compose_cmd, "down"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=compose_timeout,
        env=env,
    )
    if down.returncode != 0 and down.stderr:
        stderr = down.stderr.strip()
        if stderr and "no such" not in stderr.lower():
            print(
                tr("  Warning: compose down returned {rc}: {stderr}").format(
                    rc=down.returncode, stderr=stderr
                )
            )
    timeout_text = tr("no cap") if compose_timeout is None else f"{compose_timeout}s"
    print(
        tr(
            "Running compose up -d (timeout {timeout}). "
            "First-time image pull can take 10+ minutes on slow connections."
        ).format(timeout=timeout_text)
    )
    result = sp.run(
        [*compose_cmd, "up", "-d"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=compose_timeout,
        env=env,
    )
    if result.returncode == 0:
        print(tr("Container started."))
    else:
        msg = result.stderr.strip()
        print(tr("Failed to start container: {msg}").format(msg=msg))
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
        print(tr("Password rotation is only supported for podman/docker backends."))
        raise SystemExit(1)

    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        print(tr("Container is not running. Start it first: winpodx pod start --wait"))
        raise SystemExit(1)

    new_password = _generate_password()
    old_password = cfg.rdp.password
    old_password_updated = cfg.rdp.password_updated

    print(tr("Changing Windows user password..."))
    if not _change_windows_password(cfg, new_password):
        print(tr("Failed to change Windows password. Is the container fully booted?"))
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
        print(tr("Password rotation failed; config and compose were not modified."))
        raise

    print(tr("Password rotated successfully."))
    print(tr("New password saved to {path}").format(path=Config.path()))


def _register_all_desktop_entries() -> None:
    """Register all app definitions as .desktop entries."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import (
        install_gui_launcher_desktop,
        install_winpodx_icon,
        update_icon_cache,
    )

    install_winpodx_icon()
    # G7: install the GUI launcher .desktop so `winpodx setup` standalone
    # (pip, dev checkout, package install missing the entry) leaves the
    # GUI discoverable in the app menu. No-ops if a system copy exists.
    install_gui_launcher_desktop()
    # NB: tray autostart / pod auto-start are opt-in -- setup does NOT enable
    # them. Run `winpodx autostart on` (or the GUI checkbox) to have the pod
    # come up on login. Booting Windows every login is heavy, so it's an
    # explicit choice, never forced by install.

    apps = list_available_apps()
    for app_info in apps:
        install_desktop_entry(app_info)

    if apps:
        update_icon_cache()
        print(tr("Registered {count} apps in desktop environment.").format(count=len(apps)))
