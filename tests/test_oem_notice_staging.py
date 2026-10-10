# SPDX-License-Identifier: MIT
"""QA-role regression tests for rdprrap supplemental-notice staging (legal-audit L1).

Legal-audit 0.12.0 L1: the rdprrap ZIP's bundled crate notice uses placeholder
copyright holders and omits the real iced-project / Microsoft notices. The
remediation ships a trusted host file, ``config/oem/rdprrap-NOTICES.txt``,
that must accompany the immutable ZIP and every guest copy of the installed
binaries. These tests pin the three staging seams:

  1. ``config/oem/install.bat`` copies the notice into ``C:\\winpodx\\rdprrap``
     beside the extracted binary *before* activation, and a required copy
     failure does not claim a notice-complete install (it never reaches the
     ``.installed_version`` stamp).
  2. ``config/oem/rdprrap-activate.ps1`` stages the notice into the rdprrap
     dir before any binary use -- verified by a **real PowerShell AST parse**
     (no Windows execution, no cmdlets run).
  3. ``provisioner._apply_vbs_launchers`` serializes the notice alongside
     ``rdprrap-activate.ps1`` into the Public launchers dir byte-exact, and
     aborts (without a /exec round-trip) when the source is missing.

The PowerShell AST tests locate a real ``pwsh`` (``$WINPODX_PWSH``, the release
worktree, or ``PATH``) and skip when none is present, so a pwsh-less CI stays
green. Nothing here launches a Windows binary.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_BAT = REPO_ROOT / "config" / "oem" / "install.bat"
ACTIVATE_PS1 = REPO_ROOT / "config" / "oem" / "rdprrap-activate.ps1"
NOTICE_NAME = "rdprrap-NOTICES.txt"

# Synthetic host notice: mixes CRLF, real copyright holders, backslashes and
# non-ASCII so a Base64 round-trip that mangles bytes -- or a cp1252 re-encode
# -- is caught, not just a filename check.
SYNTHETIC_NOTICE = (
    "WinPodX supplemental notice --- rdprrap 0.3.0 (L1)\r\n"
    "iced-x86 1.21.0 : Copyright (c) iced project and contributors\r\n"
    "windows 0.58.0  : Copyright (c) Microsoft Corporation\r\n"
    "unicode-ident 1.0.24 : MIT / Unicode-3.0\r\n"
    "저작권 고지 보존\r\n"
).encode("utf-8")

_OEM_LAUNCHER_FILES = (
    "hidden-launcher.vbs",
    "launch_uwp.vbs",
    "launch_uwp.ps1",
    "agent-respawn.ps1",
    "agent-keepalive.ps1",
    "rdprrap-activate.ps1",
    "launch_file.vbs",
)

_LAUNCHER_DIR = "C:\\Users\\Public\\winpodx\\launchers"
_NOTICE_TARGET = f"{_LAUNCHER_DIR}\\{NOTICE_NAME}"


# --- provisioner._apply_vbs_launchers (serialized copy) ----------------------


def _mock_run_in_windows(monkeypatch, *, rc: int = 0, stdout: str = "", stderr: str = ""):
    """Capture every (description, payload) /exec call without touching a guest."""
    from winpodx.core.windows_exec import WindowsExecResult

    captured: list[tuple[str, str]] = []

    def fake(cfg, payload, *, timeout=60, description="windows-exec"):
        captured.append((description, payload))
        return WindowsExecResult(rc=rc, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    return captured


def _make_fake_oem(root: Path, *, include_notice: bool) -> None:
    """Write a synthetic ``config/oem`` under ``root`` for ``bundle_dir()``."""
    oem = root / "config" / "oem"
    oem.mkdir(parents=True, exist_ok=True)
    for fname in _OEM_LAUNCHER_FILES:
        (oem / fname).write_bytes(f"{fname} placeholder\r\n".encode("utf-8"))
    if include_notice:
        (oem / NOTICE_NAME).write_bytes(SYNTHETIC_NOTICE)


def _staged_files(payload: str) -> dict[str, bytes]:
    """Decode each ``[IO.File]::WriteAllBytes`` target so bytes, not names, assert."""
    staged: dict[str, bytes] = {}
    lines = payload.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r"\[IO\.File\]::WriteAllBytes\('([^']*)', \$bytes\)", line)
        if not m:
            continue
        target = m.group(1)
        bm = re.search(r"FromBase64String\('([^']*)'\)", lines[i - 1])
        assert bm, f"no Base64 line precedes write of {target!r}: {lines[i - 1]!r}"
        staged[target] = base64.b64decode(bm.group(1))
    return staged


def _cfg():
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "podman"
    return cfg


def test_apply_vbs_launchers_stages_notice_exact_bytes(tmp_path, monkeypatch):
    from winpodx.core import provisioner

    _make_fake_oem(tmp_path, include_notice=True)
    monkeypatch.setattr(provisioner, "bundle_dir", lambda: tmp_path)
    captured = _mock_run_in_windows(
        monkeypatch, rc=0, stdout="vbs_launchers applied + agent respawn queued"
    )

    provisioner._apply_vbs_launchers(_cfg())

    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-vbs-launchers"

    staged = _staged_files(payload)
    assert _NOTICE_TARGET in staged, (
        f"{NOTICE_NAME} was not staged into the Public launchers dir; "
        f"staged targets: {sorted(staged)}"
    )
    assert staged[_NOTICE_TARGET] == SYNTHETIC_NOTICE


def test_apply_vbs_launchers_aborts_when_notice_source_missing(tmp_path, monkeypatch):
    from winpodx.core import provisioner

    _make_fake_oem(tmp_path, include_notice=False)
    monkeypatch.setattr(provisioner, "bundle_dir", lambda: tmp_path)
    captured = _mock_run_in_windows(monkeypatch)

    with pytest.raises(RuntimeError, match=r"vbs_launchers source missing:.*rdprrap-NOTICES\.txt"):
        provisioner._apply_vbs_launchers(_cfg())
    # A missing notice source must abort before any /exec round-trip.
    assert captured == []


def test_apply_vbs_launchers_stages_notice_next_to_activate_ps1(tmp_path, monkeypatch):
    from winpodx.core import provisioner

    _make_fake_oem(tmp_path, include_notice=True)
    monkeypatch.setattr(provisioner, "bundle_dir", lambda: tmp_path)
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="ok")

    provisioner._apply_vbs_launchers(_cfg())

    staged = _staged_files(captured[0][1])
    assert f"{_LAUNCHER_DIR}\\rdprrap-activate.ps1" in staged
    assert _NOTICE_TARGET in staged


# --- install.bat (fresh-OEM source copy) -------------------------------------


def _active_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().casefold().startswith("rem ")
    ]


def test_install_bat_copies_notice_beside_extracted_binary():
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert 'set "RDPRRAP_NOTICE_SRC=%~dp0rdprrap-NOTICES.txt"' in text
    assert 'set "RDPRRAP_NOTICE_DST=%RDPRRAP_DIR%\\rdprrap-NOTICES.txt"' in text
    assert 'copy /Y "%RDPRRAP_NOTICE_SRC%" "%RDPRRAP_NOTICE_DST%"' in text


def test_install_bat_notice_copy_precedes_activation_and_completeness_stamp():
    text = INSTALL_BAT.read_text(encoding="utf-8")
    copy_idx = text.index('copy /Y "%RDPRRAP_NOTICE_SRC%"')
    activate_idx = text.index('-File "%~dp0rdprrap-activate.ps1"')
    stamp_idx = text.index('>"%RDPRRAP_INSTALLED%"')

    assert copy_idx < activate_idx < stamp_idx


def test_install_bat_notice_copy_failure_is_not_notice_complete():
    text = INSTALL_BAT.read_text(encoding="utf-8")
    # Both the missing-source and failed-copy branches classify the status and
    # bail to :rdprrap_done, so the .installed_version stamp below is skipped
    # and the next boot can retry.
    assert text.count("notice-copy-failed") >= 2
    stamp_idx = text.index('>"%RDPRRAP_INSTALLED%"')
    notice_block = text[text.index("RDPRRAP_NOTICE_SRC") : stamp_idx]
    assert "goto :rdprrap_done" in notice_block
    assert "(echo notice-copy-failed)" in notice_block
    assert '"%RDPRRAP_INSTALLED%"' not in notice_block


def test_install_bat_notice_additions_are_ascii():
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text)


# --- rdprrap-activate.ps1 (real PowerShell AST parse, no execution) ----------


def _find_pwsh() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("WINPODX_PWSH")
    if env:
        candidates.append(Path(env))
    candidates.append(REPO_ROOT / ".rtrt" / "tmp" / "release-0.12.0" / "powershell" / "pwsh")
    which = shutil.which("pwsh")
    if which:
        candidates.append(Path(which))
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


PWSH = _find_pwsh()

requires_pwsh = pytest.mark.skipif(
    PWSH is None, reason="no pwsh available for a real PowerShell AST parse"
)


def _pwsh_ast(script: Path) -> dict:
    """Parse ``script`` with the real PowerShell parser and return command AST facts.

    Only ``Parser::ParseFile`` runs -- no cmdlet in the target script is ever
    executed, so this is a static parse with zero Windows side effects.
    """
    program = (
        "$t=$null;$e=$null;"
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$t,[ref]$e);"
        "$cmds=$ast.FindAll({param($n)"
        " $n -is [System.Management.Automation.Language.CommandAst]},$true) |"
        " ForEach-Object { [pscustomobject]@{ name=$_.GetCommandName();"
        " offset=$_.Extent.StartOffset; text=$_.Extent.Text } };"
        "[pscustomobject]@{ errors=@($e | ForEach-Object { $_.Message });"
        " commands=@($cmds) } | ConvertTo-Json -Depth 6 -Compress"
    )
    proc = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", program],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@requires_pwsh
def test_activate_ps1_parses_without_errors() -> None:
    facts = _pwsh_ast(ACTIVATE_PS1)
    assert facts["errors"] == [], f"PowerShell parse errors: {facts['errors']}"


@requires_pwsh
def test_activate_ps1_stages_notice_before_installer_invocation() -> None:
    facts = _pwsh_ast(ACTIVATE_PS1)
    commands = facts["commands"]
    assert any(NOTICE_NAME in c["text"] for c in commands), (
        f"activator never references {NOTICE_NAME}: {[c['name'] for c in commands]}"
    )

    copies = [c for c in commands if c["name"] == "Copy-Item"]
    assert copies, "activator must Copy-Item the notice into the rdprrap dir"

    invocations = [c for c in commands if c["text"].lstrip().startswith("& $installer")]
    assert invocations, "installer invocation (& $installer ...) not found in AST"

    notice_offset = min(c["offset"] for c in copies)
    installer_offset = min(c["offset"] for c in invocations)
    assert notice_offset < installer_offset, (
        "the notice must be staged into the rdprrap dir before any binary use"
    )


def test_notice_filename_is_literal_in_all_three_seams() -> None:
    assert NOTICE_NAME == "rdprrap-NOTICES.txt"
    assert NOTICE_NAME in INSTALL_BAT.read_text(encoding="utf-8")
    assert NOTICE_NAME in ACTIVATE_PS1.read_text(encoding="utf-8")
    assert NOTICE_NAME in (REPO_ROOT / "src" / "winpodx" / "core" / "provisioner.py").read_text(
        encoding="utf-8"
    )
