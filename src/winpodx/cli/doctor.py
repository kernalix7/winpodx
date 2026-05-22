# SPDX-License-Identifier: MIT
"""``winpodx doctor`` -- diagnose common winpodx state issues (#255 PR 6).

Read-only diagnostic. Walks a small set of checks for things that
commonly leave users stuck (half-installed state, orphan containers,
stale autostart entries, broken deps) and prints a per-check report
with a severity tag and the suggested next command.

Output format mirrors ``apt`` / ``brew doctor``:

    [OK]   freerdp 3.x present at /usr/bin/xfreerdp3
    [WARN] tray autostart entry references missing binary
           Suggested: winpodx uninstall && winpodx setup
    [FAIL] container winpodx-windows exists but config is missing
           Suggested: winpodx uninstall --purge --yes

Doctor never mutates state -- the suggested commands are printed for
the user to copy.

Exit codes:
    0 -- no FAIL findings (warnings may be present)
    1 -- one or more FAIL findings

Designed to be cheap (< 2 s on a healthy install): every subprocess
probe has a short timeout, and the network never gets touched.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    severity: str  # "ok" | "warn" | "fail"
    title: str
    detail: str = ""
    suggestion: str = ""

    def severity_tag(self) -> str:
        return {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}.get(self.severity, "[?]   ")


def handle_doctor(_args: argparse.Namespace) -> None:
    """Run all checks + print the report. Exit 1 on any FAIL finding."""
    findings: list[Finding] = []

    findings.append(_check_install_source())
    findings.append(_check_freerdp())
    findings.append(_check_kvm())
    findings.extend(_check_container_backend())
    findings.append(_check_config_state())
    findings.extend(_check_container_health())
    findings.append(_check_pending_setup())
    findings.append(_check_autostart_entry())
    findings.append(_check_initialized_flag())

    print()
    print("=== winpodx doctor ===")
    print()
    fail_count = 0
    warn_count = 0
    for f in findings:
        if f is None:
            continue
        if f.severity == "fail":
            fail_count += 1
        elif f.severity == "warn":
            warn_count += 1
        print(f"{f.severity_tag()} {f.title}")
        if f.detail:
            print(f"        {f.detail}")
        if f.suggestion:
            print(f"        Suggested: {f.suggestion}")

    print()
    if fail_count:
        print(f"Summary: {fail_count} FAIL, {warn_count} WARN")
        sys.exit(1)
    elif warn_count:
        print(f"Summary: {warn_count} WARN, no FAIL — winpodx is mostly OK.")
    else:
        print("Summary: all checks passed.")


# -----------------------------------------------------------------------
# Individual checks. Each returns a single Finding or a list of them.
# -----------------------------------------------------------------------


def _check_install_source() -> Finding:
    try:
        from winpodx.utils.install_source import detect

        src = detect()
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "install source detection failed", detail=str(e))
    if src.kind == "unknown":
        return Finding(
            "warn",
            "install source not detected",
            detail=src.label,
            suggestion="Reinstall via curl install.sh or your distro's package manager.",
        )
    return Finding("ok", f"install source: {src.label}")


def _check_freerdp() -> Finding:
    for cmd in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(cmd)
        if path is None:
            continue
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        return Finding(
            "ok",
            f"freerdp present at {path}",
            detail=result.stdout.splitlines()[0] if result.stdout else "",
        )
    return Finding(
        "fail",
        "freerdp not found on PATH",
        detail="Looked for xfreerdp3 and xfreerdp; neither resolved.",
        suggestion="Install via your distro package manager (freerdp / freerdp3 / freerdp-x11).",
    )


def _check_kvm() -> Finding:
    if Path("/dev/kvm").exists():
        return Finding("ok", "/dev/kvm present")
    return Finding(
        "fail",
        "/dev/kvm not present",
        detail=(
            "Hardware virtualization is disabled, missing kvm module, "
            "or your user lacks the kvm group."
        ),
        suggestion=(
            "Check BIOS VT-x/AMD-V, run `modprobe kvm_intel` or "
            "`modprobe kvm_amd`, ensure your user is in the kvm group."
        ),
    )


def _check_container_backend() -> list[Finding]:
    """Probe the configured backend + verify it resolves."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception as e:  # noqa: BLE001
        return [Finding("warn", "config could not be loaded", detail=str(e))]

    backend = cfg.pod.backend
    if backend == "manual":
        return [Finding("ok", "backend = manual (no container management)")]
    path = shutil.which(backend)
    if path is None:
        return [
            Finding(
                "fail",
                f"configured backend {backend!r} not on PATH",
                suggestion=(
                    f"Install {backend} or change backend via `winpodx config set pod.backend ...`."
                ),
            )
        ]
    return [Finding("ok", f"backend {backend!r} at {path}")]


def _check_config_state() -> Finding:
    """Detect half-installed state: binary present but config missing,
    or vice versa."""
    from winpodx.core.config import Config

    config_path = Config.path()
    binary_path = shutil.which("winpodx")
    if binary_path and not config_path.exists():
        return Finding(
            "warn",
            "winpodx binary present but config missing",
            detail=f"binary: {binary_path}; expected config: {config_path}",
            suggestion=(
                "Run `winpodx setup` (or just `winpodx` -- first-run prompt will offer setup)."
            ),
        )
    if config_path.exists() and not binary_path:
        return Finding(
            "fail",
            "config present but winpodx binary not on PATH",
            detail=f"config: {config_path}; PATH binary: missing",
            suggestion="Reinstall winpodx via curl install.sh or your distro's package manager.",
        )
    if not binary_path and not config_path.exists():
        return Finding(
            "warn",
            "winpodx not installed (binary + config both absent)",
            suggestion="Install via `curl ... install.sh | bash` or distro package manager.",
        )
    return Finding("ok", "binary + config both present")


def _check_container_health() -> list[Finding]:
    """Check whether a container exists and matches what config expects."""
    try:
        from winpodx.core.config import Config
    except Exception:  # noqa: BLE001
        return []
    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return []
    if cfg.pod.backend not in ("podman", "docker"):
        return []
    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return []
    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [
            Finding(
                "warn",
                f"could not query {cfg.pod.backend} ps",
                suggestion=f"Check that {cfg.pod.backend} is functional.",
            )
        ]

    findings: list[Finding] = []
    container_name = cfg.pod.container_name
    found = False
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if name == container_name:
            found = True
            findings.append(Finding("ok", f"container {container_name} state: {state.lower()}"))
            break
    if not found:
        findings.append(
            Finding(
                "warn",
                f"container {container_name} not found",
                detail=(
                    "Config references a container that doesn't exist "
                    "(may be intentional if you haven't run setup yet)."
                ),
                suggestion="Run `winpodx pod start` or `winpodx setup` to create it.",
            )
        )
    return findings


def _check_pending_setup() -> Finding:
    """Half-installed marker from install.sh -- means a prior install
    didn't finish wait-ready / migrate / discovery."""
    from winpodx.utils.paths import config_dir

    pending = config_dir() / ".pending_setup"
    if not pending.exists():
        return Finding("ok", "no pending install steps")
    try:
        steps = pending.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        steps = []
    return Finding(
        "warn",
        f"pending setup steps detected ({len(steps)} item(s))",
        detail=", ".join(steps) if steps else "(marker present but empty)",
        suggestion=(
            "Run any `winpodx <cmd>` to auto-resume, or `winpodx pod wait-ready` to retry manually."
        ),
    )


def _check_autostart_entry() -> Finding:
    """Tray autostart entry referencing a missing binary is a common
    leftover after a botched uninstall."""
    from winpodx.utils.paths import config_dir

    autostart = config_dir().parent / "autostart" / "winpodx-tray.desktop"
    if not autostart.exists():
        return Finding("ok", "no autostart entry (or none expected)")
    binary = shutil.which("winpodx")
    if binary is None:
        return Finding(
            "fail",
            "autostart entry references a missing winpodx binary",
            detail=str(autostart),
            suggestion="Run `winpodx uninstall` to clean up the autostart entry.",
        )
    return Finding("ok", "autostart entry present and binary resolves")


def _check_initialized_flag() -> Finding:
    """First-run prompt fires when cfg.pod.initialized is False. Surface
    as info so users know whether the prompt is expected on next run."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return Finding("warn", "could not read initialized flag (config load failed)")
    if cfg.pod.initialized:
        return Finding("ok", "cfg.pod.initialized = true (no first-run prompt expected)")
    return Finding(
        "warn",
        "cfg.pod.initialized = false (first-run prompt will fire on next CLI/GUI launch)",
        suggestion="Run `winpodx setup` to silence the prompt and provision the guest.",
    )
