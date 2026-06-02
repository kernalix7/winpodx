# SPDX-License-Identifier: MIT
"""Migrate the Windows VM disk image from podman's named volume to a
host-local bind mount, optionally with btrfs Copy-on-Write disabled.

Background: legacy installs (everything before this module landed) used
a named volume ``winpodx-data:/storage:Z`` so podman managed the
volume's lifecycle. On btrfs hosts that volume sits under podman's
graph root and inherits the filesystem's default Copy-on-Write — every
overwrite of the Windows raw disk image (pagefile, swap, boot writes)
forks a new extent, fragmenting the image and slowing pod recreates
from ~30 s to many minutes (kernalix7 / @xiyeming hit this on cachyos
in #121, #122).

This module's :func:`migrate_storage_to_bind_mount` moves the volume's
contents to a winpodx-owned host directory (``~/.local/share/winpodx/
storage`` by default), applies ``chattr +C`` on btrfs hosts, then
flips the user's ``cfg.pod.storage_path`` so future compose
generations bind-mount the new path. The original named volume is
removed only after the copy succeeds, so an interrupted migration
leaves the source intact for retry.

Cost: a full ``rsync -a`` of the volume (60+ GB after Sysprep,
basically the size of the Windows raw disk + ISO). On NVMe ~5-10 min;
spinning rust ~30 min+. Disk space requirement: 2× volume size during
the copy window. The function refuses to start if the target path
isn't empty or there isn't enough free space on the target's
filesystem.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.btrfs import detect_path_fs, disable_cow_on_path, is_cow_disabled

log = logging.getLogger(__name__)

DEFAULT_STORAGE_RELPATH = ".local/share/winpodx/storage"

# The compose template references the volume by the bare name
# `winpodx-data`. podman-compose / docker compose, however, materialise
# named volumes with the project name as a prefix (`winpodx_winpodx-data`
# given our `name: "winpodx"` declaration in the compose template).
# kernalix7's smoke test on opensuse Tumbleweed (2026-05-06) showed that
# `podman volume exists winpodx-data` returned False there because the
# real volume was `winpodx_winpodx-data` — auto-migration silently never
# ran and the bug "migration sometimes fails" was actually "migration
# never started."
#
# We resolve by trying both names in order: the prefixed form first
# (the canonical podman-compose layout), then the bare form (a fallback
# for users who manually `podman volume create winpodx-data` or who
# created the pod outside of compose). Whichever exists wins.
NAMED_VOLUME_BARE = "winpodx-data"
NAMED_VOLUME_COMPOSE_PROJECT = "winpodx"
NAMED_VOLUME_PREFIXED = f"{NAMED_VOLUME_COMPOSE_PROJECT}_{NAMED_VOLUME_BARE}"
# Public alias kept for backward compatibility in tests / external callers.
NAMED_VOLUME = NAMED_VOLUME_BARE


def _candidate_volume_names() -> tuple[str, ...]:
    """Volume names to probe in order, preferring the podman-compose layout."""
    return (NAMED_VOLUME_PREFIXED, NAMED_VOLUME_BARE)


@dataclass
class MigrationPlan:
    """A pre-flight summary of what migration will do, surfaced to the user."""

    backend: str
    source_volume: str
    source_mountpoint: Path
    source_size_bytes: int
    target_path: Path
    target_fs: str
    chattr_will_run: bool
    free_bytes_target: int


@dataclass
class MigrationResult:
    """Outcome of an attempted migration."""

    status: str  # ok | aborted | failed
    detail: str


def default_target_path() -> Path:
    """Default bind mount path for a fresh install or migration target."""
    return Path.home() / DEFAULT_STORAGE_RELPATH


def _volume_exists_single(backend: str, name: str) -> bool:
    """Probe a single ``backend volume exists <name>`` invocation."""
    try:
        result = subprocess.run(
            [backend, "volume", "exists", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def resolve_named_volume(backend: str, name: str | None = None) -> str | None:
    """Return the actual on-host volume name for the winpodx storage volume.

    Tries the candidate names in order (compose-prefixed then bare) and
    returns the first one that exists, or None if neither does. When
    ``name`` is provided explicitly, only that name is probed.

    The two-name fallback exists because podman-compose / docker compose
    namespace volumes by project (``winpodx_winpodx-data``) but a hand-
    created or pre-compose-era volume uses the bare ``winpodx-data``.
    """
    if backend not in ("podman", "docker"):
        return None
    if shutil.which(backend) is None:
        return None

    candidates = (name,) if name is not None else _candidate_volume_names()
    for candidate in candidates:
        if _volume_exists_single(backend, candidate):
            return candidate
    return None


def named_volume_exists(backend: str, name: str | None = None) -> bool:
    """Return True if a podman/docker winpodx-storage named volume exists.

    Probes both the compose-prefixed (``winpodx_winpodx-data``) and bare
    (``winpodx-data``) forms unless an explicit ``name`` is passed.
    """
    return resolve_named_volume(backend, name) is not None


def get_volume_mountpoint(backend: str, name: str | None = None) -> Path | None:
    """Return the host filesystem path of the winpodx storage volume.

    Both runtimes expose a ``Mountpoint`` field on volume inspect; the
    JSON shape is identical enough that one parser handles both.
    Resolves the candidate names just like :func:`named_volume_exists`
    so callers get the mountpoint regardless of whether the volume
    landed under the compose-prefixed or bare name.
    """
    if backend not in ("podman", "docker"):
        return None
    if shutil.which(backend) is None:
        return None

    resolved = resolve_named_volume(backend, name)
    if resolved is None:
        return None

    try:
        result = subprocess.run(
            [backend, "volume", "inspect", resolved],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    mp = data[0].get("Mountpoint")
    if not isinstance(mp, str) or not mp:
        return None
    return Path(mp)


def _dir_size_bytes(path: Path) -> int:
    """Return total bytes of all files under ``path``. Best-effort; missing files counted as 0."""
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def plan_migration(cfg: Config, target: Path | None = None) -> MigrationPlan | str:
    """Build a :class:`MigrationPlan` or return a human-readable error string.

    Validates:
    - backend supports volumes (podman/docker)
    - source volume exists
    - source mountpoint is readable
    - target path is empty (or doesn't exist yet)
    - target filesystem has enough free space (1.1× source size, 10% buffer)

    The plan does not modify any state. Caller invokes
    :func:`execute_migration` to actually run.
    """
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return f"backend {backend!r} does not have named volumes; nothing to migrate"

    resolved_volume = resolve_named_volume(backend)
    if resolved_volume is None:
        return (
            f"no WinPodX storage volume found (tried {NAMED_VOLUME_PREFIXED!r} "
            f"and {NAMED_VOLUME_BARE!r}); nothing to migrate"
        )

    src = get_volume_mountpoint(backend, resolved_volume)
    if src is None or not src.exists():
        return f"could not resolve {resolved_volume!r} mountpoint via `{backend} volume inspect`"

    target_path = (target or default_target_path()).expanduser()

    # Refuse to overwrite a populated target.
    if target_path.exists() and any(target_path.iterdir()):
        return f"target path {target_path} is not empty; remove it or choose a different path"

    src_size = _dir_size_bytes(src)
    # Build the plan; target dir doesn't need to exist yet for findmnt.
    target_fs = detect_path_fs(target_path)

    free = -1
    try:
        # Use the parent for free-space check when target itself doesn't exist yet.
        check = target_path if target_path.exists() else target_path.parent
        if not check.exists():
            check = check.parent
        usage = shutil.disk_usage(check)
        free = usage.free
    except OSError:
        free = -1

    needed = int(src_size * 1.1) + (1 << 30)  # 1.1× + 1GB headroom
    if free >= 0 and free < needed:
        return (
            f"not enough free space at {target_path}: need ~{needed // (1 << 30)} GiB, "
            f"have {free // (1 << 30)} GiB"
        )

    return MigrationPlan(
        backend=backend,
        source_volume=resolved_volume,
        source_mountpoint=src,
        source_size_bytes=src_size,
        target_path=target_path,
        target_fs=target_fs,
        chattr_will_run=(target_fs == "btrfs"),
        free_bytes_target=free,
    )


def _stop_pod(cfg: Config) -> tuple[bool, str]:
    """Best-effort `podman/docker compose down` so the named volume isn't held open."""
    backend = cfg.pod.backend
    compose_path = Path.home() / ".config" / "winpodx" / "compose.yaml"
    if not compose_path.exists():
        return False, f"compose file not found at {compose_path}"

    cmd: list[str] | None = None
    if backend == "podman":
        if shutil.which("podman-compose"):
            cmd = ["podman-compose"]
        elif shutil.which("podman"):
            cmd = ["podman", "compose"]
    elif backend == "docker":
        if shutil.which("docker-compose"):
            cmd = ["docker-compose"]
        elif shutil.which("docker"):
            cmd = ["docker", "compose"]

    if cmd is None:
        return False, f"no compose CLI available for backend {backend!r}"

    try:
        result = subprocess.run(
            [*cmd, "down"],
            cwd=compose_path.parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"compose down raised: {e}"

    if result.returncode != 0:
        # 'no such' / 'not found' is fine — the pod was already stopped.
        if any(tok in result.stderr.lower() for tok in ("no such", "not found", "no compose")):
            return True, "no running pod"
        return False, result.stderr.strip() or f"rc={result.returncode}"
    return True, "stopped"


def _rsync_copy(src: Path, dst: Path) -> tuple[bool, str]:
    """Copy ``src/`` contents into ``dst/`` preserving permissions / owners.

    Prefers ``rsync -a`` (handles symlinks, sparse files, perms cleanly);
    falls back to ``cp -a`` if rsync isn't installed.
    """
    if shutil.which("rsync") is not None:
        # Trailing dot on src ensures we copy *contents*, not the source
        # directory itself, into dst. --sparse keeps the Windows raw
        # disk image from ballooning if it has holes.
        cmd = ["rsync", "-aS", str(src) + "/", str(dst) + "/"]
    elif shutil.which("cp") is not None:
        cmd = ["cp", "-a", str(src) + "/.", str(dst) + "/"]
    else:
        return False, "neither rsync nor cp available"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=86400)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"copy raised: {e}"

    if result.returncode != 0:
        return False, f"copy failed (rc={result.returncode}): {result.stderr.strip()}"
    return True, "ok"


def execute_migration(
    cfg: Config, plan: MigrationPlan, *, start_pod: bool = True
) -> MigrationResult:
    """Execute the migration plan. Returns the outcome.

    Steps:
      1. ``compose down`` so the named volume isn't held open.
      2. Create target dir + ``chattr +C`` if btrfs (BEFORE files exist
         so they all inherit NoCoW).
      3. ``rsync -aS`` from source mountpoint → target.
      4. Update ``cfg.pod.storage_path``, save config, regenerate compose.
      5. Remove the old named volume.
      6. Start pod if requested.

    On failure mid-copy, the source volume is left intact and the
    target dir is wiped so the user can retry. ``cfg.pod.storage_path``
    is only persisted after a successful copy + compose regenerate.
    """
    from winpodx.core.compose import generate_compose

    log.info("storage migration starting: %s -> %s", plan.source_mountpoint, plan.target_path)

    # 1. Stop pod so the named volume isn't held open.
    ok, detail = _stop_pod(cfg)
    if not ok:
        return MigrationResult(status="failed", detail=f"could not stop pod: {detail}")

    # 2. Create empty target + chattr +C if btrfs.
    try:
        plan.target_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return MigrationResult(status="failed", detail=f"mkdir {plan.target_path}: {e}")

    # Track chattr outcomes for the post-migration sanity check + result.detail.
    # 'log.warning' alone goes to stderr at WARNING level, which on some hosts
    # is filtered out of the install.sh foreground output — kernalix7's
    # 2026-05-06 smoke test on opensuse Tumbleweed had chattr +C silently
    # missing on the dir AND the 64 GiB data.img and the install completed
    # showing only "OK: migrated 64 GiB". Surfacing via print() ensures the
    # user sees it inline; sanity-checking with lsattr after rsync catches
    # the silent-no-op case where chattr returned 0 but the flag didn't
    # actually stick.
    chattr_pre_status = "skipped_not_btrfs"
    chattr_pre_detail = ""
    if plan.chattr_will_run:
        chattr_pre_status, chattr_pre_detail = disable_cow_on_path(plan.target_path)
        if chattr_pre_status == "disabled":
            print(f"  chattr +C applied to {plan.target_path} (NoCoW for new files)")
            log.info("chattr +C applied to %s (NoCoW for new files)", plan.target_path)
        elif chattr_pre_status == "already_off":
            print(f"  chattr +C already on {plan.target_path}")
            log.info("chattr +C already on %s", plan.target_path)
        else:
            # Surface to stdout AND stderr — install.sh will see both.
            print(
                f"  WARNING: chattr +C did not apply to {plan.target_path} "
                f"({chattr_pre_status}): {chattr_pre_detail}"
            )
            log.warning("chattr +C skipped (%s): %s", chattr_pre_status, chattr_pre_detail)

    # 3. Copy source -> target.
    log.info(
        "copying %.1f GiB from %s ...", plan.source_size_bytes / (1 << 30), plan.source_mountpoint
    )
    ok, detail = _rsync_copy(plan.source_mountpoint, plan.target_path)
    if not ok:
        # Copy failed — clean up target so retry can run on an empty dir.
        try:
            shutil.rmtree(plan.target_path, ignore_errors=True)
        except OSError:
            pass
        return MigrationResult(status="failed", detail=f"copy failed: {detail}")

    # 3b. Verify chattr +C actually stuck — both on the dir itself and on at
    # least one new file rsync just created. The btrfs inheritance contract
    # is "files born inside a +C dir are NoCoW from inception." If the dir
    # lost +C between step 2 and now (or chattr returned 0 but the flag
    # never persisted), files won't inherit it and the migration silently
    # produces a CoW disk image — defeating the entire point. We can't
    # retroactively fix existing files (chattr +C on a populated file does
    # not break existing CoW extents), so surface the discrepancy as a
    # WARNING in the result detail with a recovery recipe.
    cow_warnings: list[str] = []
    if plan.chattr_will_run:
        dir_state = is_cow_disabled(plan.target_path)
        if dir_state is False:
            cow_warnings.append(
                f"target dir {plan.target_path} does NOT have +C set after migration "
                f"(chattr earlier returned: {chattr_pre_status})"
            )
        # Sample one of the rsync-created files to confirm inheritance worked.
        sample_files = sorted(
            p for p in plan.target_path.iterdir() if p.is_file() and p.suffix in (".img",)
        )
        if sample_files:
            sample = sample_files[0]
            sample_state = is_cow_disabled(sample)
            if sample_state is False:
                cow_warnings.append(
                    f"sample file {sample.name} did not inherit +C from parent dir — "
                    f"existing CoW extents need to be broken via "
                    f"`cp --reflink=never {sample.name} {sample.name}.new && "
                    f"mv {sample.name}.new {sample.name}` (run from {plan.target_path} "
                    f"with the pod stopped)"
                )
        if cow_warnings:
            print("")
            print("  WARNING: NoCoW verification failed after migration:")
            for w in cow_warnings:
                print(f"    - {w}")
                log.warning("NoCoW verify: %s", w)

    # 4. Persist new storage_path + regenerate compose.
    cfg.pod.storage_path = str(plan.target_path)
    try:
        cfg.save()
        generate_compose(cfg)
    except (OSError, RuntimeError) as e:
        # Persistence failed AFTER copy succeeded. Keep target around;
        # don't remove the named volume so the user can roll back by
        # editing winpodx.toml back to storage_path = "".
        return MigrationResult(
            status="failed",
            detail=(
                f"copy succeeded but config / compose persist failed: {e}. "
                f"Target: {plan.target_path}. Original volume retained for rollback."
            ),
        )

    # 5. Start pod if requested. The named volume is intentionally NOT
    # removed yet — if the new bind-mount pod fails to start (e.g.,
    # the rsync copy was structurally fine but a permission / path
    # quirk shows up only at podman-up time), the user can edit
    # winpodx.toml's storage_path back to "" and recover the legacy
    # named-volume pod with no data loss. Removal happens in step 6
    # only after the new pod is verifiably running.
    if start_pod:
        try:
            from winpodx.core.provisioner import ensure_ready

            ensure_ready(cfg)
        except Exception as e:  # noqa: BLE001 — surface to caller via detail
            return MigrationResult(
                status="failed",
                detail=(
                    f"migration data moved but pod start failed: {e}. "
                    f"The legacy {plan.source_volume!r} volume is still in place. "
                    f"To roll back, edit ~/.config/winpodx/winpodx.toml and clear "
                    f'`storage_path = ""`, then re-run `winpodx pod start --wait`. '
                    f"Or retry: `winpodx setup --migrate-storage`."
                ),
            )

    # 6. Pod started successfully — only NOW it's safe to remove the
    # old named volume. Best-effort: a leftover volume is harmless;
    # the user can `podman volume rm winpodx-data` later.
    # Skipped entirely when start_pod=False (caller will start later
    # and can manually clean up after verification).
    if start_pod and shutil.which(plan.backend):
        try:
            subprocess.run(
                [plan.backend, "volume", "rm", plan.source_volume],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("could not remove old named volume %s: %s", plan.source_volume, e)

    base_detail = f"migrated {plan.source_size_bytes // (1 << 30)} GiB to {plan.target_path}"
    if cow_warnings:
        # Migration data-moved successfully, but NoCoW didn't take. The pod
        # works; the perf benefit is missing. Tag with status="ok_warn" so
        # callers can decide presentation, but keep status="ok" for the
        # critical "did it work" question. We surface the warnings inline
        # via print() above; the detail only carries a short summary so
        # the caller's success line stays readable.
        base_detail += f" — but NoCoW verification raised {len(cow_warnings)} warning(s); see above"
    return MigrationResult(status="ok", detail=base_detail)
