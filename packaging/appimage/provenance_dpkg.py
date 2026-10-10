# SPDX-License-Identifier: MIT
"""Fail-closed dpkg database parsing for provenance collection (Ubuntu 24.04).

Parses the conveyed-files manifest, the dpkg status database, the
``<pkg>.list`` owner maps (merged-/usr aware), and Debian copyright
files.  Every parser fails closed on malformed or unknown input --
nothing is invented or silently defaulted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from provenance_pins import ProvenanceError

_COMMON_LICENSE_RE = re.compile(r"/usr/share/common-licenses/[A-Za-z0-9.+-]+")
_LICENSE_FIELD_RE = re.compile(r"^License:[ \t]+(\S[^\n]*?)[ \t]*$", re.MULTILINE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The AppImage is unusable without the core X11 FreeRDP client -- a build
# that silently drops it must fail closed, not ship a crippled artifact.
REQUIRED_CONVEYED = ("usr/bin/xfreerdp3",)


@dataclass(frozen=True)
class ConveyedFile:
    """One file copied into the AppDir by bundle-system-bins.sh."""

    sha256: str
    src: str  # absolute path inside the pinned Ubuntu build container
    dst: str  # relative destination path inside the AppDir


def parse_conveyed_manifest(text: str) -> list[ConveyedFile]:
    """Parse the TSV manifest (sha256 \\t src \\t dst) emitted by
    bundle-system-bins.sh.  Fail closed on any malformed line."""
    files: list[ConveyedFile] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) != 3:
            raise ProvenanceError(
                f"conveyed manifest line {lineno}: expected 3 TSV fields, got {len(parts)}"
            )
        sha, src, dst = parts
        if not _SHA256_RE.match(sha):
            raise ProvenanceError(f"conveyed manifest line {lineno}: bad sha256 {sha!r}")
        if not src.startswith("/") or not dst:
            raise ProvenanceError(
                f"conveyed manifest line {lineno}: src must be absolute, dst non-empty"
            )
        files.append(ConveyedFile(sha, src, dst))
    if not files:
        raise ProvenanceError("conveyed manifest is empty -- nothing was bundled")
    return files


def parse_dpkg_status(text: str) -> dict[str, dict[str, str]]:
    """Installed packages -> {version, source} from a dpkg status database.

    Fail closed on an installed package without a Version field.
    """
    status: dict[str, dict[str, str]] = {}
    for para in re.split(r"\n\s*\n", text):
        fields: dict[str, str] = {}
        for line in para.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        name = fields.get("Package")
        if not name or fields.get("Status") != "install ok installed":
            continue
        version = fields.get("Version")
        if not version:
            raise ProvenanceError(f"dpkg status: {name} is installed but has no Version")
        source = fields.get("Source", name)
        source_name, _, explicit_version = source.partition(" (")
        status[name] = {
            "version": version,
            "source": source_name,
            "source_version": explicit_version.rstrip(")") if explicit_version else version,
        }
    return status


def build_owner_map(dpkg_info_dir: Path) -> dict[str, set[str]]:
    """Installed path -> owning package(s), from ``<pkg>.list`` files.

    Directory entries (``/``, ``/.``, ``/usr``, …) are claimed by many
    packages by design; only FILE lookups (``owner_of``) fail closed on
    multiple owners, so shared directories are not conflicts.
    """
    owners: dict[str, set[str]] = {}
    for list_file in sorted(dpkg_info_dir.glob("*.list")):
        # .list filenames carry the :<arch> qualifier (pkg:amd64.list);
        # the status db Package: field does not -- strip it.
        package = list_file.name[: -len(".list")].split(":", 1)[0]
        for line in list_file.read_text(encoding="utf-8", errors="replace").splitlines():
            path = line.strip()
            if not path.startswith("/") or path in ("/", "/."):
                continue
            owners.setdefault(path, set()).add(package)
    return owners


# Ubuntu is a merged-/usr system: dpkg .list files record /usr/... while
# ldd may report the /lib/... (or /bin/...) symlink alias of the same file.
_MERGED_USR_PREFIXES = {
    "/bin/": "/usr/bin/",
    "/sbin/": "/usr/sbin/",
    "/lib/": "/usr/lib/",
    "/lib32/": "/usr/lib32/",
    "/lib64/": "/usr/lib64/",
    "/libx32/": "/usr/libx32/",
}


def normalize_installed_path(path: str) -> str:
    """Map a merged-/usr alias to its dpkg-recorded ``/usr/...`` form."""
    for alias, real in _MERGED_USR_PREFIXES.items():
        if path.startswith(alias):
            return real + path[len(alias) :]
    return path


def owner_of(owners: dict[str, set[str]], path: str) -> str:
    """Single owning package for an installed FILE path; fail closed on
    none (unknown provenance) or several (diversion/unknown).

    Looks up the path as given, then its merged-/usr normalized form
    (``ldd`` reports ``/lib/...`` aliases; dpkg records ``/usr/lib/...``).
    """
    for candidate_path in (path, normalize_installed_path(path)):
        candidates = owners.get(candidate_path)
        if not candidates:
            continue
        if len(candidates) > 1:
            raise ProvenanceError(
                f"{candidate_path}: owned by {sorted(candidates)} -- refusing to guess"
            )
        return next(iter(candidates))
    raise ProvenanceError(f"{path}: no dpkg owner -- unknown provenance")


@dataclass(frozen=True)
class CopyrightInfo:
    """Parsed /usr/share/doc/<pkg>/copyright (Debian machine-readable)."""

    format_url: str | None
    upstream_name: str | None
    upstream_source: str | None
    license_names: tuple[str, ...]
    common_licenses: tuple[str, ...]


def parse_copyright(package: str, text: str) -> CopyrightInfo:
    """Parse a Debian copyright file.

    Machine-readable files yield ``License:`` short names.  Old-style
    prose files (e.g. libusb-1.0-0) have no ``License:`` stanzas; for
    those the FULL copyright text is the license notice and is
    conveyed verbatim, with ``license_names == ()`` recorded honestly --
    a name is never invented.  Fail closed on a missing/empty file.
    """

    def field(name: str) -> str | None:
        match = re.search(rf"^{name}:[ \t]+(\S[^\n]*)$", text, re.MULTILINE)
        return match.group(1).strip() if match else None

    if not text.strip():
        raise ProvenanceError(f"{package}: copyright file is empty")
    names = tuple(sorted(set(_LICENSE_FIELD_RE.findall(text))))
    # Sentence punctuation rides along ("... found in
    # /usr/share/common-licenses/LGPL-2.1.") -- strip it, else the
    # referenced filename never resolves.
    common = {ref.rstrip(".,;:") for ref in _COMMON_LICENSE_RE.findall(text)}
    return CopyrightInfo(
        format_url=field("Format"),
        upstream_name=field("Upstream-Name"),
        upstream_source=field("Source"),
        license_names=names,
        common_licenses=tuple(sorted(common)),
    )
