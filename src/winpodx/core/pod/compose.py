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
      TZ: "{timezone}"
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

    Two regimes:

    1. **Bundle dir is user-writable** (curl install, source checkout,
       Nix profile install -- anything where the user owns the bundle
       tree). Return bundle path directly. No copy needed: Podman's
       ``:Z`` relabel works fine because the user owns the source.
       Avoids the parent-dir traversal problem ``~/.config/winpodx/``
       under umask 077 introduces -- dockur's in-container OEM-copy
       step runs as a non-root sub-UID that can't traverse a 0700
       ancestor (PR #95 / #266 / #267 history).

    2. **Bundle dir is read-only to current user** (RPM/wheel install
       under ``/usr/share/winpodx/`` -- root-owned, world-readable
       only). Copy the OEM tree into ``~/.config/winpodx/oem/`` and
       return that. Necessary because rootless Podman can't lsetxattr
       root-owned files for ``:Z`` (pgarciaq's GH-93). Files are
       chmod'd 0644 + dirs 0755 after copy so dockur's in-container
       ``cp`` can read regardless of the user's umask.

    #254's original P1 (timezone wiring) collapsed both regimes into a
    single always-copy path so we could drop ``timezone.txt`` next to
    the OEM scripts. That re-introduced the parent-dir traversal
    problem on hosts with a 0700 ``~/.config/winpodx/`` (#266 / #267
    user report). Switched to dockur's native ``TZ`` env var for
    timezone wiring instead, which means the OEM dir no longer needs
    per-config content -- so we can restore the two-regime layout.

    Falls back to the user OEM path string when the bundle OEM dir is
    missing (broken install), so callers still get a path for error
    messages.
    """
    bundle_oem = bundle_dir() / "config" / "oem"

    # Case 1 -- user owns the bundle. Use it directly.
    if bundle_oem.is_dir() and os.access(bundle_oem, os.R_OK | os.W_OK):
        return str(bundle_oem)

    # Case 2 -- bundle is read-only (or missing). Copy into user space.
    user_oem = config_dir() / "oem"
    if not bundle_oem.is_dir():
        return str(user_oem)

    user_oem.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        os.chmod(user_oem, 0o755)
    except OSError:
        pass

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

    return str(user_oem)


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


def _resolve_timezone_for_compose(cfg: Config) -> str:
    """Resolve ``cfg.pod.timezone`` to the value passed to dockur's TZ env.

    dockur's ``TZ`` env var accepts an IANA name and translates it
    internally to the Windows ``<TimeZone>`` element written into the
    Sysprep unattend.xml. We just hand it whatever the user configured
    (or autodetect from the host if the field is empty).

    Empty string in -> host autodetect via :func:`utils.locale.detect_timezone`.
    Non-empty IANA name in -> pass through verbatim.
    Non-empty Windows TZ ID in (no ``/``) -> pass through. dockur tolerates
    Windows TZ IDs directly; older dockur builds may not, in which case
    users on niche territories the CLDR 001 wildcard doesn't cover need
    to set an IANA name instead.
    """
    raw = (cfg.pod.timezone or "").strip()
    if raw:
        return raw
    from winpodx.utils.locale import detect_timezone

    return detect_timezone()


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
        timezone=_yaml_escape(_resolve_timezone_for_compose(cfg)),
        rdp_port=cfg.rdp.port,
        vnc_port=cfg.pod.vnc_port,
        oem_dir=_find_oem_dir(),
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
