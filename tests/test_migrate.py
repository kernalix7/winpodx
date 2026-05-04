"""Tests for cli.migrate — post-upgrade wizard.

Covers version tuple parsing, installed-version I/O, fresh-install vs
pre-tracker-upgrade detection, release-note selection across a version
range, interactive prompt handling (mocked stdin), and the happy-path
flow through ``run_migrate`` with refresh skipped.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from winpodx.cli.migrate import (
    _VERSION_NOTES,
    _detect_installed_version,
    _print_whats_new,
    _prompt_yes,
    _read_installed_version,
    _version_tuple,
    _write_installed_version,
    run_migrate,
)

# --- _version_tuple ---


def test_version_tuple_basic():
    assert _version_tuple("0.1.8") == (0, 1, 8)


def test_version_tuple_four_segments():
    assert _version_tuple("1.2.3.4") == (1, 2, 3, 4)


def test_version_tuple_prerelease():
    # Pre-release suffixes ('rc1', '-RTM1', '+dev') keep the leading
    # digits of the mixed segment so the [:3] tuple is complete and
    # equal-compares to the corresponding final release. Migrate's
    # apply-fixes gate uses this — RC builds and final builds share
    # the same apply chain, so they should compare equal under [:3].
    assert _version_tuple("0.1.8rc1") == (0, 1, 8)
    assert _version_tuple("0.3.0-RTM1") == (0, 3, 0)
    assert _version_tuple("0.3.0+dev") == (0, 3, 0)
    # Equal compare for our gating purposes (final == its prereleases
    # at the [:3] level — fine since they share the apply chain).
    assert _version_tuple("0.1.8")[:3] == _version_tuple("0.1.8rc1")[:3]


def test_version_tuple_comparison():
    assert _version_tuple("0.1.7") < _version_tuple("0.1.8")
    assert _version_tuple("0.1.8") < _version_tuple("0.2.0")
    assert _version_tuple("1.0.0") > _version_tuple("0.9.99")


# --- Version marker file I/O ---


def test_write_and_read_installed_version(tmp_path, monkeypatch):
    # Redirect config_dir via a lambda so the helpers use our tmp path.
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    _write_installed_version("0.1.8")
    assert (tmp_path / "installed_version.txt").exists()
    assert _read_installed_version() == "0.1.8"


def test_read_installed_version_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    assert _read_installed_version() is None


def test_read_installed_version_empty_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("\n", encoding="utf-8")
    assert _read_installed_version() is None


def test_read_installed_version_strips_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("  0.1.8  \n", encoding="utf-8")
    assert _read_installed_version() == "0.1.8"


# --- _detect_installed_version ---


def test_detect_returns_none_for_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    # No marker, no config file => treated as fresh install.
    with patch("winpodx.core.config.Config.path", return_value=tmp_path / "noconfig.toml"):
        assert _detect_installed_version() is None


def test_detect_returns_pretracker_when_config_exists(tmp_path, monkeypatch):
    """Config exists but no marker -> user was on 0.1.7 before this command existed."""
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    cfg_path = tmp_path / "winpodx.toml"
    cfg_path.write_text("[rdp]\n", encoding="utf-8")
    with patch("winpodx.core.config.Config.path", return_value=cfg_path):
        assert _detect_installed_version() == "0.1.7"


def test_detect_prefers_marker_over_baseline(tmp_path, monkeypatch):
    """Marker file wins even when config also exists."""
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.9\n", encoding="utf-8")
    cfg_path = tmp_path / "winpodx.toml"
    cfg_path.write_text("[rdp]\n", encoding="utf-8")
    with patch("winpodx.core.config.Config.path", return_value=cfg_path):
        assert _detect_installed_version() == "0.1.9"


# --- _probe_password_sync (v0.2.0.4 false-positive fix) ---


class TestProbePasswordSync:
    """v0.2.0.4: probe must NOT classify boot-time transport errors as drift."""

    def _setup_cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.password = "abc123"
        cfg.rdp.user = "User"
        cfg.save()

    def test_transport_reset_not_classified_as_drift(self, tmp_path, monkeypatch, capsys):
        """rc=147 ERRCONNECT_CONNECT_TRANSPORT_FAILED on still-booting guest must
        produce 'probe inconclusive', not the 'cfg.password does not match'
        warning. Reproduces the v0.2.0.3 bogus-warning bug."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _probe_password_sync
        from winpodx.core.pod import PodState
        from winpodx.core.windows_exec import WindowsExecError

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_run.side_effect = WindowsExecError(
                "No result file written (FreeRDP rc=147). stderr tail: "
                "'ERRCONNECT_CONNECT_TRANSPORT_FAILED [0x0002000D] ... "
                "Connection reset by peer'"
            )

            _probe_password_sync(non_interactive=True)

        out = capsys.readouterr().out
        assert "does not match" not in out, "transport reset must not surface drift warning"
        assert "probe inconclusive" in out

    def test_genuine_auth_failure_classified_as_drift(self, tmp_path, monkeypatch, capsys):
        """An auth-flavored failure must still trigger the sync-password warning."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _probe_password_sync
        from winpodx.core.pod import PodState
        from winpodx.core.windows_exec import WindowsExecError

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_run.side_effect = WindowsExecError(
                "FreeRDP authentication failed: STATUS_LOGON_FAILURE 0xC000006D"
            )

            _probe_password_sync(non_interactive=True)

        out = capsys.readouterr().out
        assert "does not match" in out, "real auth failure must surface drift warning"
        assert "sync-password" in out

    def test_probe_skipped_when_guest_not_responsive(self, tmp_path, monkeypatch, capsys):
        """Guest still booting → probe deferred, no false alarm."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _probe_password_sync
        from winpodx.core.pod import PodState

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=False),
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)

            _probe_password_sync(non_interactive=True)

        out = capsys.readouterr().out
        assert "probe deferred" in out
        assert "does not match" not in out
        mock_run.assert_not_called()

    def test_probe_skipped_under_require_agent_env_when_agent_down(
        self, tmp_path, monkeypatch, capsys
    ):
        """v0.4.0 (post-rc1): WINPODX_REQUIRE_AGENT=1 + agent /health
        down -> probe MUST skip without firing FreeRDP. Same rationale
        as the migrate apply gate and discovery gate -- a fresh FreeRDP
        login while install.bat is in-flight kicks the autologon
        session."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _probe_password_sync
        from winpodx.core.pod import PodState
        from winpodx.core.transport.base import HealthStatus

        monkeypatch.setenv("WINPODX_REQUIRE_AGENT", "1")

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_health.return_value = HealthStatus(available=False, detail="connection refused")

            _probe_password_sync(non_interactive=True)

        out = capsys.readouterr().out
        assert "probe deferred" in out
        assert "agent /health not up" in out
        assert not mock_run.called, (
            "FreeRDP MUST NOT fire under WINPODX_REQUIRE_AGENT=1 when agent is down — "
            "would kick install.bat's autologon session"
        )

    def test_probe_agent_only_under_require_agent_even_if_exec_drops(
        self, tmp_path, monkeypatch, capsys
    ):
        """WINPODX_REQUIRE_AGENT=1 must remain agent-only even if the
        agent disappears after /health succeeds. This pins the fresh-install
        race where default dispatch fell back to FreeRDP and kicked the
        autologon session running install.bat.
        """
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _probe_password_sync
        from winpodx.core.pod import PodState
        from winpodx.core.transport.base import HealthStatus, TransportUnavailable

        monkeypatch.setenv("WINPODX_REQUIRE_AGENT", "1")

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive") as mock_wait,
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
            patch("winpodx.core.transport.agent.AgentTransport.exec") as mock_exec,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_health.return_value = HealthStatus(available=True, detail="ok")
            mock_exec.side_effect = TransportUnavailable("agent dropped after health")

            _probe_password_sync(non_interactive=True)

        out = capsys.readouterr().out
        assert "probe inconclusive" in out
        mock_wait.assert_not_called()
        mock_run.assert_not_called()


# --- _print_whats_new ---


def test_whats_new_covers_range(capsys):
    _print_whats_new("0.1.7", "0.1.8")
    out = capsys.readouterr().out
    assert "0.1.8" in out
    # A known 0.1.8 bullet must appear.
    assert "winpodx app refresh" in out


def test_whats_new_empty_range(capsys):
    # No bullets when neither endpoint covers a release with recorded notes.
    _print_whats_new("0.2.0", "0.2.1")
    out = capsys.readouterr().out
    assert "no user-facing release notes" in out.lower()


def test_whats_new_notes_present_for_current_version():
    """The current-release key in _VERSION_NOTES must be the one documented."""
    assert "0.1.8" in _VERSION_NOTES
    assert len(_VERSION_NOTES["0.1.8"]) >= 3


# --- _prompt_yes ---


def test_prompt_yes_default_accept(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes("x?", default=True) is True


def test_prompt_yes_default_decline(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes("x?", default=False) is False


def test_prompt_yes_explicit_y(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _prompt_yes("x?", default=False) is True


def test_prompt_yes_explicit_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "N")
    assert _prompt_yes("x?", default=True) is False


def test_prompt_yes_eof_returns_false(monkeypatch):
    def _raise_eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)
    assert _prompt_yes("x?") is False


# --- run_migrate ---


def _args(no_refresh: bool = True, non_interactive: bool = True) -> argparse.Namespace:
    return argparse.Namespace(no_refresh=no_refresh, non_interactive=non_interactive)


def test_run_migrate_fresh_install_writes_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    with patch("winpodx.core.config.Config.path", return_value=tmp_path / "noconfig.toml"):
        rc = run_migrate(_args())
    assert rc == 0
    assert (tmp_path / "installed_version.txt").exists()
    assert "fresh install" in capsys.readouterr().out.lower()


def test_run_migrate_already_current(tmp_path, monkeypatch, capsys):
    from winpodx import __version__ as current

    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text(current + "\n", encoding="utf-8")
    rc = run_migrate(_args())
    assert rc == 0
    assert "already current" in capsys.readouterr().out.lower()


def test_run_migrate_upgrade_skips_refresh_when_flagged(tmp_path, monkeypatch, capsys):
    """Use 0.1.0 as the 'installed' version so the test is robust whether
    or not the current package version has been bumped yet.
    """
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.0\n", encoding="utf-8")
    rc = run_migrate(_args(no_refresh=True, non_interactive=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out
    assert "Skipping app discovery" in out
    # Marker is bumped to current version on completion.
    from winpodx import __version__ as current

    assert (tmp_path / "installed_version.txt").read_text().strip() == current


def test_run_migrate_non_interactive_skips_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.0\n", encoding="utf-8")
    # no_refresh=False but non_interactive=True -> must not block on input.
    called = []
    monkeypatch.setattr("builtins.input", lambda _: called.append(True) or "y")
    rc = run_migrate(_args(no_refresh=False, non_interactive=True))
    assert rc == 0
    assert called == []  # input() must never have been called
    out = capsys.readouterr().out.lower()
    assert "--non-interactive" in out


# --- L2: marker-file size cap + strict semver regex ---


def test_read_installed_version_accepts_valid(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.8\n", encoding="utf-8")
    assert _read_installed_version() == "0.1.8"


def test_read_installed_version_accepts_prerelease_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("1.2.3rc1\n", encoding="utf-8")
    assert _read_installed_version() == "1.2.3rc1"


def test_read_installed_version_rejects_oversized(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_bytes(b"0.1.8" + b"X" * 1024)
    assert _read_installed_version() is None
    err = capsys.readouterr().err
    assert "exceeds" in err


def test_read_installed_version_rejects_binary_garbage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_bytes(b"\xff\xfe\x00bad\x01")
    assert _read_installed_version() is None
    err = capsys.readouterr().err
    assert "not valid UTF-8" in err or "not a valid version" in err


def test_read_installed_version_rejects_shell_metachars(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.8; rm -rf /\n", encoding="utf-8")
    assert _read_installed_version() is None
    err = capsys.readouterr().err
    assert "not a valid version" in err


def test_read_installed_version_falls_back_when_invalid(tmp_path, monkeypatch):
    """Invalid marker -> _detect_installed_version returns the pre-tracker baseline
    when a winpodx.toml exists, so the upgrade path still runs."""
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("not-a-version\n", encoding="utf-8")
    # Also create a winpodx.toml so the pre-tracker fallback triggers.
    import winpodx.core.config as cfgmod

    monkeypatch.setattr(cfgmod.Config, "path", classmethod(lambda c: tmp_path / "winpodx.toml"))
    (tmp_path / "winpodx.toml").write_text("[rdp]\nuser = 'x'\n", encoding="utf-8")
    assert _detect_installed_version() == "0.1.7"


# --- _apply_runtime_fixes_to_existing_guest agent gate (v0.4.0) ---


class TestApplyRuntimeFixesAgentGate:
    """v0.4.0 (post-rc1): migrate's apply chain skips entirely when the
    in-guest agent isn't up. Why: dispatch() silently falls back to
    FreerdpTransport when /health doesn't answer, and a fresh FreeRDP
    login KICKS the autologon User session that install.bat is running
    in (rdprrap multi-session isn't loaded yet on first boot, so single-
    session enforcement is in effect). install.bat dies mid-stage and
    setup_done.txt / agent.ps1 staging never lands.
    """

    def _setup_cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.password = "abc123"
        cfg.rdp.user = "User"
        cfg.save()

    def test_skips_apply_chain_when_agent_unavailable(self, tmp_path, monkeypatch, capsys):
        """Agent /health down -> apply chain MUST be skipped, no FreeRDP fallback."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _apply_runtime_fixes_to_existing_guest
        from winpodx.core.pod import PodState
        from winpodx.core.transport.base import HealthStatus

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
            patch("winpodx.core.provisioner.apply_windows_runtime_fixes") as mock_apply,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_health.return_value = HealthStatus(available=False, detail="connection refused")

            _apply_runtime_fixes_to_existing_guest(non_interactive=True)

        out = capsys.readouterr().out
        assert not mock_apply.called, (
            "apply chain MUST NOT run when agent is down — would fall back to FreeRDP "
            "and kick install.bat's autologon session"
        )
        assert "Agent not yet up" in out
        assert "kicking the autologon" in out

    def test_runs_apply_chain_when_agent_responds(self, tmp_path, monkeypatch, capsys):
        """Agent /health up -> apply chain runs as before (via AgentTransport)."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _apply_runtime_fixes_to_existing_guest
        from winpodx.core.pod import PodState
        from winpodx.core.transport.base import HealthStatus

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
            patch("winpodx.core.provisioner.apply_windows_runtime_fixes") as mock_apply,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_health.return_value = HealthStatus(available=True, version="0.4.0")
            mock_apply.return_value = {
                "max_sessions": "ok",
                "rdp_timeouts": "ok",
                "oem_runtime_fixes": "ok",
                "vbs_launchers": "ok",
                "multi_session": "ok",
            }

            _apply_runtime_fixes_to_existing_guest(non_interactive=True)

        out = capsys.readouterr().out
        mock_apply.assert_called_once()
        assert "Agent not yet up" not in out
        assert "OK: applied" in out

    def test_skip_message_points_to_apply_fixes_for_recovery(self, tmp_path, monkeypatch, capsys):
        """Skip message must tell the user how to apply the chain manually
        once the agent is up — `winpodx pod apply-fixes` is the entry."""
        self._setup_cfg(tmp_path, monkeypatch)
        from winpodx.cli.migrate import _apply_runtime_fixes_to_existing_guest
        from winpodx.core.pod import PodState
        from winpodx.core.transport.base import HealthStatus

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch("winpodx.core.provisioner.wait_for_windows_responsive", return_value=True),
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
            patch("winpodx.core.provisioner.apply_windows_runtime_fixes") as mock_apply,
        ):
            mock_status.return_value = MagicMock(state=PodState.RUNNING)
            mock_health.return_value = HealthStatus(available=False, detail="timeout")

            _apply_runtime_fixes_to_existing_guest(non_interactive=True)

        out = capsys.readouterr().out
        assert "winpodx pod apply-fixes" in out
        mock_apply.assert_not_called()


# --- discover_apps WINPODX_REQUIRE_AGENT gate (v0.4.0) ---


class TestDiscoverAppsRequireAgent:
    """v0.4.0 (post-rc1): WINPODX_REQUIRE_AGENT=1 (set by install.sh's
    post-install discovery) suppresses the FreeRDP-RemoteApp fallback
    inside discover_apps. Same rationale as the migrate gate: a fresh
    FreeRDP login while install.bat is in-flight kicks the autologon
    User session.
    """

    def test_raises_agent_unavailable_when_env_set_and_agent_down(self, tmp_path, monkeypatch):
        from winpodx.core.config import Config
        from winpodx.core.discovery import DiscoveryError, discover_apps

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()

        monkeypatch.setenv("WINPODX_REQUIRE_AGENT", "1")

        # Avoid pod_status / runtime check failures by stubbing the
        # readiness probe + transport dispatch path.
        from winpodx.core.transport.base import HealthStatus

        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch(
                "winpodx.core.discovery._wait_for_transport_ready",
                lambda *a, **k: None,
            ),
            patch(
                "winpodx.core.discovery._ps_script_path",
                lambda: tmp_path / "discover_apps.ps1",
            ),
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
        ):
            mock_health.return_value = HealthStatus(available=False, detail="connection refused")
            (tmp_path / "discover_apps.ps1").write_text("# stub\n", encoding="utf-8")

            try:
                discover_apps(cfg, timeout=5)
            except DiscoveryError as e:
                assert getattr(e, "kind", None) == "agent_unavailable"
                assert "WINPODX_REQUIRE_AGENT" in str(e) or "agent" in str(e).lower()
            else:
                raise AssertionError(
                    "discover_apps must raise DiscoveryError(kind='agent_unavailable') "
                    "when WINPODX_REQUIRE_AGENT=1 and agent /health is down"
                )

    def test_falls_back_to_freerdp_when_env_not_set(self, tmp_path, monkeypatch):
        """Default behavior (no env var) -> FreeRDP fallback still works.
        The gate is opt-in via env var; existing call sites keep their
        fallback semantics so GUI's Apply Fixes button etc. aren't
        affected."""
        from winpodx.core.config import Config
        from winpodx.core.discovery import discover_apps
        from winpodx.core.transport.base import HealthStatus
        from winpodx.core.windows_exec import WindowsExecResult

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()

        monkeypatch.delenv("WINPODX_REQUIRE_AGENT", raising=False)

        # Stub script path + readiness probe; mock dispatch to return
        # an unavailable agent so the FreeRDP fallback path executes.
        with (
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch(
                "winpodx.core.discovery._wait_for_transport_ready",
                lambda *a, **k: None,
            ),
            patch(
                "winpodx.core.discovery._ps_script_path",
                lambda: tmp_path / "discover_apps.ps1",
            ),
            patch("winpodx.core.transport.agent.AgentTransport.health") as mock_health,
            patch("winpodx.core.windows_exec.run_in_windows") as mock_run,
        ):
            mock_health.return_value = HealthStatus(available=False, detail="not up")
            mock_run.return_value = WindowsExecResult(rc=0, stdout="[]", stderr="")
            (tmp_path / "discover_apps.ps1").write_text("# stub\n", encoding="utf-8")

            apps = discover_apps(cfg, timeout=5)

        assert apps == []
        # discover_apps retries once on a suspiciously-empty result, so
        # 1-2 calls are both valid; the assertion that matters here is
        # that the FreeRDP fallback path executed at all (proving the
        # default behavior wasn't accidentally locked down).
        assert mock_run.call_count >= 1, (
            "without WINPODX_REQUIRE_AGENT, FreeRDP fallback path must execute"
        )
