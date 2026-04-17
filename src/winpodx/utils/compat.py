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


_SCALE_MIN = 100
_SCALE_MAX = 400
_SCALE_DEFAULT = 100


def _parse_scale(raw: str) -> int:
    """Parse an RDP_SCALE value and clamp to [_SCALE_MIN, _SCALE_MAX].

    Accepts either:
      * an integer percentage like ``"140"`` → 140
      * a decimal multiplier like ``"1.5"`` (common in winapps docs) → 150

    Logs a warning when the value is non-numeric or outside the supported
    range and returns the default / clamped value. Empty input falls back
    silently to the default.
    """
    value = (raw or "").strip()
    if not value:
        return _SCALE_DEFAULT

    parsed: float
    try:
        parsed = float(value)
    except ValueError:
        log.warning(
            "RDP_SCALE=%r is not numeric, using %d",
            raw,
            _SCALE_DEFAULT,
        )
        return _SCALE_DEFAULT

    # Heuristic: values <= 10 are decimal multipliers (e.g. 1.5 for 150%);
    # larger values are percentages.
    percent = int(round(parsed * 100 if parsed <= 10 else parsed))

    if percent < _SCALE_MIN or percent > _SCALE_MAX:
        clamped = max(_SCALE_MIN, min(_SCALE_MAX, percent))
        log.warning(
            "RDP_SCALE=%r resolves to %d%% which is outside [%d, %d]; clamping to %d%%",
            raw,
            percent,
            _SCALE_MIN,
            _SCALE_MAX,
            clamped,
        )
        return clamped
    return percent


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
    # the stored config.
    #
    # Security policy: if ANY flag is filtered (i.e. the raw input contains a
    # flag the allowlist rejects), we refuse to store *any* extra_flags at all.
    # The user must explicitly set safe flags via manual config edit.  This
    # prevents a partially-trusted winapps.conf from silently inheriting even
    # the "safe" portion of a crafted RDP_FLAGS value.
    raw_flags = vals.get("RDP_FLAGS", "")
    if raw_flags:
        from winpodx.core.rdp import _filter_extra_flags

        safe_flags = _filter_extra_flags(raw_flags)
        raw_flag_list = raw_flags.split()
        if safe_flags != raw_flag_list:
            blocked = [f for f in raw_flag_list if f not in safe_flags]
            log.warning(
                "import_winapps_config: one or more RDP_FLAGS were blocked by the "
                "allowlist (%r). "
                "No extra_flags will be written to the imported config — "
                "review and add safe flags manually via: winpodx config set rdp.extra_flags '...'",
                blocked,
            )
            cfg.rdp.extra_flags = ""
        else:
            cfg.rdp.extra_flags = " ".join(safe_flags)
    else:
        cfg.rdp.extra_flags = ""

    # RDP_SCALE: accept either an integer ("140") or a float ("1.5" → 150).
    # Clamp to [100, 400] and warn on out-of-range — the previous
    # ``isdigit`` gate dropped non-digit input (including floats and stray
    # whitespace) silently back to 100, which masked user-configured DPI.
    cfg.rdp.scale = _parse_scale(vals.get("RDP_SCALE", "100"))

    flavor = vals.get("WAFLAVOR", "docker")
    cfg.pod.backend = FLAVOR_MAP.get(flavor, "podman")
    cfg.pod.vm_name = vals.get("VM_NAME", "RDPWindows")

    return cfg
