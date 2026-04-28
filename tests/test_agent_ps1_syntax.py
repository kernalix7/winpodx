"""Sanity check that ``config/oem/agent/agent.ps1`` parses cleanly.

Linux CI rarely has ``pwsh`` available, so the primary check is a
deterministic structural scan: matched braces, expected endpoint
strings, loopback bind only, and core PowerShell building blocks. When
``pwsh`` *is* on PATH we additionally let it parse the file via
``[scriptblock]::Create``, which catches grammar errors the structural
scan can't.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_PS1 = REPO_ROOT / "config" / "oem" / "agent" / "agent.ps1"


def _strip_strings_and_comments(src: str) -> str:
    """Drop string literals and comments so brace-matching ignores them.

    Handles double-quoted, single-quoted, here-strings (@" ... "@ and
    @' ... '@), line comments (``#``) and block comments (``<# #>``).
    Not a real PowerShell parser — good enough to keep ``{`` / ``}``
    counts honest in the presence of payload strings that legitimately
    contain unbalanced braces (the apply payloads do).
    """
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        # Block comment <# ... #>
        if ch == "<" and nxt == "#":
            end = src.find("#>", i + 2)
            i = n if end == -1 else end + 2
            continue
        # Line comment
        if ch == "#":
            end = src.find("\n", i)
            i = n if end == -1 else end
            continue
        # Here-string @" ... "@  or  @' ... '@
        if ch == "@" and nxt in ('"', "'"):
            quote = nxt
            terminator = '\n"@' if quote == '"' else "\n'@"
            end = src.find(terminator, i + 2)
            i = n if end == -1 else end + len(terminator)
            continue
        # Regular quoted strings (no escape handling needed for our scan)
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < n:
                if src[j] == quote:
                    # PowerShell uses doubled-quote escapes inside strings.
                    if j + 1 < n and src[j + 1] == quote:
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1 if j < n else n
            continue
        out.append(ch)
        i += 1
    return "".join(out)


@pytest.fixture(scope="module")
def ps1_source() -> str:
    """Read agent.ps1 once for the whole module."""
    assert AGENT_PS1.is_file(), f"agent.ps1 missing at {AGENT_PS1}"
    return AGENT_PS1.read_text(encoding="utf-8")


def test_agent_ps1_endpoints_present(ps1_source: str) -> None:
    """All five documented endpoints appear in the source."""
    for ep in ("/health", "/exec", "/events", "/apply", "/discover"):
        assert ep in ps1_source, f"endpoint string {ep!r} missing from agent.ps1"


def test_agent_ps1_loopback_only(ps1_source: str) -> None:
    """Agent must bind to 127.0.0.1 and never to 0.0.0.0."""
    assert "127.0.0.1" in ps1_source, "agent.ps1 missing loopback bind 127.0.0.1"
    assert "0.0.0.0" not in ps1_source, "agent.ps1 must not bind to 0.0.0.0"


def test_agent_ps1_uses_httplistener(ps1_source: str) -> None:
    """Agent uses the .NET HttpListener class for its HTTP server."""
    assert "[System.Net.HttpListener]" in ps1_source


def test_agent_ps1_braces_balanced(ps1_source: str) -> None:
    """Brace counts (outside strings/comments) match — quick syntax sanity."""
    stripped = _strip_strings_and_comments(ps1_source)
    opens = stripped.count("{")
    closes = stripped.count("}")
    assert opens == closes, f"unbalanced braces: {{={opens} }}={closes}"
    parens_open = stripped.count("(")
    parens_close = stripped.count(")")
    assert parens_open == parens_close, f"unbalanced parens: (={parens_open} )={parens_close}"


def test_agent_ps1_no_double_semicolons(ps1_source: str) -> None:
    """``;;`` is almost never intentional in PowerShell — flag it."""
    stripped = _strip_strings_and_comments(ps1_source)
    assert ";;" not in stripped, "found ;; outside strings/comments"


def test_agent_ps1_parses_with_pwsh(ps1_source: str) -> None:
    """If ``pwsh`` is available, ask it to parse the file; else skip."""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh not installed on this CI runner")
    cmd = [
        pwsh,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{AGENT_PS1}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "  $errors | ForEach-Object { Write-Error $_.Message }; "
            "  exit 1 "
            "}"
        ),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"pwsh parse failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
