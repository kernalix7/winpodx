"""Compose template generation for Podman/Docker backends."""

from __future__ import annotations

import os
import secrets
import shutil
import string
import tempfile
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.paths import bundle_dir, config_dir

_COMPOSE_TEMPLATE_BASE = """\
name: "winpodx"
volumes:
  winpodx-data:
services:
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
      LANGUAGE: "English"
      REGION: "en-001"
      KEYBOARD: "en-US"
      ARGUMENTS: "-cpu host,arch_capabilities=off"
      USER_PORTS: "8765"
    volumes:
      - winpodx-data:/storage:Z
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
       This is the common case and matches the pre-PR-#95 behavior.

    2. **Bundle dir is read-only to current user** (RPM/wheel install
       under ``/usr/share/winpodx/`` -- root-owned, world-readable
       only). Copy the OEM tree into ``~/.config/winpodx/oem/`` and
       return that. Necessary because rootless Podman can't lsetxattr
       root-owned files for ``:Z`` -- pgarciaq's GH-93. Files are
       chmod'd 0644 + dirs 0755 after copy so the dockur container
       process can read them regardless of the user's umask.

    Pre-this-fix (PR #95) the function *always* copied -- and dockur's
    cp inside the container then hit ``Permission denied`` on the
    user-OEM files when ``~/.config/winpodx/`` had a 0700 default
    parent mode. Branching on user-writability lets us keep the
    SELinux fix for Fedora/RPM users while not running the copy on
    systems where it isn't needed (the curl-install majority).

    Falls back to the user OEM path string when the bundle OEM dir is
    missing (broken install), so callers still get a path for error
    messages.
    """
    bundle_oem = bundle_dir() / "config" / "oem"

    # Case 1 -- user owns the bundle. Use it directly. No copy.
    # Compose's ``:Z`` relabel is a no-op or in-place relabel on
    # user-owned files; rootless Podman handles it fine.
    if bundle_oem.is_dir() and os.access(bundle_oem, os.R_OK | os.W_OK):
        return str(bundle_oem)

    # Case 2 -- bundle is read-only (or missing). Copy into user space
    # so ``:Z`` can relabel. Permissions explicitly set to 0644 / 0755
    # so dockur's in-container cp can read regardless of umask.
    user_oem = config_dir() / "oem"
    if not bundle_oem.is_dir():
        # Broken install: bundle dir doesn't exist at all. Nothing to
        # copy; return the user_oem path for the caller's error
        # message but don't create the dir.
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


def _build_compose_content(cfg: Config) -> str:
    """Build and return compose YAML content string for *cfg*."""
    password = cfg.rdp.password or generate_password()
    template = _build_compose_template(cfg.pod.backend)
    return template.format(
        ram=cfg.pod.ram_gb,
        cpu=cfg.pod.cpu_cores,
        container_name=cfg.pod.container_name,
        image=cfg.pod.image,
        disk_size=cfg.pod.disk_size,
        user=_yaml_escape(cfg.rdp.user),
        password=_yaml_escape(password),
        home=str(Path.home()),
        win_version=cfg.pod.win_version,
        rdp_port=cfg.rdp.port,
        vnc_port=cfg.pod.vnc_port,
        oem_dir=_find_oem_dir(),
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
