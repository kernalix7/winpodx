# SPDX-License-Identifier: MIT
"""Canonical URL-scheme policy for forward URL handling (#421 / #694).

Single source of truth shared by discovery (which schemes get registered as
``x-scheme-handler/<scheme>`` on the Linux side) and rdp (which URL args get
routed to the guest app instead of mapped to a ``$HOME`` UNC path). The guest
``discover_apps.ps1`` mirrors :data:`DANGEROUS_SCHEMES` + the regex verbatim as
a coarse advisory guard, but this module owns the authoritative policy.

Leaf module: imports only ``re`` (no import cycle -- rdp does not import
discovery and discovery does not import rdp).
"""

from __future__ import annotations

import re

# RFC 3986 scheme grammar: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ), length-
# bounded to 32. The leading-letter + no-colon rule structurally prevents
# ``javascript:alert()`` from masquerading as a compound scheme.
SAFE_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]{0,31}$")

# Schemes we never register a Windows app as a handler for, nor route to the
# guest: code-execution, local-file, and settings/diagnostic vectors. A
# denylist (not an allowlist) so legitimate vendor schemes (slack, vnc, msteams,
# zoommtg, spotify, com.vendor.app, ...) still route -- the real injection
# boundary is sanitize_url_arg, not this set.
DANGEROUS_SCHEMES: frozenset[str] = frozenset(
    {
        "file",
        "javascript",
        "vbscript",
        "data",
        "about",
        "shell",
        "res",
        "chrome",
        "chrome-extension",
        "ms-settings",
        "ms-msdt",
        "ms-search",
        "search-ms",
        "hcp",
        "its",
        "mk",
        "ldap",
        "help",
        "wscript",
        "cscript",
        "view-source",
    }
)


def is_safe_scheme(scheme: str) -> bool:
    """True if ``scheme`` is routable (syntactically valid + not dangerous).

    Case-insensitive; a trailing ``:`` is tolerated so callers can pass either
    ``"mailto"`` or ``"mailto:"``.
    """
    s = scheme.strip().rstrip(":").lower()
    return bool(SAFE_SCHEME_RE.fullmatch(s)) and s not in DANGEROUS_SCHEMES


def url_scheme_of(arg: str) -> str | None:
    """Return the routable scheme if ``arg`` is a ``scheme:...`` URL we should
    hand to the guest app, else ``None``.

    A file path (no colon, or a Linux path that merely contains one) and a
    ``file:`` URI both return ``None`` -- file: is in the denylist so file opens
    keep going through the ``\\tsclient\\home`` UNC path unchanged.
    """
    if not arg or ":" not in arg:
        return None
    scheme = arg.split(":", 1)[0].strip().lower()
    return scheme if is_safe_scheme(scheme) else None


def sanitize_url_arg(url: str) -> str:
    """Neutralise the FreeRDP ``/app`` cmd injection vectors in a URL.

    A comma splits a FreeRDP 3 ``/app:`` value into sub-keys, a double-quote
    wraps the cmd value, and CR/LF/control chars could smuggle extra argv --
    exactly the hazards ``build_rdp_command`` already strips from a UNC path.
    Drop C0 controls + DEL, and space-replace ``,`` and ``"``; the URL is
    otherwise passed through intact.
    """
    cleaned = "".join(ch for ch in url if 0x20 <= ord(ch) != 0x7F)
    return cleaned.replace(",", " ").replace('"', " ")
