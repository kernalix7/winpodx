"""Phase 1 sanity checks for config/oem/agent/agent.ps1.

The .ps1 script runs inside Windows, so we cannot exercise it from CI.
We instead pin the invariants the host code (and Phase 2) depends on:

- pwsh AST parse (skipped when pwsh isn't on PATH; CI has it).
- The literal markers Phase 1 promises to ship: HttpListener, Prefix,
  /health, Wait-Token.
- The bind prefix is loopback only (127.0.0.1, never 0.0.0.0 or `+`).
  Anti-goal of v0.2.2.x design: a non-loopback bind would expose the
  agent on the QEMU NAT, breaking the threat model.
- No bare `throw` in the Wait-Token / Read-Token paths. Anti-goal #6:
  throwing on missing token kills the process and HKCU\\Run does not
  respawn it.
- Brace balance — guards against partial edits leaving the file
  half-parsed in the absence of pwsh on the dev box.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_PS1 = REPO_ROOT / "config" / "oem" / "agent" / "agent.ps1"


@pytest.fixture(scope="module")
def agent_source() -> str:
    assert AGENT_PS1.is_file(), f"agent.ps1 missing at {AGENT_PS1}"
    return AGENT_PS1.read_text(encoding="utf-8")


def test_agent_ps1_exists():
    assert AGENT_PS1.is_file()


def test_agent_ps1_pwsh_parse():
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
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{AGENT_PS1}', "
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


def test_agent_ps1_has_required_markers(agent_source: str):
    for marker in ("HttpListener", "Prefix", "/health", "Wait-Token"):
        assert marker in agent_source, f"missing required marker: {marker!r}"


def test_agent_ps1_binds_loopback_only(agent_source: str):
    """Prefix must be the literal http://127.0.0.1:8765/ — never 0.0.0.0 or +."""
    assert "http://127.0.0.1:8765/" in agent_source
    # The two non-loopback shapes that `HttpListener.Prefixes.Add` accepts
    # for "all interfaces" — both must be absent from the source.
    assert "0.0.0.0" not in agent_source
    assert "http://+:" not in agent_source
    assert "http://*:" not in agent_source


def test_agent_ps1_no_throw_on_missing_token(agent_source: str):
    """Wait-Token / Read-Token must never raise. anti-goal #6 in AGENT_V2_DESIGN.

    A `throw` anywhere in the file at all is a smell in Phase 1 — the only
    legitimate uses (auth failure, exec timeout) belong to later phases.
    Phase 1 has no such paths, so any bare `throw` token is a regression.
    """
    # Strip line comments so a `throw` mentioned in a comment doesn't trip.
    stripped_lines = []
    for raw in agent_source.splitlines():
        idx = raw.find("#")
        stripped_lines.append(raw if idx == -1 else raw[:idx])
    stripped = "\n".join(stripped_lines)
    # Look for `throw` as a standalone token (PowerShell statement).
    # Substring match is sufficient — we don't ship any identifier that
    # contains the substring "throw".
    assert "throw" not in stripped, "Phase 1 agent.ps1 must not contain `throw`"


def test_agent_ps1_braces_balanced(agent_source: str):
    """Curly braces must balance, ignoring strings/comments crudely.

    A partial edit (missing closing brace, stray opening brace) is the most
    common breakage mode for a script we can't run on CI. This is a coarse
    check — the pwsh parse test is the authoritative gate when pwsh is on
    PATH — but it catches the obvious failures on dev boxes without pwsh.
    """
    # Strip line comments and naive double-quoted strings; PowerShell here-
    # strings are not used in this file.
    cleaned: list[str] = []
    for raw in agent_source.splitlines():
        idx = raw.find("#")
        line = raw if idx == -1 else raw[:idx]
        out_chars: list[str] = []
        in_str: str | None = None
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str is None:
                if ch in ("'", '"'):
                    in_str = ch
                else:
                    out_chars.append(ch)
            else:
                if ch == in_str:
                    in_str = None
            i += 1
        cleaned.append("".join(out_chars))
    body = "\n".join(cleaned)
    opens = body.count("{")
    closes = body.count("}")
    assert opens == closes, f"unbalanced braces: {opens} '{{' vs {closes} '}}'"
