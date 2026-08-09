# SPDX-License-Identifier: MIT
"""Sanity checks for config/oem/agent/agent.ps1 (Phase 2).

The .ps1 script runs inside Windows, so we cannot exercise it from CI.
We instead pin the invariants the host code depends on:

- pwsh AST parse (skipped when pwsh isn't on PATH; CI has it).
- The literal markers Phase 1 + Phase 2 promise to ship: HttpListener,
  Prefix, /health, Wait-Token (Phase 1) and Test-Auth, /exec, 401,
  Bearer, base64 decoding (Phase 2).
- The bind prefix is loopback only (127.0.0.1, never 0.0.0.0 / `+` / `*`).
  Anti-goal of v0.2.2.x design: a non-loopback bind would expose the
  agent on the QEMU NAT, breaking the threat model.
- /health stays no-auth (anti-goal: don't auth-protect the readiness
  signal). The test asserts the dispatch shape: /health is matched
  before any Test-Auth call.
- The token is never logged or echoed back. The /exec script content
  lands in agent.log only as a SHA256 hash, never the raw payload.
- Wait-Token / Read-Token never raise. anti-goal #6: throwing kills
  the process and HKCU\\Run does not respawn it.
- Brace balance — guards against partial edits leaving the file
  half-parsed in the absence of pwsh on the dev box.
"""

from __future__ import annotations

import re
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


def _strip_comments(source: str) -> str:
    """Strip `#` line comments only, preserving string literals.

    Tests that need to see tokens which appear inside string literals
    (e.g. `'/health'` in dispatch, `"hash=$x"` in log lines) use this.
    """
    return "\n".join(
        (raw if (idx := raw.find("#")) == -1 else raw[:idx]) for raw in source.splitlines()
    )


def _strip_comments_and_strings(source: str) -> str:
    """Coarse strip of `#` line comments AND quoted string literals.

    Used by tests that scan for executable tokens (`throw`, function
    calls, log-sink calls) where matches inside a string literal would
    be a false positive. Not a full PowerShell tokenizer — but
    agent.ps1 doesn't use here-strings or backtick-escaped quotes
    inside strings, so this is sufficient.
    """
    cleaned: list[str] = []
    for raw in source.splitlines():
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
    return "\n".join(cleaned)


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


def test_agent_ps1_has_phase1_markers(agent_source: str):
    for marker in ("HttpListener", "Prefix", "/health", "Wait-Token"):
        assert marker in agent_source, f"missing required Phase 1 marker: {marker!r}"


def test_agent_ps1_has_phase2_markers(agent_source: str):
    """Phase 2 surface area: auth + /exec must all be wired in."""
    expected = (
        "Test-Auth",
        "/exec",
        "Bearer",
        "Compare-Constant",
        "FromBase64String",
        "401",
        "unauthorized",
    )
    for marker in expected:
        assert marker in agent_source, f"missing required Phase 2 marker: {marker!r}"


def test_agent_ps1_sets_401_status_code(agent_source: str):
    """The 401 path must actually pass 401 to Send-Json (which sets
    StatusCode), not just embed the literal in a comment."""
    assert re.search(r"Send-Json\s+\$resp\s+401\b", agent_source), (
        "no Send-Json call with status code 401 found"
    )


def test_agent_ps1_bind_prefix(agent_source: str):
    """Prefix must be ``http://+:8765/`` (all interfaces, port 8765).

    Why all-interfaces (``+``) and not ``127.0.0.1``: dockur's user-mode
    QEMU NAT delivers forwarded packets to the VM's slirp interface
    (10.0.2.15:8765), NOT to the VM's 127.0.0.1. A 127.0.0.1-only
    listener inside Windows means slirp's forwarded packets hit a
    closed port — kernalix7 saw "Connection reset by peer" on
    2026-04-30 from exactly this. The agent stays externally
    unreachable because compose's ``127.0.0.1:8765:8765/tcp`` mapping
    is host-loopback-only and the QEMU slirp net is private to the
    container.

    Wildcard ``0.0.0.0`` and ``*`` aren't accepted by HttpListener
    syntax for this purpose; ``+`` is the canonical "all interfaces"
    prefix.
    """
    assert "http://+:8765/" in agent_source
    # Mistakes that have shipped before — guard against regression.
    assert "http://127.0.0.1:8765/" not in agent_source
    assert "http://0.0.0.0:" not in agent_source
    assert "http://*:" not in agent_source


def test_agent_ps1_health_is_unauthenticated(agent_source: str):
    """/health must be matched BEFORE the Test-Auth gate so it answers
    even when no token has been delivered. Anti-goal: never auth-protect
    the readiness signal.

    Concretely, the `-eq '/health'` route check must precede the first
    `Test-Auth $req` call site (not the function definition).
    """
    # Comments stripped but string literals preserved — the '/health'
    # path lives inside a single-quoted string in the dispatch, which
    # _strip_comments_and_strings would erase.
    body = _strip_comments(agent_source)
    health_match = re.search(r"-eq\s+'/health'", body)
    # Match a Test-Auth call (followed by `$req`), not the
    # `function Test-Auth(` definition.
    call_match = re.search(r"(?<!function )Test-Auth\s+\$req", body)
    assert health_match is not None, "no /health route found"
    assert call_match is not None, "no Test-Auth call site found"
    assert health_match.start() < call_match.start(), (
        "/health must be dispatched before the Test-Auth gate"
    )


def test_agent_ps1_no_throw_on_missing_token(agent_source: str):
    """Wait-Token / Read-Token must never raise. Anti-goal #6 in
    AGENT_V2_DESIGN: throwing kills the process and HKCU\\Run does
    NOT auto-restart, so a transient missing-token would brick the
    agent until next user logon.

    The only legitimate throw in agent.ps1 is the HttpListener.Start()
    re-throw — that's a fatal binding failure with nothing left to do
    (urlacl missing / port already in use / etc), and the catch block
    writes the error to agent.log first so the user can see WHY.
    """
    stripped = _strip_comments_and_strings(agent_source)

    # Locate the Read-Token + Wait-Token bodies and assert no `throw`.
    for fn_name in ("Read-Token", "Wait-Token"):
        m = re.search(rf"function\s+{fn_name}\s*\{{", stripped)
        assert m is not None, f"function {fn_name} not found"
        # Walk the brace nesting to find the matching close brace.
        depth = 0
        body_start = m.end() - 1  # the `{` itself
        body_end = None
        for i in range(body_start, len(stripped)):
            ch = stripped[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
        assert body_end is not None, f"unterminated {fn_name} body"
        body = stripped[body_start:body_end]
        assert "throw" not in body, f"{fn_name} must not throw — anti-goal #6 in AGENT_V2_DESIGN"

    # Assert at most one throw in the whole file (the listener-Start fallback).
    assert stripped.count("throw") <= 1, (
        "more throws than expected — Wait-Token / Read-Token / request "
        "loop must all stay catch-and-continue. The single tolerated "
        "throw is the HttpListener.Start() failure re-throw, which is "
        "logged before exit."
    )


def test_agent_ps1_token_not_logged(agent_source: str):
    """`$script:Token` must never appear inside Add-Content / Write-Log
    arguments. The token is the bearer secret; logging it would defeat
    the entire auth model. /exec script payload is logged only by
    SHA256 hash."""
    body = _strip_comments_and_strings(agent_source)
    for line in body.splitlines():
        if "$script:Token" not in line:
            continue
        for sink in ("Add-Content", "Write-Log", "Write-Output", "Write-Host"):
            assert sink not in line, f"token leaked to log sink: {line!r}"


def test_agent_ps1_exec_logs_hash_not_payload(agent_source: str):
    """The /exec handler must compute Get-BytesHash on the decoded
    script and log only that hash — never the raw decoded body."""
    assert "Get-BytesHash" in agent_source, "missing Get-BytesHash helper"
    # The hash reaches Write-Log via the $extraLog channel as a string
    # interpolation `"hash=$($result.hash)"`. Comments stripped, but
    # strings preserved so the interpolation is visible.
    body = _strip_comments(agent_source)
    assert "hash=" in body, "exec hash never reaches the log line"


def test_agent_ps1_braces_balanced(agent_source: str):
    """Curly braces must balance, ignoring strings/comments crudely.

    A partial edit (missing closing brace, stray opening brace) is the most
    common breakage mode for a script we can't run on CI. This is a coarse
    check — the pwsh parse test is the authoritative gate when pwsh is on
    PATH — but it catches the obvious failures on dev boxes without pwsh.
    """
    body = _strip_comments_and_strings(agent_source)
    opens = body.count("{")
    closes = body.count("}")
    assert opens == closes, f"unbalanced braces: {opens} '{{' vs {closes} '}}'"


# --- guest /exec must not lose characters to the console code page ----------
#
# The first attempt at this prepended an encoding preamble to the caller's
# script. That made every /exec unparseable, because PowerShell requires
# param() and [CmdletBinding()] to be the first statement in their file and
# discover_apps.ps1 opens with both. Parsing agent.ps1 alone did not catch it:
# what breaks is the script agent.ps1 ASSEMBLES at runtime. These tests build
# that artifact and parse it.


def _render_launcher(agent_source: str, script_path: str) -> str:
    """Reproduce the launcher agent.ps1 writes, for a given inner script."""
    import re

    m = re.search(r'\$launcher = @"\n(.*?)\n"@', agent_source, re.S)
    assert m, "launcher here-string not found in agent.ps1"
    body = m.group(1)
    # PowerShell here-strings use ` to escape $; the only interpolation left
    # is the script path.
    return body.replace("`$", "$").replace("$tempFile", script_path)


def test_launcher_is_a_separate_file_not_a_preamble(agent_source: str):
    assert "$launchFile = " in agent_source
    assert '-File "' + "' + $launchFile + '" in agent_source
    # The caller's bytes must reach disk untouched.
    assert "[IO.File]::WriteAllBytes($tempFile, $bytes)" in agent_source


def test_exec_child_stdio_is_decoded_as_utf8(agent_source: str):
    assert "$psi.StandardOutputEncoding = New-Object Text.UTF8Encoding $false" in agent_source
    assert "$psi.StandardErrorEncoding  = New-Object Text.UTF8Encoding $false" in agent_source


def test_both_temp_files_are_cleaned_up(agent_source: str):
    assert "foreach ($f in @($tempFile, $launchFile))" in agent_source


def test_assembled_launcher_parses(agent_source: str, tmp_path):
    """Parse the launcher agent.ps1 actually writes — the check that would
    have caught the preamble regression."""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not installed on this host")

    launcher = tmp_path / "launch.ps1"
    launcher.write_text(
        _render_launcher(agent_source, str(tmp_path / "inner.ps1")), encoding="utf-8"
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$errors = $null; "
                f"[void][System.Management.Automation.Language.Parser]::ParseFile('{launcher}', "
                "[ref]$null, [ref]$errors); "
                "if ($errors -and $errors.Count -gt 0) { "
                "  $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 "
                "}"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"launcher does not parse:\n{result.stderr}"


def test_discovery_script_still_parses_when_launched_this_way(agent_source: str, tmp_path):
    """The real inner script keeps its param()/[CmdletBinding()] first, which
    is the property the preamble approach destroyed."""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not installed on this host")

    discover = REPO_ROOT / "scripts" / "windows" / "discover_apps.ps1"
    inner = tmp_path / "inner.ps1"
    # agent.ps1 writes the caller's bytes verbatim -- so this is byte-identical
    # to what the guest would run.
    inner.write_bytes(discover.read_bytes())

    launcher = tmp_path / "launch.ps1"
    launcher.write_text(_render_launcher(agent_source, str(inner)), encoding="utf-8")

    for target in (inner, launcher):
        result = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$errors = $null; "
                    f"[void][System.Management.Automation.Language.Parser]::ParseFile('{target}', "
                    "[ref]$null, [ref]$errors); "
                    "if ($errors -and $errors.Count -gt 0) { "
                    "  $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 "
                    "}"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"{target.name} does not parse:\n{result.stderr}"
