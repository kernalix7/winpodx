# SPDX-License-Identifier: MIT
"""Push the host-staged manifest + icons into the Windows guest.

Pairs with the guest-side :file:`config/oem/reverse-open/
register-apps.ps1` (staged into ``C:\\OEM\\reverse-open\\``
automatically by dockur's autounattend OEM copy). The sync flow:

1. Read ``apps.json`` + every ``icons/<slug>.ico`` from the local
   stage dir (under :func:`winpodx.utils.paths.data_dir`).
2. Build a single PowerShell snippet that base64-decodes each blob
   into a file under ``C:\\Users\\Public\\winpodx\\reverse-open\\``,
   then invokes ``C:\\OEM\\reverse-open\\register-apps.ps1`` against
   those paths.
3. Send the snippet over the existing :class:`AgentClient.exec`
   transport.

Why /exec rather than a dedicated agent endpoint: ``agent.ps1``
already implements bearer-auth POST /exec for arbitrary PowerShell;
adding a new endpoint for the file-blob transfer would duplicate the
auth + body-handling code with no functional gain. /exec's 60-second
default timeout is bumped to 180 s here to cover the worst case of
~50 apps with ICOs (each ICO is ~30 KB; the encoded payload is well
under the agent's 64 KB body cap per request — total snippet stays
within ~3 MB even with that many apps).

Failure modes (caller decides how to surface):

- :class:`AgentUnavailableError` — guest not up or network gone;
  the local stage still landed, so a later sync will pick it up.
- :class:`AgentAuthError` — token drift between host and guest;
  ``winpodx pod sync-password`` is the user's recovery handle.
- :class:`SyncError` — guest-side register-apps.ps1 failed; the
  result includes the snippet's stdout + stderr for triage.

This module does NOT touch :class:`Config` directly. The CLI layer
is responsible for updating ``cfg.reverse_open.last_synced_at`` on
success.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.agent import AgentClient, AgentError
from winpodx.core.config import Config
from winpodx.utils.paths import bundle_dir

logger = logging.getLogger(__name__)


# Where the synced payload lands on the guest. Public-readable so the
# user's RDP session can run register-apps.ps1 without elevation;
# winpodx-<slug> registry entries land in HKCU which is per-user and
# doesn't require admin. See config/oem/reverse-open/register-apps.ps1
# for the registry shape.
_GUEST_BASE = r"C:\Users\Public\winpodx\reverse-open"
_GUEST_APPS_JSON = _GUEST_BASE + r"\apps.json"
_GUEST_ICONS_DIR = _GUEST_BASE + r"\icons"
_GUEST_BIN_DIR = _GUEST_BASE + r"\bin"
_GUEST_SHIM_EXE = _GUEST_BIN_DIR + r"\winpodx-reverse-open-shim.exe"
_GUEST_RCEDIT_EXE = _GUEST_BIN_DIR + r"\rcedit.exe"
_GUEST_REGISTER_PS1 = _GUEST_BASE + r"\register-apps.ps1"
_GUEST_UNREGISTER_PS1 = _GUEST_BASE + r"\unregister-apps.ps1"


def is_guest_shim_path(executable: str) -> bool:
    """Return True if ``executable`` points inside the guest reverse-open bin dir.

    Single source of truth for the question "is this Windows .exe one of
    our reverse-open shims, or a real Windows app?". Used by the discovery
    junk-filter (``core.discovery._is_junk_entry``) and the discovery
    self-heal sweep (``core.discovery._purge_reverse_open_entries``) so
    those callers don't hardcode the directory layout — they import this
    function instead, and any future relocation of ``_GUEST_BIN_DIR``
    propagates without code churn.

    The match is case-insensitive and accepts forward-slash variants so
    a guest scanner that emitted POSIX-style paths is still caught.
    """
    if not executable:
        return False
    needle = (_GUEST_BIN_DIR + "\\").replace("/", "\\").lower()
    return needle in executable.replace("/", "\\").lower()


# Host-bundle layout for the assets we push to the guest. The two
# PowerShell scripts are text; the shim is a Rust-built Windows .exe
# shipped pre-compiled in the source tree (the build matrix produces
# it once per release rather than at sync time). Each install mode
# (source checkout, wheel install, FHS install, curl|bash drop)
# carries the binary alongside the .ps1 files under
# config/oem/reverse-open/, so locating them via bundle_dir() works
# uniformly without depending on dockur having staged the OEM bundle.
_HOST_BUNDLE_SUBDIR = ("config", "oem", "reverse-open")
_HOST_PS_SCRIPTS: dict[str, tuple[str, ...]] = {
    "register": ("register-apps.ps1",),
    "unregister": ("unregister-apps.ps1",),
}
_HOST_SHIM_PATH: tuple[str, ...] = (
    "shim",
    "bin",
    "winpodx-reverse-open-shim.exe",
)
# rcedit (electron/rcedit, MIT) — vendored alongside the shim. Used by
# register-apps.ps1 to embed the per-slug icon directly into the
# per-slug .exe's PE resource section. Required because Explorer's
# "Open with…" chooser picks its entry icon from the EXE's embedded
# resource, not from registry surfaces (Applications\<exe>\DefaultIcon
# nor <ProgID>\DefaultIcon work reliably on Win10/Win11). Hard-link
# inode-sharing is sacrificed to make this work — each per-slug .exe
# is now an independent copy with its own embedded icon, so the on-
# disk footprint scales with app count (~500 KB × N).
_HOST_RCEDIT_PATH: tuple[str, ...] = (
    "shim",
    "bin",
    "rcedit.exe",
)

# Agent /exec default is 60s; bumped for the sync payload because the
# guest also runs register-apps.ps1 which iterates per-app + writes
# registry per ext. Empirically ~30s for 50 apps; double that as headroom.
_SYNC_TIMEOUT_SEC = 180


@dataclass
class SyncResult:
    """Outcome from one :func:`sync_to_guest` call."""

    pushed_apps: int
    pushed_icons: int
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class SyncError(RuntimeError):
    """Sync attempt reached the guest but failed before / during register."""


def _read_manifest(stage_dir: Path) -> dict:
    apps_json = stage_dir / "apps.json"
    if not apps_json.is_file():
        raise SyncError(f"local manifest missing at {apps_json}")
    try:
        return json.loads(apps_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"local manifest parse failed: {exc}") from exc


def _collect_icons(stage_dir: Path, manifest: dict) -> dict[str, bytes]:
    """Map slug → ICO file contents for every app referenced in the manifest.

    Missing ICOs are skipped silently — register-apps.ps1 falls back
    to the default Windows icon when DefaultIcon is absent. We don't
    fail the whole sync over a single missing icon.
    """
    icons_dir = stage_dir / "icons"
    out: dict[str, bytes] = {}
    for app in manifest.get("apps", []):
        slug = app.get("slug")
        if not isinstance(slug, str):
            continue
        candidate = icons_dir / f"{slug}.ico"
        if not candidate.is_file():
            logger.debug("sync: icon missing for %s", slug)
            continue
        try:
            out[slug] = candidate.read_bytes()
        except OSError as exc:
            logger.warning("sync: cannot read icon for %s: %s", slug, exc)
    return out


def _read_host_scripts() -> dict[str, str]:
    """Read the two host-side PowerShell scripts from the bundle.

    Returns ``{name: text}`` keyed by the same keys as
    :data:`_HOST_PS_SCRIPTS`. Raises :class:`SyncError` if either is
    missing — they're shipped by every install mode (source checkout,
    wheel, distro package) under ``config/oem/reverse-open/`` so
    their absence is a real broken-install signal worth surfacing.
    """
    base = bundle_dir()
    out: dict[str, str] = {}
    for key, parts in _HOST_PS_SCRIPTS.items():
        path = base.joinpath(*_HOST_BUNDLE_SUBDIR, *parts)
        if not path.is_file():
            raise SyncError(f"bundle script missing: {path}")
        try:
            out[key] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SyncError(f"cannot read {path}: {exc}") from exc
    return out


def _read_host_shim_exe() -> bytes:
    """Read the pre-built Rust shim binary from the host bundle.

    The shim is cross-compiled to ``x86_64-pc-windows-gnu`` at release
    time and committed under ``config/oem/reverse-open/shim/bin/``.
    Raises :class:`SyncError` if the binary is missing — the install
    is broken (or running against a source checkout that hasn't built
    the shim yet), and pretending to sync would just register handlers
    that point at a nonexistent .exe on the guest.
    """
    path = bundle_dir().joinpath(*_HOST_BUNDLE_SUBDIR, *_HOST_SHIM_PATH)
    if not path.is_file():
        raise SyncError(
            f"reverse-open shim binary missing at {path}; "
            "rebuild via `cd config/oem/reverse-open/shim && "
            "cargo build --release --target x86_64-pc-windows-gnu`"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SyncError(f"cannot read shim binary {path}: {exc}") from exc


def _read_host_rcedit_exe() -> bytes:
    """Read the vendored rcedit.exe binary from the host bundle.

    ``rcedit.exe`` is committed under
    ``config/oem/reverse-open/shim/bin/`` alongside the shim. Raises
    :class:`SyncError` if missing — like the shim, the install is
    broken; pretending to sync would land per-slug .exes without
    embedded icons and the chooser fix would be invisible.
    """
    path = bundle_dir().joinpath(*_HOST_BUNDLE_SUBDIR, *_HOST_RCEDIT_PATH)
    if not path.is_file():
        raise SyncError(
            f"reverse-open rcedit binary missing at {path}; "
            "this is normally committed alongside the shim"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SyncError(f"cannot read rcedit binary {path}: {exc}") from exc


def _build_sync_script(
    apps_json_text: str,
    icons_b64: dict[str, str],
    host_scripts: dict[str, str],
    shim_b64: str,
    rcedit_b64: str,
) -> str:
    """Render the PowerShell snippet that runs on the guest via /exec.

    The snippet base64-decodes every payload (apps.json text, ICOs,
    register / unregister PowerShell scripts, and the Rust shim
    ``.exe``) and writes them to ``C:\\Users\\Public\\winpodx\\
    reverse-open\\`` before running ``register-apps.ps1``. This makes
    the sync entirely self-contained — no dependence on dockur having
    staged the OEM bundle, so the feature works on pods that were
    created before this PR landed.

    Atomicity: the shim ``.exe`` is written via the binary writer
    (raw bytes, not text-encoded) because UTF-8 decoding a PE binary
    would corrupt it. The two ``.ps1`` scripts are written via
    text-mode atomic writes for consistency with the user-readable
    intent of the files.
    """
    apps_b64 = base64.b64encode(apps_json_text.encode("utf-8")).decode("ascii")
    icon_entries = "\n".join(
        f"  @{{ slug = '{slug}'; b64 = '{b64}' }};" for slug, b64 in sorted(icons_b64.items())
    )
    register_b64 = base64.b64encode(host_scripts["register"].encode("utf-8")).decode("ascii")
    unregister_b64 = base64.b64encode(host_scripts["unregister"].encode("utf-8")).decode("ascii")

    # The PowerShell snippet keeps every string parameter inside
    # single-quoted literals (only ' itself needs escaping; the
    # base64 alphabet doesn't contain '). Multi-line construction
    # via string concatenation rather than a triple-quoted f-string
    # keeps `ruff format` happy without breaking the script body.
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$base = '{_GUEST_BASE}'\n"
        f"$iconsDir = '{_GUEST_ICONS_DIR}'\n"
        f"$binDir = '{_GUEST_BIN_DIR}'\n"
        f"$appsJson = '{_GUEST_APPS_JSON}'\n"
        f"$register = '{_GUEST_REGISTER_PS1}'\n"
        f"$unregister = '{_GUEST_UNREGISTER_PS1}'\n"
        f"$shimExe = '{_GUEST_SHIM_EXE}'\n"
        f"$rcEditExe = '{_GUEST_RCEDIT_EXE}'\n"
        "\n"
        "foreach ($d in @($base, $iconsDir, $binDir)) {\n"
        "    if (-not (Test-Path -LiteralPath $d)) "
        "{ New-Item -ItemType Directory -Path $d -Force | Out-Null }\n"
        "}\n"
        "\n"
        "function Write-TextAtomic($Path, $Base64) {\n"
        "    $bytes = [Convert]::FromBase64String($Base64)\n"
        "    $text = [Text.Encoding]::UTF8.GetString($bytes)\n"
        '    $tmp = "$Path.tmp"\n'
        "    Set-Content -LiteralPath $tmp -Value $text -Encoding UTF8 -NoNewline\n"
        "    Move-Item -LiteralPath $tmp -Destination $Path -Force\n"
        "}\n"
        "\n"
        "function Write-BinaryAtomic($Path, $Base64) {\n"
        "    $bytes = [Convert]::FromBase64String($Base64)\n"
        '    $tmp = "$Path.tmp"\n'
        "    [IO.File]::WriteAllBytes($tmp, $bytes)\n"
        "    Move-Item -LiteralPath $tmp -Destination $Path -Force\n"
        "}\n"
        "\n"
        f"Write-TextAtomic $appsJson '{apps_b64}'\n"
        f"Write-TextAtomic $register '{register_b64}'\n"
        f"Write-TextAtomic $unregister '{unregister_b64}'\n"
        f"Write-BinaryAtomic $shimExe '{shim_b64}'\n"
        f"Write-BinaryAtomic $rcEditExe '{rcedit_b64}'\n"
        "\n"
        "# Write each ICO.\n"
        "$icons = @(\n"
        f"{icon_entries}\n"
        ")\n"
        "foreach ($entry in $icons) {\n"
        '    $dst = Join-Path $iconsDir "$($entry.slug).ico"\n'
        "    $bytes = [Convert]::FromBase64String($entry.b64)\n"
        "    [IO.File]::WriteAllBytes($dst, $bytes)\n"
        "}\n"
        "\n"
        "# Run the now-staged register-apps.ps1 from the public dir.\n"
        "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $register "
        "-AppsJson $appsJson -IconsDir $iconsDir -BinDir $binDir "
        "-ShimExe $shimExe -RcEditExe $rcEditExe\n"
        "exit $LASTEXITCODE\n"
    )
    return script


def sync_to_guest(cfg: Config, stage_dir: Path) -> SyncResult:
    """Push the local stage to the guest and register the handlers.

    Raises:
      SyncError: manifest unreadable, register-apps.ps1 non-zero exit,
        or an unexpected agent error.
      AgentUnavailableError, AgentAuthError, AgentTimeoutError: from
        the underlying transport — caller decides how to surface.
    """
    manifest = _read_manifest(stage_dir)
    icons = _collect_icons(stage_dir, manifest)
    icons_b64 = {slug: base64.b64encode(data).decode("ascii") for slug, data in icons.items()}

    host_scripts = _read_host_scripts()
    shim_bytes = _read_host_shim_exe()
    shim_b64 = base64.b64encode(shim_bytes).decode("ascii")
    rcedit_bytes = _read_host_rcedit_exe()
    rcedit_b64 = base64.b64encode(rcedit_bytes).decode("ascii")
    script = _build_sync_script(
        stage_dir.joinpath("apps.json").read_text(encoding="utf-8"),
        icons_b64,
        host_scripts,
        shim_b64,
        rcedit_b64,
    )
    client = AgentClient(cfg)
    try:
        result = client.exec(script, timeout=_SYNC_TIMEOUT_SEC)
    except AgentError:
        raise

    rc = result.rc if result.rc is not None else 0
    if not result.ok:
        raise SyncError(
            f"register-apps.ps1 failed (rc={rc}); "
            f"stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
        )
    return SyncResult(
        pushed_apps=len(manifest.get("apps", [])),
        pushed_icons=len(icons),
        rc=rc,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def unregister_on_guest(cfg: Config) -> SyncResult:
    """Run ``unregister-apps.ps1`` on the guest via /exec.

    Used by ``winpodx host-open disable`` (or a future ``unregister``
    subcommand) to scrub the per-user registry entries when the
    feature is turned off. Idempotent — does nothing if no
    winpodx-<slug> entries exist.
    """
    # Use the public-dir copy we staged on the most recent sync. The
    # script exits 0 cleanly if no winpodx-<slug> ProgIDs are present
    # (idempotent), so calling this on a guest that never had
    # reverse-open enabled is a no-op.
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$unregister = '{_GUEST_UNREGISTER_PS1}'\n"
        "if (-not (Test-Path -LiteralPath $unregister)) {\n"
        '    Write-Output "unregister-apps.ps1 not staged at $unregister"\n'
        "    exit 4\n"
        "}\n"
        "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $unregister\n"
        "exit $LASTEXITCODE\n"
    )
    client = AgentClient(cfg)
    result = client.exec(script, timeout=_SYNC_TIMEOUT_SEC)
    rc = result.rc if result.rc is not None else 0
    if not result.ok:
        raise SyncError(
            f"unregister-apps.ps1 failed (rc={rc}); "
            f"stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
        )
    return SyncResult(
        pushed_apps=0,
        pushed_icons=0,
        rc=rc,
        stdout=result.stdout,
        stderr=result.stderr,
    )
