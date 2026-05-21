# SPDX-License-Identifier: MIT
"""Compose template generation for Podman/Docker backends."""

from __future__ import annotations

import os
import platform
import secrets
import shutil
import string
import tempfile
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.paths import bundle_dir, config_dir

# Two storage-volume modes are now supported (v0.4.x post-#122):
#
#   1. Named volume (legacy default for users created before storage_path
#      existed). compose has a top-level `volumes:` section declaring
#      `winpodx-data:` and the service mounts it as `winpodx-data:/storage:Z`.
#      The Windows raw disk image lives at podman's graph-root volume path,
#      which on btrfs hosts inherits Copy-on-Write — the cause of #121,
#      #122. Existing users keep this mode until they explicitly run
#      `winpodx setup --migrate-storage`.
#
#   2. Bind mount (default for fresh installs). compose has NO top-level
#      `volumes:` section; the service mounts `<storage_path>:/storage:Z`
#      directly from a host-local directory winpodx owns
#      (`~/.local/share/winpodx/storage` by default). Setup applies
#      `chattr +C` on that directory before populating it, so the Windows
#      raw disk image inherits NoCoW from the parent directory at the
#      moment dockur first writes to it. Other podman workloads on the
#      same host are completely unaffected — the +C flag only touches
#      our specific directory.
#
# `_render_storage_blocks` chooses between the two based on
# `cfg.pod.storage_path`: empty → named volume, non-empty → bind mount.

_COMPOSE_TEMPLATE_BASE = """\
name: "winpodx"
{top_volumes}services:
  windows:
    image: {image}
    container_name: {container_name}
    environment:
      VERSION: "{win_version}"
      RAM_SIZE: "{ram}G"
      CPU_CORES: "{cpu}"
      DISK_SIZE: "{disk_size}"
      USERNAME: "{user}"
      PASSWORD: "{password}"
      HOME: "{home}"
      LANGUAGE: "{language}"
      REGION: "{region}"
      KEYBOARD: "{keyboard}"
      ARGUMENTS: "{qemu_arguments}"
      USER_PORTS: "8765"
    volumes:
      - {storage_mount}
      - {oem_dir}:/oem:Z
    ports:
      - "127.0.0.1:{rdp_port}:3389/tcp"
      - "127.0.0.1:{rdp_port}:3389/udp"
      - "127.0.0.1:{vnc_port}:8006"
      - "127.0.0.1:8765:8765/tcp"
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN
"""

_COMPOSE_PODMAN_EXTRAS = """\
    group_add:
      - keep-groups
    annotations:
      run.oci.keep_original_groups: "1"
"""

_COMPOSE_TEMPLATE_FOOTER = """\
    stop_grace_period: 2m
    restart: unless-stopped
"""


def _build_compose_template(backend: str) -> str:
    """Assemble the compose template string for the given backend."""
    template = _COMPOSE_TEMPLATE_BASE
    if backend == "podman":
        template += _COMPOSE_PODMAN_EXTRAS
    template += _COMPOSE_TEMPLATE_FOOTER
    return template


def _qemu_arguments_for_host(cfg: Config | None = None) -> str:
    """Return the QEMU ``ARGUMENTS:`` value for the host + tuning profile.

    On x86_64 we pass ``arch_capabilities=off`` so QEMU doesn't expose
    Intel-CPU-only capability bits the guest's Windows kernel sometimes
    trips over. On aarch64 (Raspberry Pi 5, Ampere, Graviton, …) that
    sub-option doesn't exist — passing it crashes QEMU with
    ``Property 'host-arm-cpu.arch_capabilities' not found`` (issue
    #140); we pass only ``-cpu host`` there.

    When ``cfg`` is given and ``cfg.pod.tuning_profile`` resolves to a
    profile with ``apply_invtsc`` set, ``+invtsc`` is appended on
    x86_64 so the Windows guest sees an invariant TSC clocksource (#215).
    aarch64 ignores the profile because invtsc is x86-specific.
    """
    if platform.machine() == "aarch64":
        return "-cpu host"

    cpu = "-cpu host,arch_capabilities=off"
    if cfg is None:
        return cpu

    from winpodx.utils.specs import detect_tuning_capability, recommend_tuning_profile

    cap = detect_tuning_capability(vm_cpu_cores=cfg.pod.cpu_cores, vm_ram_gb=cfg.pod.ram_gb)
    profile = recommend_tuning_profile(cap, user_pref=cfg.pod.tuning_profile)
    if profile.apply_invtsc:
        cpu += ",+invtsc"
    return cpu


def _yaml_escape(val: str) -> str:
    """Escape a value for safe embedding in a YAML double-quoted string."""
    return (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("{", "{{")
        .replace("}", "}}")
    )


def _find_oem_dir() -> str:
    """Return a user-writable OEM directory for the compose bind mount.

    Back-compat wrapper around :func:`_prepare_oem_dir` for callers
    that don't have a Config in hand (e.g. setup_cmd's agent-token
    staging before the wizard has finished). Equivalent to
    ``_prepare_oem_dir(cfg=None)``.
    """
    return _prepare_oem_dir(cfg=None)


def _prepare_oem_dir(cfg: Config | None) -> str:
    """Return a user-writable OEM directory, populated for *cfg*.

    Always uses ``~/.config/winpodx/oem`` and copies the bundle OEM
    tree in (idempotent via ``dirs_exist_ok``). Pre-#254 the function
    had a fast-path that returned the bundle dir directly when it was
    user-writable, but #254 needs to drop per-config files into the
    OEM dir (``timezone.txt`` for the OEM ``tzutil`` step) without
    touching the source bundle, so the always-copy path is now the
    single regime.

    With *cfg* provided, also writes:

    * ``timezone.txt`` -- single line, Windows TZ ID resolved from
      ``cfg.pod.timezone`` via :func:`utils.locale.resolve_timezone_for_oem`.
      Empty when resolution returns ``"UTC"`` AND ``cfg.pod.timezone``
      was explicitly empty (host autodetect found nothing usable) -- we
      skip the file in that case so install.bat short-circuits past
      the tzutil step instead of forcing UTC on guests that may have
      a sensible default.

    Permissions on copied files: 0644 / 0755. Necessary because dockur's
    in-container ``cp`` runs as a non-root user the first time it pulls
    /oem into C:\\OEM; a default 0600 umask blocks the copy and
    install.bat never runs.

    Falls back to the empty user OEM path when the bundle OEM dir is
    missing (broken install), so callers still get a path for error
    messages.
    """
    bundle_oem = bundle_dir() / "config" / "oem"
    user_oem = config_dir() / "oem"

    user_oem.mkdir(parents=True, exist_ok=True, mode=0o755)
    # Explicit chmod on user_oem AND its parent ``~/.config/winpodx/``.
    # ``mkdir(mode=0o755)`` is umask-adjusted, so on a host with the
    # default umask 077 we'd get 0o700 and dockur's in-container ``cp``
    # (running as a non-root user) couldn't traverse the parent to
    # reach /oem at all -- the symptom is ``cp: cannot stat
    # '/oem/./<file>': Permission denied`` for every file at first
    # boot. PR #95 originally dodged this by returning the bundle dir
    # directly when user-writable; #254 P1 had to drop that fast path
    # to land per-config files (timezone.txt) without polluting the
    # bundle, so we re-establish the traversal perms explicitly.
    try:
        os.chmod(user_oem, 0o755)
    except OSError:
        pass
    try:
        os.chmod(user_oem.parent, 0o755)
    except OSError:
        pass

    if bundle_oem.is_dir():
        for item in bundle_oem.iterdir():
            dest = user_oem / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
                for root_dir, _dirs, files in os.walk(dest):
                    root_path = Path(root_dir)
                    try:
                        os.chmod(root_path, 0o755)
                    except OSError:
                        pass
                    for f in files:
                        try:
                            os.chmod(root_path / f, 0o644)
                        except OSError:
                            pass
            else:
                shutil.copy2(item, dest)
                try:
                    os.chmod(dest, 0o644)
                except OSError:
                    pass

    if cfg is not None:
        _write_oem_timezone(user_oem, cfg)

    return str(user_oem)


def _write_oem_timezone(oem_dir: Path, cfg: Config) -> None:
    """Drop ``oem_dir/timezone.txt`` with the Windows TZ ID for install.bat.

    Resolution rules and skip-conditions live in
    :func:`utils.locale.resolve_timezone_for_oem`. We treat
    ``cfg.pod.timezone == ""`` AND a resolved value of ``"UTC"`` as
    "host detection failed; let install.bat skip the tzutil step
    rather than force UTC", because most users on UTC-via-detection-
    failure are actually on a real zone and we'd rather not silently
    change their system clock to UTC. An explicit ``timezone = "UTC"``
    in the TOML, or any other resolved value, is written verbatim.
    """
    from winpodx.utils.locale import resolve_timezone_for_oem

    configured = (cfg.pod.timezone or "").strip()
    win_tz = resolve_timezone_for_oem(configured)

    target = oem_dir / "timezone.txt"
    # Detection fell all the way back to UTC: don't write the file so
    # install.bat skips the tzutil call. Users on UTC who *want* UTC
    # set it explicitly in the TOML and we honour that.
    if not configured and win_tz == "UTC":
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return

    try:
        target.write_text(win_tz + "\n", encoding="utf-8")
        os.chmod(target, 0o644)
    except OSError as e:
        # Non-fatal: install.bat will skip tzutil if the file is
        # missing, and the guest stays on its current TZ. Surface
        # the failure to the user in logs so they can diagnose.
        import logging

        logging.getLogger(__name__).warning(
            "could not write %s: %s; Windows guest TZ will stay on its current value",
            target,
            e,
        )


def _render_storage_blocks(cfg: Config) -> tuple[str, str]:
    """Return ``(top_volumes_block, storage_mount_line)`` for the compose template.

    - ``top_volumes_block`` is either an empty string (bind-mount mode,
      no top-level ``volumes:`` section) or
      ``"volumes:\\n  winpodx-data:\\n"`` (named-volume mode).
    - ``storage_mount_line`` is the value that goes after ``- `` in the
      service ``volumes:`` list — either ``"winpodx-data:/storage:Z"`` or
      ``"<expanded_storage_path>:/storage:Z"``.

    The choice keys off ``cfg.pod.storage_path``:

    - empty string → named volume (legacy compat for users who installed
      before the field existed).
    - non-empty → bind mount at that absolute path (after ``~`` expansion).

    Bind-mount paths are written verbatim into compose (no YAML escape)
    because the field is a controlled filesystem path generated by
    ``winpodx setup``, not free-form user input. We do reject obviously
    unsafe values (containing newlines, leading whitespace, or unbalanced
    braces) by falling back to the named volume.
    """
    raw = (cfg.pod.storage_path or "").strip()
    if not raw:
        return "volumes:\n  winpodx-data:\n", "winpodx-data:/storage:Z"

    # Defence against hand-edited config: a path with newline / quote /
    # brace would corrupt the compose YAML; a colon would split as a
    # second mount target (e.g., `/tmp/x:/etc/shadow` would mount
    # /etc/shadow under /storage:Z). Drop to named volume on any of
    # these. Linux bind-mount paths legitimately never need any of
    # these characters.
    if any(c in raw for c in "\n\r\"':") or "{" in raw or "}" in raw:
        return "volumes:\n  winpodx-data:\n", "winpodx-data:/storage:Z"

    expanded = str(Path(raw).expanduser())
    return "", f"{expanded}:/storage:Z"


def _build_compose_content(cfg: Config) -> str:
    """Build and return compose YAML content string for *cfg*."""
    password = cfg.rdp.password or generate_password()
    template = _build_compose_template(cfg.pod.backend)
    top_volumes, storage_mount = _render_storage_blocks(cfg)
    # All string fields that land inside a YAML double-quoted scalar
    # MUST pass through _yaml_escape — otherwise a hand-edited TOML
    # value (or a --win-version flag argument) containing ``"``, ``\n``,
    # ``\\``, or ``$`` could break out of its scalar and inject
    # arbitrary env keys into the dockur service. Defense in depth:
    # PodConfig.__post_init__ also rejects these characters, but the
    # escape here is the last line of defence on the YAML boundary.
    return template.format(
        ram=cfg.pod.ram_gb,
        cpu=cfg.pod.cpu_cores,
        container_name=_yaml_escape(cfg.pod.container_name),
        image=_yaml_escape(cfg.pod.image),
        disk_size=_yaml_escape(cfg.pod.disk_size),
        user=_yaml_escape(cfg.rdp.user),
        password=_yaml_escape(password),
        home=str(Path.home()),
        win_version=_yaml_escape(cfg.pod.win_version),
        language=_yaml_escape(cfg.pod.language),
        region=_yaml_escape(cfg.pod.region),
        keyboard=_yaml_escape(cfg.pod.keyboard),
        rdp_port=cfg.rdp.port,
        vnc_port=cfg.pod.vnc_port,
        oem_dir=_prepare_oem_dir(cfg),
        top_volumes=top_volumes,
        storage_mount=storage_mount,
        qemu_arguments=_qemu_arguments_for_host(cfg),
    )


def generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password."""
    # '$' excluded: PowerShell expands it as a variable sigil in OEM scripts.
    _SPECIALS = "!@#%&*"
    alphabet = string.ascii_letters + string.digits + _SPECIALS
    pw = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_SPECIALS),
    ]
    pw += [secrets.choice(alphabet) for _ in range(length - 4)]
    result = list(pw)
    secrets.SystemRandom().shuffle(result)
    return "".join(result)


def generate_compose(cfg: Config) -> None:
    """Generate compose.yaml for Podman/Docker backend (atomic write)."""
    compose_path = config_dir() / "compose.yaml"
    compose_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_compose_content(cfg)

    fd, tmp_path = tempfile.mkstemp(dir=compose_path.parent, prefix=".compose-", suffix=".tmp")
    fd_closed = False
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, content.encode("utf-8"))
        # fsync before rename to avoid a zero-byte file after a crash.
        os.fsync(fd)
        os.close(fd)
        fd_closed = True
        os.replace(tmp_path, str(compose_path))
    except Exception:
        if not fd_closed:
            os.close(fd)
        Path(tmp_path).unlink(missing_ok=True)
        raise


def generate_compose_to(cfg: Config, dest: Path) -> None:
    """Write compose YAML for *cfg* to *dest* (used for atomic rotation)."""
    content = _build_compose_content(cfg)
    os.chmod(dest, 0o600)
    dest.write_bytes(content.encode("utf-8"))
