# SPDX-License-Identifier: MIT
"""Scan the Linux host's installed apps for reverse-open registration.

Walks the XDG_DATA_HOME / XDG_DATA_DIRS application directories the same
way ``xdg-open`` does, collects each ``.desktop`` entry that handles a
MIME type, and returns a normalised :class:`LinuxApp` list that
``icons.py`` and the host-side sync layer can consume.

This module is read-only. It never spawns the apps it discovers, never
writes files outside the caller's pyloglevel, and validates the input
heavily because a malicious ``.desktop`` in
``~/.local/share/applications/`` is already game-over for the user — but
we will not be the vector that runs it. The discovery layer rejects
entries whose ``Exec=`` line contains shell metacharacters; entries
that try to invoke winpodx itself (recursion defence); entries from
Wine / winapps wrappers (already wrap Windows binaries, no point
double-wrapping them through the guest); and entries that don't
actually advertise a MIME-handler relationship via ``MimeType=``.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"Component contracts →
discovery.py" for the full contract.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# Slug grammar shared with :mod:`config` — keep the regex in sync.
_SLUG_CHARS_RE = re.compile(r"[^a-z0-9-]")

# Field codes the spec defines on the Exec= line. We strip the
# always-unused ones at discovery time; %f / %u / %F / %U survive and
# are substituted by the listener at spawn time.
_DROP_FIELD_CODES = ("%c", "%k", "%i", "%v", "%m", "%d", "%D", "%n", "%N")

# Anything that looks like shell metacharacter or chained-command
# punctuation in Exec= is rejected. We use a conservative set: a
# legitimate .desktop file invokes a single program with literal argv
# (post field-code substitution). Anything else routes through a shell
# or fork, which we won't drive on behalf of a remote guest.
_DANGEROUS_EXEC_CHARS_RE = re.compile(r"[`$;&|<>\n]|\|\||&&")

# Prefixes that mean "this .desktop already wraps something — don't
# wrap it again". Conservative match against the FIRST argv token after
# Exec= is split.
_WRAPPER_EXEC_PREFIXES = (
    "wine",
    "wine64",
    "wine-stable",
    "winapps",
    "winpodx",
    "winpodx-run",
    "winpodx-app",
)

# Prefix on the Name= field that winpodx itself uses for its generated
# Windows-app .desktop entries. We refuse to re-discover our own
# output (recursion defence).
_WINPODX_NAME_PREFIX = "Windows: "


@dataclass(frozen=True)
class LinuxApp:
    """One discovered host app that handles at least one MIME type."""

    slug: str
    name: str
    comment: str
    exec_argv: list[str]
    icon_name: str
    mime_types: list[str]
    desktop_file: Path
    is_default_for: list[str] = field(default_factory=list)


def slug_for_desktop(path: Path) -> str:
    """Derive a stable slug from a ``.desktop`` file path.

    ``/usr/share/applications/org.kde.kate.desktop`` → ``org-kde-kate``.
    Lowercased, dots replaced with dashes, anything else outside the
    ``[a-z0-9-]`` set is dropped. Matches the regex
    :data:`winpodx.reverse_open.config._SLUG_RE` consumers expect.
    """
    stem = path.stem.lower().replace(".", "-")
    return _SLUG_CHARS_RE.sub("", stem)


def _xdg_application_dirs() -> list[Path]:
    """Return the XDG application directories in lookup order.

    ``$XDG_DATA_HOME/applications`` first (user override; defaults to
    ``~/.local/share/applications``), then each entry of
    ``$XDG_DATA_DIRS`` (defaults to ``/usr/local/share:/usr/share``) in
    listed order. Non-existent directories are kept in the list so the
    caller can see what was checked — the scan loop itself skips them.
    """
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(home) / "applications"]
    for d in system.split(":"):
        d = d.strip()
        if not d:
            continue
        dirs.append(Path(d) / "applications")
    return dirs


def _current_desktops() -> frozenset[str]:
    """Return ``XDG_CURRENT_DESKTOP`` split on ``:`` as a set.

    Used to evaluate ``OnlyShowIn=`` / ``NotShowIn=`` filters per the
    Desktop Entry Specification. Empty set means "we don't know the
    desktop" — treat as no filter (matches gnome-shell's behaviour).
    """
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(":") if part.strip())


def _strip_field_codes(argv: list[str]) -> list[str]:
    """Drop field codes from an argv list except %f/%u/%F/%U.

    The four placeholders we keep are substituted by the listener at
    spawn time with the requested path. The rest (%c name, %k path of
    the .desktop itself, %i pair of --icon ICON tokens, deprecated %v
    / %m / %d / %D / %n / %N) carry no actionable info for a reverse-
    open invocation and just bloat argv.
    """
    out: list[str] = []
    for tok in argv:
        # Drop tokens that ARE a dropped field code in their entirety.
        if tok in _DROP_FIELD_CODES:
            continue
        # Sometimes field codes are concatenated to filenames, e.g.
        # `kate %f`. We only drop whole-token codes; partial matches
        # would risk stripping legitimate substrings. The spec lets
        # codes appear only as separate tokens, so this is safe.
        out.append(tok)
    return out


def _parse_desktop(path: Path) -> dict[str, str] | None:
    """Parse the ``[Desktop Entry]`` section of a ``.desktop`` file.

    Returns the section's key/value pairs as a flat dict, or ``None``
    if the file isn't readable, lacks a ``[Desktop Entry]`` section, or
    fails to parse. We use the stdlib :mod:`configparser` with
    ``interpolation=None`` so ``%`` in values is treated literally —
    the spec uses ``%`` for field codes, not interpolation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    parser = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        delimiters=("=",),
        comment_prefixes=("#",),
    )
    parser.optionxform = str  # preserve case on keys
    try:
        parser.read_string(text)
    except configparser.Error:
        return None

    if "Desktop Entry" not in parser:
        return None
    return dict(parser["Desktop Entry"])


def _is_displayed(entry: dict[str, str], desktops: frozenset[str]) -> bool:
    """Apply the Hidden / NoDisplay / OnlyShowIn / NotShowIn filters."""
    if entry.get("Hidden", "false").strip().lower() == "true":
        return False
    if entry.get("NoDisplay", "false").strip().lower() == "true":
        return False
    if desktops:
        only = entry.get("OnlyShowIn", "").strip()
        if only:
            allowed = frozenset(p.strip() for p in only.split(";") if p.strip())
            if not (allowed & desktops):
                return False
        not_in = entry.get("NotShowIn", "").strip()
        if not_in:
            blocked = frozenset(p.strip() for p in not_in.split(";") if p.strip())
            if blocked & desktops:
                return False
    return True


def _resolve_try_exec(value: str) -> bool:
    """Return True if a ``TryExec=`` binary is reachable on PATH or absolute."""
    value = value.strip()
    if not value:
        return True  # spec: missing TryExec means "always try Exec"
    if os.path.isabs(value):
        return os.access(value, os.X_OK)
    # Resolve relative names against PATH.
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.access(os.path.join(d, value), os.X_OK):
            return True
    return False


def _exec_is_safe(exec_line: str, argv: Sequence[str]) -> bool:
    """Reject Exec= lines that look like shell command lines.

    Defence in depth — we never feed Exec= to a shell, but a future
    refactor that does should not be able to introduce a smuggling
    avenue via this discovery layer. If a discovered file looks like
    something a shell would interpret, we skip it at scan time so it
    never reaches the apps_db.
    """
    if _DANGEROUS_EXEC_CHARS_RE.search(exec_line):
        return False
    if not argv:
        return False
    head = os.path.basename(argv[0]).lower()
    if head in _WRAPPER_EXEC_PREFIXES:
        return False
    return True


def _split_mimes(value: str) -> list[str]:
    """Split a ``MimeType=`` value into a list, lowercased + trimmed."""
    return [m.strip().lower() for m in value.split(";") if m.strip()]


def _mimeapps_candidates() -> list[Path]:
    """Return every ``mimeapps.list`` path the freedesktop spec defines.

    Walks the full canonical search order so we honour defaults set
    anywhere a desktop-environment or distro packaging convention puts
    them — not just the per-user files. For each base directory below,
    we check the desktop-prefix variant (one per
    ``XDG_CURRENT_DESKTOP`` colon-component, lowercased) followed by
    plain ``mimeapps.list``. The base-dir order, from highest to
    lowest precedence:

      1. ``$XDG_CONFIG_HOME``                                 (user)
      2. Each entry of ``$XDG_CONFIG_DIRS``                   (system config)
      3. ``$XDG_DATA_HOME/applications``                      (user, legacy)
      4. Each entry of ``$XDG_DATA_DIRS/applications``        (system data)

    Defaults follow freedesktop:

      * ``XDG_CONFIG_HOME`` → ``~/.config``
      * ``XDG_CONFIG_DIRS`` → ``/etc/xdg``
      * ``XDG_DATA_HOME``  → ``~/.local/share``
      * ``XDG_DATA_DIRS``  → ``/usr/local/share:/usr/share``

    The caller merges first-definition-wins so user-level always
    shadows system-level — matching xdg-mime's own resolution. This
    is how distros that ship a system-wide ``kde-mimeapps.list``
    (e.g. ``/usr/share/applications/kde-mimeapps.list`` with
    ``text/plain=org.kde.kate.desktop``) get picked up, and why
    GNOME-on-Wayland with no user override still resolves to its
    system-default text editor.
    """
    home = os.path.expanduser("~")

    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    cfg_dirs_raw = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    cfg_dirs = [d for d in cfg_dirs_raw.split(":") if d]

    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    data_dirs_raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    data_dirs = [d for d in data_dirs_raw.split(":") if d]

    desktops = [
        p.strip().lower() for p in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if p.strip()
    ]

    def _expand(base: Path) -> list[Path]:
        """Desktop-prefix variants (each component of XDG_CURRENT_DESKTOP)
        followed by the plain mimeapps.list under ``base``."""
        out: list[Path] = []
        for d in desktops:
            out.append(base / f"{d}-mimeapps.list")
        out.append(base / "mimeapps.list")
        return out

    paths: list[Path] = []
    # 1. user config
    paths.extend(_expand(Path(cfg_home)))
    # 2. system config (XDG_CONFIG_DIRS)
    for d in cfg_dirs:
        paths.extend(_expand(Path(d)))
    # 3. user data (legacy applications/ location)
    paths.extend(_expand(Path(data_home) / "applications"))
    # 4. system data (XDG_DATA_DIRS)
    for d in data_dirs:
        paths.extend(_expand(Path(d) / "applications"))
    return paths


def _read_default_handlers() -> dict[str, str]:
    """Read ``mimeapps.list`` for the user's per-MIME default handlers.

    The ``[Default Applications]`` section maps each MIME type to a
    ``.desktop`` basename. We walk the spec's full candidate list
    (see :func:`_mimeapps_candidates`) and merge — first definition
    wins, matching how `xdg-mime` itself resolves defaults.
    """
    merged: dict[str, str] = {}
    for path in _mimeapps_candidates():
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=False,
            delimiters=("=",),
            comment_prefixes=("#",),
        )
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            continue
        if "Default Applications" not in parser:
            continue
        for mime, desktop in parser["Default Applications"].items():
            mime_key = mime.strip().lower()
            if mime_key in merged:
                continue  # earlier file shadows
            first = desktop.split(";", 1)[0].strip()
            if first:
                merged[mime_key] = first
    return merged


def discover_apps(
    *,
    extra_dirs: Iterable[Path] | None = None,
    include_nodisplay: bool = False,
) -> list[LinuxApp]:
    """Walk the XDG application directories and return mime-handling apps.

    Args:
      extra_dirs: additional directories to scan AFTER the standard XDG
        chain. Useful for tests and for vendored flatpak-export dirs the
        user may want to include explicitly.
      include_nodisplay: include entries marked ``NoDisplay=true``. Off
        by default — those entries are typically protocol handlers /
        URL openers that don't make sense in a guest right-click menu.

    Returns:
      A list of :class:`LinuxApp`, deduplicated by basename (first hit
      wins, matching xdg-open shadowing semantics). Stable order:
      sorted by slug.
    """
    desktops = _current_desktops()
    defaults = _read_default_handlers()

    seen_basenames: set[str] = set()
    apps: dict[str, LinuxApp] = {}

    dirs: list[Path] = list(_xdg_application_dirs())
    if extra_dirs:
        dirs.extend(extra_dirs)

    for d in dirs:
        if not d.is_dir():
            continue
        try:
            files = sorted(p for p in d.iterdir() if p.suffix == ".desktop")
        except OSError as exc:
            log.debug("discovery: cannot list %s: %s", d, exc)
            continue
        for path in files:
            basename = path.name
            if basename in seen_basenames:
                continue
            seen_basenames.add(basename)

            entry = _parse_desktop(path)
            if entry is None:
                continue
            if entry.get("Type", "Application").strip() != "Application":
                continue

            mime_value = entry.get("MimeType", "").strip()
            if not mime_value:
                continue

            if not include_nodisplay and not _is_displayed(entry, desktops):
                continue
            if include_nodisplay and entry.get("Hidden", "false").strip().lower() == "true":
                # Even with --include-nodisplay, Hidden=true is a tombstone.
                continue

            try_exec = entry.get("TryExec", "").strip()
            if try_exec and not _resolve_try_exec(try_exec):
                continue

            exec_line = entry.get("Exec", "").strip()
            if not exec_line:
                continue
            try:
                argv = shlex.split(exec_line, posix=True)
            except ValueError:
                # Unbalanced quotes etc. — skip rather than guess.
                continue
            argv = _strip_field_codes(argv)
            if not _exec_is_safe(exec_line, argv):
                continue

            name = (entry.get("Name") or "").strip() or path.stem
            if name.startswith(_WINPODX_NAME_PREFIX):
                continue
            comment = (entry.get("Comment") or "").strip()
            icon_name = (entry.get("Icon") or "").strip()

            mime_types = _split_mimes(mime_value)
            if not mime_types:
                continue

            slug = slug_for_desktop(path)
            is_default_for = sorted(m for m in mime_types if defaults.get(m) == basename)

            apps[slug] = LinuxApp(
                slug=slug,
                name=name,
                comment=comment,
                exec_argv=argv,
                icon_name=icon_name,
                mime_types=sorted(set(mime_types)),
                desktop_file=path,
                is_default_for=is_default_for,
            )

    return [apps[k] for k in sorted(apps)]
