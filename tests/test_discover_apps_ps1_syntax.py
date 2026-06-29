# SPDX-License-Identifier: MIT
"""Sanity checks for scripts/windows/discover_apps.ps1.

The .ps1 runs inside the Windows guest, so CI can't exercise it end to end.
These checks (a) pwsh-AST-parse it when pwsh is on PATH (CI has it; skipped on
the dev box) so a syntax error in the guest script can't merge silently, and
(b) statically assert the #581 Start-Menu-only scan invariants the host relies
on (the literal sentinel the host patches, and that the two legacy-only sources
are gated behind the full-scan switch).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVER_PS1 = REPO_ROOT / "scripts" / "windows" / "discover_apps.ps1"


@pytest.fixture(scope="module")
def discover_source() -> str:
    assert DISCOVER_PS1.is_file(), f"discover_apps.ps1 missing at {DISCOVER_PS1}"
    return DISCOVER_PS1.read_text(encoding="utf-8")


def test_discover_apps_ps1_exists():
    assert DISCOVER_PS1.is_file()


def test_discover_apps_ps1_pwsh_parse():
    """If pwsh is on PATH, the script must parse without errors."""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not installed on this host")
    cmd = [
        pwsh,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "$errors = $null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{DISCOVER_PS1}', "
            "[ref]$null, [ref]$errors); "
            "if ($errors -and $errors.Count -gt 0) { "
            "  $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 "
            "}"
        ),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"pwsh parse failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_host_patch_sentinel_is_present_and_unique(discover_source: str):
    """The host flips this exact literal to enable full_app_scan (#581); if it
    drifts or duplicates, the one-shot replace in core.discovery silently
    no-ops or patches the wrong line."""
    assert discover_source.count("$WinpodxFullScan = $false") == 1


def test_legacy_sources_are_gated_behind_full_scan(discover_source: str):
    """App Paths (Source 1) and choco/scoop shims (Source 4) must only run in
    full-scan mode, else the default would still flood the menu."""
    assert "if ($WinpodxFullScan) {" in discover_source
    # both legacy-only sources carry the closing marker comment
    assert "end if ($WinpodxFullScan) -- Source 1" in discover_source
    assert "end if ($WinpodxFullScan) -- Source 4" in discover_source


def test_uwp_gated_by_startapps_in_default_mode(discover_source: str):
    """Default mode keeps only UWP apps whose AUMID is in Get-StartApps, with
    an empty-set fallback so we never hide every UWP app."""
    assert "$startAppNames.ContainsKey($aumid)" in discover_source
    assert "$startAppNames.Count -gt 0" in discover_source


def test_param_block_stays_first_statement(discover_source: str):
    """The host patches a body line (not a prepend) precisely because `param`
    must remain the first statement; guard that nothing crept in before it."""
    lines = [
        ln.strip()
        for ln in discover_source.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert lines[0].startswith("[CmdletBinding()]") or lines[0].startswith("param(")
