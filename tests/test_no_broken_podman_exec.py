"""Lint test: prevent regression to the broken `podman exec ... powershell.exe` path.

v0.1.9.5 migrated every host-to-Windows command path to
``winpodx.core.windows_exec.run_in_windows``. The previous architecture
that called ``podman exec winpodx-windows ...\\powershell.exe`` only
reached the dockur Linux container — never the Windows VM inside QEMU —
and so silently no-op'd for releases 0.1.0 through 0.1.9.4.

This test fails if any new code under ``src/winpodx/`` (other than
``windows_exec.py`` itself, which is the canonical channel implementation)
combines the strings ``"exec"`` and ``"powershell"`` in a single file.
That heuristic is loose enough to catch the broken pattern without
flagging legitimate uses of either word in isolation.
"""

from __future__ import annotations

from pathlib import Path

# Files that are allowed to contain both tokens. Add new entries here only
# when the file legitimately wraps the windows_exec channel itself or is
# documentation/test/comment scaffolding.
_ALLOWLIST = {
    "src/winpodx/core/windows_exec.py",
    # discovery.py historically had a podman-exec call site; v0.1.9.5
    # migrated it to windows_exec but the comment block still references
    # the old approach for posterity. The active code uses run_in_windows.
}


def test_no_broken_podman_exec_callers():
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src" / "winpodx"

    offenders: list[tuple[str, list[int]]] = []
    for py_file in src_dir.rglob("*.py"):
        rel = py_file.relative_to(repo_root).as_posix()
        if rel in _ALLOWLIST:
            continue

        try:
            text = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # Skip files that don't have both tokens at all.
        if "exec" not in text or "powershell" not in text.lower():
            continue

        # Walk lines looking for the pattern: a line containing the literal
        # string ``"exec"`` (the subprocess argument) AND a nearby line
        # containing ``"powershell"`` or the WindowsPowerShell path.
        # Allow ``windows_exec`` (module-name uses) and ``run_in_windows``
        # (the canonical helper) without flagging.
        bad_lines: list[int] = []
        lines = text.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip pure comments / docstrings — they document the migration.
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if '"exec"' not in line:
                continue
            # `"exec"` shows up in subprocess arglists for legitimate
            # purposes too (e.g. `podman exec winpodx-windows restart`).
            # The broken pattern is when it's followed within a few lines
            # by a literal `"powershell"` or the powershell.exe path.
            window = "\n".join(lines[i : i + 12]).lower()
            if "powershell" in window and "windows_exec" not in window:
                bad_lines.append(i + 1)

        if bad_lines:
            offenders.append((rel, bad_lines))

    if offenders:
        msg_parts = [
            "Found broken `podman exec ... powershell` patterns. "
            "All Windows-side commands must go through "
            "winpodx.core.windows_exec.run_in_windows. Offending files:"
        ]
        for rel, line_numbers in offenders:
            msg_parts.append(f"  {rel}: lines {line_numbers}")
        raise AssertionError("\n".join(msg_parts))
