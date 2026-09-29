# SPDX-License-Identifier: MIT
"""Static checks for config/oem/launch_file.vbs -- the file-open RemoteApp launcher."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCH_FILE_VBS = REPO_ROOT / "config" / "oem" / "launch_file.vbs"


def _text() -> str:
    return LAUNCH_FILE_VBS.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.split("'", 1)[0]
        if line.strip().casefold().startswith("rem "):
            line = ""
        kept.append(line)
    return "\n".join(kept)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_comments(text)).strip().casefold()


def _has_poll_bound(flat: str) -> bool:
    if re.search(r"\b8000\b", flat):
        return True
    if re.search(r"\b40\b", flat):
        return True
    return "timer" in flat and bool(re.search(r"[<>]=?\s*8\b", flat))


def test_launch_file_vbs_exists() -> None:
    assert LAUNCH_FILE_VBS.is_file()


def test_launch_file_vbs_takes_exactly_two_base64_utf8_args() -> None:
    flat = _flat(_text())
    assert "wscript.arguments.count" in flat
    assert re.search(r"wscript\.arguments\.count\s*<>\s*2\b", flat)
    assert re.search(r"wscript\.arguments(?:\.item)?\(0\)", flat)
    assert re.search(r"wscript\.arguments(?:\.item)?\(1\)", flat)
    assert "base64" in flat
    assert re.search(r"utf[\s\-_./]?8", flat)


def test_launch_file_vbs_uses_exact_file_fileexists() -> None:
    flat = _flat(_text())
    assert "scripting.filesystemobject" in flat
    assert "fileexists" in flat
    assert "folderexists" not in flat


def test_launch_file_vbs_polls_conditionally_200ms_bounded_8s() -> None:
    flat = _flat(_text())
    assert re.search(r"\b(?:loop|next)\b", flat)
    assert "wscript.sleep" in flat
    assert re.search(r"\b200\b", flat)
    assert re.search(r"(?:not\s+[^:]*fileexists|until\s+[^:]*fileexists)", flat)
    assert _has_poll_bound(flat)


def test_launch_file_vbs_execs_one_target_without_waiting() -> None:
    flat = _flat(_text())
    assert "wscript.shell" in flat
    assert len(re.findall(r"\.exec\b", flat)) == 1
    assert re.search(
        r"set\s+\w+\s*=\s*\w+\.exec\s*\(\s*quoteargument\s*\(\s*targetexe\s*\)"
        r"\s*&\s*\"\s\"\s*&\s*quoteargument\s*\(\s*filepath\s*\)\s*\)",
        flat,
    )
    assert "shell.application" not in flat
    assert not re.search(r"\.shellexecute\b", flat)
    assert not re.search(r"\.run\b", flat)
    assert not re.search(r"\.(?:status|exitcode|stdin|stdout|stderr)\b", flat)


def test_launch_file_vbs_maps_exec_launch_errors_to_exit_6() -> None:
    flat = _flat(_text())
    assert re.search(r"on\s+error\s+resume\s+next", flat)
    assert re.search(r"err\.clear\b.*?\.exec\b", flat)
    assert re.search(r"\.exec\b.*?if\s+err\.number\s*<>\s*0\s+then\s+wscript\.quit\s+6", flat)


def test_launch_file_vbs_quotes_one_literal_windows_argument() -> None:
    flat = _flat(_text())
    assert re.search(r"function\s+quoteargument\s*\(\s*value\s*\)", flat)
    assert re.search(r"for\s+\w+\s*=\s*len\s*\(\s*value\s*\)\s+to\s+1\s+step\s+-1", flat)
    assert re.search(r"mid\s*\(\s*value\s*,\s*\w+\s*,\s*1\s*\)\s*<>\s*\"\\\"", flat)
    assert re.search(
        r"quoteargument\s*=\s*chr\s*\(\s*34\s*\)\s*&\s*value\s*&\s*"
        r"string\s*\(\s*trailingbackslashes\s*,\s*\"\\\"\s*\)\s*&\s*"
        r"chr\s*\(\s*34\s*\)",
        flat,
    )
    assert "expandenvironmentstrings" not in flat


def test_launch_file_vbs_uses_fixed_errors() -> None:
    flat = _flat(_text())
    assert re.search(r"wscript\.quit\s*\(?\s*\d+", flat)
    assert "msgbox" not in flat
    assert "err.raise" not in flat


def test_launch_file_vbs_rejects_quotes_and_control_chars() -> None:
    flat = _flat(_text())
    assert re.search(r"chr\s*\(\s*34\s*\)", flat)
    uses_asc = re.search(r"asc\w*\s*\(", flat)
    bounds_controls = re.search(r"<\s*32\b", flat) or re.search(r"<=\s*31\b", flat)
    assert (uses_asc and bounds_controls) or re.search(r"chr\s*\(\s*(?:0|31)\s*\)", flat)
    assert re.search(r"wscript\.quit", flat)


def test_launch_file_vbs_has_no_shell_activate_or_fallback_paths() -> None:
    flat = _flat(_text())
    assert "powershell" not in flat
    assert "activateforfile" not in flat
    assert "cmd.exe" not in flat
    assert "cmd /c" not in flat
    assert "cmd/c" not in flat
    assert "%comspec%" not in flat
    assert "fallback" not in flat
    assert "fall back" not in flat
    assert "fall-back" not in flat
