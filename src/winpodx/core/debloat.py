# SPDX-License-Identifier: MIT
"""Debloat catalog loader + preset resolver (#247 phase 1).

The runtime pipeline is:

1. ``load_catalog()``           -> DebloatCatalog (items + presets) from
                                   ``data/debloat/items.toml``.
2. ``resolve_selection(...)``   -> ordered list of item names from a
                                   ``--preset`` choice and/or an
                                   explicit ``--items`` list, validated
                                   against the catalog.
3. ``build_run_script(catalog,
   selection)``                  -> a single PowerShell script that
                                   sources each selected item's per-
                                   item ``.ps1`` in order, wrapped in
                                   a thin orchestrator that prints
                                   per-item begin/end lines + a final
                                   summary. The caller hands that
                                   script to ``run_via_transport``.

The CLI handler (``winpodx debloat``) consumes the public API:
``DebloatItem``, ``DebloatCatalog``, ``DebloatCatalogError``,
``load_catalog``, ``resolve_selection``, ``build_run_script``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from winpodx.utils.paths import bundle_dir

log = logging.getLogger(__name__)

_CATALOG_REL_PATH = ("data", "debloat", "items.toml")
_SCRIPTS_REL_PATH = ("scripts", "windows", "debloat")

_VALID_RISKS = frozenset(("low", "medium", "high"))


class DebloatCatalogError(Exception):
    """Raised when ``items.toml`` is missing, malformed, or references
    item scripts that don't exist on disk."""


@dataclass(frozen=True)
class DebloatItem:
    """A single debloat action exposed in the catalog."""

    name: str
    label: str
    description: str
    script: str  # filename only, resolved against scripts/windows/debloat/
    risk: str
    # Optional: path under scripts/windows/debloat/ to the reverse-action
    # PowerShell snippet. ``None`` = this item is one-way (e.g. OneDrive
    # uninstall, where reinstalling requires re-running Microsoft's
    # installer and restoring autostart commands isn't recoverable).
    # ``winpodx debloat --undo --items <name>`` rejects items with no
    # undo_script.
    undo_script: str | None = None

    @property
    def script_path(self) -> Path:
        """Absolute path to this item's PowerShell apply script."""
        return bundle_dir().joinpath(*_SCRIPTS_REL_PATH, self.script)

    @property
    def undo_script_path(self) -> Path | None:
        """Absolute path to this item's undo script, or ``None`` if absent."""
        if self.undo_script is None:
            return None
        return bundle_dir().joinpath(*_SCRIPTS_REL_PATH, self.undo_script)

    @property
    def is_reversible(self) -> bool:
        return self.undo_script is not None


@dataclass(frozen=True)
class DebloatCatalog:
    """In-memory representation of ``items.toml``."""

    items: dict[str, DebloatItem]
    presets: dict[str, list[str]]

    @property
    def preset_names(self) -> list[str]:
        """Stable order matching insertion. Used for ``--preset`` help."""
        return list(self.presets.keys())

    def items_for_preset(self, preset: str) -> list[str]:
        """Return the ordered item-name list for ``preset``.

        Raises ``DebloatCatalogError`` for unknown presets so the CLI
        surfaces a clear error instead of silently applying nothing.
        """
        if preset not in self.presets:
            raise DebloatCatalogError(
                f"Unknown preset {preset!r}; available: {', '.join(self.preset_names)}"
            )
        return list(self.presets[preset])


def load_catalog(*, catalog_path: Path | None = None) -> DebloatCatalog:
    """Read and validate the debloat catalog.

    ``catalog_path`` defaults to ``<bundle>/data/debloat/items.toml``.
    Validation steps:

      * TOML parse must succeed.
      * Each ``[items.<name>]`` block must carry ``label`` /
        ``description`` / ``script`` / ``risk``.
      * Each ``risk`` must be one of low / medium / high.
      * Each referenced ``script`` must exist under
        ``scripts/windows/debloat/`` (catches typos at load time
        rather than at runtime via a "missing script" guest-side
        failure).
      * Each preset list must reference only known item names.

    Raises ``DebloatCatalogError`` on any failure -- the CLI catches
    this and surfaces a clear message instead of leaking a traceback.
    """
    path = catalog_path or bundle_dir().joinpath(*_CATALOG_REL_PATH)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as e:
        raise DebloatCatalogError(f"Catalog not found at {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise DebloatCatalogError(f"Catalog {path} is not valid TOML: {e}") from e

    raw_items = data.get("items", {})
    raw_presets = data.get("presets", {})
    if not isinstance(raw_items, dict) or not isinstance(raw_presets, dict):
        raise DebloatCatalogError(f"Catalog {path}: [items] and [presets] must be tables")

    items: dict[str, DebloatItem] = {}
    for name, body in raw_items.items():
        if not isinstance(body, dict):
            raise DebloatCatalogError(f"items.{name}: must be a table")
        for required in ("label", "description", "script", "risk"):
            if required not in body:
                raise DebloatCatalogError(f"items.{name}: missing required field {required!r}")
        risk = str(body["risk"]).lower()
        if risk not in _VALID_RISKS:
            raise DebloatCatalogError(
                f"items.{name}: risk={body['risk']!r} not in {sorted(_VALID_RISKS)}"
            )
        undo_script = body.get("undo_script")
        if undo_script is not None and not isinstance(undo_script, str):
            raise DebloatCatalogError(f"items.{name}: undo_script must be a string when set")
        item = DebloatItem(
            name=name,
            label=str(body["label"]),
            description=str(body["description"]),
            script=str(body["script"]),
            risk=risk,
            undo_script=str(undo_script) if undo_script else None,
        )
        if not item.script_path.exists():
            raise DebloatCatalogError(
                f"items.{name}: script {item.script_path} does not exist on disk"
            )
        if item.undo_script_path is not None and not item.undo_script_path.exists():
            raise DebloatCatalogError(
                f"items.{name}: undo_script {item.undo_script_path} does not exist on disk"
            )
        items[name] = item

    presets: dict[str, list[str]] = {}
    for preset_name, members in raw_presets.items():
        if not isinstance(members, list):
            raise DebloatCatalogError(f"presets.{preset_name}: must be a list of item names")
        for member in members:
            if member not in items:
                raise DebloatCatalogError(
                    f"presets.{preset_name}: references unknown item {member!r}"
                )
        # Dedupe while preserving order; same item in a preset twice
        # would just re-run its script with no extra effect, but we
        # filter for cleanliness in --list output.
        seen: set[str] = set()
        unique: list[str] = []
        for member in members:
            if member not in seen:
                unique.append(member)
                seen.add(member)
        presets[preset_name] = unique

    return DebloatCatalog(items=items, presets=presets)


def resolve_selection(
    catalog: DebloatCatalog,
    *,
    preset: str | None,
    items: list[str] | None,
) -> list[str]:
    """Resolve ``--preset`` and/or ``--items`` to an ordered item-name list.

    Resolution rules:

      * ``preset`` and ``items`` are both optional. At most one should
        be set; if both, ``items`` wins and ``preset`` is ignored
        (matches the principle that explicit args beat curated defaults).
      * ``preset=None, items=None`` -> default to the "normal" preset.
      * Unknown item names raise ``DebloatCatalogError``.
      * Duplicates are deduplicated, preserving first-seen order.
    """
    if items:
        names = list(items)
    elif preset:
        names = catalog.items_for_preset(preset)
    else:
        names = catalog.items_for_preset("normal")

    seen: set[str] = set()
    resolved: list[str] = []
    for name in names:
        if name not in catalog.items:
            raise DebloatCatalogError(
                f"Unknown item {name!r}; available: {', '.join(sorted(catalog.items))}"
            )
        if name not in seen:
            resolved.append(name)
            seen.add(name)
    return resolved


# PowerShell helper appended to every apply / undo payload. Reads the
# state JSON, mutates it for the current item, writes back atomically
# via a temp-file rename. Path lives under %ProgramData% so it survives
# user profile rebuilds (Sysprep, account recreation) and aligns HKLM-
# side debloat actions with HKCU state.
_STATE_HELPER_PS = r"""
$winpodxDebloatStateDir = "$env:ProgramData\winpodx"
$winpodxDebloatStatePath = "$winpodxDebloatStateDir\debloat-applied.json"

function Get-WinpodxDebloatState {
    if (-not (Test-Path $winpodxDebloatStateDir)) {
        New-Item -Path $winpodxDebloatStateDir -ItemType Directory -Force | Out-Null
    }
    if (Test-Path $winpodxDebloatStatePath) {
        try {
            $raw = Get-Content -Path $winpodxDebloatStatePath -Raw -ErrorAction Stop
            if ($raw) {
                $obj = $raw | ConvertFrom-Json -ErrorAction Stop
                if ($obj -is [PSCustomObject]) { return $obj }
            }
        } catch {
            Write-Host "  [state] discarding corrupted $winpodxDebloatStatePath"
        }
    }
    return New-Object PSObject
}

function Set-WinpodxDebloatState {
    param($state)
    $tmp = "$winpodxDebloatStatePath.tmp"
    $state | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8 -Force
    Move-Item -Path $tmp -Destination $winpodxDebloatStatePath -Force
}

function Mark-WinpodxDebloatApplied {
    param([string]$Name)
    $state = Get-WinpodxDebloatState
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $state | Add-Member -MemberType NoteProperty -Name $Name -Value @{applied_at = $stamp} -Force
    Set-WinpodxDebloatState $state
}

function Clear-WinpodxDebloatApplied {
    param([string]$Name)
    $state = Get-WinpodxDebloatState
    if ($state.PSObject.Properties.Name -contains $Name) {
        $state.PSObject.Properties.Remove($Name)
        Set-WinpodxDebloatState $state
    }
}
"""


def build_run_script(catalog: DebloatCatalog, selection: list[str]) -> str:
    """Build a single PowerShell payload that runs ``selection`` in order.

    The payload is a thin orchestrator that:

      * Defines helpers for the per-guest state JSON at
        ``%ProgramData%\\winpodx\\debloat-applied.json``.
      * Prints ``=== winpodx debloat (N items) ===`` once at the top.
      * For each selected item: prints a ``--- <name> ---`` banner,
        inlines the item's per-item ``.ps1``, marks the item applied
        in the state JSON on success, and tracks a pass/fail counter.
      * Prints a final ``=== done: <ok>/<total> succeeded ===`` line.

    The per-item ``.ps1`` files are read from disk at build time and
    inlined into the orchestrator payload, so the guest doesn't need
    access to the host filesystem -- the whole script lands as one
    blob through ``run_via_transport``.
    """
    blocks: list[str] = []
    blocks.append(_STATE_HELPER_PS)
    blocks.append('Write-Host "=== WinPodX debloat (' + str(len(selection)) + ' items) ==="')
    blocks.append("$winpodxDebloatOk = 0")
    blocks.append("$winpodxDebloatTotal = 0")

    for name in selection:
        item = catalog.items[name]
        try:
            script_text = item.script_path.read_text(encoding="utf-8")
        except OSError as e:
            # Catalog load already verified the script exists, but
            # a concurrent uninstall could remove it -- fail closed
            # with a clear message rather than emitting a half-built
            # payload.
            raise DebloatCatalogError(
                f"items.{name}: could not read {item.script_path}: {e}"
            ) from e

        blocks.append(f'Write-Host "--- {name} ({item.label}) ---"')
        blocks.append("$winpodxDebloatTotal++")
        blocks.append("try {")
        blocks.append(script_text)
        blocks.append(f'    Mark-WinpodxDebloatApplied -Name "{name}"')
        blocks.append("    $winpodxDebloatOk++")
        blocks.append("} catch {")
        blocks.append(f'    Write-Host "    [{name}] FAILED: $($_.Exception.Message)"')
        blocks.append("}")

    blocks.append('Write-Host "=== done: $winpodxDebloatOk/$winpodxDebloatTotal succeeded ==="')
    return "\n".join(blocks)


def build_undo_script(catalog: DebloatCatalog, selection: list[str]) -> str:
    """Build a PowerShell payload that runs each item's ``undo_script``.

    Raises ``DebloatCatalogError`` if any selected item is one-way
    (``undo_script == None``) -- the CLI surfaces the offending names so
    users can drop them from ``--items`` and retry.
    """
    one_way = [name for name in selection if not catalog.items[name].is_reversible]
    if one_way:
        raise DebloatCatalogError(
            "items have no undo path and are one-way: "
            + ", ".join(one_way)
            + ". Drop them from --items or accept the original apply was permanent."
        )

    blocks: list[str] = []
    blocks.append(_STATE_HELPER_PS)
    blocks.append('Write-Host "=== WinPodX debloat undo (' + str(len(selection)) + ' items) ==="')
    blocks.append("$winpodxDebloatOk = 0")
    blocks.append("$winpodxDebloatTotal = 0")

    for name in selection:
        item = catalog.items[name]
        undo_path = item.undo_script_path
        # Already guarded by is_reversible check above; the assert keeps
        # mypy happy + acts as defence against catalog hot-reload during
        # orchestration.
        assert undo_path is not None
        try:
            script_text = undo_path.read_text(encoding="utf-8")
        except OSError as e:
            raise DebloatCatalogError(f"items.{name}: could not read {undo_path}: {e}") from e

        blocks.append(f'Write-Host "--- undo {name} ({item.label}) ---"')
        blocks.append("$winpodxDebloatTotal++")
        blocks.append("try {")
        blocks.append(script_text)
        blocks.append(f'    Clear-WinpodxDebloatApplied -Name "{name}"')
        blocks.append("    $winpodxDebloatOk++")
        blocks.append("} catch {")
        blocks.append(f'    Write-Host "    [undo {name}] FAILED: $($_.Exception.Message)"')
        blocks.append("}")

    blocks.append(
        'Write-Host "=== undo done: $winpodxDebloatOk/$winpodxDebloatTotal succeeded ==="'
    )
    return "\n".join(blocks)


def format_catalog_listing(catalog: DebloatCatalog) -> str:
    """Render ``winpodx debloat --list`` output.

    Two sections: items (with risk + description) and presets (with
    member list). Stable order: items in catalog declaration order,
    presets in declaration order.
    """
    lines: list[str] = []
    lines.append("Items:")
    for item in catalog.items.values():
        lines.append(f"  {item.name:<18}  [{item.risk}]  {item.label}")
        lines.append(f"                      {item.description}")
    lines.append("")
    lines.append("Presets:")
    for preset_name, members in catalog.presets.items():
        lines.append(f"  {preset_name:<14}  {', '.join(members)}")
    return "\n".join(lines)
