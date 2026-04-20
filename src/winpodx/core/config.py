"""Configuration management for winpodx."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from winpodx.utils.paths import config_dir
from winpodx.utils.toml_writer import dumps as toml_dumps

_VALID_BACKENDS = frozenset({"podman", "docker", "libvirt", "manual"})

# Podman/Docker container name rules: alnum/_/-/., must start with alnum.
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DEFAULT_CONTAINER_NAME = "winpodx-windows"


@dataclass
class RDPConfig:
    user: str = ""
    password: str = ""
    password_updated: str = ""  # ISO 8601 timestamp
    password_max_age: int = 7  # days, 0 = disable rotation
    askpass: str = ""
    domain: str = ""
    ip: str = "127.0.0.1"
    port: int = 3390
    scale: int = 100
    dpi: int = 0  # Windows DPI %, 0 = auto-detect from Linux
    extra_flags: str = ""

    def __post_init__(self) -> None:
        self.port = max(1, min(65535, int(self.port)))
        self.scale = max(100, min(500, int(self.scale)))
        self.dpi = max(0, min(500, int(self.dpi)))
        self.password_max_age = max(0, int(self.password_max_age))


@dataclass
class PodConfig:
    backend: str = "podman"  # podman | docker | libvirt | manual
    vm_name: str = "RDPWindows"
    container_name: str = "winpodx-windows"
    win_version: str = "11"  # 11 | 10 | ltsc10 | tiny11 | tiny10
    cpu_cores: int = 4
    ram_gb: int = 4
    vnc_port: int = 8007
    auto_start: bool = True
    idle_timeout: int = 0  # 0 = disabled
    boot_timeout: int = 300  # seconds, max wait for RDP after start_pod
    # Container image for dockur/windows. Expose as config so users can
    # pin a known-good tag or switch to a mirror.
    image: str = "ghcr.io/dockur/windows:latest"
    # Virtual disk size exposed in the compose template (e.g. "64G", "128G").
    disk_size: str = "64G"
    # SHA-256 of (install.bat + oem_updater.ps1) last pushed into the VM.
    last_oem_push: str = ""

    def __post_init__(self) -> None:
        if self.backend not in _VALID_BACKENDS:
            self.backend = "podman"
        self.cpu_cores = max(1, min(128, int(self.cpu_cores)))
        self.ram_gb = max(1, min(512, int(self.ram_gb)))
        self.vnc_port = max(1, min(65535, int(self.vnc_port)))
        self.idle_timeout = max(0, int(self.idle_timeout))
        self.boot_timeout = max(30, min(3600, int(self.boot_timeout)))
        if not isinstance(self.container_name, str) or not _CONTAINER_NAME_RE.match(
            self.container_name
        ):
            # Fall back silently so a hand-edited config does not brick setup.
            self.container_name = _DEFAULT_CONTAINER_NAME
        if not isinstance(self.image, str) or not self.image.strip():
            self.image = "ghcr.io/dockur/windows:latest"
        if not isinstance(self.disk_size, str) or not self.disk_size.strip():
            self.disk_size = "64G"


@dataclass
class Config:
    rdp: RDPConfig = field(default_factory=RDPConfig)
    pod: PodConfig = field(default_factory=PodConfig)

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "winpodx.toml"

    @classmethod
    def load(cls) -> Config:
        """Load config from TOML file, falling back to defaults."""
        import logging

        path = cls.path()
        cfg = cls()
        if not path.exists():
            return cfg

        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, PermissionError) as e:
            logging.getLogger(__name__).warning("Corrupted config %s, using defaults: %s", path, e)
            return cfg

        _apply(cfg.rdp, data.get("rdp", {}))
        _apply(cfg.pod, data.get("pod", {}))
        return cfg

    def save(self) -> None:
        """Write current config to TOML file with secure permissions."""
        import os
        import tempfile

        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "rdp": {
                "user": self.rdp.user,
                "password": self.rdp.password,
                "password_updated": self.rdp.password_updated,
                "password_max_age": self.rdp.password_max_age,
                "askpass": self.rdp.askpass,
                "domain": self.rdp.domain,
                "ip": self.rdp.ip,
                "port": self.rdp.port,
                "scale": self.rdp.scale,
                "dpi": self.rdp.dpi,
                "extra_flags": self.rdp.extra_flags,
            },
            "pod": {
                "backend": self.pod.backend,
                "vm_name": self.pod.vm_name,
                "container_name": self.pod.container_name,
                "win_version": self.pod.win_version,
                "cpu_cores": self.pod.cpu_cores,
                "ram_gb": self.pod.ram_gb,
                "vnc_port": self.pod.vnc_port,
                "auto_start": self.pod.auto_start,
                "idle_timeout": self.pod.idle_timeout,
                "boot_timeout": self.pod.boot_timeout,
                "image": self.pod.image,
                "disk_size": self.pod.disk_size,
                "last_oem_push": self.pod.last_oem_push,
            },
        }

        # Atomic write: create temp file with 0600, fsync, then rename.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".winpodx-", suffix=".tmp")
        fd_closed = False
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, toml_dumps(data).encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            fd_closed = True
            os.replace(tmp_path, path)
            # Best-effort parent directory fsync so the rename itself is durable.
            try:
                dir_fd = os.open(path.parent, os.O_DIRECTORY)
            except OSError:
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                except OSError:
                    pass
                finally:
                    os.close(dir_fd)
        except Exception:
            if not fd_closed:
                os.close(fd)
            Path(tmp_path).unlink(missing_ok=True)
            raise


def _apply(obj: Any, data: dict[str, Any]) -> None:
    """Apply dict values to a dataclass instance, with type checking."""
    import dataclasses
    import logging

    log = logging.getLogger(__name__)
    allowed = {f.name for f in dataclasses.fields(obj)}
    for key, val in data.items():
        if key not in allowed:
            continue
        expected = type(getattr(obj, key))
        if expected is not type(None) and not isinstance(val, expected):
            try:
                if expected is bool and isinstance(val, str):
                    val = val.lower() in ("true", "1", "yes")
                else:
                    val = expected(val)
            except (ValueError, TypeError):
                log.warning(
                    "Config key %r: cannot coerce %r to %s, using default",
                    key,
                    val,
                    expected.__name__,
                )
                continue
        setattr(obj, key, val)
