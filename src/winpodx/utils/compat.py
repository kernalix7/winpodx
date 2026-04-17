"""Backward compatibility with winapps configuration.

Reads winapps.conf and converts to winpodx Config format,
allowing users to migrate without manual reconfiguration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from winpodx.core.config import Config

log = logging.getLogger(__name__)

WINAPPS_CONF_PATHS = [
    Path.home() / ".config" / "winapps" / "winapps.conf",
    Path("/etc/winapps/winapps.conf"),
]

FLAVOR_MAP = {
    "docker": "docker",
    "podman": "podman",
    "libvirt": "libvirt",
    "manual": "manual",
}


def find_winapps_conf() -> Path | None:
    """Locate an existing winapps.conf file."""
    for path in WINAPPS_CONF_PATHS:
        if path.exists():
            return path
    return None


def parse_winapps_conf(path: Path) -> dict[str, str]:
    """Parse a bash-style winapps.conf into a dict."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            values[key.strip()] = val
    return values


def import_winapps_config() -> Config | None:
    """Import winapps configuration into winpodx Config."""
    path = find_winapps_conf()
    if not path:
        return None

    vals = parse_winapps_conf(path)
    cfg = Config()

    cfg.rdp.user = vals.get("RDP_USER", "")
    cfg.rdp.password = vals.get("RDP_PASS", "")
    cfg.rdp.askpass = vals.get("RDP_ASKPASS", "")
    cfg.rdp.domain = vals.get("RDP_DOMAIN", "")
    cfg.rdp.ip = vals.get("RDP_IP", "127.0.0.1")

    # Filter RDP_FLAGS through the same allowlist used at runtime so that a
    # malicious winapps.conf cannot smuggle dangerous flags (e.g. /exec:) into
    # the stored config.  Removed flags are logged as warnings.
    raw_flags = vals.get("RDP_FLAGS", "")
    if raw_flags:
        from winpodx.core.rdp import _filter_extra_flags

        safe_flags = _filter_extra_flags(raw_flags)
        cfg.rdp.extra_flags = " ".join(safe_flags)
        if safe_flags != raw_flags.split():
            log.warning(
                "import_winapps_config: one or more RDP_FLAGS were blocked by the "
                "allowlist and removed from the imported config. "
                "Raw value: %r  Accepted: %r",
                raw_flags,
                cfg.rdp.extra_flags,
            )
    else:
        cfg.rdp.extra_flags = ""

    scale = vals.get("RDP_SCALE", "100")
    cfg.rdp.scale = int(scale) if scale.isdigit() else 100

    flavor = vals.get("WAFLAVOR", "docker")
    cfg.pod.backend = FLAVOR_MAP.get(flavor, "podman")
    cfg.pod.vm_name = vals.get("VM_NAME", "RDPWindows")

    return cfg
