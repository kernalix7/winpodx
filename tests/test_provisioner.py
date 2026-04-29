"""Tests for auto-provisioning engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.core.provisioner import ProvisionError


def test_provision_error():
    err = ProvisionError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


def test_ensure_config_creates_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.provisioner import _ensure_config

    cfg = _ensure_config()

    assert cfg.rdp.user == "User"
    assert cfg.rdp.ip == "127.0.0.1"
    assert (tmp_path / "winpodx" / "winpodx.toml").exists()


# C3: password rotation rollback failure handling


@pytest.fixture()
def _rotation_cfg(tmp_path, monkeypatch):
    """Config set up to trigger _auto_rotate_password work."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "old-password"
    cfg.rdp.password_max_age = 1  # day
    cfg.rdp.password_updated = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    cfg.pod.backend = "podman"
    cfg.save()
    return cfg


def test_rotation_rollback_success_reverts_password(_rotation_cfg, monkeypatch):
    # When config.save fails but Windows rollback succeeds, config keeps the old password.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        result = provisioner._auto_rotate_password(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert not provisioner._rotation_marker_path().exists()


def test_rotation_rollback_failure_writes_marker(_rotation_cfg, monkeypatch):
    # Config save and Windows rollback both fail: must log error and write .rotation_pending marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    calls: list[str] = []

    def fake_change(cfg, pw):
        calls.append(pw)
        return len(calls) == 1

    monkeypatch.setattr(provisioner, "_change_windows_password", fake_change)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        provisioner._auto_rotate_password(_rotation_cfg)

    assert len(calls) == 2
    marker = provisioner._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


def test_check_rotation_pending_warns(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import provisioner

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    with caplog.at_level(logging.ERROR, logger="winpodx.core.provisioner"):
        provisioner._check_rotation_pending()

    assert any("Pending password rotation" in r.message for r in caplog.records)


def test_rotation_marker_cleared_on_success(_rotation_cfg, monkeypatch):
    # A successful rotation must clear any previously-written marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    provisioner._auto_rotate_password(_rotation_cfg)

    assert not marker.exists()


# --- v0.1.9.4: runtime applies via FreeRDP RemoteApp (windows_exec.run_in_windows) ---
#
# The v0.1.9.0-v0.1.9.3 versions of these tests mocked podman-exec subprocess
# calls — but podman exec can't reach the Windows VM inside the dockur Linux
# container, so the helpers never actually applied anything (they just logged
# warnings). v0.1.9.4 routes them through windows_exec.run_in_windows, which
# launches PowerShell as a FreeRDP RemoteApp. These tests mock that helper.


def _mock_run_in_windows(monkeypatch, *, rc: int = 0, stdout: str = "", stderr: str = ""):
    """Return a list that captures every (description, payload) call."""
    from winpodx.core.windows_exec import WindowsExecResult

    captured: list[tuple[str, str]] = []

    def fake(cfg, payload, *, timeout=60, description="windows-exec"):
        captured.append((description, payload))
        return WindowsExecResult(rc=rc, stdout=stdout, stderr=stderr)

    import winpodx.core.provisioner as prov

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    # Make sure the lazy-imported reference inside provisioner picks up the patched fn.
    monkeypatch.setattr(prov, "_apply_max_sessions", prov._apply_max_sessions)
    return captured


def test_apply_max_sessions_skips_libvirt_backend(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_max_sessions(cfg)
    assert captured == []


def test_apply_max_sessions_runs_via_windows_exec(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.max_sessions = 25
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="max_sessions: 10 -> 25")
    provisioner._apply_max_sessions(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-max-sessions"
    assert "MaxInstanceCount" in payload
    assert "$desired = 25" in payload
    assert "fSingleSessionPerUser" in payload
    # v0.1.9.5: Restart-Service intentionally removed — restarting the
    # TermService that hosts our own RDP session kills the session
    # before the wrapper can write its result file.
    assert "Restart-Service" not in payload


def test_apply_max_sessions_raises_on_nonzero_rc(monkeypatch):
    """v0.1.9.4: helpers no longer silently swallow non-zero rc."""
    import pytest

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    _mock_run_in_windows(monkeypatch, rc=2, stderr="permission denied")
    with pytest.raises(RuntimeError, match="rc=2"):
        provisioner._apply_max_sessions(cfg)


def test_apply_max_sessions_propagates_channel_error(monkeypatch):
    import pytest

    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecError

    cfg = Config()

    def fake(*a, **k):
        raise WindowsExecError("FreeRDP not found")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    with pytest.raises(WindowsExecError, match="FreeRDP not found"):
        provisioner._apply_max_sessions(cfg)


def test_apply_rdp_timeouts_skips_libvirt(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_rdp_timeouts(cfg)
    assert captured == []


def test_apply_rdp_timeouts_payload_contains_all_keys(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="rdp_timeouts applied")
    provisioner._apply_rdp_timeouts(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-rdp-timeouts"
    for token in (
        "MaxIdleTime",
        "MaxDisconnectionTime",
        "MaxConnectionTime",
        "KeepAliveEnable",
        "KeepAliveInterval",
        "KeepAliveTimeout",
        "RDP-Tcp",
        "Terminal Services",
    ):
        assert token in payload, f"missing {token!r} in payload"


def test_apply_oem_runtime_fixes_skips_libvirt(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_oem_runtime_fixes(cfg)
    assert captured == []


def test_apply_oem_runtime_fixes_payload_contains_nic_and_termservice(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="oem v7 baseline applied")
    provisioner._apply_oem_runtime_fixes(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-oem"
    assert "Set-NetAdapterPowerManagement" in payload
    assert "AllowComputerToTurnOffDevice" in payload
    assert "sc.exe failure TermService" in payload
    assert "restart/5000/restart/5000/restart/5000" in payload


def test_ensure_ready_runs_apply_before_early_return_when_rdp_alive(monkeypatch):
    """v0.1.9.2: existing healthy pods must still get runtime fixes applied."""
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus

    cfg = Config()
    cfg.pod.backend = "podman"

    monkeypatch.setattr(provisioner, "_check_rotation_pending", lambda: None)
    monkeypatch.setattr(provisioner, "_auto_rotate_password", lambda c: c)
    monkeypatch.setattr(provisioner, "_ensure_config", lambda: cfg)
    monkeypatch.setattr(provisioner, "pod_status", lambda c: PodStatus(state=PodState.RUNNING))
    # RDP alive -> must trigger early return AFTER runtime apply.
    monkeypatch.setattr(provisioner, "check_rdp_port", lambda *a, **k: True)

    calls = {"max_sessions": 0, "rdp_timeouts": 0, "oem_runtime_fixes": 0, "multi_session": 0}

    def make_recorder(name):
        def f(c):
            calls[name] += 1

        return f

    monkeypatch.setattr(provisioner, "_apply_max_sessions", make_recorder("max_sessions"))
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", make_recorder("rdp_timeouts"))
    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", make_recorder("oem_runtime_fixes"))
    monkeypatch.setattr(provisioner, "_apply_multi_session", make_recorder("multi_session"))
    # Force the stamp short-circuit to be a no-op so the actual applies fire.
    monkeypatch.setattr(provisioner, "_self_heal_already_done", lambda c: False)
    monkeypatch.setattr(provisioner, "_record_self_heal_done", lambda c: None)
    # v0.2.2.2: bypass the OEM-install-done gate (no real container in tests).
    monkeypatch.setattr(provisioner, "_oem_install_done", lambda c: True)
    monkeypatch.setattr(provisioner, "_ensure_agent_token_in_guest", lambda c: None)

    result = provisioner.ensure_ready(cfg, timeout=1)
    assert result is cfg
    # All four idempotent applies fired exactly once even though the
    # function early-returned at the RDP-port check.
    assert calls == {
        "max_sessions": 1,
        "rdp_timeouts": 1,
        "oem_runtime_fixes": 1,
        "multi_session": 1,
    }


def test_ensure_ready_skips_apply_when_pod_not_running(monkeypatch):
    """When pod isn't running, the early-apply branch is skipped (later branch handles)."""
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus

    cfg = Config()
    cfg.pod.backend = "podman"

    monkeypatch.setattr(provisioner, "_check_rotation_pending", lambda: None)
    monkeypatch.setattr(provisioner, "_auto_rotate_password", lambda c: c)
    monkeypatch.setattr(provisioner, "_ensure_config", lambda: cfg)
    monkeypatch.setattr(provisioner, "pod_status", lambda c: PodStatus(state=PodState.STOPPED))
    monkeypatch.setattr(provisioner, "check_rdp_port", lambda *a, **k: True)
    early_calls = {"n": 0}

    def recorder(c):
        early_calls["n"] += 1

    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", recorder)
    monkeypatch.setattr(provisioner, "_apply_max_sessions", recorder)
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", recorder)
    monkeypatch.setattr(provisioner, "_apply_multi_session", recorder)

    provisioner.ensure_ready(cfg, timeout=1)
    # Stopped pod -> the early-branch `pod_status==RUNNING` guard prevents
    # the apply calls from firing on the early return path.
    assert early_calls["n"] == 0


# --- v0.1.9.3: apply_windows_runtime_fixes public API ---


def test_apply_windows_runtime_fixes_skips_libvirt():
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert "backend" in result
    assert "skipped" in result["backend"]


def test_apply_windows_runtime_fixes_returns_per_helper_status(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecResult

    cfg = Config()

    def fake(cfg_inner, payload, *, timeout=60, description="windows-exec"):
        return WindowsExecResult(rc=0, stdout="ok", stderr="")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    monkeypatch.setattr(provisioner, "_oem_install_done", lambda c: True)
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert set(result.keys()) == {
        "max_sessions",
        "rdp_timeouts",
        "oem_runtime_fixes",
        "multi_session",
    }
    for v in result.values():
        assert v == "ok"


def test_apply_windows_runtime_fixes_records_individual_failures(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()

    def fake_max_sessions(c):
        raise RuntimeError("boom max_sessions")

    monkeypatch.setattr(provisioner, "_apply_max_sessions", fake_max_sessions)
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", lambda c: None)
    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", lambda c: None)
    monkeypatch.setattr(provisioner, "_oem_install_done", lambda c: True)
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert result["max_sessions"].startswith("failed: ")
    assert result["rdp_timeouts"] == "ok"
    assert result["oem_runtime_fixes"] == "ok"


# --- v0.2.0.6: wait_for_windows_responsive retry loop ---


class TestWaitForWindowsResponsiveRetries:
    """v0.2.0.6: probe must retry until deadline, not bail on first failure.

    Reproduces the v0.2.0.5 bug where _wait_ready phase 3 returned FAIL at
    elapsed=00:00 because wait_for_windows_responsive ran exactly one
    FreeRDP probe and returned False on the first WindowsExecError.
    """

    def _cfg(self):
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.ip = "127.0.0.1"
        cfg.rdp.port = 3389
        cfg.rdp.password = "abc123"
        return cfg

    def test_returns_true_when_probe_eventually_succeeds(self, monkeypatch):
        """First N probes raise WindowsExecError; eventually one returns rc=0
        and the helper must return True instead of bailing on probe #1."""
        from winpodx.core.provisioner import wait_for_windows_responsive
        from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult

        cfg = self._cfg()
        monkeypatch.setattr(
            "winpodx.core.provisioner.check_rdp_port",
            lambda ip, port, timeout=1.0: True,
        )
        # v0.2.2.2: bypass OEM-done gate (no real container in tests).
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        # Compress the inter-probe sleep so the test is fast.
        monkeypatch.setattr("winpodx.core.provisioner.time.sleep", lambda _: None)

        attempts: list[int] = []

        def fake_run(cfg_inner, payload, *, description, timeout):
            attempts.append(len(attempts))
            if len(attempts) < 4:
                raise WindowsExecError("FreeRDP rc=147 connection reset by peer")
            return WindowsExecResult(rc=0, stdout="ping\n", stderr="")

        monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake_run)
        assert wait_for_windows_responsive(cfg, timeout=60) is True
        assert len(attempts) >= 4, "must keep probing past first failure"

    def test_returns_false_only_after_deadline(self, monkeypatch):
        """If every probe fails for the full timeout, helper must take roughly
        `timeout` seconds — not return False after the first attempt."""

        from winpodx.core.provisioner import wait_for_windows_responsive
        from winpodx.core.windows_exec import WindowsExecError

        cfg = self._cfg()
        monkeypatch.setattr(
            "winpodx.core.provisioner.check_rdp_port",
            lambda ip, port, timeout=1.0: True,
        )
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        # Use a virtual clock so we don't really wait.
        clock = {"t": 0.0}
        monkeypatch.setattr("winpodx.core.provisioner.time.monotonic", lambda: clock["t"])
        monkeypatch.setattr(
            "winpodx.core.provisioner.time.sleep",
            lambda s: clock.update(t=clock["t"] + s),
        )

        attempts: list[int] = []

        def fake_run(cfg_inner, payload, *, description, timeout):
            attempts.append(timeout)
            # Each probe consumes ~5s of virtual time.
            clock["t"] += 5
            raise WindowsExecError("FreeRDP rc=147 connection reset")

        monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake_run)

        # v0.2.2.2: Phase 3 sleeps 30s between probes (was 3s, kernalix7
        # reported PS-window flash storms on 2026-04-29 caused by 100+
        # probes over 5 min). Bump test timeout to 120s so multiple
        # attempts still fit under the new pacing.
        result = wait_for_windows_responsive(cfg, timeout=120)
        assert result is False
        assert len(attempts) >= 2, "must retry rather than bail on first failure"


# --- v0.2.0.8: self-heal stamp prevents PS flash on every launch ---


class TestSelfHealStamp:
    """v0.2.0.8: _self_heal_apply must short-circuit when a stamp records the
    same (winpodx version, container StartedAt) tuple already succeeded."""

    def _cfg(self):
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.container_name = "winpodx-windows"
        return cfg

    def test_skips_apply_when_stamp_matches(self, tmp_path, monkeypatch):
        from winpodx import __version__
        from winpodx.core.provisioner import _self_heal_apply

        monkeypatch.setattr("winpodx.core.provisioner.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "winpodx.core.provisioner._container_started_at", lambda cfg: "2026-04-27T10:00Z"
        )
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        monkeypatch.setattr("winpodx.core.provisioner._ensure_agent_token_in_guest", lambda c: None)
        (tmp_path / ".applies_stamp").write_text(
            f"{__version__}:2026-04-27T10:00Z\n", encoding="utf-8"
        )

        called: list[str] = []
        monkeypatch.setattr(
            "winpodx.core.provisioner._apply_max_sessions",
            lambda cfg: called.append("max"),
        )
        monkeypatch.setattr(
            "winpodx.core.provisioner._apply_rdp_timeouts",
            lambda cfg: called.append("rdp"),
        )
        monkeypatch.setattr(
            "winpodx.core.provisioner._apply_oem_runtime_fixes",
            lambda cfg: called.append("oem"),
        )
        monkeypatch.setattr(
            "winpodx.core.provisioner._apply_multi_session",
            lambda cfg: called.append("multi"),
        )

        _self_heal_apply(self._cfg())
        assert called == [], "stamp must short-circuit all four applies"

    def test_runs_apply_when_stamp_missing(self, tmp_path, monkeypatch):
        from winpodx.core.provisioner import _self_heal_apply

        monkeypatch.setattr("winpodx.core.provisioner.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "winpodx.core.provisioner._container_started_at", lambda cfg: "2026-04-27T10:00Z"
        )
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        monkeypatch.setattr("winpodx.core.provisioner._ensure_agent_token_in_guest", lambda c: None)

        called: list[str] = []
        for fn_name, marker in (
            ("_apply_max_sessions", "max"),
            ("_apply_rdp_timeouts", "rdp"),
            ("_apply_oem_runtime_fixes", "oem"),
            ("_apply_multi_session", "multi"),
        ):

            def make(m):
                def _stub(cfg, _m=m):
                    called.append(_m)

                return _stub

            monkeypatch.setattr(f"winpodx.core.provisioner.{fn_name}", make(marker))

        _self_heal_apply(self._cfg())
        assert called == ["max", "rdp", "oem", "multi"]

    def test_runs_apply_when_pod_restarted(self, tmp_path, monkeypatch):
        """Stamp is from a previous container start; current StartedAt differs
        → must re-run apply (TermService settings need to relaunch on
        every Windows reboot)."""
        from winpodx import __version__
        from winpodx.core.provisioner import _self_heal_apply

        monkeypatch.setattr("winpodx.core.provisioner.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "winpodx.core.provisioner._container_started_at",
            lambda cfg: "2026-04-27T11:00Z",  # different time
        )
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        monkeypatch.setattr("winpodx.core.provisioner._ensure_agent_token_in_guest", lambda c: None)
        (tmp_path / ".applies_stamp").write_text(
            f"{__version__}:2026-04-27T10:00Z\n", encoding="utf-8"
        )

        called: list[str] = []
        for fn_name, marker in (
            ("_apply_max_sessions", "max"),
            ("_apply_rdp_timeouts", "rdp"),
            ("_apply_oem_runtime_fixes", "oem"),
            ("_apply_multi_session", "multi"),
        ):

            def make(m):
                def _stub(cfg, _m=m):
                    called.append(_m)

                return _stub

            monkeypatch.setattr(f"winpodx.core.provisioner.{fn_name}", make(marker))

        _self_heal_apply(self._cfg())
        assert called == ["max", "rdp", "oem", "multi"], "pod restart must invalidate stamp"

    def test_stamp_written_after_three_successes(self, tmp_path, monkeypatch):
        from winpodx import __version__
        from winpodx.core.provisioner import _self_heal_apply

        monkeypatch.setattr("winpodx.core.provisioner.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "winpodx.core.provisioner._container_started_at",
            lambda cfg: "2026-04-27T12:00Z",
        )
        monkeypatch.setattr("winpodx.core.provisioner._oem_install_done", lambda c: True)
        monkeypatch.setattr("winpodx.core.provisioner._ensure_agent_token_in_guest", lambda c: None)
        for fn_name in (
            "_apply_max_sessions",
            "_apply_rdp_timeouts",
            "_apply_oem_runtime_fixes",
            "_apply_multi_session",
        ):
            monkeypatch.setattr(f"winpodx.core.provisioner.{fn_name}", lambda cfg: None)

        _self_heal_apply(self._cfg())
        stamp = (tmp_path / ".applies_stamp").read_text(encoding="utf-8").strip()
        assert stamp == f"{__version__}:2026-04-27T12:00Z"


# --- v0.2.2.2: OEM-install-done gate ---


class TestOemInstallDoneGate:
    """v0.2.2.2: install.bat-completion gate suppresses the "Another user is
    signed in" dialog by deferring all FreeRDP RemoteApp until rdprrap
    has activated."""

    def _cfg(self):
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.container_name = "winpodx-windows"
        return cfg

    def test_returns_true_for_libvirt_backend(self):
        """Non-dockur backends never run install.bat — gate is vacuous."""
        from winpodx.core import provisioner
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "libvirt"
        assert provisioner._oem_install_done(cfg) is True

    def _patch_health_unavailable(self, monkeypatch):
        """Default agent /health probe to "unavailable" so tests exercise
        the dockur-sentinel / time-fallback paths without hitting the
        real network. Tests that want the /health-open path patch
        AgentClient.health themselves."""
        from winpodx.core.agent import AgentUnavailableError

        def _raise(self):
            raise AgentUnavailableError("test-stub: agent down")

        monkeypatch.setattr("winpodx.core.agent.AgentClient.health", _raise)

    def test_returns_false_when_started_at_unknown(self, monkeypatch):
        """No started_at -> pod isn't even inspectable; conservatively defer."""
        from winpodx.core import provisioner

        self._patch_health_unavailable(monkeypatch)
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: "")
        assert provisioner._oem_install_done(self._cfg()) is False

    def test_agent_health_opens_gate_immediately(self, monkeypatch):
        """v0.2.2.2: agent /health is the most reliable signal — the
        listener only binds AFTER install.bat finishes (post-Sysprep,
        post-rdprrap-restart). One successful probe opens the gate."""
        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: "2026-04-29T08:00Z")

        # Even with "started just now" timestamp (no time fallback) and
        # no log sentinel, /health opening returns True.
        monkeypatch.setattr(
            "winpodx.core.agent.AgentClient.health",
            lambda self: {"version": "0.2.2", "ok": True},
        )
        assert provisioner._oem_install_done(self._cfg()) is True

    def test_dockur_ready_with_buffer_opens_gate(self, monkeypatch):
        """When dockur's "Windows started successfully" appears in container
        logs AND the buffer has elapsed, the gate opens."""
        import subprocess
        from datetime import datetime, timedelta, timezone

        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        self._patch_health_unavailable(monkeypatch)
        # Container started ~12 min ago — past the 600s (10 min) buffer.
        old = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: old)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="❯ Windows started successfully, visit ...\n",
                stderr="",
            )

        monkeypatch.setattr("winpodx.core.provisioner.subprocess.run", fake_run)
        assert provisioner._oem_install_done(self._cfg()) is True

    def test_unparseable_started_at_keeps_gate_closed(self, monkeypatch):
        """Regression: kernalix7 saw `dockur ready + 0s buffer — gate open`
        on 2026-04-29 because the path-2 condition was
        `age_seconds is None or age_seconds >= buffer` — when timestamp
        parsing failed (age_seconds=None) the OR short-circuited True
        and the gate opened immediately, before install.bat had a chance
        to finish. The fix requires age_seconds to be a real number AND
        past the threshold; if parsing fails we defer."""
        import subprocess

        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        self._patch_health_unavailable(monkeypatch)
        # Garbage that fromisoformat can't parse — emulates a podman
        # template that returned an unexpected format.
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: "not-an-iso-timestamp")

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="❯ Windows started successfully, visit ...\n",
                stderr="",
            )

        monkeypatch.setattr("winpodx.core.provisioner.subprocess.run", fake_run)
        # Even with the dockur sentinel present in logs, the gate must
        # stay closed because we cannot verify the buffer elapsed.
        assert provisioner._oem_install_done(self._cfg()) is False

    def test_dockur_ready_without_buffer_keeps_gate_closed(self, monkeypatch):
        """Sentinel appeared but buffer hasn't elapsed yet — gate stays
        closed so FreeRDP doesn't fire mid-install.bat."""
        import subprocess
        from datetime import datetime, timezone

        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        self._patch_health_unavailable(monkeypatch)
        # Container started just now.
        now = datetime.now(timezone.utc).isoformat()
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: now)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="❯ Windows started successfully, visit ...\n",
                stderr="",
            )

        monkeypatch.setattr("winpodx.core.provisioner.subprocess.run", fake_run)
        assert provisioner._oem_install_done(self._cfg()) is False

    def test_time_based_fallback_when_logs_unreadable(self, monkeypatch):
        """If `<runtime> logs` fails entirely but the container is older
        than _OEM_DONE_FALLBACK_AGE_SECONDS (30 min), the gate opens anyway."""
        import subprocess
        from datetime import datetime, timedelta, timezone

        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        self._patch_health_unavailable(monkeypatch)
        # 35 min ago — past the 1800s (30 min) fallback.
        old = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: old)

        def fake_run(*args, **kwargs):
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr("winpodx.core.provisioner.subprocess.run", fake_run)
        assert provisioner._oem_install_done(self._cfg()) is True

    def test_handles_nanosecond_timestamps(self, monkeypatch):
        """podman's StartedAt has nanosecond precision — the parser
        must not choke on 9-digit fractional seconds."""
        import subprocess
        from datetime import datetime, timedelta, timezone

        from winpodx.core import provisioner

        provisioner._OEM_DONE_CACHE.clear()
        self._patch_health_unavailable(monkeypatch)
        # 35 min ago WITH nanoseconds (9 digits) — past 30 min time fallback.
        old_dt = datetime.now(timezone.utc) - timedelta(minutes=35)
        old_ns = old_dt.strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"
        monkeypatch.setattr(provisioner, "_container_started_at", lambda c: old_ns)

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("winpodx.core.provisioner.subprocess.run", fake_run)
        assert provisioner._oem_install_done(self._cfg()) is True

    def test_self_heal_apply_defers_when_gate_closed(self, monkeypatch, tmp_path):
        """_self_heal_apply must short-circuit silently before firing any
        FreeRDP RemoteApp when the OEM install hasn't finished yet."""
        from winpodx.core import provisioner

        monkeypatch.setattr(provisioner, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(provisioner, "_oem_install_done", lambda c: False)

        called: list[str] = []
        for fn_name in (
            "_apply_max_sessions",
            "_apply_rdp_timeouts",
            "_apply_oem_runtime_fixes",
            "_apply_multi_session",
            "_ensure_agent_token_in_guest",
        ):
            monkeypatch.setattr(provisioner, fn_name, lambda c, _n=fn_name: called.append(_n))

        provisioner._self_heal_apply(self._cfg())
        assert called == [], "no FreeRDP-firing helper may run while install.bat is in flight"

    def test_apply_windows_runtime_fixes_defers_when_gate_closed(self, monkeypatch):
        """The public apply-fixes path returns a 'deferred' status when the
        OEM install is still running, surfacing it to CLI/GUI."""
        from winpodx.core import provisioner

        monkeypatch.setattr(provisioner, "_oem_install_done", lambda c: False)
        result = provisioner.apply_windows_runtime_fixes(self._cfg())
        assert "oem" in result
        assert "deferred" in result["oem"]
