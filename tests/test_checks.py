# SPDX-License-Identifier: MIT
"""Tests for winpodx.core.checks — health probes feed `winpodx check` + GUI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from winpodx.core import checks
from winpodx.core.checks import Probe
from winpodx.core.config import Config


def test_probe_dataclass_is_immutable():
    p = Probe(name="x", status="ok", detail="d", duration_ms=1)
    with pytest.raises(Exception):
        p.status = "fail"  # type: ignore[misc]


def test_probe_is_ok_property():
    assert Probe("x", "ok", "", 0).is_ok
    assert not Probe("x", "warn", "", 0).is_ok
    assert not Probe("x", "fail", "", 0).is_ok
    assert not Probe("x", "skip", "", 0).is_ok


def test_overall_all_ok():
    probes = [Probe("a", "ok", "", 0), Probe("b", "ok", "", 0)]
    assert checks.overall(probes) == "ok"


def test_overall_warn_wins_over_ok():
    probes = [Probe("a", "ok", "", 0), Probe("b", "warn", "", 0)]
    assert checks.overall(probes) == "warn"


def test_overall_fail_wins_over_everything():
    probes = [Probe("a", "ok", "", 0), Probe("b", "warn", "", 0), Probe("c", "fail", "", 0)]
    assert checks.overall(probes) == "fail"


def test_overall_skip_treated_as_neutral():
    """A skip should not pull the verdict away from ok."""
    probes = [Probe("a", "ok", "", 0), Probe("b", "skip", "", 0)]
    assert checks.overall(probes) == "ok"


def test_overall_empty_iterable():
    assert checks.overall([]) == "ok"


def test_probe_never_raises_on_internal_exception():
    """The _timed wrapper must catch any exception and surface as fail.

    A buggy probe should NOT take down `winpodx check` for the whole
    user — kernalix7's UI freezing on a transient AgentClient bug was
    exactly this class of regression.
    """

    def boom(_cfg) -> Probe:
        raise RuntimeError("simulated")

    # The probe wrapper inside checks.py wraps each function; for an
    # external function we apply _timed directly to verify the contract.
    status, detail, ms = checks._timed(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert status == "fail"
    assert "boom" in detail or "RuntimeError" in detail
    assert ms >= 0


def test_run_all_returns_one_probe_per_registered_function(monkeypatch):
    """run_all must call every probe in PROBES and return a Probe for each."""
    fake_cfg = object()
    fake_probes = [Probe(f"p{i}", "ok", f"detail-{i}", i) for i, _ in enumerate(checks.PROBES)]

    monkeypatch.setattr(checks, "PROBES", tuple(lambda _cfg, n=p: n for p in fake_probes))
    out = checks.run_all(fake_cfg)
    assert len(out) == len(fake_probes)
    assert all(isinstance(p, Probe) for p in out)


def test_run_all_skips_exec_probes_when_agent_health_not_ok(monkeypatch):
    fake_cfg = object()
    called: list[str] = []

    def pod(_cfg):
        return Probe("pod_running", "ok", "running", 1)

    def rdp(_cfg):
        return Probe("rdp_port", "ok", "rdp", 1)

    def agent(_cfg):
        return Probe("agent_health", "warn", "agent warming up", 1)

    def auth(_cfg):
        called.append("auth")
        return Probe("agent_auth_ready", "ok", "token", 1)

    def guest_exec(_cfg):
        called.append("exec")
        return Probe("guest_exec", "ok", "exec", 1)

    def guest_summary(_cfg):
        called.append("summary")
        return Probe("guest_summary", "ok", "summary", 1)

    monkeypatch.setattr(checks, "probe_agent_auth_ready", auth)
    monkeypatch.setattr(checks, "probe_guest_exec", guest_exec)
    monkeypatch.setattr(checks, "probe_guest_summary", guest_summary)
    monkeypatch.setattr(
        checks,
        "PROBES",
        (
            pod,
            rdp,
            agent,
            checks.probe_agent_auth_ready,
            checks.probe_guest_exec,
            checks.probe_guest_summary,
        ),
    )

    out = checks.run_all(fake_cfg)

    by_name = {p.name: p for p in out}
    assert by_name["agent_auth_ready"].status == "skip"
    assert by_name["guest_exec"].status == "skip"
    assert by_name["guest_summary"].status == "skip"
    assert called == []


def test_run_all_skips_exec_probes_when_auth_not_ready(monkeypatch):
    fake_cfg = object()
    called: list[str] = []

    def pod(_cfg):
        return Probe("pod_running", "ok", "running", 1)

    def rdp(_cfg):
        return Probe("rdp_port", "ok", "rdp", 1)

    def agent(_cfg):
        return Probe("agent_health", "ok", "version=x", 1)

    def auth(_cfg):
        return Probe("agent_auth_ready", "fail", "token missing", 1)

    def guest_exec(_cfg):
        called.append("exec")
        return Probe("guest_exec", "ok", "exec", 1)

    monkeypatch.setattr(checks, "probe_agent_auth_ready", auth)
    monkeypatch.setattr(checks, "probe_guest_exec", guest_exec)
    monkeypatch.setattr(
        checks,
        "PROBES",
        (pod, rdp, agent, checks.probe_agent_auth_ready, checks.probe_guest_exec),
    )

    out = checks.run_all(fake_cfg)

    by_name = {p.name: p for p in out}
    assert by_name["agent_auth_ready"].status == "fail"
    assert by_name["guest_exec"].status == "skip"
    assert called == []


def test_probe_pod_running_handles_pod_status_failure(monkeypatch):
    """If pod_status raises, the probe must return fail, not propagate."""
    import winpodx.core.pod as pod_mod

    def boom(_cfg):
        raise RuntimeError("backend gone")

    monkeypatch.setattr(pod_mod, "pod_status", boom)
    out = checks.probe_pod_running(_FakeCfg())
    assert out.name == "pod_running"
    assert out.status == "fail"
    assert "backend gone" in out.detail or "RuntimeError" in out.detail


def test_probe_password_age_skip_when_disabled():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 0
    out = checks.probe_password_age(cfg)
    assert out.status == "skip"


def test_probe_password_age_warn_when_no_timestamp():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 7
    cfg.rdp.password_updated = ""
    out = checks.probe_password_age(cfg)
    assert out.status == "warn"


def test_probe_password_age_warn_when_overdue():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 1
    cfg.rdp.password_updated = "2020-01-01T00:00:00+00:00"
    out = checks.probe_password_age(cfg)
    assert out.status == "warn"
    assert "overdue" in out.detail


def test_probe_guest_exec_skip_when_pod_not_running(monkeypatch):
    """When the pod isn't running, the round-trip probe must skip rather than
    fail — every guest probe would red-X otherwise and drown out the actual
    "pod is down" signal."""
    import winpodx.core.pod as pod_mod
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.STOPPED))
    out = checks.probe_guest_exec(_FakeCfg())
    assert out.status == "skip"
    assert "pod" in out.detail.lower()


def test_probe_guest_summary_skip_when_pod_not_running(monkeypatch):
    import winpodx.core.pod as pod_mod
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.STOPPED))
    out = checks.probe_guest_summary(_FakeCfg())
    assert out.status == "skip"


def test_probe_guest_exec_fail_when_agent_unreachable(monkeypatch):
    """When the pod IS running but agent is down, the probe must fail with
    a useful detail — proves the round-trip channel is broken even though
    the container is up."""
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import AgentUnavailableError
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            raise AgentUnavailableError("connection refused")

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_guest_exec(_FakeCfg())
    assert out.status == "fail"
    assert "connection refused" in out.detail


def test_probe_agent_auth_ready_ok(monkeypatch):
    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def auth_ready(self):
            return True, ""

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_agent_auth_ready(_FakeCfg())
    assert out.status == "ok"


def test_probe_agent_auth_ready_fail(monkeypatch):
    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def auth_ready(self):
            return False, "agent token file missing"

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_agent_auth_ready(_FakeCfg())
    assert out.status == "fail"
    assert "token" in out.detail


def test_probe_agent_health_timeout_is_warmup_warn(monkeypatch):
    from winpodx.core.agent import AgentTimeoutError

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def health(self):
            raise AgentTimeoutError("/health timed out after 2.0s")

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_agent_health(_FakeCfg())
    assert out.status == "warn"
    assert "warming up" in out.detail


def test_probe_guest_exec_timeout_is_warmup_warn(monkeypatch):
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import AgentTimeoutError
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            raise AgentTimeoutError("/exec timed out after 9.0s")

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_guest_exec(_FakeCfg())
    assert out.status == "warn"
    assert "warming up" in out.detail


def test_probe_guest_summary_timeout_is_warmup_warn(monkeypatch):
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import AgentTimeoutError
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _FakeClient:
        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            raise AgentTimeoutError("/exec timed out after 14.0s")

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
    out = checks.probe_guest_summary(_FakeCfg())
    assert out.status == "warn"
    assert "warming up" in out.detail


def test_probe_apps_discovered_warn_on_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.core.discovery.discovered_apps_dir", lambda: tmp_path / "missing")
    out = checks.probe_apps_discovered(_FakeCfg())
    assert out.status == "warn"
    assert "no apps" in out.detail.lower()


def test_probe_apps_discovered_ok_with_app_subdirs(tmp_path, monkeypatch):
    apps_dir = tmp_path / "discovered"
    apps_dir.mkdir()
    for slug in ("notepad", "msedge"):
        (apps_dir / slug).mkdir()
        (apps_dir / slug / "app.toml").write_text("name = '" + slug + "'\n")
    monkeypatch.setattr("winpodx.core.discovery.discovered_apps_dir", lambda: apps_dir)
    out = checks.probe_apps_discovered(_FakeCfg())
    assert out.status == "ok"
    assert "2 app(s)" in out.detail


@pytest.mark.parametrize(
    ("state", "status", "detail"),
    [
        ("RUNNING", "ok", "running"),
        ("STARTING", "warn", "starting"),
        ("STOPPED", "fail", "stopped"),
    ],
)
def test_probe_pod_running_verdicts(monkeypatch, state, status, detail):
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=getattr(PodState, state), ip="10.0.0.2"),
    )

    out = checks.probe_pod_running(Config())

    assert out.status == status
    assert detail in out.detail


@pytest.mark.parametrize(
    ("reachable", "status", "detail"),
    [(True, "ok", "reachable"), (False, "fail", "not reachable")],
)
def test_probe_rdp_port_verdicts(monkeypatch, reachable, status, detail):
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *_a, **_k: reachable)

    out = checks.probe_rdp_port(_FakeCfg())

    assert out.status == status
    assert "127.0.0.1:3390" in out.detail
    assert detail in out.detail


def test_probe_agent_health_ok_and_error(monkeypatch):
    from winpodx.core.agent import AgentUnavailableError

    client = type(
        "Client",
        (),
        {
            "__init__": lambda self, _cfg: None,
            "health": lambda self: {"version": "1.2.3"},
        },
    )
    monkeypatch.setattr("winpodx.core.agent.AgentClient", client)
    healthy = checks.probe_agent_health(_FakeCfg())
    assert healthy.status == "ok"
    assert healthy.detail == "version=1.2.3"

    def unavailable(_self):
        raise AgentUnavailableError("connection refused")

    client.health = unavailable
    failed = checks.probe_agent_health(_FakeCfg())
    assert failed.status == "fail"
    assert "connection refused" in failed.detail


def test_probe_guest_exec_round_trip_verdicts(monkeypatch):
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import ExecResult
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _Client:
        result = ExecResult(rc=0, stdout="ok\n", stderr="")

        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            return self.result

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _Client)
    passed = checks.probe_guest_exec(_FakeCfg())
    assert passed.status == "ok"
    assert "round-trip OK" in passed.detail

    _Client.result = ExecResult(rc=9, stdout="", stderr="access denied")
    failed = checks.probe_guest_exec(_FakeCfg())
    assert failed.status == "fail"
    assert "rc=9" in failed.detail

    _Client.result = ExecResult(rc=0, stdout="wrong", stderr="")
    degraded = checks.probe_guest_exec(_FakeCfg())
    assert degraded.status == "warn"
    assert "unexpected stdout" in degraded.detail


def test_probe_guest_summary_parses_snapshot_and_rejects_bad_json(monkeypatch):
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import ExecResult
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _Client:
        stdout = (
            '{"os":"Windows 11","build":"26100","uptime_h":5.5,'
            '"user":"WPX-User","sessions":2,"c_free_gb":42.0}'
        )

        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            return ExecResult(rc=0, stdout=self.stdout, stderr="")

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _Client)
    passed = checks.probe_guest_summary(_FakeCfg())
    assert passed.status == "ok"
    assert "Windows 11 build=26100" in passed.detail
    assert "sessions=2" in passed.detail
    assert "C:=42.0GiB free" in passed.detail

    _Client.stdout = "not-json"
    degraded = checks.probe_guest_summary(_FakeCfg())
    assert degraded.status == "warn"
    assert "non-JSON stdout" in degraded.detail


@pytest.mark.parametrize(
    ("bundled", "status", "detail"),
    [("(unknown)", "warn", "marker missing"), ("6.02", "ok", "bundle=6.02")],
)
def test_probe_oem_version_verdicts(monkeypatch, bundled, status, detail):
    monkeypatch.setattr("winpodx.core.info._bundled_oem_version", lambda: bundled)

    out = checks.probe_oem_version(_FakeCfg())

    assert out.status == status
    assert detail in out.detail


@pytest.mark.parametrize(
    ("enabled", "available", "status", "detail"),
    [
        (False, False, "skip", "disabled"),
        (True, True, "ok", "present"),
        (True, False, "warn", "not found"),
    ],
)
def test_probe_guest_mount_verdicts(monkeypatch, enabled, available, status, detail):
    cfg = Config()
    cfg.reverse_open.enabled = enabled
    monkeypatch.setattr("winpodx.core.guest_disk.kio_fuse_available", lambda: available)

    out = checks.probe_guest_mount(cfg)

    assert out.status == status
    assert detail in out.detail


def test_probe_password_age_warns_on_invalid_and_ok_on_fresh_timestamp():
    cfg = Config()
    cfg.rdp.password_max_age = 7
    cfg.rdp.password_updated = "not-a-date"
    invalid = checks.probe_password_age(cfg)
    assert invalid.status == "warn"
    assert "unparseable timestamp" in invalid.detail

    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
    fresh = checks.probe_password_age(cfg)
    assert fresh.status == "ok"
    assert "7d remaining" in fresh.detail


@pytest.mark.parametrize(
    ("free_gb", "status"),
    [(1.0, "fail"), (5.0, "warn"), (20.0, "ok")],
)
def test_probe_disk_free_thresholds(monkeypatch, free_gb, status):
    usage = type("Usage", (), {"free": int(free_gb * 1024**3), "total": 100 * 1024**3})()
    monkeypatch.setattr("shutil.disk_usage", lambda _target: usage)

    out = checks.probe_disk_free(_FakeCfg())

    assert out.status == status
    assert f"{free_gb:.1f}/100 GiB free" == out.detail


# --- guest_summary PowerShell payload (#895) ---
#
# The probe's rc comes from the payload's $LASTEXITCODE, which `query session`
# sets to 1 even when it prints the table. These tests pin the script-boundary
# fix: the reset is narrow (a later failure still fails), and the session count
# only matches real `rdp-tcp#<n>` rows on a localized guest.

# German-labelled `query session` output: the listener state reads "Abhör.",
# a console user's name contains "rdp", and three rows are real RDP sessions.
_QUERY_SESSION_TABLE = """\
 SITZUNGSNAME      BENUTZERNAME             ID  STATUS    TYP         GERÄT
 services                                    0  Getrennt
>console           RDPUser                   1  Aktiv
 rdp-tcp                                 65536  Abhör.
 rdp-tcp#8other     not-rdp                  6  Aktiv
 rdp-tcp#0         WPX-User                  2  Aktiv
 rdp-tcp#12        other                     3  Aktiv
>rdp-tcp#3         WPX-User                  4  Aktiv
"""

_CIM_STUB = """\
function Get-CimInstance {
    param(
        [string]$ClassName,
        [string]$Filter,
        [System.Management.Automation.ActionPreference]$ErrorAction
    )
    switch ($ClassName) {
        'Win32_OperatingSystem' {
            [pscustomobject]@{
                Caption = 'Microsoft Windows 11 Pro'
                BuildNumber = '26200'
                LastBootUpTime = (Get-Date).AddHours(-0.8)
            }
        }
        'Win32_ComputerSystem' { [pscustomobject]@{ UserName = 'HOST\\WPX-User' } }
        'Win32_LogicalDisk' { [pscustomobject]@{ FreeSpace = 45113556992 } }
    }
}
"""


def _pwsh_or_skip() -> str:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not installed on this host")
    return pwsh


def _install_query_fixture(bin_dir: Path, table: str = _QUERY_SESSION_TABLE) -> None:
    """Put a native `query` on PATH that prints `table` and exits 1.

    The non-zero exit is what the reporter saw: full output, rc=1. Only a real
    native command sets $LASTEXITCODE, so a shell script is the faithful stub.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "query"
    script.write_text(f"#!/bin/sh\ncat <<'TABLE'\n{table}TABLE\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)


def _run_guest_summary(
    pwsh: str, payload: str, tmp_path: Path, bin_dir: Path
) -> subprocess.CompletedProcess[str]:
    """Run the real payload through agent.ps1's `& script; exit $LASTEXITCODE`.

    CIM cmdlets do not exist on Linux pwsh, so the Win32 queries are stubbed in
    front of the payload; `query` stays a real native process so the exit-code
    leak goes through the same boundary the guest agent uses.
    """
    payload_file = tmp_path / "summary.ps1"
    payload_file.write_text(_CIM_STUB + "\n" + payload + "\n", encoding="utf-8")
    launcher = tmp_path / "launcher.ps1"
    launcher.write_text(
        f"& '{payload_file}'\n$rc = $LASTEXITCODE\nif ($null -eq $rc) {{ $rc = 0 }}\nexit $rc\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(launcher)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_guest_summary_payload_rc0_on_localized_table(tmp_path):
    pwsh = _pwsh_or_skip()
    bin_dir = tmp_path / "bin"
    _install_query_fixture(bin_dir)

    proc = _run_guest_summary(pwsh, checks._GUEST_SUMMARY_SCRIPT, tmp_path, bin_dir)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload == {
        "os": "Microsoft Windows 11 Pro",
        "build": "26200",
        "uptime_h": 0.8,
        "user": "HOST\\WPX-User",
        "c_free_gb": 42.0,
        "sessions": 3,
    }


def test_guest_summary_payload_rc0_without_session_rows(tmp_path):
    """Empty `query session` output (still rc=1) must not fail the payload."""
    pwsh = _pwsh_or_skip()
    bin_dir = tmp_path / "bin"
    _install_query_fixture(bin_dir, table="")

    proc = _run_guest_summary(pwsh, checks._GUEST_SUMMARY_SCRIPT, tmp_path, bin_dir)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["sessions"] == 0


def test_probe_guest_summary_keeps_warn_on_real_exec_failure(monkeypatch):
    """A payload-side reset must not make the host ignore a nonzero rc."""
    import winpodx.core.pod as pod_mod
    from winpodx.core.agent import ExecResult
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING))

    class _Client:
        def __init__(self, _cfg) -> None:
            pass

        def exec(self, *_a, **_k):
            return ExecResult(
                rc=1,
                stdout='{"os":"Windows 11","build":"26200","sessions":1}',
                stderr="",
            )

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _Client)
    out = checks.probe_guest_summary(_FakeCfg())

    assert out.status == "warn"
    assert "rc=1" in out.detail
    assert "stderr=''" in out.detail


# --- Helpers ---


class _FakeCfg:
    """Minimal stand-in for Config — only the fields probes touch."""

    class _Pod:
        backend = "podman"

    class _Rdp:
        ip = "127.0.0.1"
        port = 3390
        password = "x"
        password_max_age = 0
        password_updated = ""

    def __init__(self):
        self.pod = self._Pod()
        self.rdp = self._Rdp()
