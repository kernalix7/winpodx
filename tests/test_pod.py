"""Tests for winpodx.core.pod — focused on Bug B's RDP recovery helper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from winpodx.core.config import Config
from winpodx.core.pod import recover_rdp_if_needed


def _cfg(backend: str = "podman") -> Config:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.port = 3390
    cfg.pod.vnc_port = 8007
    cfg.pod.container_name = "winpodx-windows"
    return cfg


# --- recover_rdp_if_needed: short-circuit paths ---


def test_recover_rdp_no_op_when_rdp_already_alive(monkeypatch):
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", lambda *a, **k: True)
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    assert recover_rdp_if_needed(_cfg()) is True
    fake_run.assert_not_called()


def test_recover_rdp_returns_false_when_vnc_also_dead(monkeypatch):
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", lambda *a, **k: False)
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    assert recover_rdp_if_needed(_cfg()) is False
    fake_run.assert_not_called()


def test_recover_rdp_skips_libvirt_backend(monkeypatch):
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    # Returns True for libvirt/manual so callers don't block.
    assert recover_rdp_if_needed(_cfg(backend="libvirt")) is True
    assert recover_rdp_if_needed(_cfg(backend="manual")) is True
    fake_run.assert_not_called()


def test_recover_rdp_rejects_bad_container_name(monkeypatch):
    cfg = _cfg()
    cfg.pod.container_name = "../escape; rm -rf /"

    # VNC alive, RDP dead — would normally fire the exec, but bad name blocks.
    def probe(ip, port, timeout=5.0):
        return port == cfg.pod.vnc_port

    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", probe)
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    assert recover_rdp_if_needed(cfg) is False
    fake_run.assert_not_called()


# --- recover_rdp_if_needed: full restart path ---


def _make_probe(rdp_results: list[bool], vnc_alive: bool = True):
    """Probe sequence: each call to RDP port returns the next bool from rdp_results;
    VNC port returns vnc_alive; sleep is monkeypatched to a no-op so backoff doesn't slow tests."""
    rdp_iter = iter(rdp_results)

    def probe(ip, port, timeout=5.0):
        if port == 8007:  # VNC port from _cfg()
            return vnc_alive
        try:
            return next(rdp_iter)
        except StopIteration:
            return False

    return probe


def test_recover_rdp_fires_termservice_when_vnc_alive(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(
        "winpodx.core.pod.health.check_rdp_port",
        _make_probe([False, True]),  # initial probe fails, post-recovery succeeds
    )
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)

    assert recover_rdp_if_needed(cfg) is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    # v0.1.9.5: recovery now restarts the *container* (the TermService
    # path was on the broken podman exec channel and never worked). The
    # restart subcommand is what the test should verify.
    assert cmd[0] == "podman"
    assert "restart" in cmd
    assert cfg.pod.container_name in cmd


def test_recover_rdp_returns_false_after_max_attempts(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(
        "winpodx.core.pod.health.check_rdp_port",
        _make_probe([False, False, False, False]),  # always dead
    )
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)

    monkeypatch.setattr(
        "winpodx.core.pod.health.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    assert recover_rdp_if_needed(cfg, max_attempts=3) is False


def test_recover_rdp_tolerates_subprocess_errors(monkeypatch):
    """A failed/timed-out exec must not raise; recovery returns False on the fallthrough."""
    cfg = _cfg()
    monkeypatch.setattr(
        "winpodx.core.pod.health.check_rdp_port",
        _make_probe([False, False]),
    )
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", boom)
    assert recover_rdp_if_needed(cfg, max_attempts=1) is False


def test_recover_rdp_succeeds_even_when_exec_returns_nonzero(monkeypatch):
    """sc.exe failure actions can revive TermService independently of the explicit
    Restart-Service call — accept rc!=0 as long as the post-recovery probe is alive."""
    cfg = _cfg()
    monkeypatch.setattr(
        "winpodx.core.pod.health.check_rdp_port",
        _make_probe([False, True]),
    )
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)
    monkeypatch.setattr(
        "winpodx.core.pod.health.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "service stuck"),
    )
    assert recover_rdp_if_needed(cfg) is True


# --- v0.2.0.5: pod wait-ready ---


class TestWaitReady:
    """Multi-phase wait gate: container -> RDP port -> activation."""

    def _patch_cfg(self, monkeypatch):
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.container_name = "winpodx-windows"
        cfg.rdp.ip = "127.0.0.1"
        cfg.rdp.port = 3389
        monkeypatch.setattr("winpodx.core.config.Config.load", classmethod(lambda cls: cfg))
        return cfg

    def test_rejects_unsupported_backend(self, monkeypatch, capsys):
        import pytest

        from winpodx.cli.pod import _wait_ready
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "libvirt"
        monkeypatch.setattr("winpodx.core.config.Config.load", classmethod(lambda cls: cfg))

        with pytest.raises(SystemExit) as excinfo:
            _wait_ready(timeout=10, show_logs=False)
        assert excinfo.value.code == 2
        assert "wait-ready not supported" in capsys.readouterr().out

    def test_happy_path_traverses_three_phases(self, monkeypatch, capsys):
        from unittest.mock import MagicMock

        from winpodx.cli.pod import _wait_ready
        from winpodx.core.pod import PodState

        self._patch_cfg(monkeypatch)
        # Phase 1: container running immediately.
        monkeypatch.setattr(
            "winpodx.core.pod.pod_status",
            lambda cfg: MagicMock(state=PodState.RUNNING),
        )
        # Phase 2: RDP port immediately reachable.
        monkeypatch.setattr(
            "winpodx.core.pod.check_rdp_port",
            lambda ip, port, timeout=1.0: True,
        )
        # Phase 3: activation probe succeeds.
        monkeypatch.setattr(
            "winpodx.core.provisioner.wait_for_windows_responsive",
            lambda cfg, timeout=180: True,
        )

        _wait_ready(timeout=30, show_logs=False)
        out = capsys.readouterr().out
        assert "[1/3]" in out
        assert "[2/3]" in out
        assert "[3/3]" in out
        assert "Windows ready" in out
