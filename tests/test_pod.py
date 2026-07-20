# SPDX-License-Identifier: MIT
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
    # Both probes return False -> RDP dead and the VNC TCP-accept
    # fallback says "container is dead too" -> recovery skips.
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", lambda *a, **k: False)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", lambda *a, **k: False)
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    assert recover_rdp_if_needed(_cfg()) is False
    fake_run.assert_not_called()


def test_recover_rdp_skips_manual_backend(monkeypatch):
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    # Returns True for manual so callers don't block.
    assert recover_rdp_if_needed(_cfg(backend="manual")) is True
    assert recover_rdp_if_needed(_cfg(backend="manual")) is True
    fake_run.assert_not_called()


def test_recover_rdp_rejects_bad_container_name(monkeypatch):
    cfg = _cfg()
    cfg.pod.container_name = "../escape; rm -rf /"

    # VNC alive, RDP dead -- would normally fire recovery, but bad name blocks.
    # check_rdp_port is the RDP-handshake probe (False = RDP dead);
    # check_tcp_port handles the VNC liveness check (True = container alive).
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", lambda *a, **k: False)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", lambda *a, **k: True)
    fake_run = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", fake_run)
    assert recover_rdp_if_needed(cfg) is False
    fake_run.assert_not_called()


# --- recover_rdp_if_needed: full restart path ---


def _make_probe(rdp_results: list[bool], vnc_alive: bool = True):
    """Probe sequence pair: ``rdp_probe`` returns the next bool from
    rdp_results on each call (used to monkeypatch ``check_rdp_port``,
    the X.224-handshake flavor); ``tcp_probe`` returns ``vnc_alive``
    for the VNC port (used to monkeypatch ``check_tcp_port``, the
    plain TCP-accept flavor that ``recover_rdp_if_needed`` uses
    specifically for the VNC liveness probe). Both are returned so a
    single call site can wire both monkeypatches:

        rdp_probe, tcp_probe = _make_probe([False, True])
        monkeypatch.setattr(\".../check_rdp_port\", rdp_probe)
        monkeypatch.setattr(\".../check_tcp_port\", tcp_probe)
    """
    rdp_iter = iter(rdp_results)

    def rdp_probe(ip, port, timeout=5.0):
        try:
            return next(rdp_iter)
        except StopIteration:
            return False

    def tcp_probe(ip, port, timeout=5.0):
        if port == 8007:  # VNC port from _cfg()
            return vnc_alive
        return False

    return rdp_probe, tcp_probe


def test_recover_rdp_fires_termservice_when_vnc_alive(monkeypatch):
    cfg = _cfg()
    rdp_probe, tcp_probe = _make_probe([False, True])
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", rdp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", tcp_probe)
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
    rdp_probe, tcp_probe = _make_probe([False, False, False, False])  # always dead
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", rdp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", tcp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)

    monkeypatch.setattr(
        "winpodx.core.pod.health.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    assert recover_rdp_if_needed(cfg, max_attempts=3) is False


def test_recover_rdp_tolerates_subprocess_errors(monkeypatch):
    """A failed/timed-out exec must not raise; recovery returns False on the fallthrough."""
    cfg = _cfg()
    rdp_probe, tcp_probe = _make_probe([False, False])
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", rdp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", tcp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.time.sleep", lambda _x: None)

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr("winpodx.core.pod.health.subprocess.run", boom)
    assert recover_rdp_if_needed(cfg, max_attempts=1) is False


def test_recover_rdp_succeeds_even_when_exec_returns_nonzero(monkeypatch):
    """sc.exe failure actions can revive TermService independently of the explicit
    Restart-Service call -- accept rc!=0 as long as the post-recovery probe is alive."""
    cfg = _cfg()
    rdp_probe, tcp_probe = _make_probe([False, True])
    monkeypatch.setattr("winpodx.core.pod.health.check_rdp_port", rdp_probe)
    monkeypatch.setattr("winpodx.core.pod.health.check_tcp_port", tcp_probe)
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
        cfg.pod.backend = "manual"
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
        # #753 existence guard: pretend the container exists so it doesn't
        # short-circuit before phase 1 (this is a real, unmocked-by-default
        # subprocess probe -- see TestWaitReadyContainerExistsGuard below).
        monkeypatch.setattr(
            "winpodx.cli.setup_cmd._container_exists_on_backend",
            lambda cfg: True,
        )
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
        # Phase 4 (OEM reboot pass): pretend the marker was never
        # written so the helper short-circuits to True via its
        # appear-grace fallback without sleeping.
        monkeypatch.setattr(
            "winpodx.cli.pod._wait_for_oem_reboot",
            lambda cfg, timeout: True,
        )

        _wait_ready(timeout=30, show_logs=False)
        out = capsys.readouterr().out
        assert "[1/4]" in out
        assert "[2/4]" in out
        assert "[3/4]" in out
        assert "[4/4]" in out
        assert "Windows ready" in out
        assert "OEM reboot pass complete" in out


class TestWaitReadyContainerExistsGuard:
    """#753: if the container was never created (e.g. no compose provider
    was installed -- see setup_cmd.py's _recreate_container), fail fast with
    a clear message instead of letting podman's own "no such container"
    stderr leak through the [container] log-tail prefix while phase [1/4]
    spins for the whole timeout before finally failing."""

    def _patch_cfg(self, monkeypatch):
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.container_name = "winpodx-windows"
        cfg.rdp.ip = "127.0.0.1"
        cfg.rdp.port = 3389
        monkeypatch.setattr("winpodx.core.config.Config.load", classmethod(lambda cls: cfg))
        return cfg

    def test_fails_fast_when_container_does_not_exist(self, monkeypatch, capsys):
        import pytest

        from winpodx.cli.pod import _wait_ready

        self._patch_cfg(monkeypatch)
        monkeypatch.setattr(
            "winpodx.cli.setup_cmd._container_exists_on_backend",
            lambda cfg: False,
        )

        with pytest.raises(SystemExit) as excinfo:
            _wait_ready(timeout=10, show_logs=False)
        assert excinfo.value.code == 3
        out = capsys.readouterr().out
        assert "does not exist" in out
        assert "winpodx-windows" in out


# -- pod recreate --keep-iso (storage wipe that preserves the cached ISO) ----


def _populate_storage(d):
    (d / "data.img").write_text("disk")
    (d / "win11x64.iso").write_text("iso")
    (d / "windows.ver").write_text("11")
    (d / "drivers").mkdir()
    (d / "drivers" / "x.inf").write_text("drv")


def test_wipe_pod_storage_keep_iso_preserves_iso(tmp_path):
    from winpodx.cli.pod import _wipe_pod_storage

    _populate_storage(tmp_path)
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.storage_path = str(tmp_path)

    _wipe_pod_storage(cfg, keep_iso=True)

    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"win11x64.iso"}  # only the ISO survives


def test_wipe_pod_storage_full_removes_everything(tmp_path):
    from winpodx.cli.pod import _wipe_pod_storage

    _populate_storage(tmp_path)
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.storage_path = str(tmp_path)

    _wipe_pod_storage(cfg, keep_iso=False)

    assert list(tmp_path.iterdir()) == []  # dir kept, contents gone


def test_wipe_pod_storage_keep_iso_warns_when_no_iso(tmp_path, capsys):
    from winpodx.cli.pod import _wipe_pod_storage

    (tmp_path / "data.img").write_text("disk")  # no ISO present
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.storage_path = str(tmp_path)

    _wipe_pod_storage(cfg, keep_iso=True)

    assert list(tmp_path.iterdir()) == []
    assert "no cached ISO" in capsys.readouterr().out
