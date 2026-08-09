# SPDX-License-Identifier: MIT
"""Sanity checks for the reverse-open guest registration scripts.

Both .ps1 files run inside the Windows guest, so CI can't exercise them end to
end. These checks (a) pwsh-AST-parse them when pwsh is on PATH (CI has it;
skipped on the dev box) so a syntax error can't merge silently, and (b)
statically assert the URL-scheme registration invariants (#694): the scheme
denylist stays in lockstep with the host's, the ProgID is marked as a protocol
handler, and everything the register script writes is torn down again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REVERSE_OPEN_DIR = REPO_ROOT / "config" / "oem" / "reverse-open"
REGISTER_PS1 = REVERSE_OPEN_DIR / "register-apps.ps1"
UNREGISTER_PS1 = REVERSE_OPEN_DIR / "unregister-apps.ps1"


@pytest.fixture(scope="module")
def register_source() -> str:
    assert REGISTER_PS1.is_file(), f"register-apps.ps1 missing at {REGISTER_PS1}"
    return REGISTER_PS1.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def unregister_source() -> str:
    assert UNREGISTER_PS1.is_file(), f"unregister-apps.ps1 missing at {UNREGISTER_PS1}"
    return UNREGISTER_PS1.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", [REGISTER_PS1, UNREGISTER_PS1], ids=lambda p: p.name)
def test_pwsh_parse(script: Path) -> None:
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
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}', "
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


def test_scheme_denylist_matches_the_host(register_source: str) -> None:
    """The guest-side guard is coarse, but it must not permit anything the
    host's authoritative denylist refuses -- otherwise a scheme could be
    registered on the guest that the listener will always reject."""
    from winpodx.core.url_schemes import DANGEROUS_SCHEMES

    for scheme in DANGEROUS_SCHEMES:
        assert f"'{scheme}'" in register_source, f"{scheme} missing from guest denylist"


def test_scheme_mime_is_recognised(register_source: str) -> None:
    """Resolve-MimeExtensions can't match x-scheme-handler/* (its pattern has
    no dashes), which is why scheme MIMEs used to be dropped silently."""
    assert "Resolve-MimeScheme" in register_source
    assert "^x-scheme-handler/(.+)$" in register_source


def test_scheme_syntax_rule_mirrors_the_host(register_source: str) -> None:
    from winpodx.core.url_schemes import SAFE_SCHEME_RE

    # Same shape as core/url_schemes.py's SAFE_SCHEME_RE, in PowerShell form.
    assert r"^[a-z][a-z0-9+.\-]{0,31}$" in register_source
    assert SAFE_SCHEME_RE.pattern == r"^[a-z][a-z0-9+.\-]{0,31}$"


def test_progid_is_marked_as_protocol_handler(register_source: str) -> None:
    """Without an (empty) "URL Protocol" value the ProgID is not a legal
    protocol-handler target and Windows ignores it for links."""
    assert "'URL Protocol'" in register_source


def test_browser_schemes_register_under_start_menu_internet(register_source: str) -> None:
    """An app claiming http/https only appears in Settings -> Default apps ->
    Web browser if it lives under StartMenuInternet."""
    assert "StartMenuInternet" in register_source
    assert "Contains('http')" in register_source


def test_registered_applications_entry_is_written(register_source: str) -> None:
    assert "RegisteredApplications" in register_source
    assert "URLAssociations" in register_source


def test_unregister_removes_everything_register_writes(unregister_source: str) -> None:
    """Every scheme-registration surface must be torn down, or a reinstall
    leaves dangling handlers pointing at a shim that no longer exists."""
    for surface in (
        "RegisteredApplications",
        "StartMenuInternet",
        r"HKCU:\Software\winpodx",
    ):
        assert surface in unregister_source, f"{surface} not cleaned up"


def test_unregister_honours_dry_run_for_scheme_keys(unregister_source: str) -> None:
    """The script's --DryRun contract: report, never mutate."""
    scheme_section = unregister_source.split("URL-scheme registration")[1]
    scheme_section = scheme_section.split("per-ext OpenWithList")[0]
    assert "[dry-run] would remove RegisteredApplications" in scheme_section
    assert scheme_section.count("if ($DryRun)") >= 2
